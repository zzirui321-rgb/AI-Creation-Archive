import secrets
import time

from aica_schema.protocol import ProtocolError, require


class ApprovalPages:
    def __init__(self, gate):
        self.gate = gate
        self.origin = None
        self.requests = {}

    def create(self, bridge_id, message, turn=None):
        self.gate.heartbeat(bridge_id)
        request_id = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        self.requests[request_id] = {"state": "pending", "choice": False, "created": time.monotonic(),
                                     "bridge_id": bridge_id, "message": message, "nonce": nonce, "turn": turn,
                                     "coverage": turn.get("coverage") if turn else None}
        return {"status": "success", "request_id": request_id, "url": self.origin + "/approval#" + nonce}

    def for_turn(self, event, owner_pids):
        result = self.gate.hook(event)
        require(result.get("state") == "pending", "No pending turn", "turn_not_authorized")
        bridge = self.gate.matching_bridge(event.get("client", "codex"), owner_pids)
        pending = self.gate.pending(bridge, event["session_id"], event["turn_id"])
        require(pending["state"] == "pending", "No pending turn", "turn_not_authorized")
        turn = {"host_session_id": event["session_id"], "turn_id": event["turn_id"],
                "challenge": pending["challenge"], "coverage": pending["coverage"]}
        scope = "仅本次需求；工具和回复因缺少轮次标识不采集" if turn["coverage"] == "prompt_only" else "本轮需求、可关联的可见回复和本地工具调用"
        message = f"是否记录{scope}？附件另行选择，上传权限独立。\n\n本次需求：\n{pending['preview']}"
        result = self.create(bridge, message, turn)
        return result

    def finish(self, item, choice, state):
        if item["state"] != "pending":
            return
        approved = choice is True
        if item["turn"]:
            turn = item["turn"]
            try:
                result = self.gate.resolve(item["bridge_id"], turn["host_session_id"], turn["turn_id"], turn["challenge"], approved)
                approved = result["recording"]
            except ProtocolError:
                approved = False
                state = "unavailable"
        item.update(state=state, choice=approved)
        for key in ("message", "nonce", "turn"):
            item.pop(key, None)

    def expire(self):
        for request_id, item in list(self.requests.items()):
            age = time.monotonic() - item["created"]
            if item["state"] == "pending" and (age > 120 or item["bridge_id"] not in self.gate.bridges):
                self.finish(item, False, "expired")
            if age > 300:
                self.requests.pop(request_id, None)

    def status(self, request_id):
        self.expire()
        item = self.requests.get(request_id)
        require(item is not None, "Confirmation expired", "confirmation_expired")
        return {"status": "success", "state": item["state"], "choice": item["choice"], "coverage": item["coverage"]}

    def cancel(self, request_id):
        item = self.requests.get(request_id)
        if item:
            self.finish(item, False, "cancelled")
        return {"status": "success"}

    def find(self, nonce):
        self.expire()
        require(isinstance(nonce, str) and len(nonce) >= 32, "Invalid confirmation token")
        matches = [item for item in self.requests.values() if item.get("nonce") and secrets.compare_digest(item["nonce"], nonce)]
        require(len(matches) == 1, "Confirmation is no longer current", "confirmation_expired")
        return matches[0]

    def view(self, nonce):
        item = self.find(nonce)
        return {"status": "success", "project": self.gate.recorder.project.name, "message": item["message"],
                "seconds_left": max(0, int(120 - (time.monotonic() - item["created"]))), "decision_actions": True}

    def decide(self, nonce, choice, action="choose"):
        require(type(choice) is bool, "choice must be a boolean")
        require(action in {"choose", "dismiss", "timeout"} and (action == "choose" or choice is False), "Only an explicit choice can approve")
        item = self.find(nonce)
        state = "cancelled" if action == "dismiss" else "expired" if action == "timeout" else "accepted" if choice else "declined"
        self.finish(item, choice, state)
        return {"status": "success", "choice": item["choice"], "state": item["state"]}
