const nonce = location.hash.slice(1);
history.replaceState(null, "", "/approval");
let pending = true;
let decisionActions = false;
let timer;
const message = document.getElementById("message");
const status = document.getElementById("status");
const buttons = [document.getElementById("no"), document.getElementById("yes")];
async function request(path, body) {
  const response = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({nonce, ...body})});
  const result = await response.json();
  if (!response.ok || result.status !== "success") throw new Error("确认已失效，本轮未获得录制授权。");
  return result;
}
async function decide(choice, action = "choose") {
  buttons.forEach(button => { button.disabled = true; });
  clearTimeout(timer);
  try {
    const result = await request("/approval/decision", {choice, ...(decisionActions ? {action} : {})});
    status.textContent = result.choice ? "本轮已授权记录。可以关闭此页。" : "本轮不记录。可以关闭此页。";
    pending = false;
  } catch (error) { status.textContent = error.message; }
  message.textContent = "";
}
buttons[0].onclick = () => decide(false);
buttons[1].onclick = () => decide(true);
window.addEventListener("pagehide", () => {
  if (pending) navigator.sendBeacon("/approval/decision", new Blob([JSON.stringify({nonce, choice: false, ...(decisionActions ? {action: "dismiss"} : {})})], {type: "application/json"}));
});
request("/approval/view", {}).then(result => {
  decisionActions = result.decision_actions === true;
  document.getElementById("project").textContent = "项目：" + result.project;
  message.textContent = result.message;
  buttons.forEach(button => { button.disabled = false; });
  buttons[0].focus();
  timer = setTimeout(() => decide(false, "timeout"), result.seconds_left * 1000);
}).catch(error => { pending = false; message.textContent = error.message; });
