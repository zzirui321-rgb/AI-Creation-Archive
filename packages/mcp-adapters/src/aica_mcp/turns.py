import copy
import mimetypes
import time
from contextlib import contextmanager
from pathlib import Path

from aica_schema.protocol import ProtocolError, digest, dumps, new_id, require, utc_now
from aica_schema.recording import require_host_hook

from .steps import step_plan


class TurnGate:
    def __init__(self, recorder, project_access=None):
        self.recorder = recorder
        self.bridges = {}
        self.bridge_profiles = {}
        self.turns = {}
        self.project_access = project_access or (lambda: {"state": "unconfigured"})

    def attach(self, client="codex", owner_pid=None):
        bridge_id = new_id()
        self.bridges[bridge_id] = time.monotonic()
        self.bridge_profiles[bridge_id] = {"client": client, "owner_pid": owner_pid}
        return {"status": "success", "bridge_id": bridge_id}

    def key(self, client, host, turn_id):
        return (client, host, turn_id)

    def bridge_key(self, bridge_id, host, turn_id):
        require(bridge_id in self.bridge_profiles, "Reconnect the consent bridge", "bridge_expired")
        result = self.key(self.bridge_profiles[bridge_id]["client"], host, turn_id)
        return result

    def matching_bridge(self, client, owner_pids, bridge_id=None):
        self.expire()
        if bridge_id is not None:
            require(bridge_id in self.bridge_profiles and self.bridge_profiles[bridge_id]["client"] == client,
                    "The Host MCP connection is no longer attached", "bridge_expired")
            return bridge_id
        matches = [bridge for bridge, profile in self.bridge_profiles.items()
                   if profile["client"] == client and profile["owner_pid"] in owner_pids]
        require(len(matches) == 1, "No unique connected MCP bridge for this Host process", "bridge_ambiguous")
        return matches[0]

    def active(self, bridge_id):
        self.expire()
        self.heartbeat(bridge_id)
        matches = [turn for turn in self.turns.values() if turn.get("bridge_id") == bridge_id and turn["state"] == "recording"]
        turn = {"host_session_id": matches[0]["host_session_id"], "turn_id": matches[0]["turn_id"]} if len(matches) == 1 else None
        return {"status": "success", "turn": turn}

    def heartbeat(self, bridge_id):
        require(bridge_id in self.bridges, "Reconnect the consent bridge", "bridge_expired")
        self.bridges[bridge_id] = time.monotonic()
        return {"status": "success"}

    def detach(self, bridge_id):
        self.bridges.pop(bridge_id, None)
        self.bridge_profiles.pop(bridge_id, None)
        for turn in self.turns.values():
            if turn.get("bridge_id") == bridge_id:
                self.close(turn, "connection_lost")
        return {"status": "success"}

    def expire(self):
        now = time.monotonic()
        for bridge_id, seen in list(self.bridges.items()):
            if now - seen > 10:
                self.detach(bridge_id)
        for turn in self.turns.values():
            if turn["state"] == "pending" and not turn.get("conversation") and now - turn["created"] > 300:
                self.close(turn, "confirmation_timeout")

    def pending(self, bridge_id, host_session_id, turn_id):
        self.expire()
        self.heartbeat(bridge_id)
        turn = self.turns.get(self.bridge_key(bridge_id, host_session_id, turn_id))
        if turn is None:
            return {"status": "success", "state": "unavailable"}
        if turn["state"] == "pending" and turn.get("bridge_id") in {None, bridge_id}:
            turn["bridge_id"] = bridge_id
            return {"status": "success", "state": "pending", "challenge": turn["challenge"], "preview": turn["prompt"][:500],
                    "coverage": turn["coverage"]}
        return {"status": "success", "state": turn["state"] if turn.get("bridge_id") == bridge_id else "unavailable",
                "conversation": turn.get("conversation", False) if turn.get("bridge_id") == bridge_id else False,
                "project_authorized": bool(turn.get("project_grant_id")) if turn.get("bridge_id") == bridge_id else False}

    def prepare_conversation(self, bridge_id, host_session_id, turn_id):
        pending = self.pending(bridge_id, host_session_id, turn_id)
        require(pending["state"] == "pending", "No pending conversation", "turn_not_authorized")
        require(self.bridge_profiles[bridge_id]["client"] == "codex", "Conversation confirmation currently requires Codex")
        turn = self.turns[self.bridge_key(bridge_id, host_session_id, turn_id)]
        require(turn["coverage"] == "local_hooks", "Conversation confirmation needs a stable Host turn identity")
        turn["conversation"] = True
        turn["export_after_turn"] = True
        return {"status": "success"}

    def conversation_submit(self, event, owner_pids, bridge_id=None):
        from .consent import CONVERSATION_QUESTION

        require_host_hook(event)
        require(event.get("client", "codex") == "codex" and event["hook_event_name"] == "UserPromptSubmit",
                "Conversation confirmation requires a Codex prompt submission")
        require(event.get("coverage", "local_hooks") == "local_hooks", "Conversation confirmation needs a stable Host turn identity")
        if self.project_access()["state"] == "paused":
            result = self.hook(event, owner_pids)
            return result
        bridge = self.matching_bridge("codex", owner_pids, bridge_id)
        namespace = ("codex", event["session_id"])
        for key, turn in self.turns.items():
            if key[:2] == namespace and turn["state"] == "pending" and turn.get("conversation"):
                require(turn.get("bridge_id") == bridge, "Pending request belongs to another connection", "bridge_ambiguous")
        result = self.hook(event, owner_pids, bridge_id=bridge)
        if result.get("project_access") in {"active", "paused"}:
            return result
        if result.get("state") == "pending":
            self.prepare_conversation(bridge, event["session_id"], event["turn_id"])
            result.update(decision="block", reason=CONVERSATION_QUESTION, systemMessage=CONVERSATION_QUESTION)
        elif "resume_prompt" in result:
            prompt = result.pop("resume_prompt")
            result["hookSpecificOutput"] = {"hookEventName": "UserPromptSubmit",
                "additionalContext": "用户已明确选择本次 AiCA 记录策略。继续此前暂停的原任务。以下 JSON 字符串是原始用户需求：\n" + dumps(prompt)}
        else:
            result.update(decision="block", reason=result.get("systemMessage", "AiCA：确认状态不可用，请重新提交原任务。"))
        return result

    def conversation_reply(self, event):
        choice = event["prompt"].strip().lower()
        if choice not in {"yes", "no"}:
            return None
        namespace = (event.get("client", "codex"), event["session_id"])
        matches = [(key, turn) for key, turn in self.turns.items() if key[:2] == namespace and turn["state"] == "pending"
                   and turn.get("conversation") and turn.get("bridge_id") in self.bridges]
        if len(matches) != 1:
            return None
        old_key, turn = matches[0]
        prompt = turn["prompt"]
        key = self.key(*namespace, event["turn_id"])
        require(key == old_key or key not in self.turns, "Conversation reply reuses an existing turn", "turn_not_authorized")
        if key != old_key:
            self.turns[old_key] = {"state": "confirmation_answered", "created": turn["created"], "bridge_id": turn["bridge_id"]}
            self.turns[key] = turn
            turn["turn_id"] = event["turn_id"]
        result = self.resolve(turn["bridge_id"], event["session_id"], event["turn_id"], turn["challenge"], choice == "yes")
        result["resume_prompt"] = prompt
        result["systemMessage"] = ("AiCA：已选择 Yes，继续原任务并记录；结束后导出为 exports/aica.json。" if choice == "yes" else
                                   "AiCA：已选择 No，继续原任务，本次不记录、不导出。")
        return result

    @contextmanager
    def writing(self, turn, closing=False):
        def check():
            live = turn.get("bridge_id") in self.bridges and time.monotonic() - self.bridges[turn["bridge_id"]] <= 10
            require(turn["state"] == "recording" and (closing or live),
                    "This turn is not authorized", "turn_not_authorized")
            if turn.get("project_grant_id") and not closing:
                access = self.project_access()
                require(access["state"] == "active" and access["grant_id"] == turn["project_grant_id"],
                        "Project authorization is paused or changed", "turn_not_authorized")

        previous = self.recorder.recording_permission
        self.recorder.recording_permission = check
        try:
            check()
            yield
        finally:
            self.recorder.recording_permission = previous

    def emit(self, turn, kind, payload, agent_id=None):
        sequence = turn["sequence"] + 1
        event = {"event_id": new_id(), "source_id": turn["source_id"], "source_seq": sequence,
                 "session_id": turn["session"]["session_id"], "agent_id": agent_id, "type": kind,
                 "timestamp": utc_now(), "causal_parents": [], "payload": payload}
        result = self.recorder.append(event, turn["session"]["session_token"])
        turn["sequence"] = sequence
        return result

    def resolve(self, bridge_id, host_session_id, turn_id, challenge, record):
        self.expire()
        turn = self.turns.get(self.bridge_key(bridge_id, host_session_id, turn_id))
        require(turn is not None and turn["state"] == "pending" and turn.get("bridge_id") == bridge_id
                and turn["challenge"] == challenge, "Confirmation is no longer current", "turn_not_authorized")
        self.heartbeat(bridge_id)
        prompt = turn.pop("prompt", "")
        turn.pop("challenge", None)
        if record is not True:
            self.close(turn, "declined")
            return {"status": "success", "recording": False, "state": "declined"}
        turn["state"] = "recording"
        turn["export_after_turn"] = True
        with self.writing(turn):
            turn["session"] = self.recorder.start_session(new_id(), True)
            client = turn["client"]
            config = {"adapter": client + "-hooks", "host_session_id": host_session_id, "host_turn_id": turn_id,
                      "coverage": turn["coverage"]}
            harness = {"id": new_id(), "name": client + " Hook bridge", "version": "0.1.0", "config": config,
                       "state": {}, "availability": {"host_system_instructions": "missing", "transcript": "not_authorized"}}
            model = turn.pop("model", {})
            actor_id = "agent-" + digest([turn["session"]["session_id"], client, model, harness["id"]])[:32]
            agent = {"id": actor_id, "session_id": turn["session"]["session_id"], "parent_agent_id": None,
                     "harness_id": harness["id"], "client": client, "model": model}
            turn["agent_id"] = agent["id"]
            turn["harness"] = harness
            turn["agent_model"] = model
            self.emit(turn, "harness.registered", harness)
            self.emit(turn, "agent.registered", agent)
            self.emit(turn, "message.recorded", {"role": "user", "content": prompt}, agent["id"])
            missing = ["tool_calls", "assistant_replies"] if turn["coverage"] == "prompt_only" else ["hosted_tools", "intermediate_assistant_messages", "original_execution_timing"]
            self.emit(turn, "note.recorded", {"text": client + " coverage: " + turn["coverage"] + "; no transcript sweep.",
                      "provenance": {"mode": "recorded", "evidence": [],
                                     "missing": missing}})
        return {"status": "success", "recording": True, "state": "recording"}

    def close(self, turn, reason):
        if turn["state"] == "recording":
            try:
                with self.writing(turn, closing=True):
                    for plan in turn["tools"].values():
                        self.finish(turn, plan, {"recording_interrupted": True}, "unknown")
                    self.emit(turn, "session.closed", {})
            except (ProtocolError, OSError):
                pass
        retained = {key: turn[key] for key in ("created", "bridge_id") if key in turn}
        turn.clear()
        turn.update(retained, state=reason)

    def hook(self, event, owner_pids=None, bridge_id=None):
        self.expire()
        require_host_hook(event)
        require(Path(event["cwd"]).resolve() == self.recorder.project,
                "Hook workspace does not match this recorder", "workspace_mismatch")
        name = event["hook_event_name"]
        host = event["session_id"]
        client = event.get("client", "codex")
        namespace = (client, host)
        if name in {"SessionStart", "SessionEnd"}:
            if name == "SessionStart" and event.get("source") == "compact":
                return {"status": "success", "state": "unchanged"}
            for key, turn in self.turns.items():
                if key[:2] == namespace:
                    self.close(turn, "connection_reset")
            access = self.project_access()
            message = ("AiCA：项目授权已生效，自动记录和上传；可随时暂停。" if access["state"] == "active" else
                       "AiCA：项目已暂停，本次不记录、不上传。" if access["state"] == "paused" else
                       "AiCA 已连接，将按本轮确认策略处理；明确选择 No 不采集。")
            return {"status": "success", "recording": False,
                    "systemMessage": message if name == "SessionStart" else "AiCA：当前会话录制已结束。"}
        key = self.key(client, host, event["turn_id"])
        turn = self.turns.get(key)
        if name == "UserPromptSubmit":
            access = self.project_access()
            if access["state"] == "paused":
                for other, previous in self.turns.items():
                    if other[:2] == namespace:
                        self.close(previous, "project_paused")
                return {"status": "success", "recording": False, "project_access": "paused",
                        "systemMessage": "AiCA：项目已暂停，本次不记录、不上传。"}
            reply = self.conversation_reply(event)
            if reply is not None:
                return reply
            for other, previous in self.turns.items():
                if other[:2] == namespace:
                    self.close(previous, "new_submission")
            if turn is not None:
                return {"status": "success", "recording": False, "state": "ambiguous_submission",
                        "systemMessage": "AiCA：此轮标识被复用，无法区分追加消息与重试，本轮停止采集。"}
            model = event.get("agent_model", {"name": event["model"]} if event.get("model") else {})
            self.turns[key] = {"state": "pending", "created": time.monotonic(), "prompt": event["prompt"],
                               "client": client, "host_session_id": host, "turn_id": event["turn_id"],
                               "coverage": event.get("coverage", "local_hooks"),
                               "model": model, "challenge": new_id(), "sequence": 0, "source_id": new_id(),
                               "tools": {}, "completed_tools": {}, "bridge_id": None, "started_wall": time.time()}
            if access["state"] == "active":
                bridge = self.matching_bridge(client, owner_pids or [], bridge_id)
                turn = self.turns[key]
                turn.update(bridge_id=bridge, project_grant_id=access["grant_id"], export_after_turn=True)
                result = self.resolve(bridge, host, event["turn_id"], turn["challenge"], True)
                result.update(project_access="active", systemMessage="AiCA：按项目授权记录，附件和上传自动处理。")
                return result
            return {"status": "success", "recording": False, "state": "pending"}
        if turn is not None and name == "Interrupt" and turn["state"] == "pending":
            self.close(turn, "interrupted")
        if turn is None or turn["state"] != "recording":
            return {"status": "success", "recording": False}
        if turn.get("project_grant_id"):
            access = self.project_access()
            if access["state"] != "active" or access.get("grant_id") != turn["project_grant_id"]:
                self.close(turn, "project_paused")
                return {"status": "success", "recording": False}
        if name == "Interrupt":
            self.close(turn, "interrupted")
            return {"status": "success", "recording": False, "state": "interrupted"}
        if turn["coverage"] == "prompt_only" and name != "Stop":
            return {"status": "success", "recording": True, "coverage": "prompt_only"}
        with self.writing(turn):
            bridge_id = turn["bridge_id"]
            export_requested = name == "Stop" and turn.get("export_after_turn", False)
            if name in {"Stop", "AssistantMessage"}:
                if event.get("last_assistant_message") and turn["coverage"] != "prompt_only":
                    self.emit(turn, "message.recorded", {"role": "assistant", "content": event["last_assistant_message"]},
                              turn["agent_id"])
                if name == "Stop":
                    self.close(turn, "completed")
            elif name in {"PreToolUse", "PostToolUse"}:
                tool = event["tool_name"]
                if "aica_" not in tool:
                    self.observe_tool(turn, event)
                if name == "PreToolUse" and tool.split(".")[-1] in {"Agent", "spawn_agent", "task", "delegate_to_agent"}:
                    self.emit(turn, "note.recorded", {"text": "Recording closed before child Agent execution; child identity requires separate consent.",
                              "provenance": {"mode": "recorded", "evidence": [], "missing": ["child_agent_identity"]}})
                    self.close(turn, "child_scope_unsupported")
        return {"status": "success", "recording": turn["state"] == "recording", "export_requested": export_requested,
                "bridge_id": bridge_id}

    def observe_tool(self, turn, event):
        identity = event["tool_use_id"]
        if event["hook_event_name"] == "PreToolUse":
            if identity not in turn["tools"]:
                now = utc_now()
                step = {"label": event["tool_name"], "client": turn["client"], "started_at": now, "ended_at": now,
                        "state": "succeeded", "inputs": clean(event["tool_input"]), "model": turn["agent_model"],
                        "harness": turn["harness"],
                        "execution": {"kind": "manual", "tool": event["tool_name"], "version": None,
                                      "inputs": {}, "params": {}, "outputs": []},
                        "provenance": {"mode": "recorded", "evidence": [], "missing": ["executable_binding", "original_execution_timing"]}}
                archive = self.recorder.read_archive()["archive"]
                plan = step_plan(archive, turn["session"]["session_id"], identity, step)
                for message in plan["events"]:
                    self.recorder.append(message, turn["session"]["session_token"])
                turn["tools"][identity] = plan
        elif identity in turn["tools"]:
            plan = turn["tools"][identity]
            response = event.get("tool_response")
            failed = isinstance(response, dict) and (response.get("isError") is True or response.get("is_error") is True
                                                    or isinstance(response.get("exit_code"), int) and response["exit_code"] != 0)
            if turn.get("project_grant_id") and not failed:
                self.capture_generated(turn, event)
            self.finish(turn, plan, {"tool_response": clean(response)}, "failed" if failed else "succeeded")
            turn["completed_tools"][identity] = plan
            turn["tools"].pop(identity)

    def capture_generated(self, turn, event):
        tool = event["tool_name"].split(".")[-1].lower()
        if not any(name in tool for name in ("imagegen", "generate_image", "write_file", "create_file", "save_file")) and tool != "write":
            return
        paths = []

        def visit(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in {"path", "file_path", "output_path", "image_path"} and isinstance(item, str):
                        paths.append(item)
                    elif isinstance(item, (dict, list)):
                        visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(event.get("tool_response"))
        for name in list(dict.fromkeys(paths))[:32]:
            path = Path(name)
            path = path if path.is_absolute() else self.recorder.project / path
            resolved = path.resolve()
            if resolved.is_relative_to(self.recorder.project):
                relative = resolved.relative_to(self.recorder.project)
                if not any(part.startswith(".") for part in relative.parts) and resolved.is_file() and resolved.stat().st_mtime >= turn["started_wall"]:
                    try:
                        self.capture(turn["bridge_id"], turn["host_session_id"], turn["turn_id"],
                                     [relative.as_posix()], event["tool_use_id"])
                    except (ProtocolError, OSError):
                        self.emit(turn, "note.recorded", {"text": "Generated artifact was not registered; select the saved output explicitly.",
                                  "provenance": {"mode": "recorded", "evidence": [], "missing": ["generated_artifact"]}})

    def finish(self, turn, plan, outputs, state="succeeded"):
        finish = copy.deepcopy(plan["finish"])
        finish["payload"] = {"run_id": plan["run_id"], "state": state, "ended_at": utc_now(),
                             "outputs": outputs, "artifact_ids": [item["id"] for item in self.recorder.document["artifacts"]
                                                                    if item["producer_run_id"] == plan["run_id"]]}
        result = self.recorder.append(finish, turn["session"]["session_token"])
        return result

    def capture(self, bridge_id, host_session_id, turn_id, paths, tool_use_id=None):
        self.expire()
        turn = self.turns.get(self.bridge_key(bridge_id, host_session_id, turn_id))
        require(turn is not None and turn.get("bridge_id") == bridge_id,
                "This connection has no active turn", "turn_not_authorized")
        artifacts = []
        with self.writing(turn):
            plan = turn["completed_tools"].get(tool_use_id) or turn["tools"].get(tool_use_id)
            require(tool_use_id is None or plan is not None,
                    "Selected producer was not observed in this turn", "unknown_producer")
            for path in paths:
                mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
                sequence = turn["sequence"] + 1
                event = {"event_id": new_id(), "source_id": turn["source_id"], "source_seq": sequence,
                         "session_id": turn["session"]["session_id"], "agent_id": turn["agent_id"],
                         "type": "artifact.recorded", "timestamp": utc_now(), "causal_parents": [], "payload": {}}
                result = self.recorder.capture_artifact(turn["session"]["session_token"], event, path, mime_type,
                                                        plan["node_id"] if plan else None, plan["run_id"] if plan else None)
                turn["sequence"] = sequence
                artifacts.append(result["artifact"])
        return {"status": "success", "artifacts": artifacts}


def clean(value):
    if isinstance(value, dict):
        result = {key: "[redacted]" if any(part in key.lower() for part in ("token", "secret", "password", "api_key"))
                  else clean(item) for key, item in value.items()}
    elif isinstance(value, list):
        result = [clean(item) for item in value]
    else:
        result = value
    return result
