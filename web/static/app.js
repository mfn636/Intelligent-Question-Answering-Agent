"use strict";

const $ = (id) => document.getElementById(id);
const shell = document.querySelector(".workspace");
const input = $("message-input");
const busyPhases = new Set(["queued", "running"]);
const statusNames = { idle: "等待输入", running: "运行中", completed: "已完成", failed: "执行失败" };
const routeNames = { direct: "直接回复", workflow: "固定工作流", agent: "ReAct Agent" };
const drafts = new Map();
const pendingRequests = new Map();
let activeId = null;
let currentSnapshot = null;
let viewEpoch = 0;
let submitting = false;
let creating = false;
let pollTimer = null;
let toastTimer = null;
let sessionListSignature = "";
let transcriptSignature = "";
let memorySignature = "";
let eventsSignature = "";

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  svg.classList.add("icon");
  svg.setAttribute("aria-hidden", "true");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function text(id, value) { $(id).textContent = value; }
function storedSession() { try { return sessionStorage.getItem("agent-session"); } catch { return null; } }
function rememberSession(id) { try { sessionStorage.setItem("agent-session", id); } catch { /* Storage may be disabled. */ } }

function connection(online) {
  $("connection-status").className = `connection ${online ? "connected" : "offline"}`;
  text("connection-label", online ? "已连接" : "连接中断");
  if (!online) text("sync-label", "同步暂停 · 正在重连");
}

function toast(message) {
  clearTimeout(toastTimer);
  text("toast", message);
  $("toast").hidden = false;
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 3500);
}

function showError(message) {
  text("request-error", message || "");
  $("request-error").hidden = !message;
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      credentials: "same-origin", cache: "no-store", signal: AbortSignal.timeout(12000),
      ...options,
      headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers },
    });
  } catch {
    connection(false);
    throw new Error("暂时无法连接服务。请确认服务仍在运行，然后重试。");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : "请求格式不正确，请输入 1～10000 个字符。";
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  connection(true);
  return response.status === 204 ? null : response.json();
}

function updateComposer() {
  const busy = currentSnapshot && busyPhases.has(currentSnapshot.phase);
  input.disabled = !activeId;
  $("send-button").disabled = !activeId || submitting || busy || !input.value.trim();
  $("new-session").disabled = creating;
  $("mobile-new").disabled = creating;
}

function renderMessages(messages) {
  const signature = JSON.stringify(messages);
  if (signature === transcriptSignature) return;
  transcriptSignature = signature;
  const scroll = $("chat-scroll");
  const nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 120;
  const container = $("messages");
  const existing = Array.from(container.children);
  const canAppend = existing.length <= messages.length && existing.every(
    (element, index) => element.dataset.messageId === messages[index].id,
  );
  // 只追加新消息，保留旧气泡、文字选择和键盘焦点。
  if (!canAppend) container.replaceChildren();
  const fragment = document.createDocumentFragment();
  for (const item of messages.slice(canAppend ? existing.length : 0)) {
    const article = node("article", `message ${item.role}`);
    article.dataset.messageId = item.id;
    if (item.role !== "error") {
      const heading = node("div", "message-heading");
      const avatar = node("span", "message-avatar");
      if (item.role === "assistant") avatar.append(icon("spark"));
      else avatar.textContent = "你";
      const time = node("time", "", new Date(item.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }));
      time.dateTime = item.created_at;
      heading.append(avatar, node("span", "", item.role === "assistant" ? "Agent" : "你"), time);
      article.append(heading);
    }
    // All model/user/tool content is inserted as text, never interpreted as HTML.
    article.append(node("div", "message-content", item.content));
    if (item.role === "assistant") {
      const copy = node("button", "message-copy");
      copy.type = "button";
      copy.append(icon("copy"), node("span", "", "复制回答"));
      copy.addEventListener("click", async () => {
        try { await navigator.clipboard.writeText(item.content); toast("回答已复制"); }
        catch { toast("无法访问剪贴板，请选中文字复制。"); }
      });
      article.append(copy);
    }
    fragment.append(article);
  }
  container.append(fragment);
  $("welcome").hidden = messages.length > 0;
  if (nearBottom) requestAnimationFrame(() => { scroll.scrollTop = scroll.scrollHeight; });
}

function renderMemory(memory) {
  const signature = JSON.stringify(memory);
  if (signature === memorySignature) return;
  memorySignature = signature;
  text("memory-count", `${memory.messages.length} 条`);
  text("recent-count", memory.messages.length);
  text("memory-summary", memory.summary || "对话积累后，较早的内容会被整理成摘要，保留重要信息。");
  $("memory-summary").classList.toggle("muted", !memory.summary);
  text("summary-tag", memory.summary ? "已生成" : "尚未生成");
  text("summary-trigger", `> ${memory.summary_trigger} 条`);
  text("memory-window", `≤ ${memory.window} 条`);
  const fragment = document.createDocumentFragment();
  for (const item of memory.messages) {
    const entry = node("div", "memory-entry");
    entry.append(node("strong", "", item.role === "user" ? "USER · 用户" : "ASSISTANT · Agent"), node("p", "", item.content));
    fragment.append(entry);
  }
  if (!memory.messages.length) fragment.append(node("p", "empty-note", "发送第一条消息后，在这里查看记忆。"));
  $("memory-messages").replaceChildren(fragment);
}

