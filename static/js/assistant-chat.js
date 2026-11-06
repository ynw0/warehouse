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
    thinkingLevels: [],
    promptKeys: new Set(),
    promptQueue: [],
    promptQuestion: "",
    desktop: false,
    userId: null,
    resizeObserver: null,
  };
  let ready = Promise.resolve();
  let popupListeners;

  function html(welcome) {
    state.welcome = welcome || "";
    return `<button class="ai-fab" id="aiFloatBtn" type="button" title="物料小助手"><img src="/static/assistant-icon.svg" alt=""></button>
      <aside class="ai-proactive" id="aiProactive" hidden aria-live="polite">
        <div id="aiProactiveText"></div>
        <div><button id="aiProactiveOpen" type="button">查看</button><button id="aiProactiveClose" type="button">稍后</button></div>
      </aside>
      <section class="ai-chat" id="aiFloatBox" aria-label="物料小助手">
        <div class="work-head" id="aiChatHead">
          <div class="ai-heading"><span class="ai-heading-icon ai-mascot-avatar" role="img" aria-label="物料小助手"></span><div><h2>物料小助手</h2><small>查询 · 填单 · 协作</small></div></div>
          <div class="ai-head-actions">
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
            <div class="ai-notes"><strong>我的事项</strong><div id="aiNotesList"></div></div>
          </div>
          <div class="chat-log" id="aiChatLog" aria-live="polite"></div>
          <div class="ai-composer">
            <div class="ai-file-chips" id="aiFileChips"></div>
            <textarea id="aiFloatQuestion" aria-label="输入消息" placeholder="问库存、查流程，或告诉我你要填写的单据"></textarea>
            <div class="ai-send-row">
              <label class="ai-file-button">＋ 添加附件<input id="aiFileInput" type="file" accept=".png,.jpg,.jpeg,.webp,.txt,.csv,.json" multiple hidden></label>
              <span class="ai-input-hint">Enter 发送</span>
              <div class="ai-thinking-anchor">
                <button class="ai-thinking-trigger" id="aiThinkingToggle" type="button" aria-haspopup="dialog" aria-expanded="false" aria-controls="aiThinkingControl"><span class="ai-mascot-avatar" role="img" aria-label="物料小助手"></span> <span id="aiThinkingButtonLabel">思考</span></button>
                <div class="ai-thinking-popover" id="aiThinkingControl" role="dialog" aria-label="思考等级" hidden>
                  <div class="ai-thinking-title"><span class="ai-mascot-avatar" aria-hidden="true"></span><output id="aiThinkingLabel" for="aiThinkingLevel"></output><span></span></div>
                  <div class="ai-thinking-model" id="aiThinkingModel"></div>
                  <div class="ai-thinking-track"><div class="ai-thinking-marks" id="aiThinkingMarks" aria-hidden="true"></div><input id="aiThinkingLevel" type="range" min="0" max="0" step="1" value="0" aria-label="思考等级"></div>
                </div>
              </div>
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
    state.desktop = Boolean(options && options.desktop);
    const userId = options?.system?.boot?.user?.id || null;
    if (state.userId !== userId) {
      state.promptKeys.clear();
      state.promptQueue = [];
      state.userId = userId;
    }
    if (state.resizeObserver) state.resizeObserver.disconnect();
    const sizeKey = "warehouse-assistant-size:" + (options?.system?.boot?.user?.id || "desktop");
    const saved = state.desktop ? null : localStorage.getItem(sizeKey);
    if (saved) {
      const size = JSON.parse(saved);
      box.style.width = Math.min(Math.max(320, size.width), window.innerWidth - 16) + "px";
      box.style.height = Math.min(Math.max(280, size.height), window.innerHeight - 16) + "px";
    }
    function positionDialog() {
      if (state.desktop) return;
      const rect = button.getBoundingClientRect();
      const width = box.offsetWidth || Math.min(620, window.innerWidth - 16);
      const height = box.offsetHeight || 500;
      const left = rect.right + 10 + width <= window.innerWidth ? rect.right + 10 : rect.left - width - 10;
      box.style.left = Math.max(8, Math.min(window.innerWidth - width - 8, left)) + "px";
      box.style.top = Math.max(8, Math.min(window.innerHeight - height - 8, rect.top)) + "px";
      box.style.right = "auto";
      box.style.bottom = "auto";
      positionPrompt();
    }
    state.positionDialog = positionDialog;
    if (window.ResizeObserver) {
      const observer = new ResizeObserver(() => {
        if (!box.classList.contains("open") || state.desktop) return;
        localStorage.setItem(sizeKey, JSON.stringify({ width: box.offsetWidth, height: box.offsetHeight }));
        positionDialog();
      });
      observer.observe(box);
      state.resizeObserver = observer;
    }
    showWelcome();
    button.addEventListener("click", () => {
      if (options && options.system && options.system.aiDragging) return;
      box.classList.toggle("open");
      if (box.classList.contains("open")) {
        positionDialog();
        loadPendingConfirmations();
      }
    });
    document.getElementById("aiFloatClose").addEventListener("click", () => box.classList.remove("open"));
    document.getElementById("aiClearChat").addEventListener("click", () => clearChat().catch(showError));
    document.getElementById("aiMemoryToggle").addEventListener("click", () => {
      const panel = document.getElementById("aiMemoryPanel");
      panel.hidden = !panel.hidden;
      if (!panel.hidden) loadNotes().catch(showError);
      document.getElementById("aiMemoryToggle").setAttribute("aria-expanded", String(!panel.hidden));
    });
    document.getElementById("aiMemorySave").addEventListener("click", () => saveMemory().catch(showError));
    if (popupListeners) popupListeners.abort();
    popupListeners = new AbortController();
    window.addEventListener("resize", positionDialog, { signal: popupListeners.signal });
    const trigger = document.getElementById("aiThinkingToggle");
    const panel = document.getElementById("aiThinkingControl");
    trigger.addEventListener("click", () => {
      panel.hidden = !panel.hidden;
      trigger.setAttribute("aria-expanded", String(!panel.hidden));
      if (!panel.hidden) document.getElementById("aiThinkingLevel").focus();
    });
    document.addEventListener("click", event => {
      if (!panel.hidden && !event.target.closest(".ai-thinking-anchor")) {
        panel.hidden = true;
        trigger.setAttribute("aria-expanded", "false");
      }
    }, { signal: popupListeners.signal });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape" && !panel.hidden) {
        panel.hidden = true;
        trigger.setAttribute("aria-expanded", "false");
        trigger.focus();
      }
    }, { signal: popupListeners.signal });
    document.getElementById("aiThinkingLevel").addEventListener("input", event => {
      updateThinkingLabel(Number(event.target.value));
    });
    document.getElementById("aiThinkingLevel").addEventListener("change", event => {
      const level = state.thinkingLevels[Number(event.target.value)];
      if (level) saveThinking(level.id).catch(showError);
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
    document.getElementById("aiProactiveClose").addEventListener("click", () => {
      document.getElementById("aiProactive").hidden = true;
      showNextPrompt();
    });
    document.getElementById("aiProactiveOpen").addEventListener("click", () => {
      document.getElementById("aiProactive").hidden = true;
      box.classList.add("open");
      positionDialog();
      loadPendingConfirmations();
      if (state.promptQuestion) {
        document.getElementById("aiFloatQuestion").value = state.promptQuestion;
        send().catch(showError);
      } else {
        appendBubble("ai", document.getElementById("aiProactiveText").textContent);
      }
      showNextPrompt();
    });
    if (options && options.makeDraggable) options.makeDraggable(button, positionDialog);
    window.addEventListener("warehouse-ai-profile-updated", () => {
      if (!state.sending) ready = restore().catch(showError);
    });
    ready = restore().then(() => checkPrompts()).catch(showError);
  }

  function positionPrompt() {
    const bubble = document.getElementById("aiProactive");
    const button = document.getElementById("aiFloatBtn");
    if (!bubble || !button || bubble.hidden || state.desktop) return;
    const rect = button.getBoundingClientRect();
    const width = bubble.offsetWidth;
    const left = rect.right + width + 10 <= window.innerWidth ? rect.right + 10 : rect.left - width - 10;
    bubble.style.left = Math.max(8, Math.min(window.innerWidth - width - 8, left)) + "px";
    bubble.style.top = Math.max(8, Math.min(window.innerHeight - bubble.offsetHeight - 8, rect.top)) + "px";
  }

  function prompt(text, question, key) {
    if (!text || (key && state.promptKeys.has(key))) return;
    if (key) state.promptKeys.add(key);
    state.promptQueue.push({ text, question: question || "" });
    showNextPrompt();
  }

  function showNextPrompt() {
    const bubble = document.getElementById("aiProactive");
    if (!bubble || !bubble.hidden || !state.promptQueue.length) return;
    const next = state.promptQueue.shift();
    document.getElementById("aiProactiveText").textContent = next.text;
    state.promptQuestion = next.question;
    bubble.hidden = false;
    positionPrompt();
  }

  async function checkPrompts() {
    const response = await fetch("/api/ai/assistant/prompts");
    const data = await readJson(response);
    const incomplete = data.incomplete_notes || [];
    if (incomplete.length) {
      const note = incomplete[0];
      prompt("有 " + incomplete.length + " 条事项还需要补充验收明细或数量，需要现在查看吗？",
        "请列出我需要补充信息的事项", "incomplete:" + incomplete.map(item => item.id).join(","));
    }
    const responseNotes = await fetch("/api/notifications?read=0&category=assistant");
    const notices = responseNotes.ok ? (await responseNotes.json()).items || [] : [];
    if (notices.length) {
      const note = notices[0];
      prompt(note.body || note.title, "", "note:" + note.id);
    }
    const todos = data.todos || [];
    if (todos.length) {
      prompt("你有 " + todos.length + " 个待办，需要我帮你查看吗？",
        "我的账号有哪些待办", "todos:" + todos.map(item => item.form_type + ":" + item.task_id).join(","));
    }
  }

  async function loadNotes() {
    const data = await readJson(await fetch("/api/ai/assistant/notes"));
    const host = document.getElementById("aiNotesList");
    host.replaceChildren();
    const labels = { recorded: "已记录", incomplete: "待补充", watching: "等待提醒", notified: "已提醒" };
    (data.items || []).forEach(item => {
      const row = document.createElement("div");
      row.className = "ai-note-row";
      const text = document.createElement("span");
      text.textContent = item.content + " · " + (labels[item.status] || item.status) + (item.remind_at ? " · " + item.remind_at : "");
      const done = document.createElement("button");
      done.type = "button";
      done.textContent = "完成";
      done.addEventListener("click", async () => {
        try {
          await readJson(await fetch("/api/ai/assistant/notes/" + item.id + "/complete", { method: "POST" }));
          await loadNotes();
        } catch (error) { showError(error); }
      });
      row.append(text, done);
      host.appendChild(row);
    });
    if (!data.items?.length) host.textContent = "暂无事项";
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
    document.getElementById("aiThinkingModel").textContent = data.model || "当前模型";
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

  function updateThinkingLabel(index) {
    const level = state.thinkingLevels[index];
    const text = level ? level.label : "";
    document.getElementById("aiThinkingLabel").textContent = text;
    document.getElementById("aiThinkingButtonLabel").textContent = text || "思考";
    document.getElementById("aiThinkingLevel").setAttribute("aria-valuetext", text);
  }

  function fillThinkingOptions() {
    const slider = document.getElementById("aiThinkingLevel");
    const profile = state.profile || {};
    const levels = profile.thinking_levels || ["off"];
    state.thinkingLevels = levels.map(id => {
      const configured = (profile.thinking_options || []).find(item => item.id === id);
      return { id, label: configured ? configured.label : (LEVEL_LABELS[id] || id) };
    });
    if (!state.thinkingLevels.length) state.thinkingLevels = [{ id: "off", label: "服务默认" }];
    let index = state.thinkingLevels.findIndex(item => item.id === state.thinkingLevel);
    if (index < 0) index = Math.max(0, state.thinkingLevels.findIndex(item => item.id === profile.default_thinking));
    state.thinkingLevel = state.thinkingLevels[index].id;
    slider.max = String(state.thinkingLevels.length - 1);
    slider.value = String(index);
    slider.disabled = profile.capabilities?.reasoning === false || state.thinkingLevels.length < 2;
    const marks = document.getElementById("aiThinkingMarks");
    marks.replaceChildren(...state.thinkingLevels.map(() => {
      const dot = document.createElement("span");
      return dot;
    }));
    updateThinkingLabel(index);
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

  function activity(state) {
    if (window.chrome?.webview) window.chrome.webview.postMessage({ type:"assistant-state", state });
  }

  async function send() {
    await ready;
    if (state.sending) return;
    const input = document.getElementById("aiFloatQuestion");
    const question = input.value.trim();
    if (!question && !state.files.length) return;
    const files = state.files.slice();
    state.sending = true;
    activity("busy");
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
      activity("success");
    } catch (error) {
      activity("error");
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
        const index = state.thinkingLevels.findIndex(item => item.id === event.thinking_level);
        if (index >= 0) {
          document.getElementById("aiThinkingLevel").value = String(index);
          updateThinkingLabel(index);
        }
      }
    } else if (event.type === "thinking" && event.text) {
      activity("thinking");
      setThought(bubble, (bubble._thought.textContent || "") + event.text);
    } else if (event.type === "text") {
      answer = event.text || "";
      setAnswer(bubble, answer);
    } else if (event.type === "tool") {
      activity("busy");
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
      renderAnswer(answer, text);
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
    if (bubble._answer) renderAnswer(bubble._answer, text);
  }

  function renderAnswer(host, text) {
    // 使用 DOM 文本节点呈现模型回复，避免把回复当成 HTML 执行。
    const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
    const content = document.createDocumentFragment();
    let index = 0;
    while (index < lines.length) {
      const fence = lines[index].match(/^\s*`{3,}/);
      if (fence) {
        const closing = lines.findIndex((line, position) => position > index && /^\s*`{3,}\s*$/.test(line));
        if (closing !== -1) {
          const block = document.createElement("pre");
          block.textContent = lines.slice(index + 1, closing).join("\n");
          content.appendChild(block);
          index = closing + 1;
          if (index < lines.length) content.appendChild(document.createTextNode("\n"));
          continue;
        }
      }
      const header = splitTableRow(lines[index]);
      const separator = index + 1 < lines.length ? splitTableRow(lines[index + 1]) : [];
      if (header.length > 1 && separator.length === header.length &&
          separator.every(cell => /^:?-{3,}:?$/.test(cell.trim()))) {
        const wrapper = document.createElement("div");
        wrapper.className = "ai-answer-table-wrap";
        const table = document.createElement("table");
        table.className = "ai-answer-table";
        const head = document.createElement("thead");
        const headRow = document.createElement("tr");
        header.forEach((cell, column) => {
          const th = document.createElement("th");
          th.scope = "col";
          th.style.textAlign = tableAlignment(separator[column]);
          renderInline(th, cell.trim());
          headRow.appendChild(th);
        });
        head.appendChild(headRow);
        table.appendChild(head);
        const body = document.createElement("tbody");
        index += 2;
        while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
          const cells = splitTableRow(lines[index]);
          if (!cells.length) break;
          const row = document.createElement("tr");
          header.forEach((_, column) => {
            const td = document.createElement("td");
            td.style.textAlign = tableAlignment(separator[column]);
            renderInline(td, (cells[column] || "").trim());
            row.appendChild(td);
          });
          body.appendChild(row);
          index += 1;
        }
        table.appendChild(body);
        wrapper.appendChild(table);
        content.appendChild(wrapper);
        if (index < lines.length) content.appendChild(document.createTextNode("\n"));
        continue;
      }
      renderInline(content, lines[index]);
      index += 1;
      if (index < lines.length) content.appendChild(document.createTextNode("\n"));
    }
    host.replaceChildren(content);
  }

  function splitTableRow(line) {
    const source = String(line || "").trim();
    if (!source.includes("|")) return [];
    let text = source;
    if (text.startsWith("|")) text = text.slice(1);
    if (text.endsWith("|") && !text.endsWith("\\|")) text = text.slice(0, -1);
    const cells = [];
    let cell = "";
    let code = false;
    for (let index = 0; index < text.length; index += 1) {
      const character = text[index];
      if (character === "\\" && text[index + 1] === "|") {
        cell += "|";
        index += 1;
      } else if (character === "`") {
        code = !code;
        cell += character;
      } else if (character === "|" && !code) {
        cells.push(cell);
        cell = "";
      } else {
        cell += character;
      }
    }
    cells.push(cell);
    return cells;
  }

  function tableAlignment(separator) {
    const value = separator.trim();
    if (value.startsWith(":") && value.endsWith(":")) return "center";
    if (value.endsWith(":")) return "right";
    return "left";
  }

  function renderInline(host, text) {
    const pattern = /(\*\*[^*\n]+\*\*|`[^`\n]+`)/g;
    let start = 0;
    for (const match of text.matchAll(pattern)) {
      host.appendChild(document.createTextNode(text.slice(start, match.index)));
      const isBold = match[0].startsWith("**");
      const element = document.createElement(isBold ? "strong" : "code");
      element.textContent = isBold ? match[0].slice(2, -2) : match[0].slice(1, -1);
      host.appendChild(element);
      start = match.index + match[0].length;
    }
    host.appendChild(document.createTextNode(text.slice(start)));
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
    const text = document.createElement("div");
    text.className = "ai-confirm-content";
    if (item.action === "create_workflow" && item.preview) {
      renderWorkflowPreview(text, item.preview);
    } else {
      const summary = document.createElement("p");
      summary.textContent = item.summary || "该操作需要确认";
      text.appendChild(summary);
    }
    const error = document.createElement("p");
    error.className = "ai-confirm-error";
    error.hidden = true;
    const ok = document.createElement("button");
    ok.type = "button";
    ok.className = "primary";
    ok.textContent = "确认执行";
    ok.addEventListener("click", () => settleConfirmation(card, item.id, "confirm"));
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.textContent = "取消";
    cancel.addEventListener("click", () => settleConfirmation(card, item.id, "cancel"));
    const actions = document.createElement("div");
    actions.className = "ai-confirm-actions";
    actions.append(ok, cancel);
    card.append(text, error, actions);
    bubble.appendChild(card);
  }

  const PREVIEW_LABELS = {
    form_type: "流程类型", items: "物料明细", material_code: "物料编码",
    material_id: "物料 ID", material_name: "物料名称", name: "名称",
    brand_model: "品牌型号", spec: "规格", unit: "单位",
    request_quantity: "申请数量", requested_quantity: "申请数量",
    purchase_project: "采购申请项目", project_code: "项目代号",
    purpose: "用途", usage_kind: "用料形式", leader_id: "审批人 ID",
    leader_ids: "审批人 ID", validator_ids: "验收人 ID",
    has_purchase_order: "有采购订单", warehouse_user_id: "仓管员 ID",
    arrival_quantity: "到货数量", purchase_quantity: "采购数量",
    total_amount: "总金额", budget_unit_price: "预算单价", remark: "备注",
    pack_quantity: "每单位数量", arrival_unit: "到货单位",
    electronic_purchase: "电子采购", platform: "采购平台",
  };
  const PREVIEW_TYPES = {
    claim: "物料申领", borrow: "物料借用", acceptance: "物料验收",
    purchase_request: "采购申请", special_claim: "特殊申领",
    semifinished_claim: "半成品领用", semifinished: "半成品验收",
    finished: "成品验收", supply: "供货申请", common_material: "常用物料申请",
  };

  function renderWorkflowPreview(host, data) {
    const heading = document.createElement("h4");
    heading.textContent = "请核对单据";
    host.appendChild(heading);
    const fields = document.createElement("dl");
    fields.className = "ai-preview-fields";
    renderPreviewFields(fields, data);
    host.appendChild(fields);
  }

  function renderPreviewFields(host, data) {
    Object.entries(data).forEach(([key, value]) => {
      if (value === null || value === undefined || value === "") return;
      const name = document.createElement("dt");
      name.textContent = PREVIEW_LABELS[key] || key;
      const content = document.createElement("dd");
      if (Array.isArray(value)) {
        if (value.every(item => item === null || typeof item !== "object")) {
          content.textContent = value.join("、");
        } else {
          value.forEach((entry, index) => {
            const section = document.createElement("section");
            section.className = "ai-preview-item";
            const title = document.createElement("strong");
            title.textContent = key === "items" ? "第 " + (index + 1) + " 项" : "第 " + (index + 1) + " 条";
            section.appendChild(title);
            if (entry && typeof entry === "object" && !Array.isArray(entry)) {
              const nested = document.createElement("dl");
              nested.className = "ai-preview-fields";
              renderPreviewFields(nested, entry);
              section.appendChild(nested);
            } else {
              section.appendChild(document.createTextNode(String(entry)));
            }
            content.appendChild(section);
          });
        }
      } else if (typeof value === "object") {
        const nested = document.createElement("dl");
        nested.className = "ai-preview-fields";
        renderPreviewFields(nested, value);
        content.appendChild(nested);
      } else if (typeof value === "boolean") {
        content.textContent = value ? "是" : "否";
      } else {
        content.textContent = key === "form_type" ? (PREVIEW_TYPES[value] || value) : String(value);
      }
      host.append(name, content);
    });
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
      const message = card.querySelector(".ai-confirm-error");
      if (message) {
        message.textContent = error.message;
        message.hidden = false;
      }
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

  window.WarehouseAssistant = { html, mount, setWelcome, renderAnswer, prompt, checkPrompts };
})();
