import { spawn } from "node:child_process";

const python = "AICA_PYTHON_EXECUTABLE";
export const AicaPlugin = async ({ directory }) => {
  const turns = new Map();
  const parents = new Map();
  const messages = new Map();
  const tools = new Map();
  let queue = Promise.resolve();
  function send(event) {
    const task = () => new Promise(resolve => {
      const child = spawn(python, ["-m", "aica_mcp", "agent-hook", "--client", "opencode", "--confirmation", "browser"], {stdio: ["pipe", "pipe", "ignore"], cwd: directory});
      let response = "";
      child.stdout.on("data", chunk => { if (response.length < 4096) response += chunk; });
      const timer = setTimeout(() => child.kill(), event.hook_event_name === "Stop" ? 600000 : event.hook_event_name === "UserPromptSubmit" ? 130000 : 10000);
      child.on("error", () => { clearTimeout(timer); resolve(false); });
      child.on("close", code => {
        clearTimeout(timer);
        try { resolve(code === 0 && JSON.parse(response).recording === true); } catch { resolve(false); }
      });
      child.stdin.on("error", () => {});
      child.stdin.end(JSON.stringify({client: "opencode", cwd: directory, ...event}));
    });
    queue = queue.then(task, task);
    return queue;
  }
  function forget(sessionID) {
    turns.delete(sessionID);
    for (const [id, item] of parents) if (item.sessionID === sessionID) { parents.delete(id); messages.delete(id); }
    for (const [id, item] of tools) if (item.sessionID === sessionID) tools.delete(id);
  }
  async function end(sessionID) {
    forget(sessionID);
    await send({session_id: sessionID, hook_event_name: "SessionEnd"});
  }
  return {
    "chat.message": async (input, output) => {
      const sessionID = input.sessionID;
      const turnID = output.message?.id;
      if (!sessionID || !turnID || output.message?.role !== "user") return;
      forget(sessionID);
      const prompt = output.parts.filter(part => part.type === "text" && !part.synthetic).map(part => part.text).join("\n");
      const model = input.model ? {name: input.model.modelID, provider: input.model.providerID} : {};
      const recording = await send({session_id: sessionID, turn_id: turnID, hook_event_name: "UserPromptSubmit", prompt, agent_model: model});
      if (recording) turns.set(sessionID, turnID);
    },
    event: async ({event}) => {
      const props = event.properties || {};
      if (event.type === "message.updated") {
        const info = props.info;
        if (!info || info.role !== "assistant" || turns.get(info.sessionID) !== info.parentID) return;
        parents.set(info.id, {sessionID: info.sessionID, turnID: info.parentID});
        if (info.time?.completed && messages.has(info.id)) {
          const text = [...messages.get(info.id).values()].join("\n");
          messages.delete(info.id);
          if (text) await send({session_id: info.sessionID, turn_id: info.parentID, hook_event_name: "AssistantMessage", last_assistant_message: text});
        }
      } else if (event.type === "message.part.updated") {
        const part = props.part;
        const parent = part && parents.get(part.messageID);
        if (!parent || turns.get(parent.sessionID) !== parent.turnID || part.sessionID !== parent.sessionID) return;
        if (part.type === "text" && !part.synthetic) {
          if (!messages.has(part.messageID)) messages.set(part.messageID, new Map());
          messages.get(part.messageID).set(part.id, part.text);
        } else if (part.type === "tool" && part.callID && part.state?.input) {
          const key = parent.sessionID + ":" + part.callID;
          const state = part.state.status;
          const envelope = {session_id: parent.sessionID, turn_id: parent.turnID, tool_name: part.tool, tool_use_id: part.callID, tool_input: part.state.input};
          if (state === "running" && !tools.has(key)) {
            tools.set(key, {sessionID: parent.sessionID, state});
            await send({...envelope, hook_event_name: "PreToolUse"});
          } else if (["completed", "error"].includes(state) && tools.get(key)?.state === "running") {
            tools.set(key, {sessionID: parent.sessionID, state});
            await send({...envelope, hook_event_name: "PostToolUse", tool_response: state === "error" ? {isError: true, error: part.state.error} : part.state.output});
          }
        }
      } else if (["session.idle", "session.error", "session.deleted"].includes(event.type)) {
        const sessionID = props.sessionID || props.info?.id;
        if (sessionID && turns.has(sessionID)) {
          if (event.type === "session.idle") await send({session_id: sessionID, turn_id: turns.get(sessionID), hook_event_name: "Stop"});
          await end(sessionID);
        }
      }
    },
    dispose: async () => { for (const sessionID of [...turns.keys()]) await end(sessionID); }
  };
};