function formatResult(result) {
  if (typeof result !== "string") return JSON.stringify(result, null, 2);
  try { return JSON.stringify(JSON.parse(result), null, 2); }
  catch { return result; }
}

function renderEvents(events) {
  const signature = JSON.stringify(events);
  if (signature === eventsSignature) return;
  eventsSignature = signature;
  const fragment = document.createDocumentFragment();
  for (const [index, event] of events.entries()) {
    const entry = node("div", "tool-event");
    entry.append(
      node("strong", "", `${String(index + 1).padStart(2, "0")}  ${event.intent.tool}`),
      node("small", "", "调用参数"), node("pre", "", JSON.stringify(event.intent.args, null, 2)),
      node("small", "", "返回结果"), node("pre", "", formatResult(event.result)),
    );
    if (event.error) entry.append(node("small", "", "错误"), node("pre", "", event.error));
    fragment.append(entry);
  }
  if (!events.length) fragment.append(node("p", "empty-note", "本轮还没有工具调用。"));
  $("tool-events").replaceChildren(fragment);
}

function renderSnapshot(snapshot) {
  currentSnapshot = snapshot;
  text("conversation-title", snapshot.title);
  renderMessages(snapshot.messages);
  renderMemory(snapshot.memory);
  const state = snapshot.state;
  const events = state.trace.tool_events;
  const route = routeNames[state.trace.route.kind] || "尚未路由";
  text("state-status", statusNames[state.status] || state.status);
  $("state-status").dataset.status = state.status;
  text("state-step", state.step);
  text("tool-count", events.length);
  text("trace-count", events.length);
  text("state-route", route);
  text("route-badge", state.trace.route.kind ? route : "自动路由");
  text("context-count", `${state.messages.length} 条`);
  text("turn-duration", snapshot.usage.elapsed_ms !== undefined ? `${(snapshot.usage.elapsed_ms / 1000).toFixed(2)} s` : "—");
  text("token-count", snapshot.usage.total_tokens !== undefined ? snapshot.usage.total_tokens.toLocaleString() : "—");
  $("queue-note").hidden = snapshot.phase !== "queued";
  renderEvents(events);
  const raw = JSON.stringify(state, null, 2);
  if ($("raw-state").textContent !== raw) text("raw-state", raw);
  const busy = busyPhases.has(snapshot.phase);
  $("thinking").hidden = !busy;
  text("thinking-label", snapshot.phase === "queued" ? "已收到问题，等待开始处理" : `正在处理你的问题${state.step ? ` · 第 ${state.step} 步` : ""}`);
  text("sync-label", `同步于 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`);
  text("session-id", `SESSION ${snapshot.id.slice(0, 6).toUpperCase()}`);
  updateComposer();
}

function renderSessionList(sessions) {
  const signature = JSON.stringify([activeId, sessions]);
  if (signature === sessionListSignature) return;
  sessionListSignature = signature;
  text("session-count", sessions.length);
  const fragment = document.createDocumentFragment();
  for (const item of sessions) {
    const row = node("div", `session-row${item.id === activeId ? " selected" : ""}`);
    const select = node("button", "session-select");
    select.type = "button";
    select.title = item.title;
    select.setAttribute("aria-current", item.id === activeId ? "true" : "false");
    select.append(icon("chat"), node("span", "", item.title));
    select.addEventListener("click", () => selectSession(item.id));
    const remove = node("button", "session-delete");
    remove.type = "button";
    remove.setAttribute("aria-label", `删除对话：${item.title}`);
    remove.title = "删除对话";
    remove.disabled = busyPhases.has(item.phase);
    remove.append(icon("trash"));
    remove.addEventListener("click", () => deleteSession(item.id));
    row.append(select, remove);
    fragment.append(row);
  }
  $("session-list").replaceChildren(fragment);
}

async function refreshList() {
  const result = await api("/api/sessions");
  renderSessionList(result.sessions);
  return result.sessions;
}

async function selectSession(id, snapshot = null) {
  if (activeId) drafts.set(activeId, input.value);
  const epoch = ++viewEpoch;
  activeId = id;
  currentSnapshot = null;
  rememberSession(id);
  transcriptSignature = memorySignature = eventsSignature = "";
  input.value = drafts.get(id) || "";
  showError("");
  updateComposer();
  try {
    const data = snapshot || await api(`/api/sessions/${id}`);
    if (epoch !== viewEpoch) return;
    renderSnapshot(data);
    await refreshList();
    $("chat-scroll").scrollTop = $("chat-scroll").scrollHeight;
  } catch (error) { if (epoch === viewEpoch) showError(error.message); }
  schedulePoll(750);
}

