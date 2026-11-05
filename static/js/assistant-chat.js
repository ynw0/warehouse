/* 物料小助手浮窗：流式对话、附件、思考等级、用量和记忆。 */
(() => {
  "use strict";

  const LEVEL_LABELS = { off: "服务默认", low: "低", medium: "中", high: "高" };
  const state = {
    sessionId: 0,
    thinkingLevel: "medium",
    profile: null,
    files: [],
    sending: false,
    welcome: "",
    applySuggestion: null,
    openWorkflow: null,
  };
  let ready = Promise.resolve();

  function html(welcome) {
    state.welcome = welcome || "";
    return `<button class="ai-fab" id="aiFloatBtn" type="button" title="物料小助手"><img src="/static/assistant-icon.svg" alt=""></button>
      <section class="ai-chat" id="aiFloatBox" aria-label="物料小助手">
        <div class="work-head" id="aiChatHead">
          <div class="ai-heading"><span class="ai-heading-icon">✦</span><div><h2>物料小助手</h2><small>查询 · 填单 · 协作</small></div></div>
          <div class="ai-head-actions">
            <label class="ai-thinking">思考<select id="aiThinkingLevel"></select></label>
            <button id="aiMemoryToggle" type="button" aria-expanded="false">记忆</button>
            <button id="aiClearChat" type="button">清空对话</button>
            <button id="aiFloatClose" type="button">关闭</button>
          </div>
        </div>
        <div class="ai-usage" id="aiUsage">用量 --</div>
        <div class="work-body">
          <div class="ai-memory" id="aiMemoryPanel" hidden>
            <textarea id="aiMemoryBody" maxlength="4000" rows="4" placeholder="记给小助手的长期偏好，最多 4000 字"></textarea>
            <button id="aiMemorySave" type="button">保存记忆</button>
          </div>
          <div class="chat-log" id="aiChatLog" aria-live="polite"></div>
          <div class="ai-composer">
            <div class="ai-file-chips" id="aiFileChips"></div>
            <textarea id="aiFloatQuestion" aria-label="输入消息" placeholder="问库存、查流程，或告诉我你要填写的单据"></textarea>
            <div class="ai-send-row">
              <label class="ai-file-button">＋ 添加附件<input id="aiFileInput" type="file" accept=".png,.jpg,.jpeg,.webp,.txt,.csv,.json" multiple hidden></label>
              <span class="ai-input-hint">Enter 发送 · Alt + Enter 换行</span>
              <button class="primary" id="aiFloatSend" type="button">发送 <span aria-hidden="true">↑</span></button>
            </div>
          </div>
        </div>
      </section>`;
  }

  function mount(options) {
    const button = document.getElementById("aiFloatBtn");
    const box = document.getElementById("aiFloatBox");
    if (!button || !box || button.dataset.bound === "1") return;
    button.dataset.bound = "1";
    state.applySuggestion = options && options.applySuggestion;
    state.openWorkflow = options && options.openWorkflow;
    showWelcome();
    button.addEventListener("click", () => {
      if (options && options.system && options.system.aiDragging) return;
      box.classList.toggle("open");
      if (box.classList.contains("open")) loadPendingConfirmations();
    });
    document.getElementById("aiFloatClose").addEventListener("click", () => box.classList.remove("open"));
    document.getElementById("aiClearChat").addEventListener("click", () => clearChat().catch(showError));
    document.getElementById("aiMemoryToggle").addEventListener("click", () => {
      const panel = document.getElementById("aiMemoryPanel");
      panel.hidden = !panel.hidden;
      document.getElementById("aiMemoryToggle").setAttribute("aria-expanded", String(!panel.hidden));
    });
    document.getElementById("aiMemorySave").addEventListener("click", () => saveMemory().catch(showError));
    document.getElementById("aiThinkingLevel").addEventListener("change", event => {
      saveThinking(event.target.value).catch(showError);
    });
    document.getElementById("aiFloatSend").addEventListener("click", () => send().catch(showError));
    document.getElementById("aiFloatQuestion").addEventListener("keydown", event => {
      if (event.key !== "Enter" || event.altKey) return;
      event.preventDefault();
      send().catch(showError);
    });
    document.getElementById("aiFileInput").addEventListener("change", event => {
      uploadFiles(event.target.files).catch(showError);
      event.target.value = "";
    });
    if (options && options.makeDraggable) options.makeDraggable(button);
    ready = restore().catch(showError);
  }

  function setWelcome(text) {
    state.welcome = text || "";
    const welcome = document.querySelector("#aiChatLog .ai-msg-welcome");
    if (welcome) welcome.textContent = state.welcome;
  }

  function showError(error) {
    const message = error && error.message ? error.message : "操作失败";
    if (typeof window.toast === "function") window.toast(message);
  }

  function showWelcome() {
    const log = document.getElementById("aiChatLog");
    log.innerHTML = "";
    const div = document.createElement("div");
    div.className = "chat-msg ai ai-msg-welcome";
    div.textContent = state.welcome;
    log.appendChild(div);
  }

  async function restore() {
    const response = await fetch("/api/ai/assistant/session");
    const data = await readJson(response);
    state.profile = data.profile || {};
    state.thinkingLevel = data.thinking_level || "off";
    state.sessionId = data.session && data.session.id ? Number(data.session.id) : 0;
    const memory = document.getElementById("aiMemoryBody");
    if (memory) memory.value = data.memory || "";
    fillThinkingOptions();
    renderUsage(data.usage);
    const messages = data.messages || [];
    if (!messages.length) {
      showWelcome();
      return;
    }
    const log = document.getElementById("aiChatLog");
    log.innerHTML = "";
    messages.forEach(item => {
      const bubble = appendBubble(item.role === "user" ? "user" : "ai", item.content || "");
      (item.files || []).forEach(file => addFileLine(bubble, file.name || "附件"));
      (item.tools || []).forEach(name => addToolChip(bubble, name, "done", true));
      addWorkflows(bubble, item.workflows || []);
      if (item.reasoning) setThought(bubble, item.reasoning);
    });
  }

  function fillThinkingOptions() {
    const select = document.getElementById("aiThinkingLevel");
    const label = select.closest(".ai-thinking");
    const profile = state.profile || {};
    const enabled = !profile.capabilities || profile.capabilities.reasoning !== false;
    const levels = (profile.thinking_levels || ["off", "low", "medium", "high"]).slice();
    label.hidden = !enabled;
    select.innerHTML = "";
    levels.forEach(level => {
      const option = document.createElement("option");
      option.value = level;
      const configured = (profile.thinking_options || []).find(item => item.id === level);
      option.textContent = configured ? configured.label : (LEVEL_LABELS[level] || level);
      select.appendChild(option);
    });
    if (!enabled) {
      state.thinkingLevel = "off";
      return;
    }
    if (!levels.includes(state.thinkingLevel)) state.thinkingLevel = levels[0] || "off";
    select.value = state.thinkingLevel;
  }

  async function saveThinking(level) {
    state.thinkingLevel = level;
    if (!state.sessionId) return;
    const response = await fetch(`/api/ai/assistant/sessions/${state.sessionId}/thinking`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thinking_level: level }),
    });
    await readJson(response);
  }

  async function saveMemory() {
    const body = document.getElementById("aiMemoryBody").value;
    const response = await fetch("/api/ai/assistant/memory", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ body }),
    });
    const data = await readJson(response);
    document.getElementById("aiMemoryBody").value = data.memory || "";
    if (typeof window.toast === "function") window.toast("记忆已保存");
  }

  async function clearChat() {
    await ready;
    if (state.sending) return;
    const response = await fetch("/api/ai/assistant/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await readJson(response);
    state.sessionId = data.session && data.session.id ? Number(data.session.id) : 0;
    state.thinkingLevel = data.thinking_level || state.thinkingLevel;
    fillThinkingOptions();
    state.files = [];
    renderFileChips();
    showWelcome();
    renderUsage(null);
  }

  async function uploadFiles(fileList) {
    const incoming = [...(fileList || [])];
    if (!incoming.length) return;
    if (state.files.length + incoming.length > 5) throw new Error("每条消息最多 5 个附件");
    for (const file of incoming) {
      const body = new FormData();
      body.append("file", file);
      const response = await fetch("/api/ai/assistant/files", { method: "POST", body });
      const data = await readJson(response);
      state.files.push(data.file);
    }
    renderFileChips();
  }

  function renderFileChips() {
    const host = document.getElementById("aiFileChips");
    host.innerHTML = "";
    state.files.forEach((file, index) => {
      const chip = document.createElement("span");
      chip.className = "ai-file-chip";
      chip.textContent = file.name || "附件";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "×";
      remove.addEventListener("click", () => {
        state.files.splice(index, 1);
        renderFileChips();
      });
      chip.appendChild(remove);
      host.appendChild(chip);
    });
  }

  async function send() {
    await ready;
    if (state.sending) return;
    const input = document.getElementById("aiFloatQuestion");
    const question = input.value.trim();
    if (!question && !state.files.length) return;
    const files = state.files.slice();
    state.sending = true;
    document.getElementById("aiFloatSend").disabled = true;
    input.value = "";
    state.files = [];
    renderFileChips();
    const userBubble = appendBubble("user", question || "请结合附件回答。");
    files.forEach(file => addFileLine(userBubble, file.name || "附件"));
    const bubble = appendBubble("ai", "");
    setAnswer(bubble, "物料小助手回复中...");
    let answer = "";
    try {
      const response = await fetch("/api/ai/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          session_id: state.sessionId || undefined,
          file_ids: files.map(file => file.id),
          thinking_level: state.thinkingLevel,
        }),
      });
      const type = response.headers.get("content-type") || "";
      if (!response.ok || !type.includes("ndjson")) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || "发送失败");
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        lines.forEach(line => {
          if (!line.trim()) return;
          answer = applyEvent(bubble, JSON.parse(line), answer);
        });
      }
      if (buffer.trim()) answer = applyEvent(bubble, JSON.parse(buffer), answer);
      if (!answer) setAnswer(bubble, "物料小助手未返回内容");
      if (answer && state.applySuggestion) state.applySuggestion(answer);
    } catch (error) {
      setAnswer(bubble, error.message || "物料小助手回复失败");
    } finally {
      state.sending = false;
      document.getElementById("aiFloatSend").disabled = false;
    }
  }

  function applyEvent(bubble, event, answer) {
    if (event.type === "session") {
      state.sessionId = Number(event.session_id || 0);
      if (event.thinking_level) {
        state.thinkingLevel = event.thinking_level;
        const select = document.getElementById("aiThinkingLevel");
        if (select && [...select.options].some(option => option.value === event.thinking_level)) {
          select.value = event.thinking_level;
        }
      }
    } else if (event.type === "thinking" && event.text) {
      setThought(bubble, (bubble._thought.textContent || "") + event.text);
    } else if (event.type === "text") {
      answer = event.text || "";
      setAnswer(bubble, answer);
    } else if (event.type === "tool") {
      addToolChip(bubble, event.name || "工具", event.status || "start", event.ok !== false);
    } else if (event.type === "workflows") {
      addWorkflows(bubble, event.items || []);
    } else if (event.type === "confirmation") {
      addConfirmation(bubble, event);
    } else if (event.type === "usage") {
      renderUsage(event);
    } else if (event.type === "compact") {
      const note = document.createElement("div");
      note.className = "ai-note";
      note.textContent = "已压缩更早的对话";
      document.getElementById("aiChatLog").appendChild(note);
    } else if (event.type === "error") {
      setAnswer(bubble, event.error || "模型返回失败");
    }
    return answer;
  }

  function appendBubble(role, text) {
    const log = document.getElementById("aiChatLog");
    const div = document.createElement("div");
    div.className = `chat-msg ${role}`;
    if (role === "ai") {
      const tools = document.createElement("div");
      tools.className = "ai-tools";
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "ai-thought-toggle";
      toggle.textContent = "显示思考";
      toggle.hidden = true;
      const thought = document.createElement("div");
      thought.className = "ai-thought";
      const answer = document.createElement("div");
      answer.className = "ai-answer";
      answer.textContent = text || "";
      toggle.addEventListener("click", () => {
        const visible = thought.classList.toggle("show");
        toggle.textContent = visible ? "隐藏思考" : "显示思考";
      });
      div.append(tools, toggle, thought, answer);
      div._tools = tools;
      div._toggle = toggle;
      div._thought = thought;
      div._answer = answer;
    } else {
      div.textContent = text;
    }
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  function setAnswer(bubble, text) {
    if (bubble._answer) bubble._answer.textContent = text || "";
  }

  function setThought(bubble, text) {
    if (!bubble._thought) return;
    bubble._thought.textContent = text || "";
    bubble._toggle.hidden = !text;
  }

  function addFileLine(bubble, name) {
    const line = document.createElement("div");
    line.className = "ai-note";
    line.textContent = name;
    bubble.appendChild(line);
  }

  function addToolChip(bubble, name, status, ok) {
    if (!bubble._tools) return;
    let chip = [...bubble._tools.children].find(item => item.dataset.name === name && item.dataset.status === "start");
    if (!chip || status === "start") {
      chip = document.createElement("span");
      chip.className = "ai-tool";
      chip.dataset.name = name;
      bubble._tools.appendChild(chip);
    }
    chip.dataset.status = status;
    chip.textContent = status === "start" ? `${name} 执行中` : name;
    chip.classList.toggle("fail", status === "done" && !ok);
  }

  function addWorkflows(bubble, items) {
    if (!state.openWorkflow) return;
    items.forEach(item => {
      if (!item.id || bubble.querySelector("[data-workflow-id='" + Number(item.id) + "']")) return;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ai-workflow-link";
      button.dataset.workflowId = String(item.id);
      button.textContent = [item.form_no || "查看流程", item.status || ""].filter(Boolean).join(" · ");
      button.addEventListener("click", () => state.openWorkflow(Number(item.id)).catch(showError));
      bubble.appendChild(button);
    });
  }

  function addConfirmation(bubble, item) {
    if (bubble.querySelector(`.ai-confirm[data-confirmation-id="${item.id}"]`)) return;
    const card = document.createElement("div");
    card.className = "ai-confirm";
    card.dataset.confirmationId = String(item.id);
    const text = document.createElement("p");
    text.textContent = item.summary || "该操作需要确认";
    const ok = document.createElement("button");
    ok.type = "button";
    ok.className = "primary";
    ok.textContent = "确认执行";
    ok.addEventListener("click", () => settleConfirmation(card, item.id, "confirm"));
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "取消";
    cancel.addEventListener("click", () => settleConfirmation(card, item.id, "cancel"));
    card.append(text, ok, cancel);
    bubble.appendChild(card);
  }

  async function settleConfirmation(card, id, action) {
    const buttons = [...card.querySelectorAll("button")];
    buttons.forEach(button => { button.disabled = true; });
    try {
      const response = await fetch(`/api/ai/assistant/confirmations/${id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const data = await readJson(response);
      card.textContent = data.message || (action === "confirm" ? "已执行" : "已取消");
    } catch (error) {
      buttons.forEach(button => { button.disabled = false; });
      const text = card.querySelector("p");
      if (text) text.textContent = error.message;
    }
  }

  async function loadPendingConfirmations() {
    const log = document.getElementById("aiChatLog");
    if (!log) return;
    const response = await fetch("/api/ai/assistant/confirmations");
    if (!response.ok) return;
    const data = await response.json();
    const items = (data.items || []).filter(item => !log.querySelector(`.ai-confirm[data-confirmation-id="${item.id}"]`));
    if (!items.length) return;
    const bubble = appendBubble("ai", "有待确认的操作。");
    items.forEach(item => addConfirmation(bubble, item));
  }

  function renderUsage(usage) {
    const host = document.getElementById("aiUsage");
    if (!host) return;
    if (!usage || usage.used == null) {
      host.textContent = "用量 --";
      host.classList.remove("warn");
      return;
    }
    const prefix = usage.estimated ? "估算 " : "";
    host.textContent = `${prefix}${usage.used} / ${usage.window} · ${usage.percent}%`;
    host.classList.toggle("warn", Number(usage.percent) >= 72);
  }

  async function readJson(response) {
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.success === false) throw new Error(data.error || "请求失败");
    return data;
  }

  window.WarehouseAssistant = { html, mount, setWelcome };
})();