async function newSession() {
  if (creating) return;
  creating = true;
  updateComposer();
  try {
    const snapshot = await api("/api/sessions", { method: "POST" });
    await selectSession(snapshot.id, snapshot);
    input.focus();
  } catch (error) { showError(error.message); }
  finally { creating = false; updateComposer(); }
}

async function deleteSession(id) {
  if (!window.confirm("删除这段对话及其记忆？此操作无法撤销。")) return;
  try {
    await api(`/api/sessions/${id}`, { method: "DELETE" });
    drafts.delete(id);
    pendingRequests.delete(id);
    const sessions = await refreshList();
    if (id === activeId) {
      if (sessions.length) await selectSession(sessions[0].id);
      else { activeId = null; await newSession(); }
    }
  } catch (error) { toast(error.message); }
}

async function sendMessage(event) {
  event.preventDefault();
  const content = input.value.trim();
  if (!content || !activeId || submitting || busyPhases.has(currentSnapshot?.phase)) return;
  const id = activeId;
  const epoch = ++viewEpoch;
  const previous = pendingRequests.get(id);
  const request = previous?.content === content ? previous : { content, request_id: crypto.randomUUID() };
  pendingRequests.set(id, request);
  submitting = true;
  showError("");
  updateComposer();
  try {
    const snapshot = await api(`/api/sessions/${id}/messages`, { method: "POST", body: JSON.stringify(request) });
    pendingRequests.delete(id);
    drafts.set(id, "");
    if (id === activeId && epoch === viewEpoch) {
      input.value = "";
      renderSnapshot(snapshot);
      $("chat-scroll").scrollTop = $("chat-scroll").scrollHeight;
    }
    await refreshList();
  } catch (error) {
    if (id === activeId && epoch === viewEpoch) showError(error.message);
  } finally {
    submitting = false;
    updateComposer();
    schedulePoll(300);
  }
}

function schedulePoll(delay) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(poll, delay);
}

async function poll() {
  const id = activeId;
  const epoch = viewEpoch;
  try {
    if (id) {
      const snapshot = await api(`/api/sessions/${id}`);
      if (id === activeId && epoch === viewEpoch) renderSnapshot(snapshot);
    }
    await refreshList();
  } catch (error) {
    if (error.status === 404 && id === activeId) {
      activeId = null;
      currentSnapshot = null;
      ++viewEpoch;
      updateComposer();
      showError("这个会话已不存在，可能是服务重启或会话被删除。请新建对话。");
    }
  }
  schedulePoll(currentSnapshot && busyPhases.has(currentSnapshot.phase) ? 700 : 4000);
}

function setInspector(open) {
  const mobile = window.matchMedia("(max-width: 760px)").matches;
  shell.classList.toggle("inspector-open", mobile && open);
  shell.classList.toggle("inspector-hidden", !mobile && !open);
  $("inspector-backdrop").hidden = !mobile || !open;
  $("toggle-inspector").setAttribute("aria-expanded", String(open));
  if (mobile && open) $("close-inspector").focus();
  if (!open && $("inspector").contains(document.activeElement)) $("toggle-inspector").focus();
}

$("composer").addEventListener("submit", sendMessage);
input.addEventListener("input", () => { if (activeId) drafts.set(activeId, input.value); updateComposer(); });
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("composer").requestSubmit();
  }
});
$("new-session").addEventListener("click", newSession);
$("mobile-new").addEventListener("click", newSession);
document.querySelectorAll(".suggestion").forEach((button) => button.addEventListener("click", () => {
  input.value = button.dataset.prompt;
  if (activeId) drafts.set(activeId, input.value);
  updateComposer();
  input.focus();
}));
$("toggle-inspector").addEventListener("click", () => setInspector($("toggle-inspector").getAttribute("aria-expanded") !== "true"));
$("close-inspector").addEventListener("click", () => setInspector(false));
$("inspector-backdrop").addEventListener("click", () => setInspector(false));
document.addEventListener("keydown", (event) => { if (event.key === "Escape") setInspector(false); });
window.matchMedia("(max-width: 760px)").addEventListener("change", (event) => setInspector(!event.matches));

async function init() {
  setInspector(!window.matchMedia("(max-width: 760px)").matches);
  try {
    // Establish the browser cookie before issuing parallel requests.
    const sessions = await refreshList();
    const remembered = storedSession();
    const selected = sessions.find((item) => item.id === remembered) || sessions[0];
    if (selected) await selectSession(selected.id);
    else await newSession();
  } catch (error) { showError(error.message); }
  schedulePoll(1000);
}

init();
