(() => {
  if (new URLSearchParams(window.location.search).get("card") === "1") {
    return;
  }

  const system = window.WarehouseSystemState.create();
  const requestedWorkspaceView = new URLSearchParams(window.location.search).get("view");
  if (requestedWorkspaceView) system.view = requestedWorkspaceView;

  const { systemApi, money, formatDateCn, formatInputQty } = window.WarehouseSystemCore;
  let temporaryInventory;
  let dashboardRenderNonce = 0;

  const SYSTEM_PARTICLE_COUNT = 32;
  const SYSTEM_PARTICLE_FPS = 24;
  const SYSTEM_PARTICLE_FRAME_INTERVAL = 1000 / SYSTEM_PARTICLE_FPS;
  let systemParticleEffects = null;
  const VIEW_META = {
    dashboard: { title: "数据大屏", icon: "chart" }, todo: { title: "待办流程", icon: "clipboard-list" }, notifications: { title: "通知", icon: "bell" },
    myInspections: { title: "我的验收", icon: "clipboard-check" }, myStarted: { title: "我的发起", icon: "file-pen" },
    acceptance: { title: "物料验收", icon: "shield-check" }, temporaryTransfers: { title: "转正式库任务", icon: "workflow" }, semifinished: { title: "半成品验收", icon: "shield-check" }, finished: { title: "成品验收", icon: "shield-check" },
    claim: { title: "物料申领", icon: "hand-package" }, borrow: { title: "借用申请", icon: "package-open" }, myBorrow: { title: "我的借用", icon: "package-open" }, outbound: { title: "物料出库", icon: "package-open" },
    query: { title: "物料查询", icon: "search-file" }, temporaryInventory: { title: "临时库", icon: "boxes" }, temporaryLedger: { title: "临时库存流水", icon: "history" }, semifinishedInventory: { title: "半成品库", icon: "boxes" }, finishedInventory: { title: "成品库", icon: "boxes" },
    flow: { title: "流程中心", icon: "workflow" }, stats: { title: "统计", icon: "chart" }, stocktake: { title: "盘点", icon: "scan" }, logs: { title: "日志中心", icon: "history" }, recycle: { title: "回收站", icon: "trash" }, admin: { title: "系统设置", icon: "settings" },
    defectiveInventory: { title: "不良品物料", icon: "shield-check" }, commonMaterials: { title: "常用物料", icon: "bell" }, supplies: { title: "供货管理", icon: "package-open" },
  };
  const temporaryTransfers = window.WarehouseTemporaryTransfer.create({
    system,
    api: systemApi,
    formatQty: formatInputQty,
    onInventoryChanged: () => temporaryInventory?.reload(),
  });
  temporaryInventory = window.WarehouseTemporaryInventory.create({
    system,
    api: systemApi,
    formatQty: formatInputQty,
    onTransfer: row => temporaryTransfers.openCreate(row),
  });

  window.renderSystemDashboard = function renderSystemDashboard(home) {
    document.title = "\u4ed3\u5e93\u7269\u6599\u7cfb\u7edf";
    home.className = "home";
    home.style.display = "block";
    if (!system.boot) {
      home.innerHTML = `<div class="work-panel"><div class="work-body">系统加载中...</div></div>`;
      loadSystemBoot().then(() => window.renderSystemDashboard(home)).catch(error => {
        home.innerHTML = `<div class="work-panel"><div class="work-body">${escapeHtml(error.message)}</div></div>`;
      });
      return;
    }
    home.innerHTML = `
      <div class="system-bg"></div><canvas id="systemParticles"></canvas>
      <div class="system-shell app-shell">
        <aside class="system-side app-sidebar">
          <h2 id="homeLogo">${iconSvg("cube", "brand-icon")}<span>仓库物料系统</span></h2>
          <div class="system-user">
            <span class="system-user-avatar" aria-hidden="true">${escapeHtml((system.boot.user?.display_name || system.boot.user?.username || "U").trim().slice(0, 1).toUpperCase())}</span>
            <span class="system-user-details">
              <strong>${escapeHtml(system.boot.user?.display_name || system.boot.user?.username || "")}</strong>
              <small>在线</small>
            </span>
          </div>
          <div class="system-account-actions">
            <button id="logoutBtn">${iconSvg("log-out", "account-icon")}<span>退出登录</span></button>
            <button id="changePasswordBtn">${iconSvg("key", "account-icon")}<span>修改密码</span></button>
          </div>
          <nav class="system-nav app-sidebar-nav">
            ${navGroup("我的待办", "clipboard-list", [
              ["todo", "待办流程", canView("todo")],
              ["notifications", `通知${Number(system.boot.unread_notifications || 0) ? ` (${Number(system.boot.unread_notifications || 0)})` : ""}`, true],
            ])}
            ${navGroup("我的流程", "workflow", [
              ["myInspections", "我的验收", canView("myInspections")],
              ["myStarted", "我的发起", canView("myStarted")],
            ])}
            ${navGroup("验收中心", "shield-check", [
              ["acceptance", "物料验收", canView("acceptance")],
              ["temporaryTransfers", "转正式库任务", canView("temporaryTransfers")],
              ["semifinished", "半成品验收", canView("semifinished")],
              ["finished", "成品验收", canView("finished")],
            ])}
            ${navGroup("领用中心", "hand-package", [
              ["claim", "物料申领", canView("claim")],
              ["borrow", "借用申请", canView("borrow")],
              ["myBorrow", "我的借用", canView("myBorrow")],
              ["outbound", "物料出库", canView("outbound")],
              ["supplies", "供货管理", canView("supplies")],
            ])}
            ${navGroup("库存中心", "boxes", [
              ["query", "物料查询", canView("query")],
              ["temporaryInventory", "临时库", canView("temporaryInventory")],
              ["semifinishedInventory", "半成品库", canView("semifinishedInventory")],
              ["finishedInventory", "成品库", canView("finishedInventory")],
              ["defectiveInventory", "不良品物料", canView("defectiveInventory")],
              ["commonMaterials", "常用物料", canView("commonMaterials")],
              ["__card", "仓库料卡系统", canViewCardSystem()],
            ])}
            ${navGroup("审计中心", "search-file", [
              ["flow", "流程中心", canView("flow")],
              ["stats", "统计", canView("stats")],
              ["stocktake", "盘点", canView("stocktake")],
              ["logs", "日志中心", canView("logs")],
              ["temporaryLedger", "临时库存流水", canView("temporaryLedger")],
            ])}
            ${canView("recycle") ? navButton("recycle", "回收站") : ""}
            ${canView("admin") ? navButton("admin", "系统设置") : ""}
          </nav>
        </aside>
        <main class="system-main app-main">
          <header class="app-topbar" id="appTopbar"></header>
          <section class="app-content" id="systemMain"></section>
        </main>
        ${system.boot.ai_enabled ? aiFloatingHtml() : ""}
      </div>
    `;
    document.getElementById("logoutBtn").addEventListener("click", async () => {
      await systemApi("/api/logout", { method: "POST", body: "{}" });
      window.location.href = "/login";
    });
    document.getElementById("changePasswordBtn")?.addEventListener("click", () => openChangePasswordModal());
    document.getElementById("homeLogo")?.addEventListener("click", () => { system.view = "dashboard"; renderSystemMain(); });
    document.querySelectorAll("[data-system-view]").forEach(button => {
      button.addEventListener("click", () => {
        system.view = button.dataset.systemView;
        renderSystemMain();
      });
    });
    document.querySelector("[data-card-system]")?.addEventListener("click", openCardSystem);
    document.querySelectorAll(".nav-group").forEach(group => {
      group.addEventListener("toggle", () => {
        if (!group.open) return;
        document.querySelectorAll(".nav-group").forEach(peer => {
          if (peer !== group) peer.open = false;
        });
      });
    });
    setupAiFloating();
    setupParticles();
    showTodoPopup();
    startWorkflowRealtime();
    startNotificationRealtime();
    renderSystemMain();
  };

  async function loadSystemBoot() {
    system.boot = await systemApi("/api/system/bootstrap");
    system.userId = system.boot.user?.id || 0;
    system.todoKeys = new Set((system.boot.todos || []).map(todoKey));
    if (!system.boot.workflow_settings?.temporary_inventory_enabled) {
      temporaryInventory.clear();
      temporaryTransfers.clear();
      if (["temporaryInventory", "temporaryTransfers", "temporaryLedger"].includes(system.view)) system.view = "query";
    }
  }

  function todoKey(item) {
    return `${item.form_id || ""}:${item.task_id || item.id || ""}:${item.step_code || ""}:${item.status || ""}`;
  }

  function notificationKey(item) {
    return `${item.id || ""}:${item.created_at || ""}`;
  }

  function iconSvg(name, className = "app-icon") {
    return `<svg class="${className}" aria-hidden="true" focusable="false"><use href="#icon-${name}"></use></svg>`;
  }

  function viewIcon(view) { return VIEW_META[view]?.icon || "workflow"; }

  function navButton(view, label) {
    return `<button data-system-view="${view}" class="sidebar-link ${system.view === view ? "active" : ""}">${iconSvg(viewIcon(view), "menu-icon")}<span class="menu-label">${escapeHtml(label)}</span></button>`;
  }

  function navGroup(label, icon, entries) {
    const visible = (entries || []).filter(entry => entry[2]);
    if (!visible.length) return "";
    const active = visible.some(entry => entry[0] === system.view);
    const buttons = visible.map(([view, text]) => view === "__card"
      ? `<button class="sidebar-link" data-card-system>${iconSvg("cube", "menu-icon")}<span class="menu-label">${escapeHtml(text)}</span></button>`
      : navButton(view, text)).join("");
    return `<details class="nav-group" ${active ? "open" : ""}><summary>${iconSvg(icon, "menu-icon")}<span class="menu-label">${escapeHtml(label)}</span>${iconSvg("chevron-down", "menu-chevron")}</summary><div class="nav-group-body">${buttons}</div></details>`;
  }

  function updateTopbar() {
    const topbar = document.getElementById("appTopbar");
    if (!topbar) return;
    const isDashboard = system.view === "dashboard";
    topbar.hidden = isDashboard;
    if (isDashboard) return;
    const meta = VIEW_META[system.view] || { title: "仓库物料系统", icon: "workflow" };
    const user = currentUser()?.display_name || currentUser()?.username || "";
    topbar.innerHTML = `<div class="topbar-title">${iconSvg(meta.icon, "topbar-icon")}<h1>${escapeHtml(meta.title)}</h1></div><div class="topbar-actions"><span class="topbar-user">当前用户：${escapeHtml(user)}</span></div>`;
  }

  function renderSystemMain() {
    document.querySelectorAll("[data-system-view]").forEach(button => {
      button.classList.toggle("active", button.dataset.systemView === system.view);
    });
    const main = document.getElementById("systemMain");
    if (!main) return;
    updateTopbar();
    main.classList.toggle("system-dashboard-main", system.view === "dashboard");
    syncSystemParticles();
    document.querySelectorAll(".nav-group").forEach(group => {
      if (group.querySelector(`[data-system-view="${system.view}"]`)) group.open = true;
    });
    if (system.view !== "dashboard") {
      dashboardRenderNonce += 1;
      window.WarehouseDashboard?.unmount();
    }
    const views = {
      dashboard: renderDashboard,
      todo: renderTodo,
      notifications: renderNotifications,
      flow: renderFlowCenter,
      acceptance: renderAcceptance,
      claim: renderClaim,
      borrow: renderBorrow,
      myBorrow: renderMyBorrow,
      myInspections: renderMyInspections,
      myStarted: renderMyStarted,
      outbound: renderOutbound,
      semifinished: renderSemifinished,
      finished: renderFinished,
      semifinishedInventory: renderSemifinishedInventory,
      finishedInventory: renderFinishedInventory,
      query: renderQuery,
      temporaryInventory: temporaryInventory.render,
      temporaryLedger: temporaryInventory.renderLedger,
      temporaryTransfers: temporaryTransfers.render,
      stats: renderStats,
      stocktake: renderStocktake,
      logs: renderLogs,
      recycle: renderRecycle,
      defectiveInventory: main => window.WarehouseExtendedViews.renderDefectiveInventory(main),
      commonMaterials: main => window.WarehouseExtendedViews.renderCommonMaterials(main),
      supplies: main => window.WarehouseExtendedViews.renderSupplies(main),
      admin: renderAdmin,
    };
    (views[system.view] || renderDashboard)(main);
  }

  function hasRole(role) {
    return (system.boot?.user?.role_codes || []).includes("admin") || (system.boot?.user?.role_codes || []).includes(role);
  }

  function canView(view) {
    if (view === "acceptance" || view === "semifinished" || view === "finished") {
      return hasPerm("start_acceptance");
    }
    if (view === "claim") {
      return hasPerm("start_claim");
    }
    if (view === "borrow") {
      return hasPerm("start_borrow");
    }
    if (view === "myBorrow") {
      return hasPerm("view_borrow") || hasPerm("start_borrow");
    }
    if (view === "myInspections") {
      return hasRole("admin") || hasPerm("view_my_inspections");
    }
    if (view === "myStarted") {
      return hasRole("admin") || hasPerm("view_my_started");
    }
    if (view === "outbound") {
      return hasPerm("view_outbound");
    }
    if (view === "temporaryInventory") {
      return Boolean(
        system.boot?.workflow_settings?.temporary_inventory_enabled
        && hasPerm("view_temporary_inventory")
      );
    }
    if (view === "temporaryTransfers") {
      return Boolean(
        system.boot?.workflow_settings?.temporary_inventory_enabled
        && (hasPerm("process_temporary_transfer") || hasPerm("transfer_temporary_inventory"))
      );
    }
    if (view === "temporaryLedger") {
      return Boolean(system.boot?.workflow_settings?.temporary_inventory_enabled && hasPerm("view_temporary_inventory"));
    }
    // Match the backend rule: the admin role has every non-feature-gated permission.
    if (hasRole("admin")) return true;
    if (view === "semifinishedInventory") {
      return hasPerm("read_semifinished_inventory") || hasPerm("write_semifinished_inventory");
    }
    if (view === "finishedInventory") {
      return hasPerm("read_finished_inventory") || hasPerm("write_finished_inventory");
    }
    const permissions = {
      query: "view_query",
      flow: "view_flow",
      stats: "view_stats",
      logs: "view_logs",
      recycle: "view_recycle",
      defectiveInventory: "view_defective_inventory",
      commonMaterials: "view_query",
      supplies: "view_supply",
    };
    if (permissions[view]) return hasRole("admin") || hasPerm(permissions[view]);
    if (view === "stocktake") return hasPerm("view_stocktake") || hasPerm("start_stocktake") || hasPerm("edit_stocktake");
    if (view === "admin") return hasRole("admin");
    return true;
  }

  function canViewCardSystem() {
    const allowed = system.boot?.workflow_settings?.card_button_roles || ["admin", "warehouse"];
    const roles = system.boot?.user?.role_codes || [];
    return roles.some(role => allowed.includes(role));
  }

  function openCardSystem() {
    const url = new URL(window.location.href);
    url.pathname = "/";
    url.search = "?card=1";
    url.hash = "";
    window.open(url.toString(), "_blank");
  }

  function aiWelcomeMessage() {
    return system.boot?.workflow_settings?.ai_welcome_message || "\u60a8\u597d\u6211\u662f\u4ed3\u5e93\u7ba1\u7406\u5c0f\u52a9\u624b\uff0c\u6211\u53ef\u4ee5\u5e2e\u4f60\u67e5\u8be2\u7269\u6599\uff0c\u8f85\u52a9\u7f16\u5199\u65b0\u7f16\u7801\u3002";
  }

  function aiFloatingHtml() {
    return `<button class="ai-fab" id="aiFloatBtn" title="\u7269\u6599\u5c0f\u52a9\u624b"><img src="/static/assistant-icon.svg" alt=""></button><div class="ai-chat" id="aiFloatBox"><div class="work-head" id="aiChatHead"><h2>\u7269\u6599\u5c0f\u52a9\u624b</h2><div class="ai-head-actions"><button id="aiThoughtToggle" type="button">\u663e\u793a\u601d\u8003\u8fc7\u7a0b</button><button id="aiClearChat">\u6e05\u7a7a\u5bf9\u8bdd</button><button id="aiFloatClose">\u5173\u95ed</button></div></div><div class="work-body"><div class="chat-log" id="aiChatLog"><div class="chat-msg ai">${escapeHtml(aiWelcomeMessage())}</div></div><textarea id="aiFloatQuestion" placeholder="\u8f93\u5165\u95ee\u9898\uff0c\u4f8b\u5982\uff1a\u5e2e\u6211\u67e5\u8fde\u63a5\u5668\u5e93\u5b58"></textarea><p><button class="primary" id="aiFloatSend">\u53d1\u9001</button></p></div></div>`;
  }

  function setupAiFloating() {
    const btn = document.getElementById("aiFloatBtn");
    const box = document.getElementById("aiFloatBox");
    if (!btn || !box) return;
    if (!system.aiSessionId) system.aiSessionId = "s" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    btn.addEventListener("click", () => { if (!system.aiDragging) box.classList.toggle("open"); });
    document.getElementById("aiFloatClose").addEventListener("click", () => box.classList.remove("open"));
    document.getElementById("aiClearChat")?.addEventListener("click", () => {
      system.aiSessionId = "s" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
      const log = document.getElementById("aiChatLog");
      log.innerHTML = `<div class="chat-msg ai">${escapeHtml(aiWelcomeMessage())}</div>`;
    });
    document.getElementById("aiThoughtToggle")?.addEventListener("click", () => {
      system.aiThoughtsVisible = !system.aiThoughtsVisible;
      updateAiThoughtVisibility();
    });
    updateAiThoughtVisibility();
    makeDraggable(btn);
    const sendAiQuestion = async () => {
      const input = document.getElementById("aiFloatQuestion");
      const question = input.value.trim();
      if (!question) return;
      appendChat("user", question);
      input.value = "";
      const loading = appendChat("ai", "\u7269\u6599\u5c0f\u52a9\u624b\u56de\u590d\u4e2d...");
      try {
        const data = await systemApi("/api/ai/chat", { method: "POST", body: JSON.stringify({ question, session_id: system.aiSessionId }) });
        renderAiMessage(loading, data.answer || "\u7269\u6599\u5c0f\u52a9\u624b\u672a\u8fd4\u56de\u5185\u5bb9");
        applyAiSuggestion(data.answer || "");
      } catch (error) {
        loading.textContent = `\u7269\u6599\u5c0f\u52a9\u624b\u56de\u590d\u5931\u8d25\uff1a${error.message}`;
      }
    };
    document.getElementById("aiFloatSend").addEventListener("click", sendAiQuestion);
    document.getElementById("aiFloatQuestion")?.addEventListener("keydown", event => {
      if (event.key !== "Enter") return;
      if (event.altKey) return;
      event.preventDefault();
      sendAiQuestion();
    });
  }

  function appendChat(role, text) {
    const log = document.getElementById("aiChatLog");
    const div = document.createElement("div");
    div.className = `chat-msg ${role}`;
    div.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  function splitAiThought(text) {
    const thoughts = [];
    let answer = String(text || "").replace(/<think>([\s\S]*?)<\/think>/gi, (_, thought) => {
      thoughts.push(thought.trim());
      return "";
    }).replace(/<think>([\s\S]*)$/i, (_, thought) => {
      thoughts.push(thought.trim());
      return "";
    }).trim();
    return { thought: thoughts.join("\n\n").trim(), answer: answer || (thoughts.length ? "" : String(text || "").trim()) };
  }

  function renderAiMessage(host, text) {
    const { thought, answer } = splitAiThought(text);
    host.innerHTML = "";
    if (thought) {
      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "ai-thought-toggle";
      toggle.textContent = system.aiThoughtsVisible ? "\u9690\u85cf\u601d\u8003" : "\u663e\u793a\u601d\u8003";
      const thoughtBox = document.createElement("div");
      thoughtBox.className = `ai-thought${system.aiThoughtsVisible ? " show" : ""}`;
      thoughtBox.textContent = thought;
      toggle.addEventListener("click", () => {
        const visible = thoughtBox.classList.toggle("show");
        toggle.textContent = visible ? "\u9690\u85cf\u601d\u8003" : "\u663e\u793a\u601d\u8003";
      });
      host.append(toggle, thoughtBox);
    }
    const answerBox = document.createElement("div");
    answerBox.textContent = answer || "\u7269\u6599\u5c0f\u52a9\u624b\u672a\u8fd4\u56de\u5185\u5bb9";
    host.appendChild(answerBox);
  }

  function updateAiThoughtVisibility() {
    const visible = Boolean(system.aiThoughtsVisible);
    document.querySelectorAll(".ai-thought").forEach(box => box.classList.toggle("show", visible));
    document.querySelectorAll(".ai-thought-toggle").forEach(button => {
      button.textContent = visible ? "\u9690\u85cf\u601d\u8003" : "\u663e\u793a\u601d\u8003";
    });
    const globalToggle = document.getElementById("aiThoughtToggle");
    if (globalToggle) globalToggle.textContent = visible ? "\u9690\u85cf\u601d\u8003\u8fc7\u7a0b" : "\u663e\u793a\u601d\u8003\u8fc7\u7a0b";
  }

  function makeDraggable(el) {
    let startX = 0, startY = 0, left = 0, top = 0, moved = false;
    el.addEventListener("pointerdown", event => {
      startX = event.clientX; startY = event.clientY; moved = false;
      const rect = el.getBoundingClientRect(); left = rect.left; top = rect.top;
      el.setPointerCapture(event.pointerId);
    });
    el.addEventListener("pointermove", event => {
      if (!el.hasPointerCapture(event.pointerId)) return;
      const dx = event.clientX - startX, dy = event.clientY - startY;
      if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
      if (!moved) return;
      el.style.left = `${Math.max(8, Math.min(window.innerWidth - 60, left + dx))}px`;
      el.style.top = `${Math.max(8, Math.min(window.innerHeight - 60, top + dy))}px`;
      el.style.right = "auto";
    });
    el.addEventListener("pointerup", () => { system.aiDragging = moved; setTimeout(() => { system.aiDragging = false; }, 0); });
  }

  function applyAiSuggestion(answer) {
    const match = answer.match(/```(?:json)?\s*([\s\S]*?)```/i) || answer.match(/(\{[\s\S]*"action"[\s\S]*\})/);
    if (!match) return;
    let payload = null;
    try {
      const raw = (match[1] || "").trim();
      const jsonText = raw.slice(raw.indexOf("{"), raw.lastIndexOf("}") + 1);
      payload = JSON.parse(jsonText || raw);
    } catch (error) {
      return;
    }
    if (!payload.action && Array.isArray(payload.items)) {
      payload.action = system.view === "claim" ? "fill_claim" : "fill_acceptance";
    }
    if (payload.action === "fill_acceptance") {
      system.view = "acceptance";
      renderSystemMain();
      setTimeout(() => fillAiRows("accRows", "acceptance", payload.items || []), 50);
      toast("AI\u5df2\u586b\u5165\u9a8c\u6536\u8868\u5355");
    }
    if (payload.action === "fill_claim") {
      system.view = "claim";
      renderSystemMain();
      setTimeout(() => fillAiRows("claimRows", "claim", payload.items || []), 50);
      toast("AI\u5df2\u586b\u5165\u7533\u9886\u8868\u5355");
    }
  }

  function shouldAnimateSystemParticles() {
    return Boolean(systemParticleEffects) && !document.hidden && system.view !== "dashboard";
  }

  function syncSystemParticles() {
    const effects = systemParticleEffects;
    if (!effects) return;
    if (shouldAnimateSystemParticles()) {
      effects.start();
    } else {
      effects.stop();
    }
  }

  function destroySystemParticles() {
    const effects = systemParticleEffects;
    if (!effects) return;
    effects.stop();
    if (effects.resizeTimer !== null) window.clearTimeout(effects.resizeTimer);
    window.removeEventListener("resize", effects.resizeHandler);
    window.removeEventListener("mousemove", effects.mouseMoveHandler);
    window.removeEventListener("mouseleave", effects.mouseLeaveHandler);
    systemParticleEffects = null;
  }

  function setupParticles() {
    const canvas = document.getElementById("systemParticles");
    if (!canvas || !canvas.getContext) return;
    if (systemParticleEffects?.canvas === canvas) {
      syncSystemParticles();
      return;
    }
    destroySystemParticles();

    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const mouse = { x: window.innerWidth * .7, y: 120, active: false };
    const effects = {
      canvas,
      ctx,
      mouse,
      particles: [],
      animationId: null,
      lastFrameTime: 0,
      resizeTimer: null,
      width: 0,
      height: 0,
      resizeHandler: null,
      mouseMoveHandler: null,
      mouseLeaveHandler: null,
      start: null,
      stop: null
    };

    const resize = () => {
      effects.width = window.innerWidth;
      effects.height = window.innerHeight;
      canvas.width = effects.width;
      canvas.height = effects.height;
      effects.particles = Array.from({ length: SYSTEM_PARTICLE_COUNT }, () => ({
        x: Math.random() * effects.width,
        y: Math.random() * effects.height,
        homeX: Math.random() * effects.width,
        homeY: Math.random() * effects.height,
        vx: (Math.random() - .5) * .35,
        vy: (Math.random() - .5) * .35,
        phase: Math.random() * Math.PI * 2
      }));
    };
    const scheduleResize = () => {
      if (effects.resizeTimer !== null) window.clearTimeout(effects.resizeTimer);
      effects.resizeTimer = window.setTimeout(() => {
        effects.resizeTimer = null;
        resize();
      }, 200);
    };
    const draw = timestamp => {
      if (effects.animationId === null || !shouldAnimateSystemParticles()) return;
      effects.animationId = window.requestAnimationFrame(draw);
      const frameElapsed = timestamp - effects.lastFrameTime;
      if (frameElapsed < SYSTEM_PARTICLE_FRAME_INTERVAL) return;
      const frameScale = Math.min(frameElapsed / (1000 / 60), 3);
      effects.lastFrameTime = timestamp;
      ctx.clearRect(0, 0, effects.width, effects.height);
      effects.particles.forEach(particle => {
        particle.phase += .01 * frameScale;
        const homeDx = (particle.homeX + Math.cos(particle.phase) * 18) - particle.x;
        const homeDy = (particle.homeY + Math.sin(particle.phase) * 18) - particle.y;
        particle.vx += homeDx * .0007 * frameScale;
        particle.vy += homeDy * .0007 * frameScale;
        if (mouse.active) {
          const dx = mouse.x - particle.x;
          const dy = mouse.y - particle.y;
          const distance = Math.max(90, Math.hypot(dx, dy));
          const pull = distance < 260 ? .018 * (1 - distance / 260) : 0;
          particle.vx += dx / distance * pull * frameScale;
          particle.vy += dy / distance * pull * frameScale;
        }
        particle.x += particle.vx * frameScale;
        particle.y += particle.vy * frameScale;
        particle.vx *= Math.pow(.94, frameScale);
        particle.vy *= Math.pow(.94, frameScale);
        if (particle.x < 0 || particle.x > effects.width) particle.vx *= -1;
        if (particle.y < 0 || particle.y > effects.height) particle.vy *= -1;
        particle.x = Math.max(0, Math.min(effects.width, particle.x));
        particle.y = Math.max(0, Math.min(effects.height, particle.y));
        ctx.beginPath();
        ctx.arc(particle.x, particle.y, 2, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(37,99,235,.28)";
        ctx.fill();
      });
    };
    effects.start = () => {
      if (!shouldAnimateSystemParticles() || effects.animationId !== null) return;
      effects.lastFrameTime = performance.now();
      effects.animationId = window.requestAnimationFrame(draw);
    };
    effects.stop = () => {
      if (effects.animationId === null) return;
      window.cancelAnimationFrame(effects.animationId);
      effects.animationId = null;
    };
    effects.resizeHandler = scheduleResize;
    effects.mouseMoveHandler = event => {
      mouse.x = event.clientX;
      mouse.y = event.clientY;
      mouse.active = true;
    };
    effects.mouseLeaveHandler = () => { mouse.active = false; };
    window.addEventListener("resize", effects.resizeHandler);
    window.addEventListener("mousemove", effects.mouseMoveHandler);
    window.addEventListener("mouseleave", effects.mouseLeaveHandler);
    resize();
    systemParticleEffects = effects;
    syncSystemParticles();
  }

  function fillAiRows(hostId, type, items) {
    const host = document.getElementById(hostId);
    if (!host || !items.length) return;
    host.innerHTML = "";
    items.forEach(item => {
      addItemRow(host, type);
      const row = host.lastElementChild;
      if (!row) return;
      row.querySelector("[data-search]").value = item.material_name || item.name || item.keyword || "";
      const code = row.querySelector("[data-code]");
      if (code) code.value = item.material_code || "";
      const brand = row.querySelector("[data-brand]");
      if (brand) brand.value = item.brand_model || "";
      const spec = row.querySelector("[data-spec]");
      if (spec) spec.value = item.spec || "";
      const applicant = row.querySelector("[data-purchase-applicant]");
      if (applicant) applicant.value = item.purchase_applicant || "";
      const materialId = row.querySelector("[data-material-id]");
      if (materialId && item.material_id) materialId.value = item.material_id;
      const purchase = row.querySelector("[data-purchase]");
      if (purchase) purchase.value = item.purchase_quantity || item.quantity || "";
      const arrival = row.querySelector("[data-arrival]");
      if (arrival) arrival.value = item.arrival_quantity || item.quantity || "";
      const price = row.querySelector("[data-price]");
      if (price) price.value = item.unit_price || "";
      const qty = row.querySelector("[data-qty]");
      if (qty) qty.value = item.request_quantity || item.quantity || "";
    });
  }

  function showTodoPopup() {
    const todos = system.boot?.todos || [];
    if (todos.length) setTimeout(() => toast(`\u60a8\u6709 ${todos.length} \u4e2a\u5f85\u529e\u4e8b\u9879`), 400);
  }

  function startWorkflowRealtime() {
    if (system.workflowStream || system.workflowPoller) return;
    if ("EventSource" in window) {
      const stream = new EventSource("/api/todos/stream");
      system.workflowStream = stream;
      stream.addEventListener("todos", event => {
        try {
          applyRealtimeTodos(JSON.parse(event.data || "{}"));
        } catch (error) {
          console.warn("workflow stream parse failed", error);
        }
      });
      stream.onerror = () => {
        stream.close();
        system.workflowStream = null;
        ensureTodoPolling();
      };
    } else {
      ensureTodoPolling();
    }
    document.addEventListener("visibilitychange", () => {
      syncSystemParticles();
      if (!document.hidden) refreshRealtimeTodos();
    });
  }

  function ensureTodoPolling() {
    if (!system.workflowPoller) {
      system.workflowPoller = window.setInterval(refreshRealtimeTodos, 10000);
    }
  }

  async function refreshRealtimeTodos() {
    if (!system.boot?.user) return;
    try {
      const data = await systemApi("/api/todos");
      applyRealtimeTodos(data);
    } catch (error) {
      console.warn("workflow realtime refresh failed", error);
    }
  }

  function applyRealtimeTodos(data) {
    const todos = data.items || [];
    const oldKeys = system.todoKeys || new Set();
    const newItems = todos.filter(item => !oldKeys.has(todoKey(item)));
    system.boot.todos = todos;
    system.todoKeys = new Set(todos.map(todoKey));
    document.title = todos.length ? `(${todos.length}) 仓库物料系统` : "仓库物料系统";
    if (newItems.length) {
      newItems.slice(0, 3).forEach(item => showDesktopNotice("待办流程", item.notification_body || `${item.form_no || ""} ${workflowStepLabel(item.step_code || "")}`.trim(), `todo:${todoKey(item)}`));
    }
    // The dashboard owns #todoList and renders its compact telemetry rows itself.
    // The workflow table is only valid in the regular business-workbench todo view.
    if (system.view !== "dashboard") {
      const todoList = document.getElementById("todoList");
      if (todoList) {
        todoList.innerHTML = todoTable(todos);
        bindTodoButtons(todoList);
      }
    }
    if (system.view === "dashboard" && newItems.length) renderSystemMain();
  }

  function showDesktopNotice(title, body, tag) {
    const message = String(body || title || "").trim();
    if (!message) return;
    if ("Notification" in window && Notification.permission === "granted") {
      try {
        openDesktopNotification(title, message, tag);
        return;
      } catch (error) {}
    }
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission().then(permission => {
        if (permission === "granted") {
          try { openDesktopNotification(title, message, tag); } catch (error) { toast(message, 8000); }
        } else {
          toast(message, 8000);
        }
      });
      return;
    }
    toast(message, 8000);
  }

  function openDesktopNotification(title, message, tag) {
    const notice = new Notification(title || "仓库物料系统", {
      body: message,
      tag: tag || message.slice(0, 60),
      requireInteraction: true,
      renotify: true,
    });
    window.setTimeout(() => {
      try { notice.close(); } catch (error) {}
    }, 12000);
    return notice;
  }

  function startNotificationRealtime() {
    if (system.notificationPoller) return;
    refreshRealtimeNotifications({ silent: true });
    system.notificationPoller = window.setInterval(() => refreshRealtimeNotifications(), 15000);
  }

  async function refreshRealtimeNotifications(options = {}) {
    if (!system.boot?.user) return;
    try {
      const data = await systemApi("/api/notifications?read=0");
      const rows = data.items || [];
      const oldKeys = system.notificationKeys || new Set();
      const newRows = rows.filter(item => !oldKeys.has(notificationKey(item)));
      system.notificationKeys = new Set(rows.map(notificationKey));
      system.boot.unread_notifications = data.unread_count || 0;
      const nav = document.querySelector('[data-system-view="notifications"]');
      if (nav) {
        const label = nav.querySelector(".menu-label");
        const text = `通知${Number(system.boot.unread_notifications || 0) ? ` (${Number(system.boot.unread_notifications || 0)})` : ""}`;
        if (label) label.textContent = text;
        else nav.textContent = text;
      }
      if (!options.silent) {
        newRows.slice(0, 3).forEach(item => showDesktopNotice(item.title || "通知", item.body || item.title || "", `notice:${item.id || item.created_at || ""}`));
      }
      if (system.view === "notifications") {
        system.notificationRows = rows;
        const host = document.getElementById("notificationList");
        if (host && !system.notificationReadView) host.innerHTML = notificationTable(rows, false);
      }
    } catch (error) {
      console.warn("notification realtime refresh failed", error);
    }
  }


  async function renderDashboard(main) {
    const renderNonce = ++dashboardRenderNonce;
    main.innerHTML = '<div class="dashboard-loading">数据大屏加载中...</div>';
    try {
      const response = await fetch("/api/dashboard/view", { credentials: "same-origin" });
      if (!response.ok) throw new Error("数据大屏加载失败");
      const markup = await response.text();
      if (renderNonce !== dashboardRenderNonce || system.view !== "dashboard") return;
      main.innerHTML = markup;
      window.WarehouseDashboard?.mount();
    } catch (error) {
      if (renderNonce !== dashboardRenderNonce || system.view !== "dashboard") return;
      main.innerHTML = '<div class="dashboard-loading">' + escapeHtml(error.message || "数据大屏加载失败") + '</div>';
    }
  }

  async function openCodingRules() {
    openModal({ title: "物料编码规则", body: `<div id="codingRulesHtml" class="coding-rules-html">加载中...</div>`, okText: "关闭" });
    try {
      const data = await systemApi("/api/coding-rules");
      const host = document.getElementById("codingRulesHtml");
      if (host) host.innerHTML = data.html || "暂无内容";
    } catch (error) {
      const host = document.getElementById("codingRulesHtml");
      if (host) host.textContent = `加载失败：${error.message}`;
    }
  }

  async function fillNextFormNo(inputId, prefix) {
    const input = document.getElementById(inputId);
    if (!input || input.value) return;
    try {
      const data = await systemApi(`/api/workflows/next-no?prefix=${encodeURIComponent(prefix)}`);
      input.dataset.formNo = data.form_no || "";
      input.value = workflowTitleFromNo(data.form_no || "");
    } catch (error) {
      console.warn(error);
    }
  }

  function workflowTitleFromNo(formNo) {
    const user = currentUser() || {};
    return `${user.department || ""}${user.display_name || ""}${formNo || ""}`.trim();
  }

  function datePickerHtml(id, value = "") {
    const safeValue = String(value || "").slice(0, 10);
    return `<span class="date-picker" data-date-picker="${escapeAttr(id)}">
      <input id="${escapeAttr(id)}Text" type="text" readonly placeholder="年-月-日" value="${escapeAttr(formatDateCn(safeValue))}">
      <input id="${escapeAttr(id)}" type="date" value="${escapeAttr(safeValue)}">
      <button type="button" data-date-open="${escapeAttr(id)}">选择</button>
    </span>`;
  }

  function bindDatePickers(root = document) {
    root.querySelectorAll("[data-date-picker]").forEach(wrapper => {
      const id = wrapper.dataset.datePicker;
      const nativeInput = wrapper.querySelector('input[type="date"]');
      const textInput = wrapper.querySelector('input[type="text"]');
      const openButton = wrapper.querySelector("button");
      const sync = () => { textInput.value = nativeInput.value ? formatDateCn(nativeInput.value) : ""; };
      const open = () => {
        if (nativeInput.showPicker) nativeInput.showPicker();
        else nativeInput.click();
      };
      nativeInput.addEventListener("change", sync);
      textInput.addEventListener("click", open);
      openButton.addEventListener("click", open);
      sync();
    });
  }

  function renderTodo(main) {
    const todos = system.boot.todos || [];
    main.innerHTML = moduleShell("待办流程", `<div class="work-panel todo-work-panel"><div class="work-head"><h2>我的待办</h2><button id="refreshTodo" class="refresh-button" type="button">${iconSvg("refresh", "button-icon")}<span>刷新</span></button></div><div class="work-body" id="todoList">${todoTable(todos)}</div></div>`);
    document.getElementById("refreshTodo").addEventListener("click", async () => {
      const data = await systemApi("/api/todos");
      system.boot.todos = data.items || [];
      document.getElementById("todoList").innerHTML = todoTable(system.boot.todos);
      bindTodoButtons(document.getElementById("todoList"));
    });
    bindTodoButtons(document.getElementById("todoList"));
  }

  function renderNotifications(main) {
    const read = Boolean(system.notificationReadView);
    main.innerHTML = moduleShell("\u901a\u77e5", `
      <div class="work-panel"><div class="work-head"><h2>${read ? "\u5df2\u8bfb\u901a\u77e5" : "\u5f53\u524d\u901a\u77e5"}</h2><div class="flow-tools"><button id="markNotificationsRead" ${read ? "disabled" : ""}>\u4e00\u952e\u5df2\u8bfb</button><button id="toggleReadNotifications">${read ? "\u67e5\u770b\u5f53\u524d\u901a\u77e5" : "\u5df2\u8bfb\u901a\u77e5"}</button><button id="refreshNotifications">\u5237\u65b0</button></div></div><div class="work-body" id="notificationList">\u52a0\u8f7d\u4e2d...</div></div>
    `);
    document.getElementById("toggleReadNotifications").addEventListener("click", () => {
      system.notificationReadView = !system.notificationReadView;
      renderNotifications(main);
    });
    document.getElementById("refreshNotifications").addEventListener("click", loadNotifications);
    document.getElementById("markNotificationsRead").addEventListener("click", markNotificationsRead);
    loadNotifications();
  }

  async function loadNotifications() {
    const data = await systemApi(`/api/notifications?read=${system.notificationReadView ? 1 : 0}`);
    system.notificationRows = data.items || [];
    system.boot.unread_notifications = data.unread_count || 0;
    const host = document.getElementById("notificationList");
    if (!host) return;
    host.innerHTML = notificationTable(system.notificationRows, system.notificationReadView);
  }

  async function markNotificationsRead() {
    await systemApi("/api/notifications/read-all", { method: "POST", body: JSON.stringify({}) });
    toast("\u5f53\u524d\u901a\u77e5\u5df2\u5168\u90e8\u6807\u8bb0\u4e3a\u5df2\u8bfb");
    system.boot = null;
    await loadSystemBoot();
    system.notificationReadView = false;
    renderSystemMain();
    const notificationNav = document.querySelector('[data-system-view="notifications"]');
    if (notificationNav) {
      const label = notificationNav.querySelector(".menu-label");
      const text = `通知${Number(system.boot.unread_notifications || 0) ? ` (${Number(system.boot.unread_notifications || 0)})` : ""}`;
      if (label) label.textContent = text;
      else notificationNav.textContent = text;
    }
  }

  function notificationTable(rows, read) {
    return `<table class="flow-table"><thead><tr><th>\u65f6\u95f4</th><th>\u6807\u9898</th><th>\u5185\u5bb9</th><th>\u72b6\u6001</th></tr></thead><tbody>${(rows || []).map(row => `<tr><td>${escapeHtml(row.created_at || "")}</td><td>${escapeHtml(row.title || "")}</td><td>${escapeHtml(row.body || "")}</td><td>${read ? "\u5df2\u8bfb" : "\u672a\u8bfb"}</td></tr>`).join("") || `<tr><td colspan="4">${read ? "\u6682\u65e0\u5df2\u8bfb\u901a\u77e5" : "\u6682\u65e0\u5f53\u524d\u901a\u77e5"}</td></tr>`}</tbody></table>`;
  }

  function renderLogs(main) {
    main.innerHTML = moduleShell("日志中心", `
      <div class="work-panel">
        <div class="work-head"><h2>系统日志</h2><div class="flow-tools"><button class="primary" data-log-kind="runtime">运行日志</button><button data-log-kind="error">错误日志</button><button id="refreshLogs">刷新</button></div></div>
        <div class="work-body"><div id="systemLogBox" class="log-box">加载中...</div></div>
      </div>
    `);
    main.querySelectorAll("[data-log-kind]").forEach(button => button.addEventListener("click", () => loadSystemLogs(button.dataset.logKind)));
    document.getElementById("refreshLogs").addEventListener("click", () => loadSystemLogs(system.logKind || "runtime"));
    loadSystemLogs(system.logKind || "runtime");
  }

  async function loadSystemLogs(kind = "runtime") {
    system.logKind = kind;
    const host = document.getElementById("systemLogBox");
    if (!host) return;
    host.textContent = "加载中...";
    try {
      const data = await systemApi(`/api/system/logs?kind=${encodeURIComponent(kind)}&limit=500`);
      host.textContent = (data.lines || []).join("\n") || "暂无日志";
    } catch (error) {
      host.textContent = `日志加载失败：${error.message}`;
    }
  }

  function renderFlowCenter(main) {
    main.innerHTML = moduleShell("\u6d41\u7a0b\u4e2d\u5fc3", `<div class="work-panel"><div class="work-head"><h2>\u5168\u90e8\u6d41\u7a0b</h2></div><div class="work-body"><div class="form-grid"><label>\u5206\u7c7b<select id="flowType"><option value="">\u5168\u90e8</option><option value="acceptance">\u7269\u6599\u9a8c\u6536</option><option value="semifinished">\u534a\u6210\u54c1\u9a8c\u6536</option><option value="finished">\u6210\u54c1\u9a8c\u6536</option><option value="claim">\u7533\u9886/\u51fa\u5e93</option><option value="borrow">借用申请</option><option value="borrow_return">借用归还</option></select></label><label>\u72b6\u6001<select id="flowStatus"><option value="">\u5168\u90e8</option><option value="acceptance">\u9a8c\u6536\u4e2d</option><option value="leader_acceptance">\u9a8c\u6536\u9886\u5bfc\u5ba1\u6279</option><option value="inbound">\u5f85\u5165\u5e93</option><option value="leader_claim">\u7533\u9886\u5ba1\u6279</option><option value="applicant_revision">\u53d1\u8d77\u4eba\u4fee\u6539</option><option value="outbound">\u5f85\u51fa\u5e93</option><option value="leader_borrow">借用审批</option><option value="borrow_outbound">借用待出库</option><option value="return_inbound">归还待入库</option><option value="completed">\u5df2\u529e\u7ed3</option></select></label></div><div class="form-grid" style="margin-top:8px"><label>名称<input id="flowMaterialName" placeholder="物料/成品/半成品名称模糊搜索"></label><label>编号<input id="flowMaterialCode" placeholder="物料/成品/半成品编号模糊搜索"></label><label>\u54c1\u724c\u578b\u53f7<input id="flowBrandModel" placeholder="\u54c1\u724c\u578b\u53f7\u6a21\u7cca\u641c\u7d22"></label><label>\u6280\u672f\u89c4\u683c<input id="flowSpec" placeholder="技术规格/成品半成品规格参数模糊搜索"></label><label>申请人<input id="flowApplicant" placeholder="申请人姓名/账号/部门"></label><label>\u9a8c\u6536\u4eba<input id="flowInspector" placeholder="\u9a8c\u6536\u4eba\u59d3\u540d\u6a21\u7cca\u641c\u7d22"></label><label>\u6d41\u7a0b\u5355\u53f7<input id="flowFormNo" placeholder="\u6d41\u7a0b\u5355\u53f7\u6a21\u7cca\u641c\u7d22"></label></div><div style="display:flex;gap:8px;margin-top:8px"><button id="runFlowFilter" class="primary">\u7b5b\u9009</button><button id="clearFlowFilter">\u6e05\u7a7a</button></div><div class="flow-tools"><button id="exportFlowFiltered">\u5bfc\u51fa\u5f53\u524d\u7b5b\u9009</button><button id="exportFlowChecked">\u5bfc\u51fa\u52fe\u9009</button><button id="printFlowFiltered">\u6253\u5370\u5f53\u524d\u7b5b\u9009</button><button id="printFlowChecked">\u6253\u5370\u52fe\u9009</button><button id="deleteFlowChecked" class="danger">\u5220\u9664\u52fe\u9009</button></div><div id="flowCenterList"></div></div></div>`);
    document.getElementById("runFlowFilter").addEventListener("click", () => {
      system.pages.flowCenter = 1;
      loadFlowCenter();
    });
    document.getElementById("clearFlowFilter").addEventListener("click", () => {
      const fields = ["flowMaterialName", "flowMaterialCode", "flowBrandModel", "flowSpec", "flowApplicant", "flowInspector", "flowFormNo"];
      fields.forEach(id => { const el = document.getElementById(id); if (el) el.value = ""; });
      document.getElementById("flowType").value = "";
      document.getElementById("flowStatus").value = "";
      system.pages.flowCenter = 1;
      loadFlowCenter();
    });
    document.getElementById("exportFlowFiltered").addEventListener("click", () => exportFlowRows(system.flowRows, "workflows.csv"));
    document.getElementById("exportFlowChecked").addEventListener("click", () => exportFlowRows(checkedFlowRows(), "selected-workflows.csv"));
    document.getElementById("printFlowFiltered").addEventListener("click", () => printFlowRows(system.flowRows));
    document.getElementById("printFlowChecked").addEventListener("click", () => printFlowRows(checkedFlowRows()));
    document.getElementById("deleteFlowChecked").addEventListener("click", deleteCheckedFlows);
    loadFlowCenter();
  }

  function renderAcceptance(main) {
    system.selectedValidators = [];
    main.innerHTML = moduleShell("物料验收", `
      <div class="work-panel"><div class="work-head"><h2>发起验收</h2><button id="addAcceptanceRow">添加行</button></div>
        <div class="work-body">
          <div class="form-grid">
            <label class="wide">验收标题<input id="accTitle" placeholder="例如：研发物料到货验收"></label>
            <div class="wide" id="accRows"></div>
            <div class="wide submit-flow-actions"><button class="primary flow-submit-button" id="submitAcceptance">提交验收流程</button></div>
          </div>
        </div>
      </div>
      <div class="work-panel"><div class="work-head"><h2>验收流程</h2><button id="refreshAcceptance">刷新</button></div><div class="work-body" id="accList"></div></div>
    `);
    const rows = document.getElementById("accRows");
    const addRow = () => addItemRow(rows, "acceptance");
    document.getElementById("addAcceptanceRow").addEventListener("click", addRow);
    addRow();
    document.getElementById("submitAcceptance").addEventListener("click", startAcceptanceSubmit);
    document.getElementById("refreshAcceptance").addEventListener("click", loadAcceptanceList);
    fillNextFormNo("accTitle", "YS");
    loadAcceptanceList();
  }

  function renderClaim(main) {
    main.innerHTML = moduleShell("物料申领", `
      <div class="work-panel"><div class="work-head"><h2>发起申领</h2><button id="addClaimRow">添加行</button></div>
        <div class="work-body">
          <div class="form-grid">
            <label class="wide">申领标题<input id="claimTitle" placeholder="例如：综合部办公用品申领"></label>
            <div class="wide claim-purpose-row">
              <label class="compact-field">用途<select id="claimPurpose"><option value="研发" selected>研发</option><option value="办公">办公</option></select></label>
              <label class="compact-field" data-claim-rd>研发类型<select id="claimRdKind"><option value="项目物料">项目物料</option><option value="辅料">辅料</option></select></label>
              <label class="compact-field" data-claim-project>项目物料类型<select id="claimProjectKind"><option value="硬件">硬件</option><option value="结构">结构</option></select></label>
              <label class="compact-field" data-claim-project>项目代号<select id="claimProjectCode">${projectCodeOptions()}</select></label>
            </div>
            <div class="wide" id="claimRows"></div>
            <div class="wide submit-flow-actions"><button class="primary flow-submit-button" id="submitClaim">提交申领流程</button></div>
          </div>
        </div>
      </div>
      <div class="work-panel"><div class="work-head"><h2>申领流程</h2><button id="refreshClaim">刷新</button></div><div class="work-body" id="claimList"></div></div>
    `);
    const rows = document.getElementById("claimRows");
    const addRow = () => addItemRow(rows, "claim");
    document.getElementById("addClaimRow").addEventListener("click", addRow);
    addRow();
    document.getElementById("submitClaim").addEventListener("click", chooseClaimLeadersAndSubmit);
    document.getElementById("refreshClaim").addEventListener("click", loadClaimList);
    document.getElementById("claimPurpose").addEventListener("change", updateClaimPurposeFields);
    document.getElementById("claimRdKind").addEventListener("change", updateClaimPurposeFields);
    updateClaimPurposeFields();
    fillNextFormNo("claimTitle", "CK");
    loadClaimList();
  }

  function renderBorrow(main) {
    const dept = currentUser()?.department || "";
    const leaderUsers = approvalLeaderUsers("borrow", "leader_borrow", dept);
    const validLeaderIds = new Set(leaderUsers.map(user => Number(user.id)));
    if (!validLeaderIds.has(Number(system.selectedBorrowLeader || 0))) system.selectedBorrowLeader = Number(leaderUsers[0]?.id || 0);
    main.innerHTML = moduleShell("借用申请", `
      <div class="work-panel"><div class="work-head"><h2>发起借用</h2><button id="refreshBorrowItems">刷新可借物料</button></div>
        <div class="work-body">
          <div class="form-grid">
            <label class="wide">借用标题<input id="borrowTitle" placeholder="例如：项目调试物料借用"></label>
            <label class="short-date-field">预计归还日期${datePickerHtml("borrowReturnDate")}</label>
            <label class="wide">搜索可借物料/半成品/成品<input id="borrowKeyword" placeholder="物料名称 / 物料编号 / 品牌型号 / 技术规格 / 采购申请人 / 成品半成品编号 / 名称 / 规格"></label>
            <button class="primary borrow-search-button" id="searchBorrowItems">搜索</button>
          </div>
          <div id="borrowItemList" style="margin-top:10px;"></div>
          <h3>本次借用明细</h3>
          <div id="borrowDraftList"></div>
          <div class="submit-flow-actions"><button class="primary flow-submit-button" id="submitBorrow">提交借用申请</button></div>
        </div>
      </div>
      <div class="work-panel"><div class="work-head"><h2>我的借用流程</h2><button id="refreshBorrowFlows">刷新</button></div><div class="work-body" style="padding-top:0"><div class="search-bar"><input type="text" id="borrowMakerFilter" placeholder="搜索制作人..." class="search-input"><input type="text" id="borrowProjectFilter" placeholder="搜索所属项目..." class="search-input"><button id="clearBorrowFiltersBtn">清除</button></div><div id="borrowFlowList"></div></div></div>
    `);
    document.getElementById("searchBorrowItems").addEventListener("click", loadBorrowItems);
    document.getElementById("refreshBorrowItems").addEventListener("click", loadBorrowItems);
    document.getElementById("borrowKeyword").addEventListener("keydown", event => { if (event.key === "Enter") loadBorrowItems(); });
    document.getElementById("submitBorrow").addEventListener("click", chooseBorrowLeaderAndSubmit);
    document.getElementById("refreshBorrowFlows").addEventListener("click", () => fetchBorrowApplications("", ""));
    document.getElementById("borrowMakerFilter")?.addEventListener("input", debouncedFilterBorrow);
    document.getElementById("borrowProjectFilter")?.addEventListener("input", debouncedFilterBorrow);
    document.getElementById("clearBorrowFiltersBtn")?.addEventListener("click", clearBorrowFilters);
    bindDatePickers(main);
    fillNextFormNo("borrowTitle", "JY");
    system.borrowItemRows = [];
    document.getElementById("borrowItemList").innerHTML = `<div class="empty-state">请输入条件并点击搜索后显示可借物料。</div>`;
    renderBorrowDraftList();
    loadBorrowFlowList();
  }

  function renderMyBorrow(main) {
    system.myBorrowOverdueOnly = new URLSearchParams(window.location.search).get("overdue") === "1";
    main.innerHTML = moduleShell("我的借用", `
      <div class="work-panel"><div class="work-head"><h2>${system.myBorrowOverdueOnly ? "逾期借用物料" : "当前借用物料"}</h2><button id="refreshMyBorrow">刷新</button></div><div class="work-body" id="myBorrowList">加载中...</div></div>
    `);
    document.getElementById("refreshMyBorrow").addEventListener("click", loadMyBorrow);
    loadMyBorrow();
  }

  function renderRecycle(main) {
    main.innerHTML = moduleShell("回收站", `
      <div class="work-panel"><div class="work-head"><h2>回收站</h2><button id="refreshRecycleBin">刷新</button></div><div class="work-body" id="recycleBin">加载中...</div></div>
    `);
    document.getElementById("refreshRecycleBin").addEventListener("click", loadRecycleBin);
    loadRecycleBin();
  }

  function renderMyInspections(main) {
    main.innerHTML = moduleShell("我的验收", `
      <div class="work-panel"><div class="work-head"><h2>我参与验收的流程</h2><button id="refreshMyInspections">刷新</button></div><div class="work-body">${personalFlowFilterHtml("myInspection")}<div id="myInspectionList">加载中...</div></div></div>
    `);
    document.getElementById("refreshMyInspections").addEventListener("click", loadMyInspections);
    bindPersonalFlowFilters("myInspection", "myInspections", loadMyInspections);
    loadMyInspections();
  }

  function renderMyStarted(main) {
    main.innerHTML = moduleShell("我的发起", `
      <div class="work-panel"><div class="work-head"><h2>我发起的流程</h2><button id="refreshMyStarted">刷新</button></div><div class="work-body">${personalFlowFilterHtml("myStarted")}<div id="myStartedList">加载中...</div></div></div>
    `);
    document.getElementById("refreshMyStarted").addEventListener("click", loadMyStarted);
    bindPersonalFlowFilters("myStarted", "myStarted", loadMyStarted);
    loadMyStarted();
  }

  function personalFlowFilterHtml(prefix) {
    return `<div class="compact-action-row">
      <label>开始日期${datePickerHtml(`${prefix}From`)}</label>
      <label>结束日期${datePickerHtml(`${prefix}To`)}</label>
      <label>流程状态<select id="${prefix}Status">${personalFlowStatusOptions()}</select></label>
      <button type="button" class="primary query-search-button" id="${prefix}Filter">筛选</button>
      <button type="button" id="${prefix}Clear">清空</button>
    </div>`;
  }

  function personalFlowStatusOptions() {
    const statuses = ["acceptance", "leader_acceptance", "inbound", "leader_claim", "outbound", "leader_borrow", "borrow_outbound", "return_inbound", "applicant_revision", "rejected", "completed"];
    return `<option value="">全部</option>${statuses.map(status => `<option value="${status}">${escapeHtml(workflowStatusLabel(status))}</option>`).join("")}`;
  }

  function bindPersonalFlowFilters(prefix, pageKey, loader) {
    bindDatePickers(document.querySelector(".system-main") || document);
    document.getElementById(`${prefix}Filter`)?.addEventListener("click", () => {
      system.pages[pageKey] = 1;
      loader();
    });
    document.getElementById(`${prefix}Clear`)?.addEventListener("click", () => {
      [`${prefix}From`, `${prefix}To`].forEach(id => {
        const input = document.getElementById(id);
        const text = document.getElementById(`${id}Text`);
        if (input) input.value = "";
        if (text) text.value = "";
      });
      const status = document.getElementById(`${prefix}Status`);
      if (status) status.value = "";
      system.pages[pageKey] = 1;
      loader();
    });
  }

  function personalFlowQuery(prefix) {
    const params = new URLSearchParams();
    const dateFrom = document.getElementById(`${prefix}From`)?.value || "";
    const dateTo = document.getElementById(`${prefix}To`)?.value || "";
    const status = document.getElementById(`${prefix}Status`)?.value || "";
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    if (status) params.set("status", status);
    return params.toString();
  }

  function projectCodeOptions(selected = "") {
    const codes = system.boot?.workflow_settings?.project_codes || [];
    if (!codes.length) return `<option value="">未设置项目代号</option>`;
    return codes.map(code => `<option value="${escapeAttr(code)}" ${code === selected ? "selected" : ""}>${escapeHtml(code)}</option>`).join("");
  }

  function updateClaimPurposeFields() {
    const isRd = document.getElementById("claimPurpose")?.value === "研发";
    const isProjectMaterial = isRd && document.getElementById("claimRdKind")?.value === "项目物料";
    document.querySelectorAll("[data-claim-rd]").forEach(el => { el.style.display = isRd ? "" : "none"; });
    document.querySelectorAll("[data-claim-project]").forEach(el => { el.style.display = isProjectMaterial ? "" : "none"; });
  }

  function defaultValidatorSelection(formType) {
    const users = eligibleValidatorUsers(formType);
    const validIds = new Set(users.map(user => Number(user.id)));
    const selected = (system.selectedValidators || []).map(Number).filter(id => validIds.has(id));
    return selected.length ? selected : [];
  }

  function chooseValidatorsAndSubmit(formType, submitter) {
    openValidatorPicker(defaultValidatorSelection(formType), ids => {
      system.selectedValidators = ids;
      return submitter();
    }, formType);
  }

  function chooseClaimLeadersAndSubmit() {
    const dept = currentUser()?.department || "";
    const users = approvalLeaderUsers("claim", "leader_claim", dept);
    if (!users.length) {
      toast("当前部门未配置审批领导");
      return;
    }
    const validIds = new Set(users.map(user => Number(user.id)));
    const selected = (system.selectedClaimLeaders || []).map(Number).filter(id => validIds.has(id));
    if (!manualApprovalLeaderEnabled()) {
      system.selectedClaimLeaders = [selected[0] || Number(users[0].id)];
      return submitClaim();
    }
    openPeoplePicker({
      title: "选择审批领导",
      users,
      role: "leader",
      selected: selected.length ? selected : users.slice(0, 1).map(user => Number(user.id)),
      multiple: Boolean(system.boot.workflow_settings?.allow_multi_claim_leaders),
      department: dept,
      onConfirm: ids => {
        if (!ids.length) throw new Error("请选择审批领导");
        system.selectedClaimLeaders = ids;
        return submitClaim();
      },
    });
  }

  function chooseBorrowLeaderAndSubmit() {
    const dept = currentUser()?.department || "";
    const users = approvalLeaderUsers("borrow", "leader_borrow", dept);
    if (!users.length) {
      toast("当前部门未配置审批领导");
      return;
    }
    const validIds = new Set(users.map(user => Number(user.id)));
    const selected = Number(system.selectedBorrowLeader || 0);
    if (!manualApprovalLeaderEnabled()) {
      system.selectedBorrowLeader = selected && validIds.has(selected) ? selected : Number(users[0].id);
      return submitBorrow();
    }
    openPeoplePicker({
      title: "选择审批领导",
      users,
      role: "leader",
      selected: selected && validIds.has(selected) ? [selected] : users.slice(0, 1).map(user => Number(user.id)),
      multiple: false,
      department: dept,
      onConfirm: ids => {
        if (!ids.length) throw new Error("请选择审批领导");
        system.selectedBorrowLeader = Number(ids[0]);
        return submitBorrow();
      },
    });
  }

  function renderSemifinished(main) {
    system.selectedValidators = [];
    main.innerHTML = moduleShell("半成品验收", `
      <div class="work-panel"><div class="work-head"><h2>发起半成品验收</h2></div>
        <div class="work-body">
          <div class="form-grid">
            <label class="wide">验收单号/标题<input id="semiTitle"></label>
            <label>半成品名称<input id="semiName"></label>
            <label>规格参数<input id="semiSpec"></label>
            <label>验收数量<input id="semiQty" type="number" step="1" min="1" value="1"></label>
            <label>单位<input id="semiUnit" value="台"></label>
            <label>制作人<select id="semiMakerId"><option value="">请选择制作人</option></select></label>
            <label>验收日期${datePickerHtml("semiDate", new Date().toISOString().slice(0, 10))}</label>
            <label class="wide">项目代号<input id="semiProjectCode" placeholder="输入项目代号"></label>
            <div class="wide"><div class="flow-tools"><strong>单台所用物料</strong><button type="button" id="addSemiMaterial">添加物料</button></div><div id="semiMaterials"></div></div>
            <div class="wide"><span class="badge" id="semiCost">成本价：¥0.00 / 台</span></div>
            <div class="wide submit-flow-actions"><button class="primary flow-submit-button" id="submitSemi">提交半成品验收</button></div>
          </div>
        </div>
      </div>
      <div class="work-panel"><div class="work-head"><h2>半成品验收流程</h2><button id="refreshSemiFlows">刷新</button></div><div class="work-body" id="semiFlowList"></div></div>
    `);
    document.getElementById("semiQty").addEventListener("input", () => refreshComponentRows("semiMaterials", "semiQty", "semiCost"));
    const addRow = () => addMaterialComponentRow(document.getElementById("semiMaterials"), system.productionMaterialPool, "semiQty", "semiCost");
    document.getElementById("addSemiMaterial").addEventListener("click", addRow);
    document.getElementById("submitSemi").addEventListener("click", startSemifinishedSubmit);
    document.getElementById("refreshSemiFlows").addEventListener("click", loadSemifinishedFlowList);
    document.getElementById("semiTitle").value = "";
    // Load users for maker dropdown
    systemApi("/api/system/users").then(data => {
      const select = document.getElementById("semiMakerId");
      if (select && data.users) {
        select.innerHTML = '<option value="">请选择制作人</option>';
        data.users.forEach(user => {
          const option = document.createElement("option");
          option.value = user.id;
          option.textContent = user.display_name || user.username;
          select.appendChild(option);
        });
        // Select current user by default
        const currentUserId = currentUser()?.id;
        if (currentUserId) {
          select.value = currentUserId;
        }
      }
    }).catch(() => {});
    bindDatePickers(main);
    fillNextFormNo("semiTitle", "BY");
    loadProductionMaterialPool()
      .catch(error => {
        console.warn("半成品验收物料池加载失败", error);
        system.productionMaterialPool = [];
      })
      .finally(() => {
        if (!document.querySelector("#semiMaterials .component-row")) addRow();
      });
    loadSemifinishedFlowList();
  }

  function renderFinished(main) {
    system.selectedValidators = [];
    main.innerHTML = moduleShell("成品验收", `
      <div class="work-panel"><div class="work-head"><h2>发起成品验收</h2></div>
        <div class="work-body">
          <div class="form-grid">
            <label class="wide">验收单号/标题<input id="finTitle"></label>
            <label>成品名称<input id="finName"></label>
            <label>规格参数<input id="finSpec"></label>
            <label>验收数量<input id="finQty" type="number" step="1" min="1" value="1"></label>
            <label>单位<input id="finUnit" value="台"></label>
            <label>制作人<select id="finMakerId"><option value="">请选择制作人</option></select></label>
            <label>验收日期${datePickerHtml("finDate", new Date().toISOString().slice(0, 10))}</label>
            <label class="wide">项目代号<input id="finProjectCode" placeholder="输入项目代号"></label>
            <div class="wide"><div class="flow-tools"><strong>所用物料</strong><button type="button" id="addFinMaterial">添加物料</button></div><div id="finMaterials"></div></div>
            <div class="wide"><div class="flow-tools"><strong>所用半成品</strong><button type="button" id="addFinSemi">添加半成品</button></div><div id="finSemis"></div></div>
            <div class="wide"><span class="badge" id="finCost">成本价：¥0.00 / 台</span></div>
            <div class="wide submit-flow-actions"><button class="primary flow-submit-button" id="submitFin">提交成品验收</button></div>
          </div>
        </div>
      </div>
      <div class="work-panel"><div class="work-head"><h2>成品验收流程</h2><button id="refreshFinFlows">刷新</button></div><div class="work-body" id="finFlowList"></div></div>
    `);
    ["finQty", "finName"].forEach(id => document.getElementById(id).addEventListener("input", () => {
      refreshComponentRows("finMaterials", "finQty", "finCost", "finSemis");
    }));
    const addMaterial = () => addMaterialComponentRow(document.getElementById("finMaterials"), system.productionMaterialPool, "finQty", "finCost", "finSemis");
    const addSemi = () => addSemifinishedComponentRow(document.getElementById("finSemis"), system.semifinishedPool, "finQty", "finCost", "finMaterials");
    document.getElementById("addFinMaterial").addEventListener("click", addMaterial);
    document.getElementById("addFinSemi").addEventListener("click", addSemi);
    document.getElementById("submitFin").addEventListener("click", startFinishedSubmit);
    document.getElementById("refreshFinFlows").addEventListener("click", loadFinishedFlowList);
    document.getElementById("finTitle").value = "";
    systemApi("/api/system/users").then(data => {
      const select = document.getElementById("finMakerId");
      if (select && data.users) {
        select.innerHTML = '<option value="">请选择制作人</option>';
        data.users.forEach(user => {
          const option = document.createElement("option");
          option.value = user.id;
          option.textContent = user.display_name || user.username;
          select.appendChild(option);
        });
        const currentUserId = currentUser()?.id;
        if (currentUserId) {
          select.value = currentUserId;
        }
      }
    }).catch(() => {});
    bindDatePickers(main);
    fillNextFormNo("finTitle", "CY");
    loadProductionMaterialPool()
      .catch(error => {
        console.warn("成品验收物料池加载失败", error);
        system.productionMaterialPool = [];
      })
      .finally(() => {
        if (!document.querySelector("#finMaterials .component-row")) addMaterial();
      });
    loadSemifinishedPool()
      .catch(error => {
        console.warn("成品验收半成品池加载失败", error);
        system.semifinishedPool = [];
      })
      .finally(() => {
        if (!document.querySelector("#finSemis .component-row")) addSemi();
      });
    loadFinishedFlowList();
  }

  function renderSemifinishedInventory(main) {
    main.innerHTML = moduleShell("半成品库", `
      <div class="work-panel"><div class="work-head"><h2>半成品库存</h2><button id="refreshSemiInventory">刷新</button></div><div class="work-body"><div class="inventory-filter-bar"><label>关键词<input id="semiInventoryKeyword" placeholder="搜索半成品名称/规格/位置/项目代号"></label><button id="runSemiInventorySearch" class="primary query-search-button">搜索</button></div><div id="semiInventoryTabs"></div><div id="semiInventoryList"></div></div></div>
    `);
    const tabsContainer = document.getElementById("semiInventoryTabs");
    renderInventoryTabs(tabsContainer, [
      { label: "\u5408\u683c\u54c1", key: "qualified" },
      { label: "\u4e0d\u5408\u683c\u54c1", key: "defective" },
      { label: "\u62a5\u5e9f\u54c1", key: "scrapped" },
    ], (key) => {
      system.currentSemiTab = key;
      system.pages.semiInventory = 1;
      system.pages.defectiveSemi = 1;
      system.pages.scrappedSemi = 1;
      loadSemiTab(key);
    });
    document.getElementById("refreshSemiInventory").addEventListener("click", () => loadSemiTab(system.currentSemiTab));
    document.getElementById("runSemiInventorySearch").addEventListener("click", () => {
      system.pages.semiInventory = 1;
      system.pages.defectiveSemi = 1;
      system.pages.scrappedSemi = 1;
      loadSemiTab(system.currentSemiTab);
    });
    document.getElementById("semiInventoryKeyword").addEventListener("keydown", event => {
      if (event.key === "Enter") {
        system.pages.semiInventory = 1;
        system.pages.defectiveSemi = 1;
        system.pages.scrappedSemi = 1;
        loadSemiTab(system.currentSemiTab);
      }
    });
    loadSemiTab("qualified");
  }

  function renderFinishedInventory(main) {
    main.innerHTML = moduleShell("成品库", `
      <div class="work-panel"><div class="work-head"><h2>成品库存</h2><button id="refreshFinishedInventory">刷新</button></div><div class="work-body"><div class="inventory-filter-bar"><label>关键词<input id="finishedInventoryKeyword" placeholder="搜索成品名称/规格/流水号/位置/项目代号"></label><button id="runFinishedInventorySearch" class="primary query-search-button">搜索</button></div><div id="finishedInventoryTabs"></div><div id="finishedInventoryList"></div></div></div>
    `);
    const tabsContainer = document.getElementById("finishedInventoryTabs");
    renderInventoryTabs(tabsContainer, [
      { label: "\u5408\u683c\u54c1", key: "qualified" },
      { label: "\u4e0d\u5408\u683c\u54c1", key: "defective" },
      { label: "\u62a5\u5e9f\u54c1", key: "scrapped" },
    ], (key) => {
      system.currentFinishedTab = key;
      system.pages.finishedInventory = 1;
      system.pages.defectiveFinished = 1;
      system.pages.scrappedFinished = 1;
      loadFinishedTab(key);
    });
    document.getElementById("refreshFinishedInventory").addEventListener("click", () => loadFinishedTab(system.currentFinishedTab));
    document.getElementById("runFinishedInventorySearch").addEventListener("click", () => {
      system.pages.finishedInventory = 1;
      system.pages.defectiveFinished = 1;
      system.pages.scrappedFinished = 1;
      loadFinishedTab(system.currentFinishedTab);
    });
    document.getElementById("finishedInventoryKeyword").addEventListener("keydown", event => {
      if (event.key === "Enter") {
        system.pages.finishedInventory = 1;
        system.pages.defectiveFinished = 1;
        system.pages.scrappedFinished = 1;
        loadFinishedTab(system.currentFinishedTab);
      }
    });
    loadFinishedTab("qualified");
  }

  function renderOutbound(main) {
    main.innerHTML = moduleShell("物料出库", `<div class="work-panel"><div class="work-head"><h2>待出库申领单</h2><button id="refreshOutbound">刷新</button></div><div class="work-body" id="outboundList"></div></div>`);
    document.getElementById("refreshOutbound").addEventListener("click", loadOutboundList);
    loadOutboundList();
  }

  function renderQuery(main) {
    main.innerHTML = moduleShell("物料查询", `
      <div class="work-panel"><div class="work-head"><h2>查询物料和批次</h2>${canAddMaterial() ? `<button id="addMaterialFromQuery" class="primary">添加物料</button>` : ""}</div>
        <div class="work-body">
          <div class="form-grid">
            <label class="wide">关键词<input id="queryKeyword" placeholder="物料编号 / 名称 / 品牌型号 / 技术规格 / 采购申请人"></label>
            <div class="wide inventory-query-actions">
              <button id="runQuery" class="primary query-search-button">查询</button>
              ${window.WarehouseInventoryListControls?.toolbarHtml("materialQuery") || ""}
            </div>
          </div>
          <div id="queryResult"></div>
        </div>
      </div>
    `);
    document.getElementById("runQuery").addEventListener("click", runMaterialQuery);
    document.getElementById("addMaterialFromQuery")?.addEventListener("click", addMaterialFromQuery);
    document.getElementById("queryKeyword").addEventListener("keydown", event => {
      if (event.key === "Enter") runMaterialQuery();
    });
    const inventoryControls = window.WarehouseInventoryListControls;
    system.materialQueryControlState ||= { sort: "code", filters: { hideZero: false } };
    inventoryControls?.bind(main, "materialQuery", system.materialQueryControlState, () => system.materialQuerySourceRows || [], rows => {
      system.materialQueryRows = rows;
      system.pages.materialQuery = 1;
      renderMaterialQueryPage();
    });
  }

  function renderStats(main) {
    main.innerHTML = moduleShell("统计", `
      <div class="work-panel"><div class="work-head"><h2>库存流转统计</h2></div>
        <div class="work-body">
          <div class="form-grid">
            <label>类型<select id="statKind"><option value="inbound">入库统计</option><option value="outbound">出库统计</option><option value="borrow">借用统计</option><option value="return">归还统计</option></select></label>
            <label>仓库<select id="statWarehouse"><option value="">全部</option><option value="office">办公用品库</option><option value="rd">研发材料库</option></select></label>
          </div>
          <div class="compact-action-row">
            <label>开始日期${datePickerHtml("statFrom")}</label>
            <label>结束日期${datePickerHtml("statTo")}</label>
            <button class="primary" id="runStats">筛选</button>
            <button id="exportStats">导出</button>
            <button id="printStats">打印</button>
          </div>
          <div class="print-area" id="statsResult"></div>
        </div>
      </div>
    `);
    document.getElementById("runStats").addEventListener("click", runStats);
    document.getElementById("exportStats").addEventListener("click", () => exportTableCsv("statsResult", "statistics.csv"));
    document.getElementById("printStats").addEventListener("click", () => printElement("statsResult"));
    bindDatePickers(main);
  }

  function renderStocktake(main) {
    const canStartStocktake = hasPerm("start_stocktake");
    main.innerHTML = moduleShell("盘点", `
      ${canStartStocktake ? `<div class="work-panel"><div class="work-head"><h2>创建月度盘点</h2></div>
        <div class="work-body">
          <div class="stocktake-controls">
            <label>仓库<select id="pdWarehouse"><option value="">全部</option><option value="office">办公用品库</option><option value="rd">研发材料库</option></select></label>
            <label>开始日期${datePickerHtml("pdFrom")}</label>
            <label>结束日期${datePickerHtml("pdTo", new Date().toISOString().slice(0, 10))}</label>
            <label class="compact-check"><input id="pdShowZero" type="checkbox"> 显示 0 库存物品</label>
          </div>
          <div class="compact-action-row">
            <button class="primary" id="createStocktake">生成盘点单</button>
            <button id="printStocktake">打印</button>
          </div>
          <div class="print-area" id="stocktakeResult"></div>
        </div>
      </div>` : ""}
      <div class="work-panel"><div class="work-head"><h2>历史盘点</h2><button id="loadStocktakes">刷新</button></div><div class="work-body" id="stocktakeList"></div></div>
    `);
    document.getElementById("createStocktake")?.addEventListener("click", chooseStocktakeSupervisorAndCreate);
    document.getElementById("loadStocktakes").addEventListener("click", loadStocktakes);
    document.getElementById("printStocktake")?.addEventListener("click", () => printElement("stocktakeResult"));
    bindDatePickers(main);
    loadStocktakes();
  }

  function renderAdmin(main) {
    main.innerHTML = moduleShell("\u7cfb\u7edf\u8bbe\u7f6e", `
      <div class="work-panel"><div class="work-head"><h2>\u90e8\u95e8\u7ba1\u7406</h2><button id="saveDept" class="primary">\u4fdd\u5b58\u90e8\u95e8</button></div><div class="work-body"><input type="hidden" id="deptId"><div class="form-grid"><label>\u90e8\u95e8\u540d<input id="deptName"></label><label>\u63cf\u8ff0<input id="deptDesc"></label></div><div id="deptHost"></div></div></div>
      <div class="work-panel"><div class="work-head"><h2>\u8d26\u53f7\u4e0e\u89d2\u8272</h2><button id="saveUser" class="primary">\u4fdd\u5b58\u7528\u6237</button></div><div class="work-body"><input type="hidden" id="editUserId"><div class="form-grid"><label>\u8d26\u53f7<input id="newUsername"></label><label>\u59d3\u540d<input id="newDisplayName"></label><label>\u5bc6\u7801<input id="newPassword" type="password" placeholder="\u65b0\u5efa\u9ed8\u8ba4 123456\uff0c\u7f16\u8f91\u7559\u7a7a\u4e0d\u6539"></label><label>\u90e8\u95e8<select id="newDepartment">${departmentOptions()}</select></label><label>\u72b6\u6001<select id="newActive"><option value="1">\u542f\u7528</option><option value="0">\u505c\u7528</option></select></label><label class="wide">\u89d2\u8272<select id="newRoles" multiple size="5">${(system.boot.roles || []).map(r => `<option value="${r.code}">${escapeHtml(r.name)}</option>`).join("")}</select></label></div><div id="usersHost"></div></div></div>
      <div class="work-panel"><div class="work-head"><h2>密码策略</h2><button id="savePasswordPolicy" class="primary">保存密码策略</button></div><div class="work-body" id="passwordPolicySettings">加载中...</div></div>
      <div class="work-panel"><div class="work-head"><h2>数据校验</h2><button id="saveDataValidation" class="primary">保存数据校验</button></div><div class="work-body" id="dataValidationSettings">加载中...</div></div>
      <div class="work-panel"><div class="work-head"><h2>\u6d41\u7a0b\u8bbe\u7f6e</h2><button id="saveWorkflowSettings" class="primary">\u4fdd\u5b58\u6d41\u7a0b\u8bbe\u7f6e</button></div><div class="work-body" id="workflowSettings">\u52a0\u8f7d\u4e2d...</div></div>
      <div class="work-panel"><div class="work-head"><h2>备份与恢复</h2><button id="saveBackupSettings" class="primary">保存备份设置</button></div><div class="work-body" id="backupSettings">加载中...</div></div>
      <div class="work-panel"><div class="work-head"><h2>回收站</h2><button id="refreshRecycleBin">刷新</button></div><div class="work-body" id="recycleBin">加载中...</div></div>
      <div class="work-panel"><div class="work-head"><h2>关键操作审计</h2><button id="exportAuditLogs">导出审计</button></div><div class="work-body"><div class="form-grid"><label>关键词<input id="auditKeyword" placeholder="账号/操作/对象"></label><label>开始日期<input id="auditFrom" type="date"></label><label>结束日期<input id="auditTo" type="date"></label><button id="searchAuditLogs" class="primary">查询</button></div><div id="auditLogList"></div></div></div>
      ${system.boot.ai_enabled ? `<div class="work-panel"><div class="work-head"><h2>\u7269\u6599\u5c0f\u52a9\u624b\u914d\u7f6e</h2><button id="saveAiConfig">\u4fdd\u5b58\u914d\u7f6e</button></div><div class="work-body" id="aiConfig">\u52a0\u8f7d\u4e2d...</div></div>` : ""}
    `);
    setupAdminTabs(main);
    document.getElementById("saveDept").addEventListener("click", saveDepartment);
    document.getElementById("saveUser").addEventListener("click", saveNewUser);
    document.getElementById("usersHost").insertAdjacentHTML("beforebegin", `<div class="flow-tools"><button id="downloadUserTemplate">下载导入模板</button><input id="userImportFile" type="file" accept=".csv,text/csv"><button id="importUsers" class="primary">导入用户</button></div>`);
    document.getElementById("downloadUserTemplate").addEventListener("click", () => { window.location.href = "/api/system/users/template"; });
    document.getElementById("importUsers").addEventListener("click", importUsers);
    loadUsers();
    loadDepartments();
    loadPasswordPolicy();
    loadDataValidationSettings();
    loadWorkflowSettings();
    loadBackupSettings();
    loadRecycleBin();
    loadAuditLogs();
    document.getElementById("saveBackupSettings").addEventListener("click", saveBackupSettings);
    document.getElementById("refreshRecycleBin").addEventListener("click", loadRecycleBin);
    document.getElementById("searchAuditLogs").addEventListener("click", loadAuditLogs);
    document.getElementById("exportAuditLogs").addEventListener("click", () => { window.location.href = "/api/system/audit-logs/export"; });
    if (system.boot.ai_enabled) loadAiConfig();
  }

  function setupAdminTabs(main) {
    const content = Array.from(main.children).find(el => el.classList?.contains("module-content")) || main;
    const panels = Array.from(content.children).filter(el => el.classList?.contains("work-panel"));
    if (!panels.length) return;
    const tabs = document.createElement("div");
    tabs.className = "settings-tabs";
    panels.forEach((panel, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = panel.querySelector(".work-head h2")?.textContent || `设置 ${index + 1}`;
      button.addEventListener("click", () => {
        panels.forEach((item, itemIndex) => { item.style.display = itemIndex === index ? "" : "none"; });
        tabs.querySelectorAll("button").forEach((tab, itemIndex) => tab.classList.toggle("active", itemIndex === index));
      });
      tabs.appendChild(button);
    });
    content.insertBefore(tabs, panels[0]);
    tabs.querySelector("button")?.click();
  }

  function renderAi(main) {
    main.innerHTML = moduleShell("AI 助手", `
      <div class="work-panel"><div class="work-head"><h2>AI 配置</h2><button id="saveAiConfig">保存配置</button></div><div class="work-body" id="aiConfig">加载中...</div></div>
      <div class="work-panel"><div class="work-head"><h2>问 AI 物料</h2></div><div class="work-body">
        <textarea id="aiQuestion" rows="4" placeholder="例如：帮我查询 连接器 库存；或问新物料编码"></textarea>
        <p><button id="askAi" class="primary">发送</button></p>
        <pre id="aiAnswer" class="empty-state"></pre>
      </div></div>
    `);
    loadAiConfig();
    document.getElementById("askAi").addEventListener("click", askAi);
  }

  function moduleShell(title, body) {
    return `<div class="module-content" data-page-title="${escapeHtml(title)}">${body}</div>`;
  }

  function metric(label, value) {
    return `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`;
  }

  function currentUser() {
    return (system.boot?.users || []).find(user => user.id === system.userId) || system.boot?.user;
  }

  function openChangePasswordModal() {
    openModal({
      title: "修改密码",
      body: `<div class="form-grid">
        <label>当前密码<input id="oldPassword" type="password" autocomplete="current-password"></label>
        <label>新密码<input id="newSelfPassword" type="password" autocomplete="new-password"></label>
        <label>确认新密码<input id="confirmSelfPassword" type="password" autocomplete="new-password"></label>
      </div>`,
      okText: "保存密码",
      onOk: async () => {
        await systemApi("/api/change-password", {
          method: "POST",
          body: JSON.stringify({
            current_password: document.getElementById("oldPassword").value,
            new_password: document.getElementById("newSelfPassword").value,
            confirm_password: document.getElementById("confirmSelfPassword").value,
          }),
        });
        toast("密码已修改");
      },
    });
  }

  const permissionAliases = {
    delete_acceptance: "edit_acceptance",
    delete_claim: "edit_claim",
    delete_borrow: "edit_borrow",
    delete_outbound: "edit_outbound",
    delete_stocktake: "edit_stocktake",
    delete_department: "edit_department",
    delete_material: "edit_material",
    delete_attachment: "delete_material_attachment",
    delete_semifinished_inventory: "write_semifinished_inventory",
    delete_finished_inventory: "write_finished_inventory",
  };

  function hasPerm(key) {
    const permissions = system.boot?.user_permissions || {};
    return Boolean(permissions[key] || permissions[permissionAliases[key]]);
  }

  function usersBy(roleCode, department = "") {
    return (system.boot?.users || []).filter(user => {
      const roleMatch = !roleCode || String(user.role_codes || "").split(",").includes(roleCode) || (user.role_codes || []).includes?.(roleCode);
      const departmentMatch = !department || user.department === department;
      return roleMatch && departmentMatch;
    });
  }

  function userRoleCodes(user) {
    if (Array.isArray(user?.role_codes)) return user.role_codes;
    return String(user?.role_codes || "").split(",").map(item => item.trim()).filter(Boolean);
  }

  function roleHasPermission(roleCode, key) {
    const actual = permissionAliases[key] || key;
    return Boolean(system.boot?.role_permissions?.[roleCode]?.[actual]);
  }

  function userHasRolePermission(user, key) {
    return userRoleCodes(user).some(role => role === "admin" || roleHasPermission(role, key));
  }

  function stepAssigneeConfig(formType, stepCode) {
    const raw = system.boot?.workflow_settings?.workflow_step_assignees?.[formType]?.[stepCode] || {};
    if (Array.isArray(raw)) return { roles: [], users: raw.map(Number).filter(Boolean) };
    return {
      roles: (raw.roles || []).map(String).filter(Boolean),
      users: (raw.users || raw.user_ids || []).map(Number).filter(Boolean),
    };
  }

  function stepAssigneeIds(formType, stepCode) {
    return stepAssigneeConfig(formType, stepCode).users;
  }

  function stepAssigneeRoleCodes(formType, stepCode) {
    return stepAssigneeConfig(formType, stepCode).roles;
  }

  function stepAssigneeConfigured(formType, stepCode) {
    const config = stepAssigneeConfig(formType, stepCode);
    return Boolean(config.users.length || config.roles.length);
  }

  function usersForStep(formType, stepCode, fallbackRole = "", department = "") {
    const config = stepAssigneeConfig(formType, stepCode);
    if (config.users.length || config.roles.length) {
      const idSet = new Set(config.users);
      const roleSet = new Set(config.roles);
      return (system.boot?.users || []).filter(user => idSet.has(Number(user.id)) || userRoleCodes(user).some(role => roleSet.has(role)));
    }
    return usersBy(fallbackRole, department);
  }

  function userOptionsFrom(users, selectedId = 0) {
    return (users || []).map(user => `<option value="${user.id}" ${Number(selectedId || 0) === Number(user.id) ? "selected" : ""}>${escapeHtml(user.display_name)} · ${escapeHtml(user.department || "")}</option>`).join("");
  }

  function userOptionsForStep(formType, stepCode, fallbackRole = "", department = "", selectedId = 0) {
    const users = usersForStep(formType, stepCode, fallbackRole, department);
    const selected = Number(selectedId || 0);
    if (selected && !users.some(user => Number(user.id) === selected)) {
      const extra = (system.boot?.users || []).find(user => Number(user.id) === selected);
      if (extra) users.unshift(extra);
    }
    return userOptionsFrom(users, selected);
  }

  function defaultStepUserId(formType, stepCode, fallbackRole = "", department = "") {
    return (usersForStep(formType, stepCode, fallbackRole, department)[0] || usersBy(fallbackRole)[0] || {}).id || "";
  }

  function manualApprovalLeaderEnabled() {
    return Boolean(system.boot?.workflow_settings?.allow_manual_approval_leader);
  }

  function approvalLeaderUsers(formType, stepCode, department = currentUser()?.department || "") {
    const configured = usersForStep(formType, stepCode, "leader", "");
    const configuredLeaders = configured.filter(user => userRoleCodes(user).includes("leader"));
    const scoped = department ? configuredLeaders.filter(user => user.department === department) : configuredLeaders;
    const fallback = department ? usersBy("leader", department) : usersBy("leader");
    const source = scoped.length ? scoped : fallback;
    const seen = new Set();
    return source.filter(user => {
      const id = Number(user.id);
      if (!id || seen.has(id)) return false;
      seen.add(id);
      return true;
    });
  }

  function defaultApprovalLeaderId(formType, stepCode, department = currentUser()?.department || "") {
    return Number(approvalLeaderUsers(formType, stepCode, department)[0]?.id || 0);
  }

  function approvalLeaderOptions(formType, stepCode, department = currentUser()?.department || "", selectedId = 0) {
    const users = approvalLeaderUsers(formType, stepCode, department);
    const selected = Number(selectedId || users[0]?.id || 0);
    return userOptionsFrom(users, selected);
  }

  function approvalLeaderName(id) {
    const user = (system.boot?.users || []).find(item => Number(item.id) === Number(id));
    return user ? `${user.display_name || user.username || ""}${user.department ? ` · ${user.department}` : ""}` : "";
  }

  function eligibleValidatorUsers(formType = "acceptance") {
    if (stepAssigneeConfigured(formType, "acceptance")) return usersForStep(formType, "acceptance");
    return (system.boot?.users || []).filter(user => !userRoleCodes(user).includes("admin"));
  }

  function userOptions(roleCode) {
    if (arguments.length > 1) {
      const department = arguments[1] || "";
      const selectedId = Number(arguments[2] || 0);
      const local = usersBy(roleCode, department);
      const rest = department ? usersBy(roleCode).filter(user => user.department !== department) : [];
      return local.concat(rest).map(user => `<option value="${user.id}" ${selectedId === Number(user.id) ? "selected" : ""}>${escapeHtml(user.display_name)} · ${escapeHtml(user.department || "")}</option>`).join("");
    }
    return usersBy(roleCode).map(user => `<option value="${user.id}">${escapeHtml(user.display_name)} · ${escapeHtml(user.department || "")}</option>`).join("");
  }

  function defaultUserId(roleCode, department = "") {
    return (usersBy(roleCode, department)[0] || usersBy(roleCode)[0] || {}).id || "";
  }

  function pageRows(key, rows, pageSize = 20) {
    const total = (rows || []).length;
    const pages = Math.max(1, Math.ceil(total / pageSize));
    const page = Math.min(Math.max(1, Number(system.pages[key] || 1)), pages);
    system.pages[key] = page;
    return rows.slice((page - 1) * pageSize, page * pageSize);
  }

  function paginationHtml(key, rows, pageSize = 20) {
    const total = (rows || []).length;
    if (total <= pageSize) return "";
    const pages = Math.max(1, Math.ceil(total / pageSize));
    const page = Math.min(Math.max(1, Number(system.pages[key] || 1)), pages);
    return `<div class="pager"><span>共 ${total} 条，第 ${page} / ${pages} 页</span><button type="button" data-page-key="${key}" data-page-value="${page - 1}" ${page <= 1 ? "disabled" : ""}>上一页</button><button type="button" data-page-key="${key}" data-page-value="${page + 1}" ${page >= pages ? "disabled" : ""}>下一页</button></div>`;
  }

  function bindPagination(host, key, renderPage) {
    host.querySelectorAll(`[data-page-key="${key}"]`).forEach(button => {
      button.addEventListener("click", () => {
        system.pages[key] = Number(button.dataset.pageValue || 1);
        renderPage();
      });
    });
  }

  function departmentOptions() {
    return `<option value="">全部部门</option>${(system.boot?.departments || []).map(dep => `<option value="${escapeHtml(dep.name)}">${escapeHtml(dep.name)}</option>`).join("")}`;
  }

  function updatePeopleSummary(hostId, ids) {
    const host = document.getElementById(hostId);
    if (!host) return;
    const names = ids.map(id => (system.boot.users || []).find(user => user.id === Number(id))?.display_name).filter(Boolean);
    host.textContent = names.length ? names.join("、") : "未选择";
  }

  function openPeoplePicker({ title, role, users: providedUsers = null, selected = [], multiple = true, department = "", onConfirm }) {
    const baseUsers = providedUsers || usersBy(role);
    const hasDepartmentUsers = department && baseUsers.some(user => user.department === department);
    const users = hasDepartmentUsers ? baseUsers.filter(user => user.department === department) : baseUsers;
    const grouped = (system.boot.departments || []).map(dep => ({
      name: dep.name,
      users: users.filter(user => user.department === dep.name),
    })).filter(group => group.users.length);
    const inputType = multiple ? "checkbox" : "radio";
    openModal({
      title,
      body: `<input id="peopleSearch" placeholder="搜索姓名 / 账号 / 英文字母" style="margin-bottom:10px;"><div class="people-grid">${grouped.map(group => `<div class="people-dept"><h3>${escapeHtml(group.name)}</h3>${group.users.map(user => `<label data-person-text="${escapeAttr(`${user.display_name} ${user.username} ${user.department || ""}`.toLowerCase())}"><input type="${inputType}" name="peoplePick" value="${user.id}" ${selected.includes(user.id) ? "checked" : ""}>${escapeHtml(user.display_name)}<small>${escapeHtml(user.username)}</small></label>`).join("")}</div>`).join("") || `<div class="empty-state">没有可选人员</div>`}</div>`,
      okText: "确认选择",
      onReady: () => {
        document.getElementById("peopleSearch")?.addEventListener("input", event => {
          const keyword = event.target.value.trim().toLowerCase();
          document.querySelectorAll("[data-person-text]").forEach(label => { label.style.display = label.dataset.personText.includes(keyword) ? "flex" : "none"; });
        });
      },
      onOk: () => {
        const ids = [...document.querySelectorAll('input[name="peoplePick"]:checked')].map(input => Number(input.value));
        return onConfirm(multiple ? ids : ids.slice(0, 1));
      },
    });
  }

  function openValidatorPicker(selected = [], onConfirm, formType = "") {
    const users = eligibleValidatorUsers(formType || (system.view === "semifinished" ? "semifinished" : (system.view === "finished" ? "finished" : "acceptance")));
    const selectedSet = new Set((selected || []).map(Number));
    const grouped = (system.boot.departments || []).map(dep => ({
      name: dep.name,
      users: users.filter(user => user.department === dep.name),
    })).filter(group => group.users.length);
    openModal({
      title: "选择验收员",
      body: `<input id="validatorSearch" placeholder="搜索姓名 / 账号 / 部门" style="margin-bottom:10px;"><div class="people-grid">${grouped.map(group => `<div class="people-dept"><h3>${escapeHtml(group.name)}</h3>${group.users.map(user => `<label data-validator-row data-validator-text="${escapeAttr(`${user.display_name} ${user.username} ${user.department || ""}`.toLowerCase())}"><input type="checkbox" name="validatorPick" value="${user.id}" ${selectedSet.has(Number(user.id)) ? "checked" : ""}>${escapeHtml(user.display_name)}<small>${escapeHtml(user.username || "")}</small></label>`).join("")}</div>`).join("") || `<div class="empty-state">没有已配置验收权的可选人员</div>`}</div>`,
      okText: "确认选择",
      onReady: () => {
        document.getElementById("validatorSearch")?.addEventListener("input", event => {
          const keyword = event.target.value.trim().toLowerCase();
          document.querySelectorAll("[data-validator-row]").forEach(label => { label.style.display = label.dataset.validatorText.includes(keyword) ? "flex" : "none"; });
        });
      },
      onOk: () => {
        const ids = [...document.querySelectorAll('input[name="validatorPick"]:checked')].map(input => Number(input.value));
        if (!ids.length) throw new Error("请至少选择一名验收员");
        return onConfirm(ids);
      },
    });
  }


  function newAttachmentToken() {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID().replace(/-/g, "");
    return `att${Date.now()}${Math.random().toString(16).slice(2)}`;
  }

  function normalizedAttachmentType(type) {
    const text = String(type || "").toLowerCase();
    if (text === "photo") return "material_photo";
    if (["certificate", "invoice", "other"].includes(text)) return "document";
    return text === "material_photo" ? "material_photo" : "document";
  }

  function attachmentTypeLabel(type) {
    return normalizedAttachmentType(type) === "material_photo" ? "\u7269\u6599\u7167\u7247" : "\u8d44\u6599";
  }

  function attachmentFileInputHtml() {
    return `<select data-attachment-type><option value="material_photo">\u7269\u6599\u7167\u7247</option><option value="document">\u8d44\u6599</option></select><label class="attachment-file-pick"><input data-attachment-files type="file" multiple accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.csv,.txt"><span data-file-label>\u9009\u62e9\u6587\u4ef6</span></label><button type="button" class="attachment-upload-button" data-upload-attachments>\u4e0a\u4f20</button><div data-attachment-list class="attachment-list empty-state">\u672a\u4e0a\u4f20\u9644\u4ef6</div>`;
  }

  function updateAttachmentFileLabel(input) {
    const label = input?.closest(".attachment-file-pick")?.querySelector("[data-file-label]");
    if (!label) return;
    const files = input.files || [];
    const fallback = input.dataset.emptyLabel || "\u9009\u62e9\u6587\u4ef6";
    label.textContent = files.length === 1 ? files[0].name : (files.length > 1 ? `\u5df2\u9009 ${files.length} \u4e2a\u6587\u4ef6` : fallback);
  }

  function bindAttachmentFilePickLabels(root = document) {
    root.querySelectorAll(".attachment-file-pick input[type='file']").forEach(input => {
      if (input.dataset.filePickBound) return;
      input.dataset.filePickBound = "1";
      input.addEventListener("change", () => updateAttachmentFileLabel(input));
      updateAttachmentFileLabel(input);
    });
  }

  async function uploadAttachmentFiles(options) {
    const uploader = window.WarehouseAttachmentUpload;
    if (!uploader?.uploadFiles) throw new Error("附件上传模块未加载");
    return uploader.uploadFiles(options);
  }

  async function deleteAttachmentById(attachmentId) {
    return systemApi(`/api/material-attachments/${attachmentId}`, { method: "DELETE", body: JSON.stringify({}) });
  }

  function renderAttachmentList(host, attachments, options = {}) {
    if (!host) return;
    const rows = attachments || [];
    const allowDelete = Boolean(options.allowDelete);
    const deleteLabel = options.deleteLabel || "删除";
    host.classList.toggle("empty-state", !rows.length);
    host.innerHTML = rows.length
      ? rows.map(item => `<div class="attachment-chip"><a href="${escapeAttr(item.download_url || "#")}" target="_blank" rel="noopener">打开/下载</a>${allowDelete ? `<button type="button" data-delete-attachment="${item.id}">${escapeHtml(deleteLabel)}</button>` : ""}</div>`).join("")
      : "未上传附件";
    if (allowDelete && typeof options.onDelete === "function") {
      host.querySelectorAll("[data-delete-attachment]").forEach(button => {
        button.addEventListener("click", () => options.onDelete(Number(button.dataset.deleteAttachment || 0)));
      });
    }
  }

  function renderInspectionAttachmentList(row, attachmentType) {
    const normalizedType = normalizedAttachmentType(attachmentType);
    const list = row.querySelector(`[data-inspection-list="${normalizedType}"]`);
    const rows = (row._attachments || []).filter(item => normalizedAttachmentType(item.attachment_type) === normalizedType);
    renderAttachmentList(list, rows, {
      allowDelete: true,
      deleteLabel: "\u64a4\u9500",
      onDelete: async attachmentId => {
        if (!attachmentId || !confirm("\u786e\u8ba4\u64a4\u9500\u8be5\u9644\u4ef6\uff1f")) return;
        try {
          await deleteAttachmentById(attachmentId);
          row._attachments = (row._attachments || []).filter(item => Number(item.id) !== attachmentId);
          renderInspectionAttachmentList(row, normalizedType);
          toast("\u9644\u4ef6\u5df2\u64a4\u9500");
        } catch (error) {
          toast(error.message || "\u64a4\u9500\u9644\u4ef6\u5931\u8d25");
        }
      },
    });
  }

  async function uploadInspectionAttachments(row, attachmentType) {
    const normalizedType = normalizedAttachmentType(attachmentType);
    const input = row.querySelector(`[data-inspection-files="${normalizedType}"]`);
    const materialId = Number(row.dataset.materialId || 0);
    if (!materialId) {
      toast("验收物料缺少物料ID");
      return;
    }
    try {
      const data = await uploadAttachmentFiles({
        files: input?.files || [],
        materialId,
        workflowFormId: row.dataset.formId,
        workflowItemId: row.dataset.inspectRow,
        attachmentType: normalizedType,
      });
      row._attachments = [...(row._attachments || []), ...(data.attachments || [])];
      renderInspectionAttachmentList(row, normalizedType);
      if (input) {
        input.value = "";
        updateAttachmentFileLabel(input);
      }
      toast(`${attachmentTypeLabel(normalizedType)}已上传`);
    } catch (error) {
      toast(error.message || "附件上传失败");
    }
  }

  async function captureInspectionAttachment(row, attachmentType) {
    const normalizedType = normalizedAttachmentType(attachmentType);
    const materialId = Number(row.dataset.materialId || 0);
    if (!materialId) {
      toast("验收物料缺少物料ID");
      return;
    }
    if (!window.WarehouseAttachmentCamera?.capture) {
      toast("当前浏览器或客户端不支持直接拍照，请选择图片上传");
      return;
    }
    try {
      const label = attachmentTypeLabel(normalizedType);
      const file = await window.WarehouseAttachmentCamera.capture({
        title: `拍摄${label}`,
        filePrefix: normalizedType === "material_photo" ? "material-photo" : "document-photo",
      });
      if (!file) return;
      const data = await uploadAttachmentFiles({
        files: [file],
        materialId,
        workflowFormId: row.dataset.formId,
        workflowItemId: row.dataset.inspectRow,
        attachmentType: normalizedType,
      });
      row._attachments = [...(row._attachments || []), ...(data.attachments || [])];
      renderInspectionAttachmentList(row, normalizedType);
      toast(`${label}已上传`);
    } catch (error) {
      toast(error.message || "拍照上传失败");
    }
  }

  function bindInspectionAttachmentUploads() {
    document.querySelectorAll("[data-inspect-row]").forEach(row => {
      ["material_photo", "document"].forEach(type => {
        row.querySelector(`[data-upload-inspection="${type}"]`)?.addEventListener("click", () => uploadInspectionAttachments(row, type));
        row.querySelector(`[data-capture-inspection="${type}"]`)?.addEventListener("click", () => captureInspectionAttachment(row, type));
      });
    });
  }

  function inspectionAttachmentPanelHtml(item, form, canUpload) {
    if (!canUpload) return "";
    const settings = system.boot?.workflow_settings || {};
    const photoRequired = settings.acceptance_material_photo_required ? `<span class="badge required">必填</span>` : "";
    const documentRequired = settings.acceptance_document_required ? `<span class="badge required">必填</span>` : "";
    return `<div class="wide attachment-panel"><div class="attachment-panel-head"><h3>验收附件</h3></div><div class="attachment-upload-grid"><div class="attachment-upload-box photo"><div class="attachment-upload-title"><strong>物料照片</strong>${photoRequired}</div><div class="attachment-upload-controls"><button type="button" class="attachment-camera-button" data-capture-inspection="material_photo">拍照</button><label class="attachment-file-pick"><input data-inspection-files="material_photo" data-empty-label="选择图片" type="file" multiple accept="image/*"><span data-file-label>选择图片</span></label><button type="button" class="attachment-upload-button" data-upload-inspection="material_photo">上传</button></div><div data-inspection-list="material_photo" class="attachment-list empty-state">未上传附件</div></div><div class="attachment-upload-box document"><div class="attachment-upload-title"><strong>资料</strong>${documentRequired}</div><div class="attachment-upload-controls"><button type="button" class="attachment-camera-button" data-capture-inspection="document">拍照</button><label class="attachment-file-pick"><input data-inspection-files="document" data-empty-label="选择资料" type="file" multiple accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.csv,.txt"><span data-file-label>选择资料</span></label><button type="button" class="attachment-upload-button" data-upload-inspection="document">上传</button></div><div data-inspection-list="document" class="attachment-list empty-state">未上传附件</div></div></div></div>`;
  }

  function attachmentCardHtml(item, allowDelete = false) {
    const preview = item.is_image
      ? `<a href="${escapeAttr(item.download_url)}" target="_blank" rel="noopener"><img src="${escapeAttr(item.download_url)}" alt="${escapeAttr(item.original_name || "附件")}"></a>`
      : `<div class="attachment-file-icon">${escapeHtml(attachmentTypeLabel(item.attachment_type))}</div>`;
    return `<div class="attachment-card compact">${preview}<div class="attachment-card-actions"><a href="${escapeAttr(item.download_url)}" target="_blank" rel="noopener">打开/下载</a>${allowDelete ? `<button type="button" class="danger" data-delete-material-attachment="${item.id}">删除</button>` : ""}</div></div>`;
  }

  function attachmentGalleryHtml(rows, allowDelete = false) {
    return rows.length ? `<div class="attachment-gallery">${rows.map(item => attachmentCardHtml(item, allowDelete)).join("")}</div>` : `<div class="empty-state">暂无附件</div>`;
  }

  function materialAttachmentSectionsHtml(rows, allowDelete = false) {
    const attachments = rows || [];
    if (!attachments.length) return `<div class="empty-state">暂无附件</div>`;
    const photos = attachments.filter(item => normalizedAttachmentType(item.attachment_type) === "material_photo");
    const documents = attachments.filter(item => normalizedAttachmentType(item.attachment_type) !== "material_photo");
    return `<div class="attachment-detail-sections"><section><h4>物料照片</h4>${attachmentGalleryHtml(photos, allowDelete)}</section><section><h4>资料</h4>${attachmentGalleryHtml(documents, allowDelete)}</section></div>`;
  }

  function materialBatchAttachmentsHtml(material, allowDelete = false) {
    const batches = material.batches || [];
    const allAttachments = material.attachments || [];
    const unbound = material.unbound_attachments || allAttachments.filter(item => !item.material_batch_id);
    const batchSections = batches.map(batch => {
      const batchId = Number(batch.id || 0);
      const attachments = batch.attachments || allAttachments.filter(item => Number(item.material_batch_id || 0) === batchId);
      const quantityText = `${formatQty(batch.quantity || 0)} ${escapeHtml(material.unit || "")}`.trim();
      return `<section class="batch-attachment-card"><div class="batch-attachment-head"><div><h4>${escapeHtml(batch.batch_no || `批次 ${batchId}`)}</h4><p>${quantityText} · ${escapeHtml(batch.received_date || "-")} · ${escapeHtml(batch.shelf_name || batch.warehouse_type || "")} ${escapeHtml(batch.layer_number || "")}${batch.layer_number ? "层" : ""} ${escapeHtml(batch.zone_name || "")}</p></div><span class="badge ${Number(batch.quantity || 0) > 0 ? "badge-green" : ""}">${Number(batch.quantity || 0) > 0 ? "有库存" : "无库存"}</span></div>${materialAttachmentSectionsHtml(attachments, allowDelete)}</section>`;
    }).join("");
    const historySection = unbound.length ? `<section class="batch-attachment-card history"><div class="batch-attachment-head"><div><h4>未绑定批次的历史附件</h4><p>旧数据保留在这里，可按权限删除。</p></div></div>${materialAttachmentSectionsHtml(unbound, allowDelete)}</section>` : "";
    return `<div class="batch-attachment-list">${batchSections || `<div class="empty-state">暂无批次附件</div>`}${historySection}</div>`;
  }

  function materialAttachmentsHtml(attachments, allowDelete = false) {
    return materialAttachmentSectionsHtml(attachments || [], allowDelete);
  }

  function canDeleteMaterialAttachment() {
    return hasPerm("delete_material_attachment");
  }

  function removeMaterialAttachmentFromDetailModel(material, attachmentId) {
    material.attachments = (material.attachments || []).filter(item => Number(item.id) !== attachmentId);
    material.unbound_attachments = (material.unbound_attachments || []).filter(item => Number(item.id) !== attachmentId);
    (material.batches || []).forEach(batch => {
      batch.attachments = (batch.attachments || []).filter(item => Number(item.id) !== attachmentId);
    });
  }

  function bindMaterialDetailAttachmentDeletes(material) {
    document.querySelectorAll("[data-delete-material-attachment]").forEach(button => {
      button.addEventListener("click", async () => {
        const attachmentId = Number(button.dataset.deleteMaterialAttachment || 0);
        if (!attachmentId || !confirm("确认删除该附件？")) return;
        try {
          await deleteAttachmentById(attachmentId);
          removeMaterialAttachmentFromDetailModel(material, attachmentId);
          const host = document.getElementById("materialDetailBatchAttachments");
          if (host) host.innerHTML = materialBatchAttachmentsHtml(material, canDeleteMaterialAttachment());
          bindMaterialDetailAttachmentDeletes(material);
          toast("附件已删除");
        } catch (error) {
          toast(error.message || "删除附件失败");
        }
      });
    });
  }

  function detailAttachmentBatchOptionsHtml(material) {
    const batches = material.batches || [];
    const active = batches.filter(batch => Number(batch.quantity || 0) > 0);
    const rows = active.length ? active : batches;
    return rows.map(batch => `<option value="${Number(batch.id || 0)}">${escapeHtml(batch.batch_no || `批次 ${batch.id}`)} · ${formatQty(batch.quantity || 0)} ${escapeHtml(material.unit || "")}</option>`).join("");
  }

  function materialDetailHtml(material) {
    const batchOptions = detailAttachmentBatchOptionsHtml(material);
    const uploadHtml = canModifyMaterial()
      ? `<div class="wide attachment-inline-upload"><h3>补充批次附件</h3>${batchOptions ? `<div class="attachment-upload-controls"><select id="detailAttachmentBatch" aria-label="选择批次">${batchOptions}</select><select id="detailAttachmentType" aria-label="附件类型"><option value="material_photo">物料照片</option><option value="document">资料</option></select><label class="attachment-file-pick"><input id="detailAttachmentFiles" data-empty-label="选择文件" type="file" multiple accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.csv,.txt"><span data-file-label>选择文件</span></label><button type="button" class="attachment-upload-button" id="uploadDetailAttachments">上传</button></div>` : `<div class="empty-state">该物料暂无批次，入库生成批次后再上传附件。</div>`}</div>`
      : "";
    return `<div class="form-grid material-detail-grid"><div class="wide"><h3>${escapeHtml(material.material_code || "")} ${escapeHtml(material.name || "")}</h3></div><div><span class="hint">品牌型号</span><strong>${escapeHtml(material.brand_model || "-")}</strong></div><div><span class="hint">技术规格</span><strong>${escapeHtml(material.spec || "-")}</strong></div><div><span class="hint">单位</span><strong>${escapeHtml(material.unit || "-")}</strong></div><div><span class="hint">库存总量</span><strong>${formatQty(material.quantity || 0)}</strong></div><div><span class="hint">采购申请人</span><strong>${escapeHtml(material.purchase_applicant || "-")}</strong></div><div><span class="hint">位置</span><strong>${escapeHtml(material.shelf_name || "")} ${escapeHtml(material.layer_number || "")} ${escapeHtml(material.zone_name || "")}</strong></div><div class="wide"><h3>批次信息</h3>${batchSummaryHtml(material.batches || []) || `<div class="empty-state">暂无批次</div>`}</div><div class="wide"><h3>批次附件</h3><div id="materialDetailBatchAttachments">${materialBatchAttachmentsHtml(material, canDeleteMaterialAttachment())}</div></div>${uploadHtml}</div>`;
  }

  async function viewMaterialDetail(materialId) {
    const material = await systemApi(`/api/materials/${materialId}`);
    openModal({
      title: "物料详情",
      body: materialDetailHtml(material),
      hideOk: true,
      onReady: () => {
        bindMaterialDetailAttachmentDeletes(material);
        bindAttachmentFilePickLabels(document.querySelector(".modal") || document);
        document.getElementById("uploadDetailAttachments")?.addEventListener("click", async () => {
          try {
            const batchId = Number(document.getElementById("detailAttachmentBatch")?.value || 0);
            if (!batchId) throw new Error("请选择要绑定的批次");
            const batch = (material.batches || []).find(item => Number(item.id || 0) === batchId);
            const result = await uploadAttachmentFiles({
              files: document.getElementById("detailAttachmentFiles")?.files || [],
              materialId,
              batchId,
              attachmentType: document.getElementById("detailAttachmentType")?.value || "document",
            });
            const uploaded = (result.attachments || []).map(item => ({
              ...item,
              material_batch_id: item.material_batch_id || batchId,
              batch_no: item.batch_no || batch?.batch_no || "",
            }));
            material.attachments = [...uploaded, ...(material.attachments || [])];
            if (batch) batch.attachments = [...uploaded, ...(batch.attachments || [])];
            const host = document.getElementById("materialDetailBatchAttachments");
            if (host) host.innerHTML = materialBatchAttachmentsHtml(material, canDeleteMaterialAttachment());
            bindMaterialDetailAttachmentDeletes(material);
            const detailInput = document.getElementById("detailAttachmentFiles");
            if (detailInput) {
              detailInput.value = "";
              updateAttachmentFileLabel(detailInput);
            }
            toast("附件已上传并绑定批次");
          } catch (error) {
            toast(error.message || "附件上传失败");
          }
        });
      },
    });
  }

  function addItemRow(host, type) {
    const row = document.createElement("div");
    row.className = `inline-row ${type === "claim" ? "claim" : ""}`;
    row.innerHTML = type === "claim"
      ? `<label class="material-search">物料<input data-search placeholder="输入编号/名称/规格/采购申请人"><div data-result-list class="material-results"></div><small data-stock-info class="hint"></small></label><label>编号<input data-code readonly></label><label>品牌型号<input data-brand readonly></label><label>技术规格<input data-spec readonly></label><label>领用数量<input data-qty type="number" step="1" min="0"><small data-allocation-info class="claim-allocation-preview"></small></label><button data-remove>删除</button><input type="hidden" data-material-id><input type="hidden" data-stock-quantity>`
      : `<label class="material-search">物料<input data-search placeholder="输入编号/名称/规格"><div data-result-list class="material-results"></div></label><label>编号<input data-code></label><label>品牌型号<input data-brand></label><label>技术规格<input data-spec></label><label>采购申请人<input data-purchase-applicant></label><label>采购数量<input data-purchase type="number" step="1"></label><label>到货数量<input data-arrival type="number" step="1"></label><label>单价<input data-price type="number" step="0.01"></label><button data-remove>删除</button><input type="hidden" data-material-id>`;
    row.querySelector("[data-remove]").addEventListener("click", () => row.remove());
    const search = row.querySelector("[data-search]");
    let timer = 0;
    search.addEventListener("input", () => {
      window.clearTimeout(timer);
      row.querySelector("[data-material-id]").value = "";
      timer = window.setTimeout(() => resolveMaterial(row, type === "claim"), 220);
    });
    search.addEventListener("focus", () => {
      resolveMaterial(row, type === "claim");
    });
    search.addEventListener("keydown", event => {
      if (event.key === "Escape") hideMaterialResults(row);
    });
    row.querySelector("[data-qty]")?.addEventListener("input", () => updateClaimAllocationPreview(row));
    document.addEventListener("click", event => {
      if (!row.contains(event.target)) hideMaterialResults(row);
    });
    host.appendChild(row);
  }

  async function resolveMaterial(row, positiveOnly) {
    const keyword = row.querySelector("[data-search]").value.trim();
    const results = positiveOnly
      ? await api(`/api/claims/materials${keyword ? `?keyword=${encodeURIComponent(keyword)}` : ""}`)
      : (keyword
        ? await api(`/api/materials/search?keyword=${encodeURIComponent(keyword)}`)
        : await freshMaterialRowsForPicker());
    const filtered = positiveOnly
      ? results.filter(item => Number(item.total_available_quantity ?? item.quantity ?? 0) > 0)
      : results;
    const list = row.querySelector("[data-result-list]");
    if (!filtered.length) {
      hideMaterialResults(row);
      row.querySelector("[data-material-id]").value = "";
      return;
    }
    row._materialResults = filtered;
    const temporaryEnabled = Boolean(system.boot?.workflow_settings?.temporary_inventory_enabled);
    list.innerHTML = filtered.map((material, index) => `
      <button class="material-option" type="button" data-material-index="${index}">
        ${escapeHtml(material.material_code)} · ${escapeHtml(material.name)}
        <small>${escapeHtml(material.brand_model || "")} ${escapeHtml(material.spec || "")} · 采购申请人 ${escapeHtml(material.purchase_applicant || "-")} · ${positiveOnly ? escapeHtml(ClaimSourceUI.availabilityText(material, temporaryEnabled, formatQty)) : `总余量 ${formatQty(material.quantity || 0)}`} ${escapeHtml(material.unit || "")}${!positiveOnly && material.batch_summary ? ` · 批次 ${escapeHtml(material.batch_summary)}` : ""}</small>
      </button>
    `).join("");
    positionMaterialResults(row, list, filtered.length);
    resizeMaterialResults(list, filtered.length);
    list.style.display = "block";
    list.querySelectorAll("[data-material-index]").forEach(button => {
      button.addEventListener("click", () => applyMaterialSelection(row, filtered[Number(button.dataset.materialIndex)]));
    });
  }

  function applyMaterialSelection(row, material) {
    row._selectedMaterial = material;
    row.querySelector("[data-material-id]").value = material.id;
    row.querySelector("[data-search]").value = material.name || "";
    row.querySelector("[data-code]") && (row.querySelector("[data-code]").value = material.material_code);
    row.querySelector("[data-brand]") && (row.querySelector("[data-brand]").value = material.brand_model || "");
    row.querySelector("[data-spec]") && (row.querySelector("[data-spec]").value = material.spec || "");
    row.querySelector("[data-purchase-applicant]") && (row.querySelector("[data-purchase-applicant]").value = material.purchase_applicant || "");
    hideMaterialResults(row);
    const totalQuantity = Number(material.total_available_quantity ?? material.quantity ?? 0);
    const stockInput = row.querySelector("[data-stock-quantity]");
    if (stockInput) stockInput.value = totalQuantity;
    const stockInfo = row.querySelector("[data-stock-info]");
    if (stockInfo) {
      stockInfo.textContent = ClaimSourceUI.availabilityText(
        material,
        Boolean(system.boot?.workflow_settings?.temporary_inventory_enabled),
        formatQty,
      );
    }
    updateClaimAllocationPreview(row);
  }

  function updateClaimAllocationPreview(row) {
    const host = row.querySelector("[data-allocation-info]");
    if (!host) return;
    const text = ClaimSourceUI.allocationEstimate(
      row._selectedMaterial,
      row.querySelector("[data-qty]")?.value,
      Boolean(system.boot?.workflow_settings?.temporary_inventory_enabled),
      formatQty,
    );
    host.textContent = text;
    host.classList.toggle("has-shortage", text.includes("不足"));
  }

  async function freshMaterialRowsForPicker() {
    const now = Date.now();
    if (!system.materialPickerCacheAt || now - system.materialPickerCacheAt > 2000) {
      const rows = await refreshMaterialsCache();
      system.materialPickerCacheAt = Date.now();
      return rows;
    }
    return typeof state !== "undefined" && Array.isArray(state.materials) ? state.materials : refreshMaterialsCache();
  }

  function hideMaterialResults(row) {
    const list = row.querySelector("[data-result-list]");
    if (list) {
      list.style.display = "none";
      list.innerHTML = "";
    }
  }

  function resizeMaterialResults(list, count) {
    const top = Number.parseFloat(list.style.top || "0") || list.getBoundingClientRect().top;
    const viewportRoom = Math.max(180, window.innerHeight - top - 18);
    const ideal = Math.max(120, count * 58 + 8);
    list.style.maxHeight = `${Math.min(ideal, viewportRoom, Math.max(window.innerHeight * 0.72, 260))}px`;
  }

  function positionMaterialResults(row, list, count) {
    const input = row.querySelector("[data-search]") || row.querySelector("[data-prod-material-search]") || row.querySelector("[data-prod-semi-search]");
    const rect = input.getBoundingClientRect();
    const maxWidth = Math.min(Math.max(rect.width, 420), window.innerWidth - 24);
    list.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - maxWidth - 12))}px`;
    list.style.top = `${Math.min(rect.bottom + 4, window.innerHeight - 120)}px`;
    list.style.width = `${maxWidth}px`;
    resizeMaterialResults(list, count);
  }

  function todoTypePresentation(type) {
    const raw = String(type || "").trim();
    const labels = {
      claim: ["申领/出库", "type-issue"], acceptance: ["物料验收", "type-acceptance"],
      semifinished: ["半成品验收", "type-acceptance"], finished: ["成品验收", "type-acceptance"],
      borrow: ["借用申请", "type-borrow"], borrow_return: ["借用归还", "type-return"],
      common_material: ["常用物料申请", "type-common"], supply: ["供货申请", "type-supply"],
      supply_return: ["供货回寄", "type-return"], supply_extension: ["供货延期", "type-supply"],
      temporary_transfer: ["临时库转正式库", "type-transfer"],
    };
    const item = labels[raw];
    return { label: item ? item[0] : (raw || "-"), className: item ? item[1] : "type-default" };
  }

  function todoStatusPresentation(status) {
    const raw = String(status || "").trim();
    const label = workflowStatusLabel(raw) || raw || "-";
    const classes = {
      applicant_revision: "status-revision", acceptance: "status-acceptance", leader_acceptance: "status-pending", inbound: "status-pending",
      leader_claim: "status-pending", outbound: "status-pending", leader_borrow: "status-pending", borrow_outbound: "status-pending", return_inbound: "status-pending",
      leader_common_material: "status-pending", leader_supply: "status-pending", supply_outbound: "status-pending", supply_return_inbound: "status-acceptance", leader_supply_extension: "status-pending", external_open: "status-transfer-warning",
      awaiting_purchase: "status-transfer-purchase", acceptance_in_progress: "status-transfer-processing", acceptance_failed: "status-transfer-exception",
      formal_inbound_partial: "status-transfer-warning", formal_inbound_complete: "status-transfer-processing", reserving: "status-transfer-processing",
      auto_claim_creating: "status-transfer-processing", auto_claim_pending: "status-transfer-purchase", auto_claim_exception: "status-transfer-exception",
      paused: "status-default", exception: "status-transfer-exception", completed: "status-completed", rejected: "status-rejected", cancelled: "status-default",
    };
    return { label, className: classes[raw] || "status-default" };
  }

  function todoTable(rows) {
    const list = Array.isArray(rows) ? rows : [];
    const tableRows = list.map(item => {
      const type = todoTypePresentation(item.form_type);
      const status = todoStatusPresentation(item.status);
      const action = item.form_type === "stocktake"
        ? `<button class="handle-button" type="button" data-supervise-stocktake="${Number(item.form_id) || 0}">${iconSvg("file-check", "button-icon")}<span>办理</span></button>`
        : `<button class="handle-button" type="button" data-detail="${Number(item.form_id) || 0}">${iconSvg("file-check", "button-icon")}<span>办理</span></button>`;
      return `<tr><td class="flow-number">${escapeHtml(item.form_no || "-")}</td><td><span class="flow-type-badge ${type.className}">${escapeHtml(type.label)}</span></td><td class="current-step">${escapeHtml(workflowStepLabel(item.step_code) || "-")}</td><td><span class="status-badge ${status.className}">${escapeHtml(status.label)}</span></td><td>${action}</td></tr>`;
    }).join("");
    return `<div class="table-wrapper"><table class="todo-table flow-table"><thead><tr><th>流程单号</th><th>类型</th><th>当前环节</th><th>状态</th><th>操作</th></tr></thead><tbody>${tableRows || '<tr><td colspan="5" class="todo-empty">暂无待办流程</td></tr>'}</tbody></table></div>`;
  }

  function bindTodoButtons(host) {
    host.querySelectorAll("[data-detail]").forEach(btn => btn.addEventListener("click", () => {
      const formId = Number(btn.dataset.detail);
      const todo = (system.boot.todos || []).find(item => Number(item.form_id) === formId);
      if (todo?.form_type === "temporary_transfer") {
        system.view = "temporaryTransfers";
        renderSystemMain();
        return temporaryTransfers.openDetail(formId);
      }
      return openFlowDetail(formId);
    }));
    host.querySelectorAll("[data-supervise-stocktake]").forEach(btn => btn.addEventListener("click", () => superviseStocktake(Number(btn.dataset.superviseStocktake))));
  }

  function acceptanceItemsWithRows() {
    const rows = [...document.querySelectorAll("#accRows .inline-row")];
    const items = rows.map(row => ({
      material_id: row.querySelector("[data-material-id]").value,
      material_code: row.querySelector("[data-code]").value,
      material_name: row.querySelector("[data-search]").value,
      brand_model: row.querySelector("[data-brand]")?.value || "",
      spec: row.querySelector("[data-spec]")?.value || "",
      purchase_applicant: row.querySelector("[data-purchase-applicant]")?.value || "",
      purchase_quantity: row.querySelector("[data-purchase]").value,
      arrival_quantity: row.querySelector("[data-arrival]").value,
      unit_price: row.querySelector("[data-price]").value,
    }));
    return { rows, items };
  }

  async function startAcceptanceSubmit() {
    const { rows, items } = acceptanceItemsWithRows();
    const canContinue = await confirmDuplicateAcceptance("acceptance", { items }, rows);
    if (!canContinue) return;
    chooseValidatorsAndSubmit("acceptance", submitAcceptance);
  }

  async function startSemifinishedSubmit() {
    const payload = semifinishedPayload();
    const canContinue = await confirmDuplicateAcceptance("semifinished", payload);
    if (!canContinue) return;
    chooseValidatorsAndSubmit("semifinished", submitSemifinished);
  }

  async function startFinishedSubmit() {
    const payload = finishedPayload();
    const canContinue = await confirmDuplicateAcceptance("finished", payload);
    if (!canContinue) return;
    chooseValidatorsAndSubmit("finished", submitFinished);
  }

  async function confirmDuplicateAcceptance(formType, payload, rows = []) {
    let data;
    try {
      data = await systemApi("/api/workflows/duplicate-check", {
        method: "POST",
        body: JSON.stringify({ form_type: formType, ...payload }),
      });
    } catch (error) {
      toast(`重复验收验证失败：${error.message}`);
      return false;
    }
    const matches = data.matches || [];
    if (!matches.length) return true;
    const decision = await duplicateAcceptanceDecision(matches, data.days || 7, formType);
    if (decision === "continue") return true;
    if (decision === "remove" && formType === "acceptance") {
      const indexes = new Set(matches.map(match => Number(match.index)).filter(index => Number.isInteger(index) && index >= 0));
      rows.forEach((row, index) => {
        if (indexes.has(index)) row.remove();
      });
      if (!document.querySelector("#accRows .inline-row")) addItemRow(document.getElementById("accRows"), "acceptance");
      toast("已删除重复物料行");
    } else if (decision === "remove") {
      toast("已取消提交，请修改后再重新提交");
    }
    return false;
  }

  function duplicateAcceptanceDecision(matches, days, formType) {
    const typeLabel = ({ acceptance: "物料", semifinished: "半成品", finished: "成品" })[formType] || "物品";
    return new Promise(resolve => {
      let settled = false;
      const finish = value => {
        if (settled) return;
        settled = true;
        closeModal();
        resolve(value);
      };
      const rowsHtml = matches.map(match => `
        <tr>
          <td>${escapeHtml(match.item_label || typeLabel)}</td>
          <td>${escapeHtml(match.form_no || "")}</td>
          <td>${escapeHtml(match.created_at || "")}</td>
          <td><button type="button" data-duplicate-view="${Number(match.form_id || 0)}">查看</button></td>
        </tr>
      `).join("");
      const firstLabel = matches[0]?.item_label || typeLabel;
      const cancelText = formType === "acceptance" ? "否，删除重复行" : "否，取消提交";
      openModal({
        title: "重复验收提醒",
        body: `<div class="form-grid">
          <p class="wide">检测到${typeLabel}${escapeHtml(firstLabel)}近期已进行过验收，是否继续验收？</p>
          <p class="hint wide">系统按最近 ${Number(days || 7)} 天内完全相同的验收信息进行匹配。</p>
          <div class="wide"><table class="flow-table"><thead><tr><th>名称</th><th>流程单号</th><th>提交时间</th><th>操作</th></tr></thead><tbody>${rowsHtml}</tbody></table></div>
          <div class="wide mini-actions"><button type="button" class="danger" id="duplicateRemoveRows">${cancelText}</button></div>
        </div>`,
        okText: "是，继续验收",
        onReady: () => {
          document.getElementById("modalCancel")?.addEventListener("click", () => finish("cancel"));
          document.getElementById("modalClose")?.addEventListener("click", () => finish("cancel"));
          document.getElementById("duplicateRemoveRows")?.addEventListener("click", () => finish("remove"));
          document.querySelectorAll("[data-duplicate-view]").forEach(button => {
            button.addEventListener("click", () => {
              const formId = Number(button.dataset.duplicateView || 0);
              finish("cancel");
              if (formId) openFlowDetail(formId, true);
            });
          });
        },
        onOk: () => finish("continue"),
      });
    });
  }

  async function submitAcceptance() {
    const submitButton = document.getElementById("submitAcceptance");
    if (submitButton) submitButton.disabled = true;
    try {
      const { items } = acceptanceItemsWithRows();
      await systemApi("/api/acceptance", { method: "POST", body: JSON.stringify({ title: document.getElementById("accTitle").value, validator_ids: system.selectedValidators, items }) });
      toast("\u9a8c\u6536\u6d41\u7a0b\u5df2\u63d0\u4ea4");
      system.boot = null;
      await loadSystemBoot();
      renderSystemMain();
    } catch (error) {
      if (submitButton) submitButton.disabled = false;
      toast(`\u63d0\u4ea4\u9a8c\u6536\u6d41\u7a0b\u5931\u8d25\uff1a${error.message}`);
    }
  }

  async function submitClaim() {
    const submitButton = document.getElementById("submitClaim");
    if (submitButton) submitButton.disabled = true;
    try {
      const items = [...document.querySelectorAll("#claimRows .inline-row")].map(row => ({
        material_id: row.querySelector("[data-material-id]").value,
        material_code: row.querySelector("[data-code]")?.value || "",
        material_name: row.querySelector("[data-search]")?.value || "",
        brand_model: row.querySelector("[data-brand]")?.value || "",
        spec: row.querySelector("[data-spec]")?.value || "",
        request_quantity: row.querySelector("[data-qty]").value,
      }));
      [...document.querySelectorAll("#claimRows .inline-row")].forEach(row => {
        const qty = Number(row.querySelector("[data-qty]").value || 0);
        const stockRaw = row.querySelector("[data-stock-quantity]")?.value;
        const stock = Number(stockRaw || 0);
        if (stockRaw != null && stockRaw !== "" && qty > stock + 1e-9) {
          throw new Error("\u7533\u9886\u6570\u91cf\u4e0d\u80fd\u5927\u4e8e\u5f53\u524d\u5e93\u5b58\u6570\u91cf");
        }
      });
      await systemApi("/api/claims", { method: "POST", body: JSON.stringify({
        title: document.getElementById("claimTitle").value,
        leader_id: system.selectedClaimLeaders[0],
        leader_ids: system.selectedClaimLeaders,
        purpose: document.getElementById("claimPurpose").value,
        rd_item_kind: document.getElementById("claimRdKind")?.value || "",
        project_material_kind: document.getElementById("claimProjectKind")?.value || "",
        project_code: document.getElementById("claimProjectCode")?.value || "",
        items,
      }) });
      toast("\u7533\u9886\u6d41\u7a0b\u5df2\u63d0\u4ea4");
      system.boot = null;
      await loadSystemBoot();
      renderSystemMain();
    } catch (error) {
      if (submitButton) submitButton.disabled = false;
      toast(`\u63d0\u4ea4\u7533\u9886\u6d41\u7a0b\u5931\u8d25\uff1a${error.message}`);
    }
  }

  async function loadAcceptanceList() {
    const rows = await systemApi("/api/workflows?type=acceptance&active=1&mine=1");
    renderFlowList("accList", rows);
  }

  async function loadClaimList() {
    const rows = await systemApi("/api/workflows?type=claim&active=1&mine=1");
    renderFlowList("claimList", rows);
  }

  async function loadBorrowFlowList() {
    const rows = await systemApi("/api/workflows?type=borrow&active=1&mine=1");
    renderFlowList("borrowFlowList", rows);
  }

  let borrowFilterTimer;
  function debouncedFilterBorrow() {
    clearTimeout(borrowFilterTimer);
    borrowFilterTimer = setTimeout(() => {
      const maker = document.getElementById("borrowMakerFilter")?.value || "";
      const project = document.getElementById("borrowProjectFilter")?.value || "";
      fetchBorrowApplications(maker, project);
    }, 300);
  }

  async function fetchBorrowApplications(maker, project) {
    let url = "/api/workflows?type=borrow&active=1&mine=1";
    if (maker) url += "&maker=" + encodeURIComponent(maker);
    if (project) url += "&project=" + encodeURIComponent(project);
    const rows = await systemApi(url);
    renderFlowList("borrowFlowList", rows);
  }

  function clearBorrowFilters() {
    const makerInput = document.getElementById("borrowMakerFilter");
    const projectInput = document.getElementById("borrowProjectFilter");
    if (makerInput) makerInput.value = "";
    if (projectInput) projectInput.value = "";
    fetchBorrowApplications("", "");
  }

  async function loadBorrowItems() {
    const host = document.getElementById("borrowItemList");
    if (!host) return;
    host.innerHTML = `<div class="empty-state">加载中...</div>`;
    const keyword = document.getElementById("borrowKeyword")?.value || "";
    const data = await systemApi(`/api/borrow/items?keyword=${encodeURIComponent(keyword)}`);
    system.borrowItemRows = data.items || [];
    system.pages.borrowItems = 1;
    renderBorrowItemList();
  }

  function renderBorrowItemList() {
    const host = document.getElementById("borrowItemList");
    if (!host) return;
    const rows = system.borrowItemRows || [];
    const visible = pageRows("borrowItems", rows, 12);
    const temporaryEnabled = Boolean(system.boot?.workflow_settings?.temporary_inventory_enabled);
    host.innerHTML = `<table class="flow-table"><thead><tr><th>类型</th><th>编号</th><th>名称</th><th>品牌</th><th>规格</th><th>库存余量</th><th>位置</th><th>数量</th><th>操作</th></tr></thead><tbody>${visible.map((item, index) => {
      const actualIndex = (Number(system.pages.borrowItems || 1) - 1) * 12 + index;
      const availability = item.item_type === "material" ? ClaimSourceUI.availabilityText(item, temporaryEnabled, formatQty) : `可借 ${formatQty(item.available_quantity || 0)}`;
      return `<tr><td>${borrowItemTypeLabel(item.item_type)}</td><td>${escapeHtml(item.item_code || "")}</td><td>${escapeHtml(item.item_name || "")}</td><td>${escapeHtml(item.brand_model || "无")}</td><td>${escapeHtml(item.spec || "")}</td><td>${escapeHtml(availability)} ${escapeHtml(item.unit || "")}</td><td>${escapeHtml(item.location_text || "")}</td><td><span class="qty-with-unit"><input data-borrow-qty="${actualIndex}" type="number" step="1" min="0" max="${Number(item.available_quantity || 0)}" value="1"><span>${escapeHtml(item.unit || "")}</span></span></td><td><button type="button" data-borrow-add="${actualIndex}">添加借用</button></td></tr>`;
    }).join("") || `<tr><td colspan="9">暂无可借物料</td></tr>`}</tbody></table>${paginationHtml("borrowItems", rows, 12)}`;
    host.querySelectorAll("[data-borrow-add]").forEach(button => button.addEventListener("click", () => addBorrowDraft(Number(button.dataset.borrowAdd))));
    bindPagination(host, "borrowItems", renderBorrowItemList);
  }

  function addBorrowDraft(index) {
    const item = (system.borrowItemRows || [])[index];
    if (!item) return;
    const qty = Number(document.querySelector(`[data-borrow-qty="${index}"]`)?.value || 0);
    if (qty <= 0) {
      toast("借用数量必须大于 0");
      return;
    }
    if (qty > Number(item.available_quantity || 0) + 1e-9) {
      toast("借用数量不能大于可借数量");
      return;
    }
    const key = `${item.item_type}:${item.item_ref_id}`;
    const existing = system.borrowDraftRows.find(row => `${row.item_type}:${row.item_ref_id}` === key);
    if (existing) {
      existing.request_quantity = Math.min(Number(existing.request_quantity || 0) + qty, Number(item.available_quantity || 0));
    } else {
      system.borrowDraftRows.push({ ...item, request_quantity: qty });
    }
    renderBorrowDraftList();
  }

  function renderBorrowDraftList() {
    const host = document.getElementById("borrowDraftList");
    if (!host) return;
    const rows = system.borrowDraftRows || [];
    const temporaryEnabled = Boolean(system.boot?.workflow_settings?.temporary_inventory_enabled);
    host.innerHTML = `<table class="flow-table"><thead><tr><th>类型</th><th>编号</th><th>名称</th><th>品牌</th><th>规格</th><th>数量与预计来源</th><th>操作</th></tr></thead><tbody>${rows.map((item, index) => `<tr><td>${borrowItemTypeLabel(item.item_type)}</td><td>${escapeHtml(item.item_code || "")}</td><td>${escapeHtml(item.item_name || "")}</td><td>${escapeHtml(item.brand_model || "无")}</td><td>${escapeHtml(item.spec || "")}</td><td><span class="qty-with-unit"><input data-borrow-draft-qty="${index}" type="number" step="1" min="0" max="${Number(item.available_quantity || 0)}" value="${formatInputQty(item.request_quantity || 0)}"><span>${escapeHtml(item.unit || "")}</span></span><small class="claim-allocation-preview" data-borrow-estimate="${index}">${item.item_type === "material" ? escapeHtml(ClaimSourceUI.allocationEstimate(item, item.request_quantity, temporaryEnabled, formatQty)) : ""}</small></td><td><button type="button" class="danger" data-borrow-remove="${index}">移除</button></td></tr>`).join("") || `<tr><td colspan="7">暂无借用明细</td></tr>`}</tbody></table>`;
    host.querySelectorAll("[data-borrow-draft-qty]").forEach(input => input.addEventListener("input", () => {
      const index = Number(input.dataset.borrowDraftQty);
      const item = rows[index];
      if (!item) return;
      item.request_quantity = Number(input.value || 0);
      const estimate = host.querySelector(`[data-borrow-estimate="${index}"]`);
      if (estimate && item.item_type === "material") {
        estimate.textContent = ClaimSourceUI.allocationEstimate(item, item.request_quantity, temporaryEnabled, formatQty);
      }
    }));
    host.querySelectorAll("[data-borrow-remove]").forEach(button => button.addEventListener("click", () => {
      system.borrowDraftRows.splice(Number(button.dataset.borrowRemove), 1);
      renderBorrowDraftList();
    }));
  }

  async function submitBorrow() {
    const rows = (system.borrowDraftRows || []).map((item, index) => ({
      item_type: item.item_type,
      item_ref_id: item.item_ref_id,
      quantity: document.querySelector(`[data-borrow-draft-qty="${index}"]`)?.value || item.request_quantity,
    })).filter(item => Number(item.quantity || 0) > 0);
    if (!rows.length) {
      toast("请先加入借用明细");
      return;
    }
    await systemApi("/api/borrows", {
      method: "POST",
      body: JSON.stringify({
        title: document.getElementById("borrowTitle").value,
        expected_return_date: document.getElementById("borrowReturnDate").value,
        leader_id: system.selectedBorrowLeader,
        items: rows,
      }),
    });
    toast("借用申请已提交");
    system.borrowDraftRows = [];
    system.boot = null;
    await loadSystemBoot();
    renderSystemMain();
  }

  async function loadMyBorrow() {
    const host = document.getElementById("myBorrowList");
    if (!host) return;
    const data = await systemApi("/api/borrows/mine");
    system.myBorrowRows = data.items || [];
    renderMyBorrowList();
  }

  function renderMyBorrowList() {
    const host = document.getElementById("myBorrowList");
    if (!host) return;
    const rows = (system.myBorrowRows || []).filter(item => !system.myBorrowOverdueOnly || item.is_overdue);
    const visible = pageRows("myBorrow", rows, 20);
    const userId = currentUser()?.id;
    host.innerHTML = `<table class="flow-table"><thead><tr><th>借用单号</th><th>类型</th><th>来源</th><th>编号</th><th>名称</th><th>品牌</th><th>规格</th><th>借用数量</th><th>已归还</th><th>未归还</th><th>预计归还日</th><th>出库日期</th><th>操作</th></tr></thead><tbody>${visible.map(item => {
      const itemData = item.data || {};
      const isTransferring = item.status === 'transferring';
      const isReceiver = isTransferring && Number(itemData.transfer_receiver_id) === Number(userId);
      let actions = '';
      if (isReceiver) {
        actions = `<button type="button" class="btn-accept-transfer" data-accept-transfer="${item.id}">接收</button><button type="button" class="danger btn-reject-transfer" data-reject-transfer="${item.id}">拒绝</button>`;
      } else if (isTransferring) {
        actions = `<span class="transferring-label">转借中...</span>`;
      } else {
        actions = `<button type="button" data-return-borrow="${item.id}">我要归还</button><button type="button" class="btn-transfer" data-transfer-borrow="${item.id}">我要转借</button>`;
      }
      return `<tr><td>${escapeHtml(item.borrow_no || "")}</td><td>${borrowItemTypeLabel(item.item_type)}</td><td>${ClaimSourceUI.sourceBadge(item.stock_source)}</td><td>${escapeHtml(item.item_code || "")}</td><td>${escapeHtml(item.item_name || "")}</td><td>${escapeHtml(item.brand_model || "无")}</td><td>${escapeHtml(item.spec || "")}</td><td>${formatQty(item.quantity || 0)} ${escapeHtml(item.unit || "")}</td><td>${formatQty(item.returned_quantity || 0)}</td><td>${formatQty(item.remaining_quantity || 0)}</td><td>${item.is_overdue ? `<span class="status-badge status-transfer-warning">${escapeHtml(item.expected_return_date || "")}</span>` : escapeHtml(item.expected_return_date || "-")}</td><td>${escapeHtml(item.outbound_date || "")}</td><td>${actions}</td></tr>`;
    }).join("") || `<tr><td colspan="13">${system.myBorrowOverdueOnly ? "暂无逾期借用物料" : "暂无借用中的物料"}</td></tr>`}</tbody></table>${paginationHtml("myBorrow", rows, 20)}`;
    host.querySelectorAll("[data-return-borrow]").forEach(button => button.addEventListener("click", () => openBorrowReturn(Number(button.dataset.returnBorrow))));
    host.querySelectorAll("[data-transfer-borrow]").forEach(button => button.addEventListener("click", () => initiateTransfer(Number(button.dataset.transferBorrow))));
    host.querySelectorAll("[data-accept-transfer]").forEach(button => button.addEventListener("click", () => acceptTransfer(Number(button.dataset.acceptTransfer))));
    host.querySelectorAll("[data-reject-transfer]").forEach(button => button.addEventListener("click", () => rejectTransfer(Number(button.dataset.rejectTransfer))));
    bindPagination(host, "myBorrow", renderMyBorrowList);
  }

  async function loadMyInspections() {
    const host = document.getElementById("myInspectionList");
    if (!host) return;
    host.innerHTML = "加载中...";
    try {
      const query = personalFlowQuery("myInspection");
      const data = await systemApi(`/api/my-flows/inspections${query ? `?${query}` : ""}`);
      system.myInspectionRows = data.items || [];
      renderPersonalFlowList("myInspectionList", system.myInspectionRows, "myInspections", false);
    } catch (error) {
      host.innerHTML = `<div class="empty-state">加载失败：${escapeHtml(error.message || error)}</div>`;
    }
  }

  async function loadMyStarted() {
    const host = document.getElementById("myStartedList");
    if (!host) return;
    host.innerHTML = "加载中...";
    try {
      const query = personalFlowQuery("myStarted");
      const data = await systemApi(`/api/my-flows/started${query ? `?${query}` : ""}`);
      system.myStartedRows = data.items || [];
      renderPersonalFlowList("myStartedList", system.myStartedRows, "myStarted", true);
    } catch (error) {
      host.innerHTML = `<div class="empty-state">加载失败：${escapeHtml(error.message || error)}</div>`;
    }
  }

  function renderPersonalFlowList(hostId, rows, pageKey, allowApplicantActions) {
    const host = document.getElementById(hostId);
    if (!host) return;
    const visible = pageRows(pageKey, rows, 20);
    host.innerHTML = `<table class="flow-table"><thead><tr><th>单号</th><th>标题</th><th>类型</th><th>状态</th><th>申请人</th><th>时间</th><th>操作</th></tr></thead><tbody>${visible.map(form => {
      const applicantActions = allowApplicantActions && form.can_withdraw
        ? `<button data-edit-flow="${form.id}">修改</button><button class="danger" data-withdraw-flow="${form.id}">撤销</button>`
        : "";
      return `<tr><td>${escapeHtml(form.form_no || "")}</td><td>${escapeHtml(form.title || "")}</td><td>${flowTypeLabel(form.form_type)}</td><td>${escapeHtml(workflowStatusLabel(form.status))}</td><td>${escapeHtml(form.applicant_name || "")}</td><td>${escapeHtml(form.created_at || "")}</td><td class="mini-actions"><button data-detail="${form.id}">查看</button>${applicantActions}</td></tr>`;
    }).join("") || `<tr><td colspan="7">暂无流程</td></tr>`}</tbody></table>${paginationHtml(pageKey, rows, 20)}`;
    host.querySelectorAll("[data-detail]").forEach(btn => btn.addEventListener("click", () => openFlowDetail(Number(btn.dataset.detail), true)));
    host.querySelectorAll("[data-edit-flow]").forEach(btn => btn.addEventListener("click", () => editWorkflow(Number(btn.dataset.editFlow))));
    host.querySelectorAll("[data-withdraw-flow]").forEach(btn => btn.addEventListener("click", () => withdrawWorkflow(Number(btn.dataset.withdrawFlow))));
    bindPagination(host, pageKey, () => renderPersonalFlowList(hostId, rows, pageKey, allowApplicantActions));
  }

  async function withdrawWorkflow(formId) {
    if (!confirm("确认撤销该流程？撤销后流程会进入回收站。")) return;
    await systemApi(`/api/workflows/${formId}`, { method: "DELETE" });
    toast("流程已撤销");
    await refreshAfterFlowMutation();
  }

  function openBorrowReturn(recordId) {
    const record = (system.myBorrowRows || []).find(item => Number(item.id) === Number(recordId));
    if (!record) return;
    const isProduction = record.item_type === 'semifinished' || record.item_type === 'finished';
    const warehouseId = defaultStepUserId("borrow_return", "return_inbound", "warehouse");

    let body = `<div class="form-grid">
      <p class="hint wide">未归还数量：${formatQty(record.remaining_quantity || 0)} ${escapeHtml(record.unit || "")}</p>
      <label>归还数量<input id="returnBorrowQty" type="number" step="1" min="0" max="${Number(record.remaining_quantity || 0)}" value="${formatInputQty(record.remaining_quantity || 0)}"></label>
      <label>归还状态
        <select id="returnStatus">
          <option value="完好">完好</option>
          <option value="报废">报废</option>
          <option value="异常">异常</option>
        </select>
      </label>
      <label id="remarksLabel" style="display:none">备注说明<textarea id="returnRemarks" rows="2"></textarea></label>
      <label class="wide">仓库验收入库人<select id="returnBorrowWarehouse">${userOptionsForStep("borrow_return", "return_inbound", "warehouse", "", warehouseId)}</select></label>`;

    if (isProduction) {
      body += `
      <label>是否存在变更
        <select id="hasChanges">
          <option value="否">否</option>
          <option value="是">是</option>
        </select>
      </label>
      <div id="changeFields" style="display:none">
        <label>变更情况
          <select id="changeType">
            <option value="软件">软件</option>
            <option value="硬件">硬件</option>
          </select>
        </label>
        <label id="softwareFields"><input id="versionAfter" placeholder="软件变更后版本号"></label>
        <label id="hardwareFields" style="display:none"><textarea id="changeDetail" rows="2" placeholder="硬件变更详情"></textarea></label>
        <label>是否正常使用
          <select id="normalUse">
            <option value="是">是</option>
            <option value="否">否</option>
          </select>
        </label>
      </div>`;
    }

    body += `</div>`;

    openModal({
      title: `归还 - ${record.item_name || ""}`,
      body: body,
      okText: "提交归还",
      onReady: () => {
        const statusSelect = document.getElementById('returnStatus');
        const remarksLabel = document.getElementById('remarksLabel');
        statusSelect.onchange = () => {
          remarksLabel.style.display = (statusSelect.value === '报废' || statusSelect.value === '异常') ? '' : 'none';
        };

        if (isProduction) {
          const hasChanges = document.getElementById('hasChanges');
          const changeFields = document.getElementById('changeFields');
          const changeType = document.getElementById('changeType');
          const softwareFields = document.getElementById('softwareFields');
          const hardwareFields = document.getElementById('hardwareFields');

          hasChanges.onchange = () => {
            changeFields.style.display = hasChanges.value === '是' ? '' : 'none';
          };
          changeType.onchange = () => {
            softwareFields.style.display = changeType.value === '软件' ? '' : 'none';
            hardwareFields.style.display = changeType.value === '硬件' ? '' : 'none';
          };
        }
      },
      onOk: async () => {
        const status = document.getElementById('returnStatus').value;
        const remarks = document.getElementById('returnRemarks')?.value || '';

        if ((status === '报废' || status === '异常') && !remarks.trim()) {
          throw new Error('报废/异常需填写备注');
        }

        const payload = {
          borrow_record_id: record.id,
          return_quantity: parseFloat(document.getElementById('returnBorrowQty').value),
          warehouse_user_id: document.getElementById('returnBorrowWarehouse').value,
          status: status,
          remarks: remarks
        };

        if (isProduction) {
          const hasChangesVal = document.getElementById('hasChanges').value;
          if (hasChangesVal === '是') {
            payload.has_changes = '是';
            payload.change_type = document.getElementById('changeType').value;
            payload.normal_use = document.getElementById('normalUse').value;
            if (payload.change_type === '软件') {
              payload.version_after = document.getElementById('versionAfter').value;
            } else {
              payload.change_detail = document.getElementById('changeDetail').value;
            }
          } else {
            payload.has_changes = '否';
          }
        }

        await systemApi("/api/borrow-returns", {
          method: "POST",
          body: JSON.stringify(payload),
        });

        toast("归还流程已发起");
        await loadMyBorrow();
        await refreshRealtimeTodos();
      },
    });
  }

  function initiateTransfer(recordId) {
    const record = (system.myBorrowRows || []).find(item => Number(item.id) === Number(recordId));
    if (!record) return;
    const selfId = Number(currentUser()?.id);
    const users = (system.boot?.users || []).filter(u => Number(u.id) !== selfId);
    if (!users.length) { toast("没有可转借的用户"); return; }
    openPeoplePicker({
      title: `转借 - ${escapeHtml(record.item_name || "")}`,
      users: users,
      multiple: false,
      onConfirm: async (ids) => {
        const receiverId = ids[0];
        if (!receiverId) { toast("请选择接收人"); return; }
        await systemApi(`/api/borrows/${recordId}/transfer`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ receiver_id: receiverId }),
        });
        toast("转借请求已发送");
        await refreshMyBorrows();
      },
    });
  }

  async function acceptTransfer(recordId) {
    if (!confirm("确认接收该转借物料？接收后该物料将转移至您的名下。")) return;
    await systemApi(`/api/transfers/${recordId}/accept`, { method: "POST" });
    toast("已接收转借物料");
    await refreshMyBorrows();
  }

  async function rejectTransfer(recordId) {
    if (!confirm("确认拒绝该转借？")) return;
    await systemApi(`/api/transfers/${recordId}/reject`, { method: "POST" });
    toast("已拒绝转借");
    await refreshMyBorrows();
  }

  async function refreshMyBorrows() {
    await loadMyBorrow();
  }

  function borrowItemTypeLabel(type) {
    if (type === "semifinished") return "半成品";
    if (type === "finished") return "成品";
    return "物料";
  }

  async function loadSemifinishedFlowList() {
    const rows = await systemApi("/api/workflows?type=semifinished&active=1&mine=1");
    renderFlowList("semiFlowList", rows);
  }

  async function loadFinishedFlowList() {
    const rows = await systemApi("/api/workflows?type=finished&active=1&mine=1");
    renderFlowList("finFlowList", rows);
  }

  async function loadOutboundList() {
    const rows = await systemApi("/api/workflows?type=claim&status=outbound");
    renderFlowList("outboundList", rows);
  }

  async function loadFlowCenter() {
    const params = new URLSearchParams();
    const type = document.getElementById("flowType")?.value || "";
    const status = document.getElementById("flowStatus")?.value || "";
    if (type) params.set("type", type);
    if (status) params.set("status", status);
    const materialName = document.getElementById("flowMaterialName")?.value || "";
    const materialCode = document.getElementById("flowMaterialCode")?.value || "";
    const brandModel = document.getElementById("flowBrandModel")?.value || "";
    const spec = document.getElementById("flowSpec")?.value || "";
    const applicant = document.getElementById("flowApplicant")?.value || "";
    const inspector = document.getElementById("flowInspector")?.value || "";
    const formNo = document.getElementById("flowFormNo")?.value || "";
    if (materialName) params.set("material_name", materialName);
    if (materialCode) params.set("material_code", materialCode);
    if (brandModel) params.set("brand_model", brandModel);
    if (spec) params.set("spec", spec);
    if (applicant) params.set("applicant", applicant);
    if (inspector) params.set("inspector", inspector);
    if (formNo) params.set("form_no", formNo);
    const rows = await systemApi(`/api/workflows?${params.toString()}`);
    system.flowRows = rows;
    renderFlowList("flowCenterList", rows);
  }

  function renderFlowList(hostId, rows) {
    const host = document.getElementById(hostId);
    const selectable = hostId === "flowCenterList";
    const pageKey = selectable ? "flowCenter" : "";
    const visibleRows = pageKey ? pageRows(pageKey, rows) : rows;
    host.innerHTML = `<table class="flow-table"><thead><tr>${selectable ? `<th><input id="checkAllFlows" type="checkbox"></th>` : ""}<th>单号</th><th>标题</th><th>类型</th><th>状态</th><th>申请人</th><th>领导签字</th><th>时间</th><th>操作</th></tr></thead><tbody>
      ${visibleRows.map(form => `<tr>${selectable ? `<td><input type="checkbox" data-flow-check="${form.id}"></td>` : ""}<td>${escapeHtml(form.form_no)}</td><td>${escapeHtml(form.title || "")}</td><td>${flowTypeLabel(form.form_type)}</td><td>${escapeHtml(workflowStatusLabel(form.status))}</td><td>${escapeHtml(form.applicant_name || "")}</td><td>${escapeHtml(form.leader_signatures || "")}</td><td>${escapeHtml(form.created_at || "")}</td><td class="mini-actions"><button data-detail="${form.id}">详情/办理</button></td></tr>`).join("") || `<tr><td colspan="${selectable ? 9 : 8}">暂无数据</td></tr>`}
    </tbody></table>${pageKey ? paginationHtml(pageKey, rows) : ""}`;
    if (selectable) {
      rows.forEach(form => {
        const detail = host.querySelector(`[data-detail="${form.id}"]`);
        if (!detail) return;
        if (workflowCan(form, "edit")) detail.insertAdjacentHTML("afterend", `<button data-edit-flow="${form.id}">\u4fee\u6539</button>`);
        if (workflowCan(form, "delete")) detail.insertAdjacentHTML("afterend", `<button class="danger" data-delete-flow="${form.id}">\u5220\u9664</button>`);
      });
    }
    document.getElementById("checkAllFlows")?.addEventListener("change", event => {
      host.querySelectorAll("[data-flow-check]").forEach(input => { input.checked = event.target.checked; });
    });
    host.querySelectorAll("[data-detail]").forEach(btn => btn.addEventListener("click", () => openFlowDetail(Number(btn.dataset.detail))));
    host.querySelectorAll("[data-edit-flow]").forEach(btn => btn.addEventListener("click", () => editWorkflow(Number(btn.dataset.editFlow))));
    host.querySelectorAll("[data-delete-flow]").forEach(btn => btn.addEventListener("click", () => deleteWorkflow(Number(btn.dataset.deleteFlow))));
    if (pageKey) bindPagination(host, pageKey, () => renderFlowList(hostId, rows));
  }

  function workflowCan(form, action) {
    const isAcceptance = form.form_type === "acceptance" || form.form_type === "semifinished" || form.form_type === "finished";
    if (form.form_type === "borrow" || form.form_type === "borrow_return") return hasPerm(`${action}_borrow`);
    const isOutbound = form.form_type === "claim" && form.status === "outbound";
    const key = `${action}_${isAcceptance ? "acceptance" : (isOutbound ? "outbound" : "claim")}`;
    return hasPerm(key);
  }

  function flowTypeLabel(type) {
    if (type === "common_material") return "常用物料申请";
    if (type === "supply") return "供货申请";
    if (type === "supply_return") return "供货回寄";
    if (type === "supply_extension") return "供货延期";
    if (type === "claim") return "申领/出库";
    if (type === "borrow") return "借用申请";
    if (type === "borrow_return") return "借用归还";
    if (type === "semifinished") return "半成品验收";
    if (type === "finished") return "成品验收";
    if (type === "stocktake") return "盘点";
    return "物料验收";
  }

  function workflowStatusLabel(status) {
    const labels = {
      acceptance: "验收中",
      leader_acceptance: "验收领导审批",
      inbound: "待入库",
      leader_claim: "申领审批",
      outbound: "待出库",
      leader_borrow: "借用审批",
      borrow_outbound: "借用待出库",
      return_inbound: "归还待入库",
      applicant_revision: "发起人修改",
      awaiting_purchase: "等待采购处理",
      acceptance_in_progress: "验收处理中",
      acceptance_failed: "验收失败",
      formal_inbound_partial: "正式入库不足",
      formal_inbound_complete: "正式入库完成，等待结算",
      reserving: "正在预留正式库存",
      auto_claim_creating: "正在创建自动领用",
      auto_claim_pending: "等待自动领用完成",
      auto_claim_exception: "自动领用异常",
      paused: "已暂停",
      exception: "异常",
      supervisor: "待监盘签字",
      stocktake_supervisor: "待监盘签字",
      completed: "已办结",
      rejected: "已驳回",
      cancelled: "已取消",
      leader_common_material: "常用物料审批",
      leader_supply: "供货审批",
      supply_outbound: "供货出库",
      supply_return_inbound: "供货回寄验收入库",
      leader_supply_extension: "供货延期审批",
      external_open: "外部待结清",
    };
    return labels[status] || status || "";
  }

  function workflowStepLabel(step) {
    const labels = {
      acceptance: "验收",
      leader_acceptance: "审批领导审批",
      inbound: "入库",
      leader_claim: "申领审批",
      outbound: "出库",
      leader_borrow: "借用审批",
      borrow_outbound: "借用出库",
      return_inbound: "归还验收入库",
      applicant_revision: "发起人修改",
      temporary_transfer: "转正式库办理",
      stocktake_supervisor: "监盘签字",
      supervisor: "监盘签字",
      completed: "已办结",
      rejected: "已驳回",
      cancelled: "已取消",
      leader_common_material: "常用物料审批",
      leader_supply: "供货审批",
      supply_outbound: "供货出库",
      supply_return_inbound: "供货回寄验收入库",
      leader_supply_extension: "供货延期审批",
      external_open: "外部待结清",
    };
    return labels[step] || workflowStatusLabel(step) || step || "";
  }

  function stocktakeStatusLabel(status) {
    const labels = {
      supervisor: "待监盘签字",
      stocktake_supervisor: "待监盘签字",
      completed: "已完成",
      cancelled: "已取消",
    };
    return labels[status] || workflowStatusLabel(status) || status || "";
  }

  async function editWorkflow(formId) {
    const form = system.flowRows.find(row => row.id === formId) || await systemApi(`/api/workflows/${formId}`);
    const detail = form.items ? form : await systemApi(`/api/workflows/${formId}`);
    const isApplicant = Number(detail.applicant_id || 0) === Number(system.userId || 0);
    if (isApplicant && !workflowCan(detail, "edit")) {
      return editApplicantWorkflow(detail);
    }
    openModal({ title: `修改 ${detail.form_no}`, body: `<div class="form-grid">
      <label class="wide">标题<input id="editFlowTitle" value="${escapeAttr(detail.title || detail.form_no || "")}"></label>
      <label>状态<input id="editFlowStatus" value="${escapeAttr(detail.status || "")}"></label>
      <label>当前步骤<input id="editFlowStep" value="${escapeAttr(detail.current_step || "")}"></label>
      <label>领导<select id="editFlowLeader"><option value="">未指定</option>${userOptionsFrom(system.boot.users || [], Number(detail.leader_id || 0))}</select></label>
      <label>库管/出入库人<select id="editFlowWarehouse"><option value="">未指定</option>${userOptionsFrom(system.boot.users || [], Number(detail.warehouse_user_id || 0))}</select></label>
      <div class="wide">${editWorkflowItemsHtml(detail)}</div>
      <div class="wide">${editWorkflowTasksHtml(detail)}</div>
    </div>`, okText: "保存修改", onReady: () => {
      document.getElementById("editFlowLeader").value = detail.leader_id || "";
      document.getElementById("editFlowWarehouse").value = detail.warehouse_user_id || "";
      document.querySelectorAll("[data-delete-edit-item]").forEach(btn => {
        btn.addEventListener("click", function () {
          const itemId = this.dataset.deleteEditItem;
          const panel = document.querySelector(`[data-edit-flow-item="${itemId}"]`);
          if (panel) {
            panel.setAttribute("data-edit-flow-deleted", "1");
            panel.style.display = "none";
          }
        });
      });
    }, onOk: async () => {
      await systemApi(`/api/workflows/${formId}`, { method: "PUT", body: JSON.stringify({
        title: document.getElementById("editFlowTitle").value,
        status: document.getElementById("editFlowStatus").value,
        current_step: document.getElementById("editFlowStep").value,
        leader_id: document.getElementById("editFlowLeader").value,
        warehouse_user_id: document.getElementById("editFlowWarehouse").value,
        items: collectEditWorkflowItems(),
        tasks: collectEditWorkflowTasks(),
        delete_item_ids: collectEditWorkflowDeletedItems(),
      }) });
      if ((detail.current_step || detail.status) === "applicant_revision") {
        await systemApi(`/api/workflows/${formId}/resubmit-returned`, { method: "POST", body: JSON.stringify({}) });
      }
      toast("\u6d41\u7a0b\u5df2\u4fee\u6539");
      refreshAfterFlowMutation();
    }});
  }

  function returnedWorkflowItemFields(form) {
    const type = form.form_type;
    if (type === "claim" || type === "borrow" || type === "borrow_return") {
      return [
        ["material_code", "编号", "text", true],
        ["material_name", "名称", "text", true],
        ["brand_model", "品牌型号", "text", true],
        ["spec", "规格", "text", true],
        ["unit", "单位", "text", true],
        ["request_quantity", type === "borrow_return" ? "归还数量" : "申请数量", "number", false],
      ];
    }
    if (type === "semifinished" || type === "finished") {
      return [
        ["material_name", type === "semifinished" ? "半成品名称" : "成品名称", "text", false],
        ["spec", "规格参数", "text", false],
        ["unit", "单位", "text", false],
        ["arrival_quantity", "验收数量", "number", false],
      ];
    }
    return [
      ["material_code", "编号", "text", false],
      ["material_name", "名称", "text", false],
      ["brand_model", "品牌型号", "text", false],
      ["spec", "技术规格", "text", false],
      ["purchase_applicant", "采购申请人", "text", false],
      ["unit", "单位", "text", false],
      ["request_quantity", "采购数量", "number", false],
      ["arrival_quantity", "到货数量", "number", false],
      ["unit_price", "单价", "number", false],
    ];
  }

  function returnedWorkflowItemsHtml(form) {
    const fields = returnedWorkflowItemFields(form);
    const items = ["claim", "borrow"].includes(form.form_type)
      ? ClaimSourceUI.groupClaimRevisionItems(form.items || [])
      : (form.items || []);
    return `<h3>表单明细</h3>${items.map(item => `<div class="work-panel" data-returned-flow-item="${item.id}" data-allocation-group="${escapeAttr(item.allocation_group_key || "")}"><div class="work-body"><div class="form-grid">${fields.map(([key, label, type, readonly]) => `<label>${label}<input data-returned-item-field="${key}" type="${type}" ${type === "number" ? `step="${returnedWorkflowNumberStep(key)}" min="0"` : ""} ${readonly ? "readonly" : ""} value="${escapeAttr(item[key] ?? "")}"></label>`).join("")}</div>${["claim", "borrow"].includes(form.form_type) ? `<small class="hint">重新提交时由服务器按最新库存重新分配来源</small>` : ""}</div></div>`).join("") || `<div class="empty-state">暂无明细</div>`}`;
  }

  function returnedWorkflowNumberStep(key) {
    return key === "unit_price" ? "0.01" : "1";
  }

  function collectReturnedWorkflowItems() {
    return [...document.querySelectorAll("[data-returned-flow-item]")].map(row => {
      const item = {
        id: Number(row.dataset.returnedFlowItem),
        allocation_group_key: row.dataset.allocationGroup || "",
      };
      row.querySelectorAll("[data-returned-item-field]").forEach(input => {
        if (!input.readOnly) item[input.dataset.returnedItemField] = input.value;
      });
      return item;
    });
  }

  async function editApplicantWorkflow(detail) {
    const isReturned = (detail.current_step || detail.status) === "applicant_revision";
    const returnReasons = (detail.tasks || [])
      .filter(task => task.data?.return_reason)
      .map(task => `<p class="hint">${escapeHtml(workflowStepLabel(task.step_code))}退回原因：${escapeHtml(task.data.return_reason || "")}</p>`)
      .join("");
    openModal({
      title: `${isReturned ? "重新提交" : "修改表单"} ${detail.form_no}`,
      body: `<div class="form-grid">
        <p class="hint wide">仅可修改发起表单内容，流程状态、当前环节、办理记录和删除明细不可由发起人修改。</p>
        ${returnReasons ? `<div class="wide">${returnReasons}</div>` : ""}
        <label class="wide">标题<input id="returnedFlowTitle" value="${escapeAttr(detail.title || detail.form_no || "")}"></label>
        <div class="wide">${returnedWorkflowItemsHtml(detail)}</div>
      </div>`,
      okText: "重新提交",
      onOk: async () => {
        await systemApi(`/api/workflows/${detail.id}`, {
          method: "PUT",
          body: JSON.stringify({
            title: document.getElementById("returnedFlowTitle").value,
            items: collectReturnedWorkflowItems(),
          }),
        });
        if (isReturned) {
          await systemApi(`/api/workflows/${detail.id}/resubmit-returned`, { method: "POST", body: JSON.stringify({}) });
          toast("流程已重新提交");
        } else {
          toast("流程已修改");
        }
        closeModal();
        await refreshAfterFlowMutation();
      },
    });
  }

  function editWorkflowItemsHtml(form) {
    const fields = [
      ["material_code", "编号"],
      ["material_name", "名称"],
      ["brand_model", "品牌型号"],
      ["spec", "技术规格"],
      ["unit", "单位"],
      ["request_quantity", "申请/采购数量"],
      ["arrival_quantity", "到货/验收数量"],
      ["qualified_quantity", "合格数量"],
      ["unqualified_quantity", "不合格数量"],
      ["approved_quantity", "核准入库"],
      ["outbound_quantity", "出库数量"],
      ["unit_price", "单价"],
    ];
    return `<h3>表单明细</h3>${(form.items || []).map(item => `<div class="work-panel" data-edit-flow-item="${item.id}"><div class="work-body"><div class="form-grid">${fields.map(([key, label]) => `<label>${label}<input data-edit-item-field="${key}" value="${escapeAttr(item[key] ?? "")}"></label>`).join("")}</div><div style="margin-top:8px;display:flex;justify-content:flex-end"><button class="danger" data-delete-edit-item="${item.id}">删除此物料行</button></div></div></div>`).join("") || `<div class="empty-state">暂无明细</div>`}`;
  }

  function editWorkflowTasksHtml(form) {
    return `<h3>签字/办理记录</h3>${(form.tasks || []).map(task => `<div class="work-panel" data-edit-flow-task="${task.id}"><div class="work-body"><div class="form-grid"><label>环节<input value="${escapeAttr(workflowStepLabel(task.step_code || ""))}" readonly></label><label>办理人<select data-edit-task-field="assignee_id"><option value="">未指定</option>${userOptionsFrom(system.boot.users || [], Number(task.assignee_id || 0))}</select></label><label>状态<input data-edit-task-field="status" value="${escapeAttr(task.status || "")}"></label><label>意见<input data-edit-task-field="decision" value="${escapeAttr(task.decision || "")}"></label><label>签字<input data-edit-task-field="signature" value="${escapeAttr(task.signature || "")}"></label><label>日期<input data-edit-task-field="signed_at" value="${escapeAttr(task.signed_at || "")}"></label><label class="wide">备注<textarea data-edit-task-field="remark">${escapeHtml(task.data?.remark || "")}</textarea></label></div></div></div>`).join("") || `<div class="empty-state">暂无办理记录</div>`}`;
  }

  function collectEditWorkflowItems() {
    return [...document.querySelectorAll("[data-edit-flow-item]")].filter(row => !row.dataset.editFlowDeleted).map(row => {
      const item = { id: Number(row.dataset.editFlowItem) };
      row.querySelectorAll("[data-edit-item-field]").forEach(input => { item[input.dataset.editItemField] = input.value; });
      return item;
    });
  }

  function collectEditWorkflowDeletedItems() {
    return [...document.querySelectorAll("[data-edit-flow-item][data-edit-flow-deleted]")].map(row => Number(row.dataset.editFlowItem));
  }

  function collectEditWorkflowTasks() {
    return [...document.querySelectorAll("[data-edit-flow-task]")].map(row => {
      const task = { id: Number(row.dataset.editFlowTask) };
      row.querySelectorAll("[data-edit-task-field]").forEach(input => { task[input.dataset.editTaskField] = input.value; });
      return task;
    });
  }

  async function deleteWorkflow(formId) {
    if (!confirm("\u786e\u8ba4\u5220\u9664\u8be5\u6d41\u7a0b\u5355\uff1f")) return;
    await systemApi(`/api/workflows/${formId}`, { method: "DELETE" });
    toast("\u6d41\u7a0b\u5df2\u5220\u9664");
    refreshAfterFlowMutation();
  }

  async function refreshAfterFlowMutation() {
    system.boot = null;
    await loadSystemBoot();
    if (system.view === "flow") return loadFlowCenter();
    if (system.view === "myStarted") return loadMyStarted();
    if (system.view === "myInspections") return loadMyInspections();
    renderSystemMain();
  }

  async function deleteCheckedFlows() {
    const rows = checkedFlowRows();
    if (!rows.length) {
      toast("请先勾选要删除的流程");
      return;
    }
    if (!confirm(`确认删除已勾选的 ${rows.length} 个流程单？`)) return;
    if (!confirm("删除后不可恢复，请再次确认删除勾选流程。")) return;
    for (const row of rows) {
      await systemApi(`/api/workflows/${row.id}`, { method: "DELETE" });
    }
    toast("已删除勾选流程");
    loadFlowCenter();
  }

  function checkedFlowRows() {
    const ids = [...document.querySelectorAll("[data-flow-check]:checked")].map(input => Number(input.dataset.flowCheck));
    return system.flowRows.filter(row => ids.includes(row.id));
  }

  function exportFlowRows(rows, filename) {
    exportFullFlowRows(rows, filename);
    return;
    const data = rows.length ? rows : system.flowRows;
    const csv = [["单号", "标题", "类型", "状态", "申请人", "时间"], ...data.map(row => [row.form_no, row.title || "", row.form_type === "claim" ? "申领" : "验收", row.status, row.applicant_name || "", row.created_at || ""])]
      .map(line => line.map(cell => `"${String(cell).replaceAll('"', '""')}"`).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  async function printFlowRows(rows) {
    if (system.printing) return;
    system.printing = true;
    try {
      await printFullFlowRows(rows);
    } finally {
      window.setTimeout(() => { system.printing = false; }, 600);
    }
    return;
    const data = rows.length ? rows : system.flowRows;
    const html = `<table class="flow-table"><thead><tr><th>单号</th><th>标题</th><th>类型</th><th>状态</th><th>申请人</th><th>时间</th></tr></thead><tbody>${data.map(row => `<tr><td>${escapeHtml(row.form_no)}</td><td>${escapeHtml(row.title || "")}</td><td>${row.form_type === "claim" ? "申领" : "验收"}</td><td>${escapeHtml(row.status)}</td><td>${escapeHtml(row.applicant_name || "")}</td><td>${escapeHtml(row.created_at || "")}</td></tr>`).join("")}</tbody></table>`;
    printHtml("流程表单", html);
  }

  async function fullFlowRows(rows) {
    const result = [];
    for (const row of rows) result.push(row.tasks ? row : await systemApi(`/api/workflows/${row.id}`));
    return result;
  }

  async function exportFullFlowRows(rows, filename) {
    const data = await fullFlowRows(rows.length ? rows : system.flowRows);
    const csv = [["单号", "标题", "类型", "状态", "申请人", "时间", "物料明细", "审批/办理记录"], ...data.map(row => [row.form_no, row.title || "", flowTypeLabel(row.form_type), workflowStatusLabel(row.status), row.applicant_name || "", row.created_at || "", flowItemsText(row), flowTasksText(row)])].map(line => line.map(cell => `"${String(cell).replaceAll('"', '""')}"`).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = filename; a.click(); URL.revokeObjectURL(a.href);
  }

  async function printFullFlowRows(rows) {
    const data = await fullFlowRows(rows.length ? rows : system.flowRows);
    const html = data.map(row => `<section style="page-break-inside:avoid;margin-bottom:18px;"><h2>${escapeHtml(row.form_no)} ${escapeHtml(row.title || "")}</h2><p>类型：${flowTypeLabel(row.form_type)}，状态：${escapeHtml(workflowStatusLabel(row.status))}，申请人：${escapeHtml(row.applicant_name || "")}，时间：${escapeHtml(row.created_at || "")}</p>${itemsTable(row.items || [])}<h3>审批/办理记录</h3><table class="flow-table"><thead><tr><th>环节</th><th>办理人</th><th>意见</th><th>签字</th><th>日期</th><th>备注</th></tr></thead><tbody>${(row.tasks || []).map(t => `<tr><td>${escapeHtml(workflowStepLabel(t.step_code))}</td><td>${escapeHtml(t.assignee_name || "")}</td><td>${escapeHtml(t.decision || "")}</td><td>${escapeHtml(t.signature || "")}</td><td>${escapeHtml(t.signed_at || "")}</td><td>${escapeHtml(t.data?.remark || t.data?.return_reason || "")}</td></tr>`).join("")}</tbody></table></section>`).join("");
    printHtml("流程表单", html);
  }

  function flowItemsText(row) {
    return (row.items || []).map(i => `${i.material_code} ${i.material_name} 数量:${i.request_quantity || i.arrival_quantity || ""} 合格:${i.qualified_quantity || ""} 单价:${i.unit_price || ""}`).join("；");
  }

  function flowTasksText(row) {
    return (row.tasks || []).map(t => `${workflowStepLabel(t.step_code)}/${t.assignee_name || ""}/${t.decision || ""}/${t.signature || ""}/${t.signed_at || ""}/${t.data?.remark || t.data?.return_reason || ""}`).join("；");
  }

  async function openFlowDetail(formId, readonly = false) {
    const form = await systemApi(`/api/workflows/${formId}`);
    const taskButtons = readonly ? `<button data-flow-action="printDetail">打印</button>` : flowActions(form);
    const leaderSignatures = (form.tasks || []).filter(task => task.step_code === "leader_acceptance" || task.step_code === "leader_claim" || task.step_code === "leader_borrow").map(task => task.signature).filter(Boolean).join("、");
    openModal({
      title: `${form.form_no} · ${form.title || ""}`,
      hideOk: true,
      body: `
        <div class="facts">
          <div class="fact"><span>状态</span><strong>${escapeHtml(workflowStatusLabel(form.status))}</strong></div>
          <div class="fact"><span>申请人</span><span>${escapeHtml(form.applicant_name || "")}</span></div>
          <div class="fact"><span>验收签字</span><span>${escapeHtml(form.data?.acceptance_signatures || "")}</span></div>
          <div class="fact"><span>领导签字</span><span>${escapeHtml(leaderSignatures || "")}</span></div>
        </div>
        ${form.transfer_task ? `<div class="facts transfer-origin-facts"><div class="fact"><span>来源</span><strong>临时库转正式库</strong></div><div class="fact"><span>转移任务</span><strong>${escapeHtml(form.transfer_task.transfer_no)}</strong></div><div class="fact"><span>临时库存快照</span><strong>${formatInputQty(form.transfer_task.temporary_quantity_snapshot)}</strong></div><div class="fact"><span>历史待结算量</span><strong>${formatInputQty(form.transfer_task.obligation_quantity_snapshot)}</strong></div><div class="fact"><span>目标验收数量</span><strong>${formatInputQty(form.transfer_task.target_acceptance_quantity)}</strong></div><div class="fact"><span>已正式入库</span><strong>${formatInputQty(form.transfer_task.accepted_quantity)}</strong></div></div>` : ""}
        ${flowItemsTable(form)}
        <h3>审批/办理记录</h3>${flowTaskTable(form)}
        <div class="mini-actions">${taskButtons}</div>
      `,
      onReady: () => {
        document.querySelectorAll("[data-flow-action]").forEach(btn => btn.addEventListener("click", () => handleFlowAction(form, btn.dataset.flowAction)));
      },
    });
  }

  function flowActions(form) {
    const buttons = [];
    if (isProductionFlow(form) && form.status === "acceptance") buttons.push(`<button data-flow-action="productionInspect" class="primary">填写验收</button>`);
    if (isProductionFlow(form) && form.status === "leader_acceptance") buttons.push(`<button data-flow-action="productionLeader" class="primary">领导审批</button>`);
    if (isProductionFlow(form) && form.status === "inbound") buttons.push(`<button data-flow-action="productionInbound" class="good">办理入库</button>`);
    if (form.form_type === "acceptance" && form.status === "acceptance") buttons.push(`<button data-flow-action="inspect" class="primary">填写验收</button>`);
    if (form.form_type === "acceptance" && form.status === "leader_acceptance") buttons.push(`<button data-flow-action="leaderAcceptance" class="primary">领导审批</button>`);
    if (form.form_type === "acceptance" && form.status === "inbound") buttons.push(`<button data-flow-action="inbound" class="good">办理入库</button>`);
    if (form.form_type === "claim" && form.status === "leader_claim") buttons.push(`<button data-flow-action="leaderClaim" class="primary">领导审批</button>`);
    if (form.form_type === "claim" && form.status === "outbound") buttons.push(`<button data-flow-action="outbound" class="warn">办理出库</button>`);
    if (form.form_type === "borrow" && form.status === "leader_borrow") buttons.push(`<button data-flow-action="leaderBorrow" class="primary">领导审批</button>`);
    if (form.form_type === "borrow" && form.status === "borrow_outbound") buttons.push(`<button data-flow-action="borrowOutbound" class="warn">办理出库</button>`);
    if (form.form_type === "borrow_return" && form.status === "return_inbound") buttons.push(`<button data-flow-action="borrowReturnInbound" class="good">验收入库</button>`);
    if (form.form_type === "common_material" && form.status === "leader_common_material") buttons.push('<button data-flow-action="leaderCommonMaterial" class="primary">常用物料审批</button>');
    if (form.form_type === "supply" && form.status === "leader_supply") buttons.push('<button data-flow-action="leaderSupply" class="primary">供货审批</button>');
    if (form.form_type === "supply" && form.status === "supply_outbound") buttons.push('<button data-flow-action="supplyOutbound" class="warn">打开供货出库</button>');
    if (form.form_type === "supply_return" && form.status === "supply_return_inbound") buttons.push('<button data-flow-action="supplyReturnInbound" class="good">验收回寄</button>');
    if (form.form_type === "supply_extension" && form.status === "leader_supply_extension") buttons.push('<button data-flow-action="leaderSupplyExtension" class="primary">延期审批</button>');
    if (form.status === "applicant_revision" && currentPendingTask(form)) buttons.push(`<button data-flow-action="returnedRevision" class="primary">重新提交</button>`);
    if (workflowCanReturn(form)) buttons.push(`<button data-flow-action="returnFlow" class="danger">退回</button>`);
    buttons.push(`<button data-flow-action="printDetail">打印</button>`);
    return buttons.join("");
  }

  async function handleFlowAction(form, action) {
    closeModal();
    if (action === "productionInspect") return inspectProductionFlow(form);
    if (action === "productionLeader") return leaderDecision(`/api/production/${form.form_type}/${form.id}/leader`, form);
    if (action === "productionInbound") return inboundProductionFlow(form);
    if (action === "inspect") return inspectFlow(form);
    if (action === "leaderAcceptance") return leaderDecision(`/api/acceptance/${form.id}/leader`, form);
    if (action === "inbound") return inboundFlowV2(form);
    if (action === "leaderClaim") return leaderDecision(`/api/claims/${form.id}/leader`, form);
    if (action === "outbound") return outboundFlowV2(form);
    if (action === "leaderBorrow") return leaderDecision(`/api/borrows/${form.id}/leader`, form);
    if (action === "leaderCommonMaterial") return leaderDecision("/api/common-material-applications/" + form.id + "/leader", form);
    if (action === "leaderSupply") return leaderDecision("/api/supplies/" + form.id + "/leader", form);
    if (action === "supplyOutbound") { system.view = "supplies"; return renderSystemMain(); }
    if (action === "leaderSupplyExtension") return leaderDecision("/api/supply-extensions/" + form.id + "/leader", form);
    if (action === "supplyReturnInbound") { system.view = "supplies"; return renderSystemMain(); }
    if (action === "borrowOutbound") return borrowOutboundFlow(form);
    if (action === "borrowReturnInbound") return borrowReturnInboundFlow(form);
    if (action === "returnedRevision") return editApplicantWorkflow(form);
    if (action === "returnFlow") return returnWorkflowFlow(form);
    if (action === "printDetail") return printFullFlowRows([form]);
  }

  function workflowStepCodes(type) {
    const map = {
      acceptance: ["acceptance", "leader_acceptance", "inbound"],
      claim: ["leader_claim", "outbound"],
      borrow: ["leader_borrow", "borrow_outbound"],
      borrow_return: ["return_inbound"],
      common_material: ["leader_common_material"],
      supply: ["leader_supply", "supply_outbound"],
      supply_return: ["supply_return_inbound"],
      supply_extension: ["leader_supply_extension"],
      semifinished: ["acceptance", "leader_acceptance", "inbound"],
      finished: ["acceptance", "leader_acceptance", "inbound"],
    };
    return map[type] || [];
  }

  function currentPendingTask(form) {
    const step = form.current_step || form.status;
    const isAdmin = (system.boot?.user?.role_codes || []).includes("admin");
    return (form.tasks || []).find(task => task.status === "pending" && task.step_code === step && (isAdmin || Number(task.assignee_id || 0) === Number(system.userId || 0)));
  }

  function workflowCanReturn(form) {
    const steps = workflowStepCodes(form.form_type);
    const current = form.current_step || form.status;
    return currentPendingTask(form) && steps.includes(current);
  }

  function returnWorkflowFlow(form) {
    const steps = workflowStepCodes(form.form_type);
    const current = form.current_step || form.status;
    const currentIndex = steps.indexOf(current);
    const targets = ["applicant_revision", ...steps.slice(0, Math.max(0, currentIndex))];
    openModal({
      title: `退回 ${form.form_no}`,
      body: `<div class="form-grid">
        <label>退回到<select id="returnTargetStep">${targets.map(step => `<option value="${step}">${escapeHtml(workflowStepLabel(step))}</option>`).join("")}</select></label>
        <label class="wide">退回原因<textarea id="returnReason" rows="3"></textarea></label>
      </div>`,
      okText: "确认退回",
      onOk: async () => {
        await systemApi(`/api/workflows/${form.id}/return`, {
          method: "POST",
          body: JSON.stringify({
            target_step: document.getElementById("returnTargetStep").value,
            reason: document.getElementById("returnReason").value,
            task_id: currentPendingTask(form)?.id || 0,
          }),
        });
        toast("流程已退回");
        await refreshAfterFlowMutation();
      },
    });
  }

  function flowTaskTable(form) {
    return `<table class="flow-table"><thead><tr><th>环节</th><th>办理人</th><th>意见</th><th>签字</th><th>日期</th><th>备注</th></tr></thead><tbody>${(form.tasks || []).map(t => `<tr><td>${escapeHtml(workflowStepLabel(t.step_code))}</td><td>${escapeHtml(t.assignee_name || "")}</td><td>${escapeHtml(t.decision || "")}</td><td>${escapeHtml(t.signature || "")}</td><td>${escapeHtml(t.signed_at || "")}</td><td>${escapeHtml(t.data?.remark || t.data?.return_reason || "")}</td></tr>`).join("") || `<tr><td colspan="6">暂无办理记录</td></tr>`}</tbody></table>`;
  }

  function isProductionFlow(form) {
    return form.form_type === "semifinished" || form.form_type === "finished";
  }

  function productionNameLabel(form) {
    return form.form_type === "semifinished" ? "半成品名称" : "成品名称";
  }

  function productionComponentLabel(form) {
    return form.form_type === "semifinished" ? "组成物料" : "组成物料/半成品";
  }

  function productionHeaderTable(form, item) {
    return `<table class="flow-table"><thead><tr><th>${productionNameLabel(form)}</th><th>参数</th><th>验收数量</th><th>单位</th></tr></thead><tbody><tr><td>${escapeHtml(item.material_name || "")}</td><td>${escapeHtml(item.spec || "")}</td><td>${formatQty(item.arrival_quantity || item.request_quantity || 0)}</td><td>${escapeHtml(item.unit || "")}</td></tr></tbody></table><h3>${productionComponentLabel(form)}</h3>`;
  }

  function inspectProductionFlow(form) {
    const item = (form.items || [])[0] || {};
    const dept = currentUser()?.department || "";
    const leaderLocked = Boolean(form.leader_id);
    const selectedLeader = Number(form.leader_id || defaultApprovalLeaderId(form.form_type, "leader_acceptance", dept));
    const selectedWarehouse = Number(form.warehouse_user_id || defaultStepUserId(form.form_type, "inbound", "warehouse"));
    const leaderText = approvalLeaderName(selectedLeader) || "当前部门未配置审批领导";
    const leaderField = leaderLocked || !manualApprovalLeaderEnabled()
      ? `<label class="wide">下一步审批人<input id="prodInspectLeaderText" value="${escapeAttr(leaderText)}" readonly><input id="prodInspectLeader" type="hidden" value="${selectedLeader || ""}"></label>`
      : `<label class="wide">下一步审批人<select id="prodInspectLeader">${approvalLeaderOptions(form.form_type, "leader_acceptance", dept, selectedLeader)}</select></label>`;
    const qty = Number(item.arrival_quantity || item.request_quantity || 0);
    openModal({
      title: `填写${flowTypeLabel(form.form_type)}结果`,
      body: `<div class="form-grid">
        ${leaderField}
        <label class="wide">入库流程办理人<select id="prodInspectWarehouse">${userOptionsForStep(form.form_type, "inbound", "warehouse", "", selectedWarehouse)}</select></label>
        ${leaderLocked ? `<p class="hint wide">已有验收员指定领导审批，其他验收员不可更改。</p>` : ""}
        <div class="wide">${productionHeaderTable(form, item)}${productionComponentsTable(item)}<input id="prodQty" type="hidden" value="${formatInputQty(qty)}"><h3>逐件编号验收</h3><div id="prodSerials"></div>${qualityInputs("prod")}</div>
        <label class="wide">其他问题<textarea id="prodRemark"></textarea></label>
        <label>是否同意<select id="prodInspectDecision"><option>同意</option><option>不同意</option></select></label>
        <label>签字<input id="prodInspectSign" value="${escapeAttr(currentUser()?.display_name || "")}"></label>
      </div>`,
      okText: "提交验收",
      onReady: () => {
        ["prodAppearance", "prodFunction", "prodPerformance"].forEach(id => { document.getElementById(id).value = formatInputQty(qty); });
        ["prodAppearance", "prodFunction", "prodPerformance", "prodQualified", "prodUnqualified"].forEach(id => { const input = document.getElementById(id); if (input) input.readOnly = true; });
        refreshProductionSerialRows(form);
      },
      onOk: async () => {
        const serialRows = productionSerialRowsFromHost();
        if (serialRows.some(row => !row.qualified && !(row.abnormal_conditions || []).length)) {
          throw new Error("不合格物品请至少勾选一项异常情况");
        }
        const itemPayload = {
          id: item.id,
          appearance_ok_quantity: document.getElementById("prodAppearance").value,
          function_ok_quantity: document.getElementById("prodFunction").value,
          performance_ok_quantity: document.getElementById("prodPerformance").value,
          serial_items: serialRows,
          defects: serialRows.filter(row => !row.qualified).map(row => ({ serial_no: row.serial_no, abnormal_conditions: row.abnormal_conditions })),
          remark: document.getElementById("prodRemark").value,
        };
        await systemApi(`/api/production/${form.form_type}/${form.id}/inspect`, {
          method: "POST",
          body: JSON.stringify({
            items: [itemPayload],
            leader_id: document.getElementById("prodInspectLeader").value,
            warehouse_user_id: document.getElementById("prodInspectWarehouse").value,
            decision: document.getElementById("prodInspectDecision").value,
            signature: document.getElementById("prodInspectSign").value,
          }),
        });
        toast("验收结果已提交");
        renderSystemMain();
      },
    });
  }

  let productionDefectPreviewToken = 0;
  let productionSerialPreviewToken = 0;

  async function refreshProductionSerialRows(form) {
    const host = document.getElementById("prodSerials");
    if (!host) return;
    const item = (form.items || [])[0] || {};
    const count = Math.max(0, Math.round(Number(item.arrival_quantity || item.request_quantity || 0)));
    if (!count) {
      host.innerHTML = `<div class="empty-state">暂无待验收明细</div>`;
      return;
    }
    const token = ++productionSerialPreviewToken;
    const name = item.material_name || (form.form_type === "semifinished" ? "半成品" : "成品");
    const data = await systemApi(`/api/production/${form.form_type}/serials?name=${encodeURIComponent(name)}&count=${count}`);
    if (token !== productionSerialPreviewToken) return;
    const existing = item.data?.serial_items || [];
    const serials = existing.length === count ? existing.map(row => row.serial_no) : (data.serials || []);
    host.innerHTML = `<table class="flow-table"><thead><tr><th>编号</th><th>是否合格</th><th>异常情况</th><th>备注</th></tr></thead><tbody>${serials.map((serial, index) => {
      const current = existing[index] || {};
      const qualified = current.qualified !== false;
      const abnormal = new Set(current.abnormal_conditions || []);
      return `<tr data-prod-serial-row><td><input class="serial-no-input" data-prod-serial-no value="${escapeAttr(serial || current.serial_no || "")}"></td><td><select data-prod-serial-qualified><option value="1" ${qualified ? "selected" : ""}>合格</option><option value="0" ${!qualified ? "selected" : ""}>不合格</option></select></td><td class="abnormal-cell"><span class="hint" data-prod-no-abnormal>无异常</span><div class="abnormal-options" data-prod-abnormal-box><label><input type="checkbox" data-prod-abnormal value="外观" ${abnormal.has("外观") ? "checked" : ""}> 外观</label><label><input type="checkbox" data-prod-abnormal value="功能" ${abnormal.has("功能") ? "checked" : ""}> 功能</label><label><input type="checkbox" data-prod-abnormal value="性能" ${abnormal.has("性能") ? "checked" : ""}> 性能</label></div></td><td><input data-prod-serial-remark value="${escapeAttr(current.remark || "")}"></td></tr>`;
    }).join("")}</tbody></table>`;
    host.querySelectorAll("[data-prod-serial-qualified], [data-prod-abnormal]").forEach(input => input.addEventListener("change", () => {
      refreshProductionAbnormalVisibility();
      updateProductionQualityFromSerialRows();
    }));
    refreshProductionAbnormalVisibility();
    updateProductionQualityFromSerialRows();
  }

  function refreshProductionAbnormalVisibility() {
    document.querySelectorAll("#prodSerials [data-prod-serial-row]").forEach(row => {
      const qualified = row.querySelector("[data-prod-serial-qualified]")?.value === "1";
      const box = row.querySelector("[data-prod-abnormal-box]");
      const noAbnormal = row.querySelector("[data-prod-no-abnormal]");
      if (!box) return;
      box.style.display = qualified ? "none" : "flex";
      if (noAbnormal) noAbnormal.style.display = qualified ? "inline" : "none";
      if (qualified) row.querySelectorAll("[data-prod-abnormal]").forEach(input => { input.checked = false; });
    });
  }

  function productionSerialRowsFromHost() {
    return [...document.querySelectorAll("#prodSerials [data-prod-serial-row]")].map(row => {
      const qualified = row.querySelector("[data-prod-serial-qualified]").value === "1";
      return {
        serial_no: row.querySelector("[data-prod-serial-no]").value.trim(),
        qualified,
        abnormal_conditions: qualified ? [] : [...row.querySelectorAll("[data-prod-abnormal]:checked")].map(input => input.value),
        remark: row.querySelector("[data-prod-serial-remark]")?.value || "",
      };
    });
  }

  function updateProductionQualityFromSerialRows() {
    const rows = productionSerialRowsFromHost();
    const qualified = rows.filter(row => row.qualified).length;
    const unqualified = rows.length - qualified;
    const okByKind = kind => rows.length - rows.filter(row => !row.qualified && (row.abnormal_conditions || []).includes(kind)).length;
    const values = {
      prodAppearance: okByKind("外观"),
      prodFunction: okByKind("功能"),
      prodPerformance: okByKind("性能"),
      prodQualified: qualified,
    };
    Object.entries(values).forEach(([id, value]) => {
      const input = document.getElementById(id);
      if (input) input.value = formatInputQty(value);
    });
    const input = document.getElementById("prodUnqualified");
    if (input) input.value = formatInputQty(unqualified);
  }

  async function refreshProductionDefects(form) {
    const host = document.getElementById("prodDefects");
    if (!host) return;
    const count = Math.max(0, Math.round(Number(document.getElementById("prodUnqualified")?.value || 0)));
    if (!count) {
      host.innerHTML = `<div class="empty-state">无不合格成品</div>`;
      return;
    }
    const productName = (form.items || [])[0]?.material_name || "成品";
    const token = ++productionDefectPreviewToken;
    const data = await systemApi(`/api/production/finished-serials?product_name=${encodeURIComponent(productName)}&count=${count}`);
    if (token !== productionDefectPreviewToken) return;
    const serials = data.serials || [];
    host.innerHTML = serials.map((serial, index) => `<div class="defect-row" data-defect-row><strong>${escapeHtml(serial)}</strong><div><label><input type="checkbox" data-defect-abnormal="${index}" value="外观"> 外观</label><label><input type="checkbox" data-defect-abnormal="${index}" value="功能"> 功能</label><label><input type="checkbox" data-defect-abnormal="${index}" value="性能"> 性能</label></div></div>`).join("");
  }

  function defectRowsFromHost(hostId) {
    return [...document.querySelectorAll(`#${hostId} [data-defect-row]`)].map((row, index) => ({
      abnormal_conditions: [...row.querySelectorAll(`[data-defect-abnormal="${index}"]:checked`)].map(input => input.value),
    }));
  }

  function inboundProductionFlow(form) {
    const item = (form.items || [])[0] || {};
    const shelves = typeof state !== "undefined" && Array.isArray(state.shelves) && state.shelves.length ? state.shelves : [];
    const fetchShelves = shelves.length ? Promise.resolve(shelves) : systemApi("/api/shelves").then(data => { if (typeof state !== "undefined") state.shelves = data; return data; });
    fetchShelves.then(shelvesData => {
      window._shelfData = shelvesData;
      openModal({
        title: `办理${flowTypeLabel(form.form_type)}入库`,
        body: `<div class="form-grid">
          <div class="wide">${productionHeaderTable(form, item)}${productionComponentsTable(item)}<p class="hint">合格数量：${formatQty(item.qualified_quantity || 0)}；不合格数量：${formatQty(item.unqualified_quantity || 0)}</p></div>
          <label>核准入库数量<input id="prodInboundQty" type="number" step="1" min="0" max="${Number(item.qualified_quantity || 0)}" value="${formatInputQty(item.qualified_quantity || 0)}"></label>
          <label>入库日期${datePickerHtml("prodInboundDate", new Date().toISOString().slice(0, 10))}</label>
          ${shelfLocationSelects(shelvesData, "prod")}
          <label>库管签字<input id="prodInboundSign" value="${escapeAttr(currentUser()?.display_name || "")}"></label>
        </div>`,
        okText: "确认入库",
        onReady: () => {
          bindDatePickers(document.querySelector(".modal") || document);
          document.querySelectorAll("[data-shelf]").forEach(sel => { if (sel.tagName === "SELECT") window._updateShelfLocation(sel); });
        },
        onOk: async () => {
          await systemApi(`/api/production/${form.form_type}/${form.id}/inbound`, {
            method: "POST",
            body: JSON.stringify({
              approved_quantity: document.getElementById("prodInboundQty").value,
              inbound_date: document.getElementById("prodInboundDate").value,
              shelf_id: document.querySelector(`[data-shelf="prod"]`).value,
              layer_number: document.querySelector(`[data-layer="prod"]`).value,
              zone_name: document.querySelector(`[data-zone="prod"]`).value,
              signature: document.getElementById("prodInboundSign").value,
            }),
          });
          toast("生产验收入库完成");
          await loadBootstrap();
        },
      });
    });
  }

  function inspectFlow(form) {
    const dept = currentUser()?.department || "";
    const leaderLockedNew = Boolean(form.leader_id);
    const selectedLeaderNew = Number(form.leader_id || defaultApprovalLeaderId("acceptance", "leader_acceptance", dept));
    const selectedWarehouseNew = Number(form.warehouse_user_id || defaultStepUserId("acceptance", "inbound", "warehouse"));
    const canUploadInspectionAttachments = hasRole("warehouse");
    const leaderText = approvalLeaderName(selectedLeaderNew) || "\u5f53\u524d\u90e8\u95e8\u672a\u914d\u7f6e\u5ba1\u6279\u9886\u5bfc";
    const leaderField = leaderLockedNew || !manualApprovalLeaderEnabled()
      ? `<label class="wide">\u4e0b\u4e00\u6b65\u5ba1\u6279\u4eba<input id="inspectLeaderText" value="${escapeAttr(leaderText)}" readonly><input id="inspectLeader" type="hidden" value="${selectedLeaderNew || ""}"></label>`
      : `<label class="wide">\u4e0b\u4e00\u6b65\u5ba1\u6279\u4eba<select id="inspectLeader">${approvalLeaderOptions("acceptance", "leader_acceptance", dept, selectedLeaderNew)}</select></label>`;
    const rowsHtml = form.items.map(item => `<div class="wide work-panel" data-inspect-row="${item.id}" data-form-id="${form.id}" data-material-id="${item.material_id || ""}" data-arrival="${Number(item.arrival_quantity || 0)}"><div class="work-body"><strong>${escapeHtml(item.material_name)}</strong><div class="form-grid"><label>\u5408\u683c\u6570\u91cf<input data-q="${item.id}" type="number" readonly value="${item.arrival_quantity || 0}"></label><label>\u4e0d\u5408\u683c\u6570\u91cf<input data-uq="${item.id}" type="number" readonly value="0"></label><label>\u5305\u88c5\u5b8c\u597d\u6570\u91cf<input data-pkg="${item.id}" type="number" value="${item.arrival_quantity || 0}"></label><label>\u5916\u89c2\u5b8c\u597d\u6570\u91cf<input data-app="${item.id}" type="number" value="${item.arrival_quantity || 0}"></label><label>\u54c1\u540d\u3001\u89c4\u683c\u4e00\u81f4\u6570\u91cf<input data-specok="${item.id}" type="number" value="${item.arrival_quantity || 0}"></label><label>\u7b26\u5408\u4f7f\u7528\u8981\u6c42\u6570\u91cf<input data-useok="${item.id}" type="number" value="${item.arrival_quantity || 0}"></label><label class="wide">\u5176\u4ed6\u95ee\u9898<textarea data-remark="${item.id}"></textarea></label>${inspectionAttachmentPanelHtml(item, form, canUploadInspectionAttachments)}</div></div></div>`).join("");
    openModal({ title: "\u586b\u5199\u9a8c\u6536\u7ed3\u679c", body: `<div class="form-grid">${leaderField}<label class="wide">\u5165\u5e93\u6d41\u7a0b\u529e\u7406\u4eba<select id="inspectWarehouse">${userOptionsForStep("acceptance", "inbound", "warehouse", "", selectedWarehouseNew)}</select></label>${leaderLockedNew ? `<p class="hint wide">\u5df2\u6709\u9a8c\u6536\u5458\u6307\u5b9a\u9886\u5bfc\u5ba1\u6279\uff0c\u5176\u4ed6\u9a8c\u6536\u5458\u4e0d\u53ef\u66f4\u6539\u3002</p>` : ""}${rowsHtml}<label>\u662f\u5426\u540c\u610f<select id="inspectDecision"><option>\u540c\u610f</option><option>\u4e0d\u540c\u610f</option></select></label><label>\u7b7e\u5b57<input id="inspectSign" value="${escapeAttr(currentUser()?.display_name || "")}"></label></div>`, okText: "\u63d0\u4ea4\u9a8c\u6536", onReady: () => {
      bindMaterialAcceptanceAutoCalc();
      bindInspectionAttachmentUploads();
      bindAttachmentFilePickLabels(document.querySelector(".modal") || document);
    }, onOk: async () => {
      const items = form.items.map(item => ({ id: item.id, qualified_quantity: document.querySelector(`[data-q="${item.id}"]`).value, unqualified_quantity: document.querySelector(`[data-uq="${item.id}"]`).value, package_ok_quantity: document.querySelector(`[data-pkg="${item.id}"]`).value, appearance_ok_quantity: document.querySelector(`[data-app="${item.id}"]`).value, name_spec_ok_quantity: document.querySelector(`[data-specok="${item.id}"]`).value, usage_ok_quantity: document.querySelector(`[data-useok="${item.id}"]`).value, remark: document.querySelector(`[data-remark="${item.id}"]`).value }));
      await systemApi(`/api/acceptance/${form.id}/inspect`, { method: "POST", body: JSON.stringify({ items, leader_id: document.getElementById("inspectLeader").value, warehouse_user_id: document.getElementById("inspectWarehouse").value, decision: document.getElementById("inspectDecision").value, signature: document.getElementById("inspectSign").value }) });
      toast("\u9a8c\u6536\u7ed3\u679c\u5df2\u63d0\u4ea4"); renderSystemMain();
    }});
  }

  function bindMaterialAcceptanceAutoCalc() {
    document.querySelectorAll("[data-inspect-row]").forEach(row => {
      const itemId = row.dataset.inspectRow;
      const arrival = Number(row.dataset.arrival || 0);
      const inputs = ["pkg", "app", "specok", "useok"].map(key => row.querySelector(`[data-${key}="${itemId}"]`));
      const update = () => {
        const values = inputs.map(input => Math.max(0, Math.min(arrival, Number(input?.value || 0))));
        const qualified = Math.min(...values);
        row.querySelector(`[data-q="${itemId}"]`).value = formatInputQty(qualified);
        row.querySelector(`[data-uq="${itemId}"]`).value = formatInputQty(Math.max(0, arrival - qualified));
      };
      inputs.forEach(input => input?.addEventListener("input", update));
      update();
    });
  }

  function leaderDecision(url, form = {}) {
    const isClaim = url.includes("/claims/");
    const isBorrow = url.includes("/borrows/");
    const nextStep = isBorrow ? "borrow_outbound" : (isClaim ? "outbound" : "inbound");
    const formType = isBorrow ? "borrow" : (isClaim ? "claim" : (form.form_type || "acceptance"));
    const warehouseId = defaultStepUserId(formType, nextStep, "warehouse");
    openModal({ title: "领导审批", body: `<div class="form-grid"><label>是否同意<select id="leaderDecision"><option>同意</option><option>不同意</option></select></label><label>${isClaim || isBorrow ? "出库办理人" : "入库办理人"}<select id="leaderWarehouse">${userOptionsForStep(formType, nextStep, "warehouse", "", warehouseId)}</select></label><label>签字<input id="leaderSign" value="${escapeAttr(currentUser()?.display_name || "")}"></label><label class="wide">备注/意见<textarea id="leaderRemark"></textarea></label></div>`, okText: "提交审批", onReady: () => {
      const decision = document.getElementById("leaderDecision");
      const remark = document.getElementById("leaderRemark");
      decision.addEventListener("change", () => { remark.required = decision.value === "不同意"; remark.placeholder = remark.required ? "不同意时必须填写审批意见" : ""; });
    }, onOk: async () => {
      const decision = document.getElementById("leaderDecision").value;
      const remark = document.getElementById("leaderRemark").value.trim();
      if (decision === "不同意" && !remark) throw new Error("不同意时必须填写审批意见");
      await systemApi(url, { method: "POST", body: JSON.stringify({ decision, warehouse_user_id: document.getElementById("leaderWarehouse").value, signature: document.getElementById("leaderSign").value, remark }) });
      toast("审批已提交"); renderSystemMain();
    }});
    return;
    openModal({ title: "领导审批", body: `<label>是否同意<select id="leaderDecision"><option>同意</option><option>不同意</option></select></label><label>签字<input id="leaderSign" value="${escapeAttr(currentUser()?.display_name || "")}"></label><label>备注<textarea id="leaderRemark"></textarea></label>`, okText: "提交审批", onOk: async () => {
      await systemApi(url, { method: "POST", body: JSON.stringify({ decision: document.getElementById("leaderDecision").value, signature: document.getElementById("leaderSign").value, remark: document.getElementById("leaderRemark").value }) });
      toast("审批已提交"); renderSystemMain();
    }});
  }

  function inboundFlow(form) {
    const shelves = typeof state !== "undefined" && Array.isArray(state.shelves) && state.shelves.length ? state.shelves : [];
    const fetchShelves = shelves.length ? Promise.resolve(shelves) : systemApi("/api/shelves").then(data => { if (typeof state !== "undefined") state.shelves = data; return data; });
    fetchShelves.then(shelvesData => {
      window._shelfData = shelvesData;
      openModal({ title: "办理入库", body: `<div class="form-grid">${form.items.map(item => `<div class="wide work-panel"><div class="work-body"><strong>${escapeHtml(item.material_name)}</strong><label>核准入库数量<input data-in="${item.id}" type="number" value="${item.qualified_quantity || item.arrival_quantity || 0}"></label><div class="form-grid">${shelfLocationSelects(shelvesData, item.id)}</div></div></div>`).join("")}<label class="wide">库管签字<input id="inSign" value="${escapeAttr(currentUser()?.display_name || "")}"></label></div>`, okText: "确认入库", onReady: () => {
        document.querySelectorAll("[data-shelf]").forEach(sel => { if (sel.tagName === "SELECT") window._updateShelfLocation(sel); });
      }, onOk: async () => {
        const items = form.items.map(item => ({ id: item.id, approved_quantity: document.querySelector(`[data-in="${item.id}"]`).value, shelf_id: document.querySelector(`[data-shelf="${item.id}"]`).value, layer_number: document.querySelector(`[data-layer="${item.id}"]`).value, zone_name: document.querySelector(`[data-zone="${item.id}"]`).value }));
        await systemApi(`/api/acceptance/${form.id}/inbound`, { method: "POST", body: JSON.stringify({ items, signature: document.getElementById("inSign").value }) });
        toast("入库完成，料卡已同步"); await loadBootstrap();
      }});
    });
  }

  function inboundFlowV2(form) {
    const shelves = typeof state !== "undefined" && Array.isArray(state.shelves) && state.shelves.length ? state.shelves : [];
    const fetchShelves = shelves.length ? Promise.resolve(shelves) : systemApi("/api/shelves").then(data => { if (typeof state !== "undefined") state.shelves = data; return data; });
    fetchShelves.then(shelvesData => {
      window._shelfData = shelvesData;
      const singleEnabled = Boolean(system.boot?.workflow_settings?.single_item_inbound_enabled);
      const collectItem = item => ({
        id: item.id,
        approved_quantity: document.querySelector(`[data-in="${item.id}"]`).value,
        shelf_id: document.querySelector(`[data-shelf="${item.id}"]`).value,
        layer_number: document.querySelector(`[data-layer="${item.id}"]`).value,
        zone_name: document.querySelector(`[data-zone="${item.id}"]`).value,
      });
      const markInboundRowsDone = items => {
        (items || []).forEach(item => {
          const id = Number(item.id);
          const original = (form.items || []).find(row => Number(row.id) === id);
          if (original) original.data = { ...(original.data || {}), inbound_done: true };
          document.querySelector(`[data-in="${id}"]`)?.setAttribute("disabled", "disabled");
          document.querySelector(`[data-shelf="${id}"]`)?.setAttribute("disabled", "disabled");
          document.querySelector(`[data-layer="${id}"]`)?.setAttribute("disabled", "disabled");
          document.querySelector(`[data-zone="${id}"]`)?.setAttribute("disabled", "disabled");
          document.querySelector(`[data-single-inbound="${id}"]`)?.setAttribute("disabled", "disabled");
          const panel = document.querySelector(`[data-inbound-card="${id}"]`);
          if (panel && !panel.querySelector("[data-inbound-done-badge]")) {
            panel.querySelector("[data-inbound-title]")?.insertAdjacentHTML("beforeend", ` <span class="badge" data-inbound-done-badge>已入库</span>`);
          }
        });
      };
      const submitInbound = async (items, options = {}) => {
        await systemApi(`/api/acceptance/${form.id}/inbound`, { method: "POST", body: JSON.stringify({ items, signature: document.getElementById("inSign").value }) });
        toast(items.length === 1 ? "该物料已入库" : "入库完成，料卡已同步");
        system.boot = null;
        await loadSystemBoot();
        if (options.stay) {
          markInboundRowsDone(items);
          return;
        }
        closeModal();
        renderSystemMain();
      };
      openModal({ title: "办理入库", body: `<div class="form-grid">${form.items.map(item => {
        const done = Boolean(item.data?.inbound_done);
        return `<div class="wide work-panel" data-inbound-card="${item.id}"><div class="work-body"><strong data-inbound-title>${escapeHtml(item.material_name)}${done ? ` <span class="badge" data-inbound-done-badge>已入库</span>` : ""}</strong><p>入库批次：<b>${escapeHtml(item.data?.inbound_batch_no || expectedInboundBatchNo(item))}</b></p><label>核准入库数量<input data-in="${item.id}" type="number" value="${item.qualified_quantity || item.arrival_quantity || 0}" ${done ? "disabled" : ""}></label><div class="form-grid">${shelfLocationSelects(shelvesData, item.id)}</div>${singleEnabled ? `<div class="inbound-single-actions"><button type="button" class="good" data-single-inbound="${item.id}" ${done ? "disabled" : ""}>单独入库</button></div>` : ""}</div></div>`;
      }).join("")}<label class="wide">库管签字<input id="inSign" value="${escapeAttr(currentUser()?.display_name || "")}"></label></div>`, okText: singleEnabled ? "一键入库" : "确认入库", onReady: () => {
        document.querySelectorAll("[data-shelf]").forEach(sel => { if (sel.tagName === "SELECT") window._updateShelfLocation(sel); });
        document.querySelectorAll("[data-single-inbound]").forEach(button => button.addEventListener("click", async () => {
          const item = (form.items || []).find(row => Number(row.id) === Number(button.dataset.singleInbound));
          if (!item) return;
          button.disabled = true;
          try {
            await submitInbound([collectItem(item)], { stay: true });
          } catch (error) {
            button.disabled = false;
            toast(`单独入库失败：${error.message}`);
          }
        }));
      }, onOk: async () => {
        const items = form.items.filter(item => !item.data?.inbound_done).map(collectItem);
        await submitInbound(items);
      }});
    });
  }


  function outboundFlow(form) {
    openModal({ title: "办理出库", body: `<div class="form-grid">${form.items.map(item => `<label>${escapeHtml(item.material_name)} 出库数量<input data-out="${item.id}" type="number" value="${item.request_quantity || 0}"></label>`).join("")}<label>库管签字<input id="outSign" value="${escapeAttr(currentUser()?.display_name || "")}"></label></div>`, okText: "确认出库", onOk: async () => {
      const items = form.items.map(item => ({ id: item.id, outbound_quantity: document.querySelector(`[data-out="${item.id}"]`).value }));
      await systemApi(`/api/claims/${form.id}/outbound`, { method: "POST", body: JSON.stringify({ items, signature: document.getElementById("outSign").value }) });
      toast("出库完成，料卡已同步"); await loadBootstrap();
    }});
  }

  function outboundFlowV2(form) {
    openModal({ title: "办理出库", body: `<div class="form-grid">${form.items.map(item => `<div class="wide work-panel"><div class="work-body"><h3>${escapeHtml(item.material_code)} ${escapeHtml(item.material_name)} ${ClaimSourceUI.sourceBadge(item.stock_source)}</h3><p>申领数量：${formatQty(item.request_quantity)} ${escapeHtml(item.unit || "")}；该来源可用库存：${formatQty(item.current_stock_quantity || 0)} ${escapeHtml(item.unit || "")}</p><label>出库数量<input data-out="${item.id}" type="number" step="1" value="${item.request_quantity || 0}"></label>${batchPickTable(item)}</div></div>`).join("")}<label>库管签字<input id="outSign" value="${escapeAttr(currentUser()?.display_name || "")}"></label></div>`, okText: "确认出库", onOk: async () => {
      const items = form.items.map(item => ({
        id: item.id,
        outbound_quantity: document.querySelector(`[data-out="${item.id}"]`).value,
        batches: [...document.querySelectorAll(`[data-batch-item="${item.id}"]`)].map(input => ({ batch_id: input.dataset.batchId, quantity: input.value })),
      }));
      await systemApi(`/api/claims/${form.id}/outbound`, { method: "POST", body: JSON.stringify({ items, signature: document.getElementById("outSign").value }) });
      toast("出库完成，料卡已同步"); await loadBootstrap();
    }});
  }

  function borrowOutboundFlow(form) {
    openModal({ title: "办理借用出库", body: `<div class="form-grid">${(form.items || []).map(item => `<div class="wide work-panel"><div class="work-body"><h3>${escapeHtml(item.material_code || "")} ${escapeHtml(item.material_name || "")} ${ClaimSourceUI.sourceBadge(item.stock_source)}</h3><p>借用数量：${formatQty(item.request_quantity || 0)} ${escapeHtml(item.unit || "")}</p><label>出库数量<input data-borrow-out="${item.id}" type="number" step="1" min="0" value="${formatInputQty(item.request_quantity || 0)}"></label>${(item.data?.borrow_item_type || "material") === "material" ? batchPickTable(item) : ""}</div></div>`).join("")}<label>出库日期${datePickerHtml("borrowOutDate", new Date().toISOString().slice(0, 10))}</label><label>库管签字<input id="borrowOutSign" value="${escapeAttr(currentUser()?.display_name || "")}"></label></div>`, okText: "确认出库", onReady: () => bindDatePickers(document.querySelector(".modal") || document), onOk: async () => {
      const items = (form.items || []).map(item => ({
        id: item.id,
        outbound_quantity: document.querySelector(`[data-borrow-out="${item.id}"]`).value,
        batches: [...document.querySelectorAll(`[data-batch-item="${item.id}"]`)].map(input => ({ batch_id: input.dataset.batchId, quantity: input.value })),
      }));
      await systemApi(`/api/borrows/${form.id}/outbound`, { method: "POST", body: JSON.stringify({ items, outbound_date: document.getElementById("borrowOutDate").value, signature: document.getElementById("borrowOutSign").value }) });
      toast("借用出库完成");
      await loadBootstrap();
    }});
  }

  function borrowReturnInboundFlow(form) {
    const item = (form.items || [])[0] || {};
    const isMaterial = (item.data?.borrow_item_type || (item.material_id ? "material" : "")) === "material";
    const formData = (typeof form.data_json === 'string' ? JSON.parse(form.data_json) : (form.data_json || item.data || {}));
    const status = formData.status || '';
    const remarks = formData.remarks || '';
    const hasChanges = formData.has_changes || '';
    const changeType = formData.change_type || '';
    const versionAfter = formData.version_after || '';
    const changeDetail = formData.change_detail || '';
    const normalUse = formData.normal_use || '';

    const statusBadge = status === '报废' ? '<span style="color:#e74c3c;font-weight:bold">报废</span>' :
                        status === '异常' ? '<span style="color:#e67e22;font-weight:bold">异常</span>' :
                        status ? '<span style="color:#27ae60;font-weight:bold">完好</span>' : '';

    let enhancedInfo = '';
    if (status) enhancedInfo += `<p>归还状态：${statusBadge}</p>`;
    if (remarks) enhancedInfo += `<p>备注说明：${escapeHtml(remarks)}</p>`;
    if (hasChanges === '是') {
      enhancedInfo += `<p>变更类型：${escapeHtml(changeType)}</p>`;
      if (changeType === '软件') enhancedInfo += `<p>变更后版本号：${escapeHtml(versionAfter)}</p>`;
      if (changeType === '硬件') enhancedInfo += `<p>硬件变更详情：${escapeHtml(changeDetail)}</p>`;
      enhancedInfo += `<p>是否正常使用：${normalUse === '否' ? '<span style="color:#e67e22">异常</span>' : '正常'}</p>`;
    }

    const shelves = typeof state !== "undefined" && Array.isArray(state.shelves) && state.shelves.length ? state.shelves : [];
    const fetchShelves = shelves.length ? Promise.resolve(shelves) : systemApi("/api/shelves").then(data => { if (typeof state !== "undefined") state.shelves = data; return data; });
    fetchShelves.then(shelvesData => {
      window._shelfData = shelvesData;
      openModal({ title: "借用归还验收入库", body: `<div class="form-grid">
        <div class="wide"><strong>${escapeHtml(item.material_name || "")}</strong> ${ClaimSourceUI.sourceBadge(item.stock_source)}<p>归还数量：${formatQty(item.request_quantity || 0)} ${escapeHtml(item.unit || "")}</p>
        ${enhancedInfo}</div>
        <label>验收入库数量<input id="borrowReturnQty" type="number" step="1" min="0" value="${formatInputQty(item.request_quantity || 0)}"></label>
        <label>入库日期${datePickerHtml("borrowReturnInboundDate", new Date().toISOString().slice(0, 10))}</label>
        ${isMaterial ? shelfLocationSelects(shelvesData, "borrowReturn") : ""}
        <label>库管签字<input id="borrowReturnSign" value="${escapeAttr(currentUser()?.display_name || "")}"></label>
        <div class="wide" style="display:flex;gap:8px;margin-top:12px">
          <button id="approveInbound" class="primary" style="background:#27ae60;flex:1">批准入库</button>
          <button id="rejectInbound" class="danger" style="flex:1">拒绝</button>
        </div>
      </div>`, hideOk: true, onReady: () => {
        bindDatePickers(document.querySelector(".modal") || document);
        document.querySelectorAll("[data-shelf]").forEach(sel => { if (sel.tagName === "SELECT") window._updateShelfLocation(sel); });

        async function processDecision(decision) {
          try {
            await systemApi(`/api/borrow-returns/${form.id}/inbound`, {
              method: "POST",
              body: JSON.stringify({
                return_quantity: document.getElementById("borrowReturnQty").value,
                inbound_date: document.getElementById("borrowReturnInboundDate").value,
                shelf_id: document.querySelector(`[data-shelf="borrowReturn"]`)?.value || "",
                layer_number: document.querySelector(`[data-layer="borrowReturn"]`)?.value || "",
                zone_name: document.querySelector(`[data-zone="borrowReturn"]`)?.value || "",
                signature: document.getElementById("borrowReturnSign").value,
                decision: decision,
              }),
            });
            toast(decision === '同意' ? "借用归还入库完成" : "已拒绝归还");
            closeModal();
            await loadBootstrap();
          } catch (error) {
            toast(error.message || "操作失败");
          }
        }

        document.getElementById("approveInbound").addEventListener("click", () => processDecision("同意"));
        document.getElementById("rejectInbound").addEventListener("click", () => processDecision("拒绝"));
      }});
    });
  }

  function shelfLocationSelects(shelves, itemId) {
    return window.WarehouseShelfLocation.render(shelves, itemId);
  }

  window._updateShelfLocation = function (select) {
    window.WarehouseShelfLocation.update(select, window._shelfData || []);
  };

  function batchPickTable(item) {
    const batches = item.batches || [];
    const suggested = new Map((item.suggested_batches || []).map(batch => [Number(batch.batch_id), Number(batch.quantity || 0)]));
    return `<table class="flow-table"><thead><tr><th>出库</th><th>批次</th><th>入库日期</th><th>批次库存</th><th>库龄</th></tr></thead><tbody>${batches.map(batch => `<tr><td><input data-batch-item="${item.id}" data-batch-id="${batch.id}" type="number" step="1" min="0" max="${Number(batch.quantity || 0)}" value="${formatInputQty(suggested.get(Number(batch.id)) || 0)}"></td><td>${escapeHtml(batch.batch_no || "")}</td><td>${escapeHtml(batch.received_date || "")}</td><td>${formatQty(batch.quantity)} ${escapeHtml(item.unit || "")}</td><td>${Number(batch.age_days || 0)} 天</td></tr>`).join("") || `<tr><td colspan="5">暂无可用批次</td></tr>`}</tbody></table>`;
  }
  function qualityInputs(prefix) {
    return `<div class="quality-grid">
      <label>外观合格数量<input id="${prefix}Appearance" type="number" step="1" min="0" value="1"></label>
      <label>功能合格数量<input id="${prefix}Function" type="number" step="1" min="0" value="1"></label>
      <label>性能合格数量<input id="${prefix}Performance" type="number" step="1" min="0" value="1"></label>
      <label>合格数量<input id="${prefix}Qualified" type="number" readonly value="1"></label>
      <label>不合格数量<input id="${prefix}Unqualified" type="number" readonly value="0"></label>
    </div>`;
  }

  function bindQualityAuto(prefix, qtyId, afterUpdate) {
    const ids = [`${prefix}Appearance`, `${prefix}Function`, `${prefix}Performance`, qtyId];
    const update = () => {
      const qty = Number(document.getElementById(qtyId)?.value || 0);
      const values = ids.slice(0, 3).map(id => Math.max(0, Math.min(qty || 0, Number(document.getElementById(id)?.value || 0))));
      const qualified = Math.min(...values);
      document.getElementById(`${prefix}Qualified`).value = formatInputQty(qualified);
      document.getElementById(`${prefix}Unqualified`).value = formatInputQty(Math.max(0, qty - qualified));
      if (afterUpdate) afterUpdate();
    };
    ids.forEach(id => document.getElementById(id)?.addEventListener("input", update));
    update();
  }

  async function loadProductionMaterialPool() {
    const data = await systemApi("/api/production/material-batch-pool");
    system.productionMaterialPool = data.items || [];
    return system.productionMaterialPool;
  }

  async function loadSemifinishedPool() {
    const data = await systemApi("/api/production/semifinished-pool");
    system.semifinishedPool = data.items || [];
    return system.semifinishedPool;
  }

  function materialComponentOptions(pool) {
    if (!(pool || []).length) return `<option value="">暂无已领用未消耗物料</option>`;
    return (pool || []).map(item => `<option value="${item.material_id}" data-cost="${Number(item.unit_cost || 0)}" data-available="${Number(item.available_quantity || 0)}" data-unit="${escapeAttr(item.unit || "")}">${escapeHtml(item.material_code)} · ${escapeHtml(item.name)} · 可用 ${formatQty(item.available_quantity)} ${escapeHtml(item.unit || "")}</option>`).join("");
  }

  function semifinishedComponentOptions(pool) {
    if (!(pool || []).length) return `<option value="">暂无可用半成品</option>`;
    return (pool || []).map(item => `<option value="${item.id}" data-cost="${Number(item.cost_price || 0)}" data-available="${Number(item.remaining_quantity || 0)}" data-unit="${escapeAttr(item.unit || "")}">${escapeHtml(item.name)} · ${escapeHtml(item.spec || "")} · 余量 ${formatQty(item.remaining_quantity)} ${escapeHtml(item.unit || "")}</option>`).join("");
  }

  function addMaterialComponentRow(host, pool, qtyId, costId, peerHostId = "") {
    const row = document.createElement("div");
    row.className = "component-row";
    row.dataset.qtyId = qtyId;
    row.dataset.costId = costId;
    row.dataset.peerHostId = peerHostId;
    row.innerHTML = `<label class="material-search">所用物料/批次<input data-prod-material-search placeholder="搜索编号/名称/品牌/规格/批次/申领人"><div data-result-list class="material-results"></div></label><label>名称<input data-prod-name readonly></label><label>品牌<input data-prod-brand readonly></label><label>规格<input data-prod-spec readonly></label><label>批次<input data-prod-batch-text readonly></label><label>单台用量<input data-prod-per-unit type="number" step="1" min="0" value="1"></label><label>总消耗<input data-prod-total readonly></label><span class="hint" data-prod-info></span><button type="button" data-remove>删除</button><input type="hidden" data-prod-material><input type="hidden" data-prod-batch>`;
    row.querySelector("[data-remove]").addEventListener("click", () => { row.remove(); refreshComponentRows(host.id, qtyId, costId, peerHostId); });
    const search = row.querySelector("[data-prod-material-search]");
    let timer = 0;
    search.addEventListener("input", () => {
      window.clearTimeout(timer);
      row.querySelector("[data-prod-material]").value = "";
      row.querySelector("[data-prod-batch]").value = "";
      fillComponentInfo(row, {});
      timer = window.setTimeout(() => resolveProductionMaterial(row, pool), 160);
      refreshComponentRows(host.id, qtyId, costId, peerHostId);
    });
    search.addEventListener("focus", () => {
      resolveProductionMaterial(row, pool);
    });
    search.addEventListener("keydown", event => {
      if (event.key === "Escape") hideMaterialResults(row);
    });
    document.addEventListener("click", event => {
      if (!row.contains(event.target)) hideMaterialResults(row);
    });
    row.querySelector("[data-prod-per-unit]").addEventListener("input", () => refreshComponentRows(host.id, qtyId, costId, peerHostId));
    host.appendChild(row);
    refreshComponentRows(host.id, qtyId, costId, peerHostId);
  }

  function resolveProductionMaterial(row, pool) {
    const keyword = row.querySelector("[data-prod-material-search]").value.trim().toLowerCase();
    const list = row.querySelector("[data-result-list]");
    const filtered = keyword
      ? (pool || []).filter(item => `${item.material_code || ""} ${item.name || ""} ${item.brand_model || ""} ${item.spec || ""} ${item.batch_no || ""} ${item.claim_applicant || ""}`.toLowerCase().includes(keyword))
      : (pool || []);
    if (!filtered.length) {
      hideMaterialResults(row);
      return;
    }
    list.innerHTML = filtered.map((item, index) => `
      <button class="material-option" type="button" data-prod-material-index="${index}">
        ${escapeHtml(item.material_code)} · ${escapeHtml(item.name)}
        <small>批次 ${escapeHtml(item.batch_no || "")} · 可用 ${formatQty(item.available_quantity)} ${escapeHtml(item.unit || "")} · 单价 ${money(item.unit_cost || 0)} · 申领人 ${escapeHtml(item.claim_applicant || "-")} · ${escapeHtml(item.brand_model || "")} ${escapeHtml(item.spec || "")}</small>
      </button>
    `).join("");
    positionMaterialResults(row, list, filtered.length);
    list.style.display = "block";
    list.querySelectorAll("[data-prod-material-index]").forEach(button => {
      button.addEventListener("click", () => applyProductionMaterial(row, filtered[Number(button.dataset.prodMaterialIndex)]));
    });
  }

  function applyProductionMaterial(row, item) {
    row.querySelector("[data-prod-material]").value = item.material_id || "";
    row.querySelector("[data-prod-batch]").value = item.batch_id || "";
    row.querySelector("[data-prod-material-search]").value = `${item.material_code || ""} ${item.name || ""} ${item.batch_no || ""}`.trim();
    fillComponentInfo(row, { name: item.name || "", brand: item.brand_model || "无", spec: item.spec || "", batch: item.batch_no || "" });
    row.dataset.available = Number(item.available_quantity || 0);
    row.dataset.unit = item.unit || "";
    row.dataset.cost = Number(item.unit_cost || 0);
    hideMaterialResults(row);
    refreshComponentRows(row.parentElement.id, row.dataset.qtyId || "", row.dataset.costId || "", row.dataset.peerHostId || "");
  }

  function fillComponentInfo(row, data) {
    const values = {
      name: data.name || "",
      brand: data.brand || "",
      spec: data.spec || "",
      batch: data.batch || "",
    };
    row.querySelector("[data-prod-name]").value = values.name;
    row.querySelector("[data-prod-brand]").value = values.brand;
    row.querySelector("[data-prod-spec]").value = values.spec;
    row.querySelector("[data-prod-batch-text]").value = values.batch;
  }

  function addSemifinishedComponentRow(host, pool, qtyId, costId, peerHostId = "") {
    const row = document.createElement("div");
    row.className = "component-row slim";
    row.dataset.qtyId = qtyId;
    row.dataset.costId = costId;
    row.dataset.peerHostId = peerHostId;
    row.innerHTML = `<label class="material-search">半成品<input data-prod-semi-search placeholder="搜索半成品名称/规格/制作人"><div data-result-list class="material-results"></div></label><label>名称<input data-prod-name readonly></label><label>品牌<input data-prod-brand readonly></label><label>规格<input data-prod-spec readonly></label><label>批次<input data-prod-batch-text readonly></label><label>单台用量<input data-prod-per-unit type="number" step="1" min="0" value="1"></label><label>总消耗<input data-prod-total readonly></label><span class="hint" data-prod-info></span><button type="button" data-remove>删除</button><input type="hidden" data-prod-semi>`;
    row.querySelector("[data-remove]").addEventListener("click", () => { row.remove(); refreshComponentRows(host.id, qtyId, costId, peerHostId); });
    const search = row.querySelector("[data-prod-semi-search]");
    let timer = 0;
    search.addEventListener("input", () => {
      window.clearTimeout(timer);
      row.querySelector("[data-prod-semi]").value = "";
      fillComponentInfo(row, {});
      timer = window.setTimeout(() => resolveProductionSemi(row, pool), 160);
      refreshComponentRows(host.id, qtyId, costId, peerHostId);
    });
    search.addEventListener("focus", () => resolveProductionSemi(row, pool));
    search.addEventListener("keydown", event => {
      if (event.key === "Escape") hideMaterialResults(row);
    });
    document.addEventListener("click", event => {
      if (!row.contains(event.target)) hideMaterialResults(row);
    });
    row.querySelector("[data-prod-per-unit]").addEventListener("input", () => refreshComponentRows(host.id, qtyId, costId, peerHostId));
    host.appendChild(row);
    refreshComponentRows(host.id, qtyId, costId, peerHostId);
  }

  function resolveProductionSemi(row, pool) {
    const keyword = row.querySelector("[data-prod-semi-search]").value.trim().toLowerCase();
    const list = row.querySelector("[data-result-list]");
    const filtered = keyword
      ? (pool || []).filter(item => `${item.serial_no || ""} ${item.name || ""} ${item.spec || ""} ${item.unit || ""} ${item.maker || ""}`.toLowerCase().includes(keyword))
      : (pool || []);
    if (!filtered.length) {
      hideMaterialResults(row);
      return;
    }
    list.innerHTML = filtered.map((item, index) => `
      <button class="material-option" type="button" data-prod-semi-index="${index}">
        ${escapeHtml(item.name || "")}
        <small>${escapeHtml(item.spec || "")} · 余量 ${formatQty(item.remaining_quantity || 0)} ${escapeHtml(item.unit || "")} · 成本 ${money(item.cost_price || 0)} · 制作人 ${escapeHtml(item.maker || "-")}</small>
      </button>
    `).join("");
    positionMaterialResults(row, list, filtered.length);
    list.style.display = "block";
    list.querySelectorAll("[data-prod-semi-index]").forEach(button => {
      button.addEventListener("click", () => applyProductionSemi(row, filtered[Number(button.dataset.prodSemiIndex)]));
    });
  }

  function applyProductionSemi(row, item) {
    row.querySelector("[data-prod-semi]").value = item.id || "";
    row.querySelector("[data-prod-semi-search]").value = `${item.name || ""} ${item.spec || ""}`.trim();
    fillComponentInfo(row, { name: item.name || "", brand: "无", spec: item.spec || "", batch: item.serial_no || "无" });
    row.dataset.available = Number(item.remaining_quantity || 0);
    row.dataset.unit = item.unit || "";
    row.dataset.cost = Number(item.cost_price || 0);
    hideMaterialResults(row);
    refreshComponentRows(row.parentElement.id, row.dataset.qtyId || "", row.dataset.costId || "", row.dataset.peerHostId || "");
  }

  function refreshComponentRows(hostId, qtyId, costId, peerHostId = "") {
    const qty = Number(document.getElementById(qtyId)?.value || 0);
    let perUnitCost = 0;
    [hostId, peerHostId].filter(Boolean).forEach(id => {
      document.querySelectorAll(`#${id} .component-row`).forEach(row => {
        const select = row.querySelector("select");
        const option = select?.selectedOptions?.[0];
        const perUnit = Number(row.querySelector("[data-prod-per-unit]")?.value || 0);
        const total = perUnit * qty;
        const available = Number((select ? option?.dataset.available : row.dataset.available) || 0);
        const unit = (select ? option?.dataset.unit : row.dataset.unit) || "";
        const unitCost = Number((select ? option?.dataset.cost : row.dataset.cost) || 0);
        row.querySelector("[data-prod-total]").value = formatInputQty(total);
        const selected = select ? select.value : (row.querySelector("[data-prod-material]")?.value || row.querySelector("[data-prod-semi]")?.value);
        row.querySelector("[data-prod-info]").textContent = selected ? `可用 ${formatQty(available)} ${unit}；单价 ${money(unitCost)}` : "";
        row.querySelector("[data-prod-info]").style.color = total > available + 1e-9 ? "#b45309" : "";
        perUnitCost += perUnit * unitCost;
      });
    });
    const cost = document.getElementById(costId);
    if (cost) cost.textContent = `成本价：${money(perUnitCost)} / 台`;
  }

  function materialComponentsFrom(hostId) {
    return [...document.querySelectorAll(`#${hostId} [data-prod-material]`)].map(input => ({
      material_id: input.value,
      batch_id: input.closest(".component-row").querySelector("[data-prod-batch]")?.value || "",
      per_unit_quantity: input.closest(".component-row").querySelector("[data-prod-per-unit]").value,
    })).filter(item => Number(item.material_id || 0) && Number(item.per_unit_quantity || 0) > 0);
  }

  function semifinishedComponentsFrom(hostId) {
    return [...document.querySelectorAll(`#${hostId} [data-prod-semi]`)].map(select => ({
      semifinished_inventory_id: select.value,
      per_unit_quantity: select.closest(".component-row").querySelector("[data-prod-per-unit]").value,
    })).filter(item => Number(item.semifinished_inventory_id || 0) && Number(item.per_unit_quantity || 0) > 0);
  }

  function semifinishedPayload() {
    return {
      title: document.getElementById("semiTitle")?.value || "",
      name: document.getElementById("semiName")?.value || "",
      spec: document.getElementById("semiSpec")?.value || "",
      acceptance_quantity: document.getElementById("semiQty")?.value || "",
      unit: document.getElementById("semiUnit")?.value || "",
      maker_id: document.getElementById("semiMakerId")?.value || "",
      acceptance_date: document.getElementById("semiDate")?.value || "",
      project_code: document.getElementById("semiProjectCode")?.value || "",
      components: materialComponentsFrom("semiMaterials"),
    };
  }

  function finishedPayload() {
    return {
      title: document.getElementById("finTitle")?.value || "",
      product_name: document.getElementById("finName")?.value || "",
      spec: document.getElementById("finSpec")?.value || "",
      acceptance_quantity: document.getElementById("finQty")?.value || "",
      unit: document.getElementById("finUnit")?.value || "",
      maker_id: document.getElementById("finMakerId")?.value || "",
      acceptance_date: document.getElementById("finDate")?.value || "",
      project_code: document.getElementById("finProjectCode")?.value || "",
      material_components: materialComponentsFrom("finMaterials"),
      semifinished_components: semifinishedComponentsFrom("finSemis"),
    };
  }

  async function submitSemifinished() {
    const submitButton = document.getElementById("submitSemi");
    if (submitButton) submitButton.disabled = true;
    const payload = { ...semifinishedPayload(), validator_ids: system.selectedValidators };
    try {
      await systemApi("/api/production/semifinished/workflows", { method: "POST", body: JSON.stringify(payload) });
      toast("半成品验收流程已提交");
      system.boot = null;
      await loadSystemBoot();
      renderSystemMain();
    } catch (error) {
      if (submitButton) submitButton.disabled = false;
      toast(`半成品验收流程提交失败：${error.message}`);
    }
  }

  async function submitFinished() {
    const submitButton = document.getElementById("submitFin");
    if (submitButton) submitButton.disabled = true;
    const payload = { ...finishedPayload(), validator_ids: system.selectedValidators };
    try {
      await systemApi("/api/production/finished/workflows", { method: "POST", body: JSON.stringify(payload) });
      toast("成品验收流程已提交");
      system.boot = null;
      await loadSystemBoot();
      renderSystemMain();
    } catch (error) {
      if (submitButton) submitButton.disabled = false;
      toast(`成品验收流程提交失败：${error.message}`);
    }
  }

  async function loadSemifinishedList() {
    const host = document.getElementById("semiList");
    if (!host) return;
    const data = await systemApi("/api/production/semifinished");
    const inventory = data.inventory || [];
    const defective = data.defective_goods || [];
    const acceptances = data.acceptances || [];
    host.innerHTML = semifinishedInventoryHtml(inventory, defective) + semifinishedAcceptanceHistoryHtml(acceptances);
  }

  async function loadFinishedList() {
    const host = document.getElementById("finList");
    if (!host) return;
    const data = await systemApi("/api/production/finished");
    const qualified = data.qualified_inventory || [];
    const defective = data.defective_goods || [];
    const acceptances = data.acceptances || [];
    host.innerHTML = finishedInventoryHtml(qualified, defective) + finishedAcceptanceHistoryHtml(acceptances);
  }

  async function loadSemiTab(tab) {
    system.currentSemiTab = tab;
    const keyword = (document.getElementById("semiInventoryKeyword")?.value || "").trim().toLowerCase();
    const backendProjectCodeFilter = keyword;
    if (tab === "scrapped") {
      const data = await systemApi("/api/production/scrapped-semifinished");
      system.scrappedSemifinishedRows = filterRowsByKeyword(data.items || [], keyword, ["serial_no", "name", "spec", "created_at", "scrap_reason"]);
      renderSemiTabPage();
    } else {
      let url = "/api/production/semifinished";
      if (backendProjectCodeFilter) url += "?project_code=" + encodeURIComponent(backendProjectCodeFilter);
      const data = await systemApi(url);
      if (tab === "qualified") {
        const inventory = filterRowsByKeyword(data.inventory || [], keyword, ["serial_no", "name", "spec", "unit", "shelf_name", "zone_name", "acceptance_date"]);
        system.semifinishedInventoryRows = await checkWarningsForItems(inventory, 'semifinished');
      } else {
        system.defectiveSemifinishedRows = filterRowsByKeyword(data.defective_goods || [], keyword, ["name", "spec", "serial_no", "created_at", "abnormal_conditions"]);
      }
      renderSemiTabPage();
    }
  }

  async function loadSemifinishedInventoryModule() {
    return loadSemiTab(system.currentSemiTab);
  }

  function renderSemiTabPage() {
    const host = document.getElementById("semiInventoryList");
    if (!host) return;
    const tab = system.currentSemiTab;
    if (tab === "qualified") {
      const rows = system.semifinishedInventoryRows || [];
      const visible = pageRows("semiInventory", rows);
      host.innerHTML = semifinishedQualifiedTableHtml(visible) + paginationHtml("semiInventory", rows);
    } else if (tab === "defective") {
      const rows = system.defectiveSemifinishedRows || [];
      const visible = pageRows("defectiveSemi", rows);
      host.innerHTML = semifinishedDefectiveTableHtml(visible) + paginationHtml("defectiveSemi", rows);
    } else {
      const rows = system.scrappedSemifinishedRows || [];
      const visible = pageRows("scrappedSemi", rows);
      host.innerHTML = scrappedSemifinishedHtml(visible) + paginationHtml("scrappedSemi", rows);
    }
    bindProductionInventoryDeleteActions();
    bindPagination(host, "semiInventory", renderSemiTabPage);
    bindPagination(host, "defectiveSemi", renderSemiTabPage);
    bindPagination(host, "scrappedSemi", renderSemiTabPage);
  }

  function renderSemifinishedInventoryPage() {
    renderSemiTabPage();
  }

  async function loadFinishedTab(tab) {
    system.currentFinishedTab = tab;
    const keyword = (document.getElementById("finishedInventoryKeyword")?.value || "").trim().toLowerCase();
    const backendProjectCodeFilter = keyword;
    if (tab === "scrapped") {
      const data = await systemApi("/api/production/scrapped-finished");
      system.scrappedFinishedRows = filterRowsByKeyword(data.items || [], keyword, ["serial_no", "product_name", "spec", "created_at", "scrap_reason"]);
      renderFinishedTabPage();
    } else {
      let url = "/api/production/finished";
      if (backendProjectCodeFilter) url += "?project_code=" + encodeURIComponent(backendProjectCodeFilter);
      const data = await systemApi(url);
      if (tab === "qualified") {
        const qualified = filterRowsByKeyword(data.qualified_inventory || [], keyword, ["serial_no", "product_name", "spec", "unit", "shelf_name", "zone_name", "acceptance_date"]);
        system.finishedInventoryRows = await checkWarningsForItems(qualified, 'finished');
      } else {
        system.defectiveFinishedRows = filterRowsByKeyword(data.defective_goods || [], keyword, ["product_name", "spec", "serial_no", "created_at", "abnormal_conditions"]);
      }
      renderFinishedTabPage();
    }
  }

  async function loadFinishedInventoryModule() {
    return loadFinishedTab(system.currentFinishedTab);
  }

  function renderFinishedTabPage() {
    const host = document.getElementById("finishedInventoryList");
    if (!host) return;
    const tab = system.currentFinishedTab;
    if (tab === "qualified") {
      const rows = system.finishedInventoryRows || [];
      const visible = pageRows("finishedInventory", rows);
      host.innerHTML = finishedQualifiedTableHtml(visible) + paginationHtml("finishedInventory", rows);
    } else if (tab === "defective") {
      const rows = system.defectiveFinishedRows || [];
      const visible = pageRows("defectiveFinished", rows);
      host.innerHTML = finishedDefectiveTableHtml(visible) + paginationHtml("defectiveFinished", rows);
    } else {
      const rows = system.scrappedFinishedRows || [];
      const visible = pageRows("scrappedFinished", rows);
      host.innerHTML = scrappedFinishedHtml(visible) + paginationHtml("scrappedFinished", rows);
    }
    bindProductionInventoryDeleteActions();
    bindPagination(host, "finishedInventory", renderFinishedTabPage);
    bindPagination(host, "defectiveFinished", renderFinishedTabPage);
    bindPagination(host, "scrappedFinished", renderFinishedTabPage);
  }

  function renderFinishedInventoryPage() {
    renderFinishedTabPage();
  }

  function semifinishedQualifiedTableHtml(rows) {
    const canWrite = hasPerm("write_semifinished_inventory");
    const hasActions = canWrite;
    return `<h3>半成品库</h3><table class="flow-table"><thead><tr><th>编号</th><th>名称</th><th>规格</th><th>数量</th><th>已借用</th><th>用于成品</th><th>余量</th><th>成本价</th><th>位置</th><th>日期</th>${hasActions ? `<th>操作</th>` : ""}</tr></thead><tbody>${(rows || []).map(item => `<tr><td>${escapeHtml(item.serial_no || "")}</td><td>${escapeHtml(item.name)}${item._has_warning ? ` <div class="item-warning"><span class="item-warning-icon">⚠️</span> ${escapeHtml(item._change_type || '硬件')}异常</div>` : ''}</td><td>${escapeHtml(item.spec || "")}</td><td>${formatQty(item.quantity)} ${escapeHtml(item.unit || "")}</td><td>${formatQty(item.borrowed_quantity || 0)}</td><td>${formatQty(item.used_quantity || 0)}</td><td>${formatQty(item.remaining_quantity || 0)}</td><td>${money(item.cost_price || 0)}</td><td>${escapeHtml(locationText(item))}</td><td>${escapeHtml(item.acceptance_date || "")}</td>${hasActions ? `<td class="mini-actions"><button type="button" onclick="viewItemHistory('semifinished',${item.id})">查看详情</button>${canWrite ? `<button type="button" data-edit-semi-inventory="${item.id}">修改</button><button type="button" class="danger" data-delete-semi-inventory="${item.id}">删除</button>` : ""}</td>` : ""}</tr>`).join("") || `<tr><td colspan="${hasActions ? 11 : 10}">暂无半成品库存</td></tr>`}</tbody></table>`;
  }

  function semifinishedDefectiveTableHtml(rows) {
    const canWrite = hasPerm("write_semifinished_inventory");
    const hasActions = canWrite;
    return `<h3>不合格半成品库</h3><table class="flow-table"><thead><tr><th>编号</th><th>半成品</th><th>规格</th><th>异常情况</th><th>时间</th>${hasActions ? `<th>操作</th>` : ""}</tr></thead><tbody>${(rows || []).map(item => `<tr><td>${escapeHtml(item.serial_no || "")}</td><td>${escapeHtml(item.name || "")}</td><td>${escapeHtml(item.spec || "")}</td><td>${escapeHtml((item.abnormal_conditions || []).join("、"))}</td><td>${escapeHtml(item.created_at || "")}</td>${hasActions ? `<td class="mini-actions"><button type="button" onclick="viewItemHistory('defective_semifinished',${item.id})">查看详情</button>${canWrite ? `<button type="button" data-edit-defective-semi="${item.id}">修改</button><button type="button" class="danger" data-delete-defective-semi="${item.id}">删除</button>` : ""}</td>` : ""}</tr>`).join("") || `<tr><td colspan="${hasActions ? 6 : 5}">暂无不合格半成品</td></tr>`}</tbody></table>`;
  }

  function finishedQualifiedTableHtml(rows) {
    const canWrite = hasPerm("write_finished_inventory");
    const hasActions = canWrite;
    return `<h3>合格成品库</h3><table class="flow-table"><thead><tr><th>编号</th><th>成品</th><th>规格</th><th>数量</th><th>已借用</th><th>余量</th><th>成本价</th><th>位置</th><th>日期</th>${hasActions ? `<th>操作</th>` : ""}</tr></thead><tbody>${(rows || []).map(item => `<tr><td>${escapeHtml(item.serial_no || "")}</td><td>${escapeHtml(item.product_name)}${item._has_warning ? ` <div class="item-warning"><span class="item-warning-icon">⚠️</span> ${escapeHtml(item._change_type || '硬件')}异常</div>` : ''}</td><td>${escapeHtml(item.spec || "")}</td><td>${formatQty(item.quantity)} ${escapeHtml(item.unit || "")}</td><td>${formatQty(item.borrowed_quantity || 0)}</td><td>${formatQty(item.remaining_quantity ?? (Number(item.quantity || 0) - Number(item.borrowed_quantity || 0)))}</td><td>${money(item.cost_price || 0)}</td><td>${escapeHtml(locationText(item))}</td><td>${escapeHtml(item.acceptance_date || "")}</td>${hasActions ? `<td class="mini-actions"><button type="button" onclick="viewItemHistory('finished',${item.id})">查看详情</button>${canWrite ? `<button type="button" data-edit-finished-inventory="${item.id}">修改</button><button type="button" class="danger" data-delete-finished-inventory="${item.id}">删除</button>` : ""}</td>` : ""}</tr>`).join("") || `<tr><td colspan="${hasActions ? 10 : 9}">暂无合格成品库存</td></tr>`}</tbody></table>`;
  }

  function finishedDefectiveTableHtml(rows) {
    const canWrite = hasPerm("write_finished_inventory");
    const hasActions = canWrite;
    return `<h3>不合格成品库</h3><table class="flow-table"><thead><tr><th>流水号</th><th>成品</th><th>规格</th><th>异常情况</th><th>时间</th>${hasActions ? `<th>操作</th>` : ""}</tr></thead><tbody>${(rows || []).map(item => `<tr><td>${escapeHtml(item.serial_no)}</td><td>${escapeHtml(item.product_name)}</td><td>${escapeHtml(item.spec || "")}</td><td>${escapeHtml((item.abnormal_conditions || []).join("、"))}</td><td>${escapeHtml(item.created_at || "")}</td>${hasActions ? `<td class="mini-actions"><button type="button" onclick="viewItemHistory('defective_finished',${item.id})">查看详情</button>${canWrite ? `<button type="button" data-edit-defective-finished="${item.id}">修改</button><button type="button" class="danger" data-delete-defective-finished="${item.id}">删除</button>` : ""}</td>` : ""}</tr>`).join("") || `<tr><td colspan="${hasActions ? 6 : 5}">暂无不合格成品</td></tr>`}</tbody></table>`;
  }

  function scrappedSemifinishedHtml(rows) {
    return `<h3>报废半成品</h3><table class="flow-table"><thead><tr><th>编号</th><th>名称</th><th>规格</th><th>报废原因</th><th>报废时间</th><th>操作</th></tr></thead><tbody>${(rows || []).map(item => `<tr><td>${escapeHtml(item.serial_no || "")}</td><td>${escapeHtml(item.name || "")}</td><td>${escapeHtml(item.spec || "")}</td><td>${escapeHtml(item.scrap_reason || "")}</td><td>${escapeHtml(item.scrapped_at || item.created_at || "")}</td><td class="mini-actions"><button type="button" onclick="viewItemHistory('scrapped_semifinished',${item.id})">查看详情</button></td></tr>`).join("") || `<tr><td colspan="6">暂无报废半成品</td></tr>`}</tbody></table>`;
  }

  function scrappedFinishedHtml(rows) {
    return `<h3>报废成品</h3><table class="flow-table"><thead><tr><th>编号</th><th>成品名称</th><th>规格</th><th>报废原因</th><th>报废时间</th><th>操作</th></tr></thead><tbody>${(rows || []).map(item => `<tr><td>${escapeHtml(item.serial_no || "")}</td><td>${escapeHtml(item.product_name || item.name || "")}</td><td>${escapeHtml(item.spec || "")}</td><td>${escapeHtml(item.scrap_reason || "")}</td><td>${escapeHtml(item.scrapped_at || item.created_at || "")}</td><td class="mini-actions"><button type="button" onclick="viewItemHistory('scrapped_finished',${item.id})">查看详情</button></td></tr>`).join("") || `<tr><td colspan="6">暂无报废成品</td></tr>`}</tbody></table>`;
  }

  function bindProductionInventoryDeleteActions() {
    document.querySelectorAll("[data-edit-semi-inventory]").forEach(btn => btn.addEventListener("click", () => editProductionInventory("semi", Number(btn.dataset.editSemiInventory))));
    document.querySelectorAll("[data-edit-defective-semi]").forEach(btn => btn.addEventListener("click", () => editProductionInventory("defectiveSemi", Number(btn.dataset.editDefectiveSemi))));
    document.querySelectorAll("[data-edit-finished-inventory]").forEach(btn => btn.addEventListener("click", () => editProductionInventory("finished", Number(btn.dataset.editFinishedInventory))));
    document.querySelectorAll("[data-edit-defective-finished]").forEach(btn => btn.addEventListener("click", () => editProductionInventory("defective", Number(btn.dataset.editDefectiveFinished))));
    document.querySelectorAll("[data-delete-semi-inventory]").forEach(btn => btn.addEventListener("click", () => deleteProductionInventory("semi", Number(btn.dataset.deleteSemiInventory))));
    document.querySelectorAll("[data-delete-defective-semi]").forEach(btn => btn.addEventListener("click", () => deleteProductionInventory("defectiveSemi", Number(btn.dataset.deleteDefectiveSemi))));
    document.querySelectorAll("[data-delete-finished-inventory]").forEach(btn => btn.addEventListener("click", () => deleteProductionInventory("finished", Number(btn.dataset.deleteFinishedInventory))));
    document.querySelectorAll("[data-delete-defective-finished]").forEach(btn => btn.addEventListener("click", () => deleteProductionInventory("defective", Number(btn.dataset.deleteDefectiveFinished))));
  }

  function shelfSelectHtml(selectedId) {
    const shelves = typeof state !== "undefined" && Array.isArray(state.shelves) ? state.shelves : [];
    return `<option value="">未指定</option>${shelves.map(shelf => `<option value="${shelf.id}" ${Number(selectedId || 0) === Number(shelf.id) ? "selected" : ""}>${escapeHtml(shelf.name)}</option>`).join("")}`;
  }

  function canModifyMaterial() {
    return hasPerm("edit_material");
  }

  function canAddMaterial() {
    return hasPerm("add_material");
  }

  function canDeleteMaterial() {
    return hasPerm("edit_material");
  }

  async function refreshMaterialsCache() {
    const rows = await systemApi(`/api/materials?_=${Date.now()}`);
    if (typeof state !== "undefined") state.materials = rows;
    return rows;
  }

  function prettyJson(value) {
    try {
      return JSON.stringify(value || [], null, 2);
    } catch (error) {
      return "[]";
    }
  }

  function jsonArrayFromInput(id, label) {
    const text = (document.getElementById(id)?.value || "").trim();
    if (!text) return [];
    const parsed = JSON.parse(text);
    if (!Array.isArray(parsed)) throw new Error(`${label}必须是 JSON 数组`);
    return parsed;
  }

  function materialBatchRowHtml(batch = {}) {
    return `<tr data-material-batch-row data-batch-id="${batch.id || ""}">
      <td><input data-batch-no value="${escapeAttr(batch.batch_no || "")}"></td>
      <td><input data-batch-date type="date" value="${escapeAttr(batch.received_date || "")}"></td>
      <td><input data-batch-qty type="number" step="1" value="${Number(batch.quantity || 0)}"></td>
      <td><input data-batch-price type="number" step="0.01" value="${Number(batch.unit_price || 0)}"></td>
      <td><select data-batch-shelf>${shelfSelectHtml(batch.shelf_id)}</select></td>
      <td><input data-batch-layer type="number" value="${escapeAttr(batch.layer_number || "")}"></td>
      <td><input data-batch-zone value="${escapeAttr(batch.zone_name || "")}"></td>
      <td><input data-batch-source value="${escapeAttr(batch.source_form_no || "")}"></td>
      <td><button type="button" class="danger" data-remove-material-batch>删除</button></td>
    </tr>`;
  }

  function updateMaterialBatchTotal() {
    const total = [...document.querySelectorAll("[data-material-batch-row]")].reduce((sum, row) => sum + Number(row.querySelector("[data-batch-qty]")?.value || 0), 0);
    const input = document.getElementById("editMatQuantity");
    if (input) input.value = formatInputQty(total);
  }

  function bindMaterialBatchEditor() {
    const addButton = document.getElementById("addMaterialBatch");
    if (addButton) addButton.onclick = () => {
      document.getElementById("editMaterialBatchRows")?.insertAdjacentHTML("beforeend", materialBatchRowHtml({ received_date: new Date().toISOString().slice(0, 10) }));
      bindMaterialBatchEditor();
      updateMaterialBatchTotal();
    };
    document.querySelectorAll("[data-remove-material-batch]").forEach(btn => {
      btn.onclick = () => {
        btn.closest("[data-material-batch-row]")?.remove();
        updateMaterialBatchTotal();
      };
    });
    document.querySelectorAll("[data-batch-qty]").forEach(input => { input.oninput = updateMaterialBatchTotal; });
  }

  function materialBatchPayloadFromEditor(defaultLocation) {
    return [...document.querySelectorAll("[data-material-batch-row]")].map(row => ({
      id: row.dataset.batchId ? Number(row.dataset.batchId) : undefined,
      batch_no: row.querySelector("[data-batch-no]").value.trim(),
      received_date: row.querySelector("[data-batch-date]").value,
      quantity: row.querySelector("[data-batch-qty]").value,
      unit_price: row.querySelector("[data-batch-price]").value,
      shelf_id: row.querySelector("[data-batch-shelf]").value || defaultLocation.shelf_id,
      layer_number: row.querySelector("[data-batch-layer]").value || defaultLocation.layer_number,
      zone_name: row.querySelector("[data-batch-zone]").value || defaultLocation.zone_name,
      source_form_no: row.querySelector("[data-batch-source]").value,
    })).filter(batch => batch.id || batch.batch_no || Number(batch.quantity || 0) || batch.received_date || batch.shelf_id);
  }

  function bindMaterialQueryActions() {
    document.querySelectorAll("[data-view-material]").forEach(btn => btn.addEventListener("click", () => viewMaterialDetail(Number(btn.dataset.viewMaterial))));
    document.querySelectorAll("[data-edit-material]").forEach(btn => btn.addEventListener("click", () => editMaterialFromQuery(Number(btn.dataset.editMaterial))));
    document.querySelectorAll("[data-delete-material]").forEach(btn => btn.addEventListener("click", () => deleteMaterialFromQuery(Number(btn.dataset.deleteMaterial))));
  }

  async function addMaterialFromQuery() {
    openModal({
      title: "添加物料",
      body: `<div class="form-grid">
        <label>物料编码<input id="newMatCode" placeholder="14位数字编码"></label>
        <label>名称<input id="newMatName"></label>
        <label>品牌型号<input id="newMatBrand"></label>
        <label>技术规格<input id="newMatSpec"></label>
        <label>采购申请人<input id="newMatApplicant"></label>
        <label>单位<input id="newMatUnit" value="个"></label>
        <label>仓库码<input id="newMatWarehouseCode"></label>
        <label>大类码<input id="newMatMajorCode"></label>
        <label>中类码<input id="newMatMiddleCode"></label>
        <label>小类码<input id="newMatSmallCode"></label>
        <label>明细码<input id="newMatDetailCode"></label>
        <label>分类名称<input id="newMatCategoryName"></label>
        <label>物料类型<input id="newMatType"></label>
      </div>`,
      okText: "保存物料",
      onOk: async () => {
        const payload = {
          material_code: document.getElementById("newMatCode").value.trim(),
          name: document.getElementById("newMatName").value.trim(),
          brand_model: document.getElementById("newMatBrand").value.trim(),
          spec: document.getElementById("newMatSpec").value.trim(),
          purchase_applicant: document.getElementById("newMatApplicant").value.trim(),
          unit: document.getElementById("newMatUnit").value.trim(),
          warehouse_code: document.getElementById("newMatWarehouseCode").value.trim(),
          major_code: document.getElementById("newMatMajorCode").value.trim(),
          middle_code: document.getElementById("newMatMiddleCode").value.trim(),
          small_code: document.getElementById("newMatSmallCode").value.trim(),
          detail_code: document.getElementById("newMatDetailCode").value.trim(),
          category_name: document.getElementById("newMatCategoryName").value.trim(),
          material_type: document.getElementById("newMatType").value.trim(),
        };
        await systemApi("/api/material-master", { method: "POST", body: JSON.stringify(payload) });
        toast("物料已添加");
        await refreshMaterialsCache();
        await runMaterialQuery();
      },
    });
  }

  async function editMaterialFromQuery(materialId) {
    const material = await systemApi(`/api/materials/${materialId}`);
    const fallbackBatch = material.batches?.length ? [] : [{ quantity: material.quantity || 0, shelf_id: material.shelf_id, layer_number: material.layer_number, zone_name: material.zone_name, received_date: new Date().toISOString().slice(0, 10), batch_no: material.material_code ? `00000000${material.material_code}` : "" }];
    const batches = [...(material.batches || []), ...fallbackBatch];
    openModal({
      title: "修改物料",
      body: `<div class="form-grid">
        <label>物料编号<input id="editMatCode" value="${escapeAttr(material.material_code || "")}"></label>
        <label>名称<input id="editMatName" value="${escapeAttr(material.name || "")}"></label>
        <label>品牌型号<input id="editMatBrand" value="${escapeAttr(material.brand_model || "")}"></label>
        <label>技术规格<input id="editMatSpec" value="${escapeAttr(material.spec || "")}"></label>
        <label>采购申请人<input id="editMatApplicant" value="${escapeAttr(material.purchase_applicant || "")}"></label>
        <label>单位<input id="editMatUnit" value="${escapeAttr(material.unit || "")}"></label>
        <label>库存合计<input id="editMatQuantity" type="number" step="1" value="${Number(material.quantity || 0)}" readonly></label>
        <label>仓库码<input id="editMatWarehouseCode" value="${escapeAttr(material.warehouse_code || "")}"></label>
        <label>大类码<input id="editMatMajorCode" value="${escapeAttr(material.major_code || material.category || "")}"></label>
        <label>中类码<input id="editMatMiddleCode" value="${escapeAttr(material.middle_code || "")}"></label>
        <label>小类码<input id="editMatSmallCode" value="${escapeAttr(material.small_code || "")}"></label>
        <label>明细码<input id="editMatDetailCode" value="${escapeAttr(material.detail_code || "")}"></label>
        <label>分类名称<input id="editMatCategoryName" value="${escapeAttr(material.category_name || "")}"></label>
        <label>物料类型<input id="editMatType" value="${escapeAttr(material.material_type || "")}"></label>
        <label>图标<input id="editMatIcon" value="${escapeAttr(material.icon || "")}"></label>
        <label>默认货架<select id="editMatShelf">${shelfSelectHtml(material.shelf_id)}</select></label>
        <label>默认层<input id="editMatLayer" type="number" value="${escapeAttr(material.layer_number || "")}"></label>
        <label>默认分区<input id="editMatZone" value="${escapeAttr(material.zone_name || "")}"></label>
        <label>默认格位<input id="editMatSlot" type="number" value="${escapeAttr(material.slot_index || 0)}"></label>
        <div class="wide" style="overflow:auto;">
          <div class="flow-tools"><button type="button" id="addMaterialBatch">新增批次</button></div>
          <table class="flow-table"><thead><tr><th>批次号</th><th>日期</th><th>数量</th><th>单价</th><th>货架</th><th>层</th><th>分区</th><th>来源</th><th>操作</th></tr></thead><tbody id="editMaterialBatchRows">${batches.map(materialBatchRowHtml).join("")}</tbody></table>
        </div>
      </div>`,
      okText: "保存修改",
      onReady: () => {
        bindMaterialBatchEditor();
        updateMaterialBatchTotal();
      },
      onOk: async () => {
        const defaultLocation = {
          shelf_id: document.getElementById("editMatShelf").value,
          layer_number: document.getElementById("editMatLayer").value,
          zone_name: document.getElementById("editMatZone").value,
        };
        await systemApi(`/api/materials/${materialId}`, {
          method: "PUT",
          body: JSON.stringify({
            material_code: document.getElementById("editMatCode").value,
            name: document.getElementById("editMatName").value,
            brand_model: document.getElementById("editMatBrand").value,
            spec: document.getElementById("editMatSpec").value,
            purchase_applicant: document.getElementById("editMatApplicant").value,
            unit: document.getElementById("editMatUnit").value,
            warehouse_code: document.getElementById("editMatWarehouseCode").value,
            major_code: document.getElementById("editMatMajorCode").value,
            middle_code: document.getElementById("editMatMiddleCode").value,
            small_code: document.getElementById("editMatSmallCode").value,
            detail_code: document.getElementById("editMatDetailCode").value,
            category_name: document.getElementById("editMatCategoryName").value,
            material_type: document.getElementById("editMatType").value,
            icon: document.getElementById("editMatIcon").value,
            shelf_id: defaultLocation.shelf_id,
            layer_number: defaultLocation.layer_number,
            zone_name: defaultLocation.zone_name,
            slot_index: document.getElementById("editMatSlot").value,
            batches: materialBatchPayloadFromEditor(defaultLocation),
          }),
        });
        toast("已保存");
        await refreshMaterialsCache();
        await runMaterialQuery();
      },
    });
  }

  async function deleteMaterialFromQuery(materialId) {
    const material = system.materialQueryRows.find(item => Number(item.id) === materialId) || {};
    if (!confirm(`确认删除物料 ${material.material_code || ""} ${material.name || ""}？`)) return;
    if (!confirm("删除后会移除该物料库存、批次和位置信息，请再次确认。")) return;
    await systemApi(`/api/materials/${materialId}`, { method: "DELETE" });
    toast("已删除");
    await refreshMaterialsCache();
    await runMaterialQuery();
  }

  async function editProductionInventory(kind, id) {
    const row = kind === "semi"
      ? system.semifinishedInventoryRows.find(item => Number(item.id) === id)
      : kind === "finished"
        ? system.finishedInventoryRows.find(item => Number(item.id) === id)
        : kind === "defectiveSemi"
          ? system.defectiveSemifinishedRows.find(item => Number(item.id) === id)
          : system.defectiveFinishedRows.find(item => Number(item.id) === id);
    if (!row) return;
    if (kind === "semi") {
      openModal({ title: "修改半成品库存", body: `<div class="form-grid">
        <label>半成品名称<input id="editSemiName" value="${escapeAttr(row.name || "")}"></label>
        <label>规格参数<input id="editSemiSpec" value="${escapeAttr(row.spec || "")}"></label>
        <label>单位<input id="editSemiUnit" value="${escapeAttr(row.unit || "")}"></label>
        <label>数量<input id="editSemiQty" type="number" step="1" value="${Number(row.quantity || 0)}"></label>
        <label>用于成品<input id="editSemiUsed" type="number" step="1" value="${Number(row.used_quantity || 0)}"></label>
        <label>成本价<input id="editSemiCost" type="number" step="0.01" value="${Number(row.cost_price || 0)}"></label>
        <label>日期<input id="editSemiDate" type="date" value="${escapeAttr(row.acceptance_date || "")}"></label>
        <label>货架<select id="editSemiShelf">${shelfSelectHtml(row.shelf_id)}</select></label>
        <label>层<input id="editSemiLayer" type="number" value="${escapeAttr(row.layer_number || "")}"></label>
        <label>分区<input id="editSemiZone" value="${escapeAttr(row.zone_name || "")}"></label>
        <label class="wide">组成物料 JSON<textarea id="editSemiComponents" rows="8">${escapeHtml(prettyJson(row.components || []))}</textarea></label>
      </div>`, okText: "保存修改", onOk: async () => {
        await systemApi(`/api/production/semifinished-inventory/${id}`, { method: "PUT", body: JSON.stringify({ name: document.getElementById("editSemiName").value, spec: document.getElementById("editSemiSpec").value, unit: document.getElementById("editSemiUnit").value, quantity: document.getElementById("editSemiQty").value, used_quantity: document.getElementById("editSemiUsed").value, cost_price: document.getElementById("editSemiCost").value, acceptance_date: document.getElementById("editSemiDate").value, shelf_id: document.getElementById("editSemiShelf").value, layer_number: document.getElementById("editSemiLayer").value, zone_name: document.getElementById("editSemiZone").value, components: jsonArrayFromInput("editSemiComponents", "组成物料") }) });
        toast("已保存");
        loadSemifinishedInventoryModule();
      }});
    } else if (kind === "defectiveSemi") {
      openModal({ title: "修改不合格半成品", body: `<div class="form-grid"><label>编号<input id="editDefSemiSerial" value="${escapeAttr(row.serial_no || "")}"></label><label>半成品名称<input id="editDefSemiName" value="${escapeAttr(row.name || "")}"></label><label>规格参数<input id="editDefSemiSpec" value="${escapeAttr(row.spec || "")}"></label><label class="wide">异常情况<input id="editDefSemiAbnormal" value="${escapeAttr((row.abnormal_conditions || []).join("、"))}" placeholder="外观、功能、性能"></label></div>`, okText: "保存修改", onOk: async () => {
        await systemApi(`/api/production/defective-semifinished/${id}`, { method: "PUT", body: JSON.stringify({ serial_no: document.getElementById("editDefSemiSerial").value, name: document.getElementById("editDefSemiName").value, spec: document.getElementById("editDefSemiSpec").value, abnormal_conditions: document.getElementById("editDefSemiAbnormal").value }) });
        toast("已保存");
        loadSemifinishedInventoryModule();
      }});
    } else if (kind === "finished") {
      openModal({ title: "修改成品库存", body: `<div class="form-grid">
        <label>成品名称<input id="editFinName" value="${escapeAttr(row.product_name || "")}"></label>
        <label>规格参数<input id="editFinSpec" value="${escapeAttr(row.spec || "")}"></label>
        <label>单位<input id="editFinUnit" value="${escapeAttr(row.unit || "")}"></label>
        <label>数量<input id="editFinQty" type="number" step="1" value="${Number(row.quantity || 0)}"></label>
        <label>成本价<input id="editFinCost" type="number" step="0.01" value="${Number(row.cost_price || 0)}"></label>
        <label>日期<input id="editFinDate" type="date" value="${escapeAttr(row.acceptance_date || "")}"></label>
        <label>货架<select id="editFinShelf">${shelfSelectHtml(row.shelf_id)}</select></label>
        <label>层<input id="editFinLayer" type="number" value="${escapeAttr(row.layer_number || "")}"></label>
        <label>分区<input id="editFinZone" value="${escapeAttr(row.zone_name || "")}"></label>
        <label class="wide">所用物料 JSON<textarea id="editFinMaterialComponents" rows="8">${escapeHtml(prettyJson(row.material_components || []))}</textarea></label>
        <label class="wide">所用半成品 JSON<textarea id="editFinSemiComponents" rows="8">${escapeHtml(prettyJson(row.semifinished_components || []))}</textarea></label>
      </div>`, okText: "保存修改", onOk: async () => {
        await systemApi(`/api/production/finished-inventory/${id}`, { method: "PUT", body: JSON.stringify({ product_name: document.getElementById("editFinName").value, spec: document.getElementById("editFinSpec").value, unit: document.getElementById("editFinUnit").value, quantity: document.getElementById("editFinQty").value, cost_price: document.getElementById("editFinCost").value, acceptance_date: document.getElementById("editFinDate").value, shelf_id: document.getElementById("editFinShelf").value, layer_number: document.getElementById("editFinLayer").value, zone_name: document.getElementById("editFinZone").value, material_components: jsonArrayFromInput("editFinMaterialComponents", "所用物料"), semifinished_components: jsonArrayFromInput("editFinSemiComponents", "所用半成品") }) });
        toast("已保存");
        loadFinishedInventoryModule();
      }});
    } else {
      openModal({ title: "修改不合格成品", body: `<div class="form-grid"><label>流水号<input id="editDefSerial" value="${escapeAttr(row.serial_no || "")}"></label><label>成品名称<input id="editDefName" value="${escapeAttr(row.product_name || "")}"></label><label>规格参数<input id="editDefSpec" value="${escapeAttr(row.spec || "")}"></label><label class="wide">异常情况<input id="editDefAbnormal" value="${escapeAttr((row.abnormal_conditions || []).join("、"))}" placeholder="外观、功能、性能"></label></div>`, okText: "保存修改", onOk: async () => {
        await systemApi(`/api/production/defective-finished/${id}`, { method: "PUT", body: JSON.stringify({ serial_no: document.getElementById("editDefSerial").value, product_name: document.getElementById("editDefName").value, spec: document.getElementById("editDefSpec").value, abnormal_conditions: document.getElementById("editDefAbnormal").value }) });
        toast("已保存");
        loadFinishedInventoryModule();
      }});
    }
  }

  async function deleteProductionInventory(kind, id) {
    if (!id) return;
    const names = { semi: "\u534a\u6210\u54c1\u5e93\u8bb0\u5f55", defectiveSemi: "不合格半成品记录", finished: "\u6210\u54c1\u5e93\u8bb0\u5f55", defective: "\u4e0d\u5408\u683c\u6210\u54c1\u8bb0\u5f55" };
    if (!confirm(`\u786e\u8ba4\u5220\u9664\u8be5${names[kind] || "\u8bb0\u5f55"}\uff1f`)) return;
    const urls = {
      semi: `/api/production/semifinished-inventory/${id}`,
      defectiveSemi: `/api/production/defective-semifinished/${id}`,
      finished: `/api/production/finished-inventory/${id}`,
      defective: `/api/production/defective-finished/${id}`,
    };
    await systemApi(urls[kind], { method: "DELETE" });
    toast("\u5df2\u5220\u9664");
    if (kind === "semi" || kind === "defectiveSemi") loadSemifinishedInventoryModule();
    else loadFinishedInventoryModule();
  }

  function filterRowsByKeyword(rows, keyword, keys) {
    if (!keyword) return rows;
    return (rows || []).filter(row => keys.some(key => {
      const value = row?.[key];
      return (Array.isArray(value) ? value.join(" ") : String(value || "")).toLowerCase().includes(keyword);
    }));
  }

  function locationText(item) {
    const shelves = typeof state !== "undefined" && Array.isArray(state.shelves) ? state.shelves : [];
    const shelfName = item?.shelf_name || shelves.find(shelf => Number(shelf.id) === Number(item?.shelf_id))?.name || "";
    const parts = [shelfName, item?.layer_number ? `${item.layer_number}层` : "", item?.zone_name || ""].filter(Boolean);
    return parts.join(" ");
  }

  async function checkItemWarning(itemType, itemId) {
    try {
      const data = await systemApi(`/api/items/${encodeURIComponent(itemType)}/${itemId}/history?limit=20`);
      const historyEntries = data.history || [];
      const warningEntry = historyEntries.find(
        entry => entry.event_type === "变更" && entry.normal_use === "否"
      );
      if (warningEntry) {
        return { _has_warning: true, _change_type: warningEntry.change_type || "" };
      }
    } catch (error) {
      // Silently ignore failures — item may have no change records
    }
    return { _has_warning: false, _change_type: "" };
  }

  async function checkWarningsForItems(items, itemType) {
    const itemsToCheck = (items || []).filter(item => Number(item.borrowed_quantity || 0) > 0);
    if (!itemsToCheck.length) return items;
    const results = await Promise.all(
      itemsToCheck.map(item =>
        checkItemWarning(itemType, item.id).then(result => ({ id: item.id, ...result }))
      )
    );
    const warningMap = {};
    results.forEach(item => {
      warningMap[item.id] = { _has_warning: item._has_warning, _change_type: item._change_type };
    });
    return (items || []).map(item => {
      const warning = warningMap[item.id];
      return warning ? { ...item, ...warning } : item;
    });
  }

  function semifinishedInventoryHtml(inventory, defective = []) {
    const canWrite = hasPerm("write_semifinished_inventory");
    const hasActions = canWrite;
    return `<h3>半成品库</h3><table class="flow-table"><thead><tr><th>编号</th><th>名称</th><th>规格</th><th>数量</th><th>已借用</th><th>用于成品</th><th>余量</th><th>成本价</th><th>位置</th><th>日期</th>${hasActions ? `<th>操作</th>` : ""}</tr></thead><tbody>${(inventory || []).map(item => `<tr><td>${escapeHtml(item.serial_no || "")}</td><td>${escapeHtml(item.name)}${item._has_warning ? ` <div class="item-warning"><span class="item-warning-icon">⚠️</span> ${escapeHtml(item._change_type || '硬件')}异常</div>` : ''}</td><td>${escapeHtml(item.spec || "")}</td><td>${formatQty(item.quantity)} ${escapeHtml(item.unit || "")}</td><td>${formatQty(item.borrowed_quantity || 0)}</td><td>${formatQty(item.used_quantity || 0)}</td><td>${formatQty(item.remaining_quantity || 0)}</td><td>${money(item.cost_price || 0)}</td><td>${escapeHtml(locationText(item))}</td><td>${escapeHtml(item.acceptance_date || "")}</td>${hasActions ? `<td class="mini-actions"><button type="button" onclick="viewItemHistory('semifinished',${item.id})">查看详情</button>${canWrite ? `<button type="button" data-edit-semi-inventory="${item.id}">修改</button><button type="button" class="danger" data-delete-semi-inventory="${item.id}">删除</button>` : ""}</td>` : ""}</tr>`).join("") || `<tr><td colspan="${hasActions ? 11 : 10}">暂无半成品库存</td></tr>`}</tbody></table><h3>不合格半成品库</h3><table class="flow-table"><thead><tr><th>编号</th><th>半成品</th><th>规格</th><th>异常情况</th><th>时间</th>${hasActions ? `<th>操作</th>` : ""}</tr></thead><tbody>${(defective || []).map(item => `<tr><td>${escapeHtml(item.serial_no || "")}</td><td>${escapeHtml(item.name || "")}</td><td>${escapeHtml(item.spec || "")}</td><td>${escapeHtml((item.abnormal_conditions || []).join("、"))}</td><td>${escapeHtml(item.created_at || "")}</td>${hasActions ? `<td class="mini-actions"><button type="button" onclick="viewItemHistory('defective_semifinished',${item.id})">查看详情</button>${canWrite ? `<button type="button" data-edit-defective-semi="${item.id}">修改</button><button type="button" class="danger" data-delete-defective-semi="${item.id}">删除</button>` : ""}</td>` : ""}</tr>`).join("") || `<tr><td colspan="${hasActions ? 6 : 5}">暂无不合格半成品</td></tr>`}</tbody></table>`;
  }

  function semifinishedAcceptanceHistoryHtml(acceptances) {
    return `<h3>验收记录</h3><table class="flow-table"><thead><tr><th>单号</th><th>半成品</th><th>验收/合格/不合格</th><th>外观/功能/性能</th><th>成本价</th><th>日期</th></tr></thead><tbody>${(acceptances || []).map(item => `<tr><td>${escapeHtml(item.acceptance_no)}</td><td>${escapeHtml(item.name)}<br><small>${escapeHtml(item.spec || "")}</small></td><td>${formatQty(item.acceptance_quantity)} / ${formatQty(item.qualified_quantity)} / ${formatQty(item.unqualified_quantity)}</td><td>${formatQty(item.appearance_ok_quantity)} / ${formatQty(item.function_ok_quantity)} / ${formatQty(item.performance_ok_quantity)}</td><td>${money(item.cost_price || 0)}</td><td>${escapeHtml(item.acceptance_date || "")}</td></tr>`).join("") || `<tr><td colspan="6">暂无验收记录</td></tr>`}</tbody></table>`;
  }

  function finishedInventoryHtml(qualified, defective) {
    const canWrite = hasPerm("write_finished_inventory");
    const hasActions = canWrite;
    return `<h3>合格成品库</h3><table class="flow-table"><thead><tr><th>编号</th><th>成品</th><th>规格</th><th>数量</th><th>已借用</th><th>余量</th><th>成本价</th><th>位置</th><th>日期</th>${hasActions ? `<th>操作</th>` : ""}</tr></thead><tbody>${(qualified || []).map(item => `<tr><td>${escapeHtml(item.serial_no || "")}</td><td>${escapeHtml(item.product_name)}${item._has_warning ? ` <div class="item-warning"><span class="item-warning-icon">⚠️</span> ${escapeHtml(item._change_type || '硬件')}异常</div>` : ''}</td><td>${escapeHtml(item.spec || "")}</td><td>${formatQty(item.quantity)} ${escapeHtml(item.unit || "")}</td><td>${formatQty(item.borrowed_quantity || 0)}</td><td>${formatQty(item.remaining_quantity ?? (Number(item.quantity || 0) - Number(item.borrowed_quantity || 0)))}</td><td>${money(item.cost_price || 0)}</td><td>${escapeHtml(locationText(item))}</td><td>${escapeHtml(item.acceptance_date || "")}</td>${hasActions ? `<td class="mini-actions"><button type="button" onclick="viewItemHistory('finished',${item.id})">查看详情</button>${canWrite ? `<button type="button" data-edit-finished-inventory="${item.id}">修改</button><button type="button" class="danger" data-delete-finished-inventory="${item.id}">删除</button>` : ""}</td>` : ""}</tr>`).join("") || `<tr><td colspan="${hasActions ? 10 : 9}">暂无合格成品库存</td></tr>`}</tbody></table><h3>不合格成品库</h3><table class="flow-table"><thead><tr><th>流水号</th><th>成品</th><th>规格</th><th>异常情况</th><th>时间</th>${hasActions ? `<th>操作</th>` : ""}</tr></thead><tbody>${(defective || []).map(item => `<tr><td>${escapeHtml(item.serial_no)}</td><td>${escapeHtml(item.product_name)}</td><td>${escapeHtml(item.spec || "")}</td><td>${escapeHtml((item.abnormal_conditions || []).join("、"))}</td><td>${escapeHtml(item.created_at || "")}</td>${hasActions ? `<td class="mini-actions"><button type="button" onclick="viewItemHistory('defective_finished',${item.id})">查看详情</button>${canWrite ? `<button type="button" data-edit-defective-finished="${item.id}">修改</button><button type="button" class="danger" data-delete-defective-finished="${item.id}">删除</button>` : ""}</td>` : ""}</tr>`).join("") || `<tr><td colspan="${hasActions ? 6 : 5}">暂无不合格成品</td></tr>`}</tbody></table>`;
  }

  function finishedAcceptanceHistoryHtml(acceptances) {
    return `<h3>验收记录</h3><table class="flow-table"><thead><tr><th>单号</th><th>成品</th><th>验收/合格/不合格</th><th>外观/功能/性能</th><th>成本价</th><th>日期</th></tr></thead><tbody>${(acceptances || []).map(item => `<tr><td>${escapeHtml(item.acceptance_no)}</td><td>${escapeHtml(item.product_name)}<br><small>${escapeHtml(item.spec || "")}</small></td><td>${formatQty(item.acceptance_quantity)} / ${formatQty(item.qualified_quantity)} / ${formatQty(item.unqualified_quantity)}</td><td>${formatQty(item.appearance_ok_quantity)} / ${formatQty(item.function_ok_quantity)} / ${formatQty(item.performance_ok_quantity)}</td><td>${money(item.cost_price || 0)}</td><td>${escapeHtml(item.acceptance_date || "")}</td></tr>`).join("") || `<tr><td colspan="6">暂无验收记录</td></tr>`}</tbody></table>`;
  }

  let defectPreviewToken = 0;
  async function refreshFinishedDefects() {
    const host = document.getElementById("finDefects");
    if (!host) return;
    const count = Math.max(0, Math.round(Number(document.getElementById("finUnqualified")?.value || 0)));
    if (!count) {
      host.innerHTML = `<div class="empty-state">无不合格成品</div>`;
      return;
    }
    const productName = document.getElementById("finName")?.value.trim() || "成品";
    const token = ++defectPreviewToken;
    const data = await systemApi(`/api/production/finished-serials?product_name=${encodeURIComponent(productName)}&count=${count}`);
    if (token !== defectPreviewToken) return;
    const serials = data.serials || [];
    host.innerHTML = serials.map((serial, index) => `<div class="defect-row" data-defect-row><strong>${escapeHtml(serial)}</strong><div><label><input type="checkbox" data-defect-abnormal="${index}" value="外观"> 外观</label><label><input type="checkbox" data-defect-abnormal="${index}" value="功能"> 功能</label><label><input type="checkbox" data-defect-abnormal="${index}" value="性能"> 性能</label></div></div>`).join("");
  }

  function defectRowsFromPage() {
    return [...document.querySelectorAll("[data-defect-row]")].map((row, index) => ({
      abnormal_conditions: [...row.querySelectorAll(`[data-defect-abnormal="${index}"]:checked`)].map(input => input.value),
    }));
  }

  async function runMaterialQuery() {
    const keyword = document.getElementById("queryKeyword").value.trim();
    const localMaterials = typeof state !== "undefined" && Array.isArray(state.materials) ? state.materials : await refreshMaterialsCache();
    const rows = keyword ? await systemApi(`/api/materials/search?keyword=${encodeURIComponent(keyword)}`) : localMaterials;
    system.materialQuerySourceRows = rows;
    system.materialQueryControlState ||= { sort: "code", filters: { hideZero: false } };
    system.materialQueryRows = window.WarehouseInventoryListControls?.apply(rows, system.materialQueryControlState) || rows;
    system.pages.materialQuery = 1;
    renderMaterialQueryPage();
  }

  function renderMaterialQueryPage() {
    const host = document.getElementById("queryResult");
    if (!host) return;
    const rows = system.materialQueryRows || [];
    const visibleRows = pageRows("materialQuery", rows);
    host.innerHTML = materialTableV2(visibleRows) + paginationHtml("materialQuery", rows);
    bindMaterialQueryActions();
    bindPagination(host, "materialQuery", renderMaterialQueryPage);
  }

  async function runStats() {
    const params = new URLSearchParams({ warehouse_type: document.getElementById("statWarehouse").value, date_from: document.getElementById("statFrom").value, date_to: document.getElementById("statTo").value });
    const data = await systemApi(`/api/statistics/${document.getElementById("statKind").value}?${params}`);
    document.getElementById("statsResult").innerHTML = `<p>合计数量：${formatQty(data.total_quantity)}，合计金额：${money(data.total_amount)}</p>${recordsTable(data.rows)}`;
  }

  function chooseStocktakeSupervisorAndCreate() {
    const dept = currentUser()?.department || "";
    const leaders = usersBy("leader", dept);
    const users = leaders.length ? leaders : usersBy("leader");
    openPeoplePicker({
      title: "选择监盘人",
      users,
      role: "leader",
      selected: users.slice(0, 1).map(user => Number(user.id)),
      multiple: false,
      department: leaders.length ? dept : "",
      onConfirm: ids => createStocktake(Number(ids[0] || 0)),
    });
  }

  async function createStocktake(supervisorId = 0) {
    const stocktake = await systemApi("/api/stocktakes", { method: "POST", body: JSON.stringify({ warehouse_type: document.getElementById("pdWarehouse").value, date_from: document.getElementById("pdFrom").value, date_to: document.getElementById("pdTo").value, supervisor_id: supervisorId, show_zero: document.getElementById("pdShowZero").checked }) });
    document.getElementById("stocktakeResult").innerHTML = stocktakeTable(stocktake.stocktake);
    toast("盘点单已生成");
    loadStocktakes();
  }

  async function loadStocktakes() {
    const rows = await systemApi("/api/stocktakes");
    document.getElementById("stocktakeList").innerHTML = `<table class="flow-table"><thead><tr><th>\u5355\u53f7</th><th>\u4ed3\u5e93</th><th>\u8303\u56f4</th><th>\u72b6\u6001</th><th>\u64cd\u4f5c</th></tr></thead><tbody>${rows.map(r => `<tr><td><button class="linklike" data-view-stocktake="${r.id}">${escapeHtml(r.form_no)}</button></td><td>${r.warehouse_type || "\u5168\u90e8"}</td><td>${formatDateCn(r.date_from)} \u81f3 ${formatDateCn(r.date_to)}</td><td>${escapeHtml(stocktakeStatusLabel(r.status || ""))}</td><td class="mini-actions"><button data-view-stocktake="${r.id}">\u67e5\u770b</button>${hasPerm("edit_stocktake") ? `<button data-edit-stocktake="${r.id}">\u4fee\u6539</button><button class="danger" data-delete-stocktake="${r.id}">\u5220\u9664</button>` : ""}${canView("stocktake") ? `<button data-supervise-stocktake="${r.id}">\u76d1\u76d8\u7b7e\u5b57</button>` : ""}</td></tr>`).join("") || `<tr><td colspan="5">\u6682\u65e0\u6570\u636e</td></tr>`}</tbody></table>`;
    document.querySelectorAll("[data-view-stocktake]").forEach(btn => btn.addEventListener("click", () => openStocktake(Number(btn.dataset.viewStocktake))));
    document.querySelectorAll("[data-edit-stocktake]").forEach(btn => btn.addEventListener("click", () => editStocktake(Number(btn.dataset.editStocktake))));
    document.querySelectorAll("[data-supervise-stocktake]").forEach(btn => btn.addEventListener("click", () => superviseStocktake(Number(btn.dataset.superviseStocktake))));
    document.querySelectorAll("[data-delete-stocktake]").forEach(btn => btn.addEventListener("click", () => deleteStocktake(Number(btn.dataset.deleteStocktake))));
    return;
    document.getElementById("stocktakeList").innerHTML = `<table class="flow-table"><thead><tr><th>单号</th><th>仓库</th><th>范围</th><th>状态</th></tr></thead><tbody>${rows.map(r => `<tr><td>${r.form_no}</td><td>${r.warehouse_type || "全部"}</td><td>${r.date_from} 至 ${r.date_to}</td><td>${r.status}</td></tr>`).join("") || `<tr><td colspan="4">暂无数据</td></tr>`}</tbody></table>`;
  }

  async function openStocktake(stocktakeId) {
    const form = await systemApi(`/api/stocktakes/${stocktakeId}`);
    openModal({ title: form.form_no, hideOk: true, body: `<div class="print-area" id="stocktakeDetailPrint">${stocktakeTable(form)}</div><div class="mini-actions"><button id="printStocktakeDetail">\u6253\u5370</button></div>`, onReady: () => {
      document.getElementById("printStocktakeDetail")?.addEventListener("click", () => printElement("stocktakeDetailPrint"));
    }});
  }

  async function editStocktake(stocktakeId) {
    const form = await systemApi(`/api/stocktakes/${stocktakeId}`);
    openModal({ title: `\u4fee\u6539 ${form.form_no}`, body: `<div class="form-grid"><label>\u4ed3\u5e93<select id="editPdWarehouse"><option value="" ${!form.warehouse_type ? "selected" : ""}>\u5168\u90e8</option><option value="office" ${form.warehouse_type === "office" ? "selected" : ""}>\u529e\u516c\u7528\u54c1\u5e93</option><option value="rd" ${form.warehouse_type === "rd" ? "selected" : ""}>\u7814\u53d1\u6750\u6599\u5e93</option></select></label><label>\u5f00\u59cb\u65e5\u671f${datePickerHtml("editPdFrom", form.date_from || "")}</label><label>\u7ed3\u675f\u65e5\u671f${datePickerHtml("editPdTo", form.date_to || "")}</label><label>\u72b6\u6001<input id="editPdStatus" value="${escapeAttr(form.status || "")}"></label><label><input id="editPdShowZero" type="checkbox" ${form.show_zero ? "checked" : ""}> \u663e\u793a 0 \u5e93\u5b58\u7269\u54c1</label><label>\u76d1\u76d8\u4eba<select id="editPdSupervisor"><option value="">\u672a\u6307\u5b9a</option>${userOptions("leader")}</select></label><label>\u76d8\u70b9\u4eba\u7b7e\u5b57<input id="editPdCheckerSign" value="${escapeAttr(form.checker_signature || "")}"></label><label>\u76d8\u70b9\u65e5\u671f${datePickerHtml("editPdCheckerDate", form.checker_date || "")}</label><label>\u76d1\u76d8\u7b7e\u5b57<input id="editPdSupervisorSign" value="${escapeAttr(form.supervisor_signature || "")}"></label><label>\u76d1\u76d8\u65e5\u671f${datePickerHtml("editPdSupervisorDate", form.supervisor_date || "")}</label></div>`, okText: "\u4fdd\u5b58\u4fee\u6539", onReady: () => {
      bindDatePickers(document.querySelector(".modal") || document);
      const select = document.getElementById("editPdSupervisor");
      if (select) select.value = form.supervisor_id || "";
    }, onOk: async () => {
      await systemApi(`/api/stocktakes/${stocktakeId}`, { method: "PUT", body: JSON.stringify({ warehouse_type: document.getElementById("editPdWarehouse").value, date_from: document.getElementById("editPdFrom").value, date_to: document.getElementById("editPdTo").value, status: document.getElementById("editPdStatus").value, show_zero: document.getElementById("editPdShowZero").checked, supervisor_id: document.getElementById("editPdSupervisor").value, checker_signature: document.getElementById("editPdCheckerSign").value, checker_date: document.getElementById("editPdCheckerDate").value, supervisor_signature: document.getElementById("editPdSupervisorSign").value, supervisor_date: document.getElementById("editPdSupervisorDate").value }) });
      toast("\u76d8\u70b9\u5355\u5df2\u4fee\u6539"); loadStocktakes();
    }});
  }

  async function superviseStocktake(stocktakeId) {
    openModal({ title: "\u76d1\u76d8\u7b7e\u5b57", body: `<div class="form-grid"><label>\u7b7e\u5b57<input id="stocktakeSupervisorSign" value="${escapeAttr(currentUser()?.display_name || "")}"></label><label>\u65e5\u671f${datePickerHtml("stocktakeSupervisorDate", new Date().toISOString().slice(0, 10))}</label></div>`, okText: "\u63d0\u4ea4", onReady: () => bindDatePickers(document.querySelector(".modal") || document), onOk: async () => {
      await systemApi(`/api/stocktakes/${stocktakeId}/supervise`, { method: "POST", body: JSON.stringify({ signature: document.getElementById("stocktakeSupervisorSign").value, date: document.getElementById("stocktakeSupervisorDate").value }) });
      toast("\u76d1\u76d8\u5df2\u7b7e\u5b57"); loadStocktakes();
    }});
  }

  async function deleteStocktake(stocktakeId) {
    if (!confirm("\u786e\u8ba4\u5220\u9664\u8be5\u76d8\u70b9\u5355\uff1f")) return;
    await systemApi(`/api/stocktakes/${stocktakeId}`, { method: "DELETE" });
    toast("\u76d8\u70b9\u5355\u5df2\u5220\u9664");
    loadStocktakes();
  }

  async function loadUsers() {
    const data = await systemApi("/api/system/users");
    system.boot.departments = data.departments || system.boot.departments || [];
    system.boot.roles = data.roles || system.boot.roles || [];
    system.boot.users = data.users || [];
    document.getElementById("usersHost").innerHTML = `<table class="flow-table"><thead><tr><th>\u8d26\u53f7</th><th>\u59d3\u540d</th><th>\u90e8\u95e8</th><th>\u89d2\u8272</th><th>\u72b6\u6001</th><th>\u64cd\u4f5c</th></tr></thead><tbody>${data.users.map(u => `<tr><td>${escapeHtml(u.username)}</td><td>${escapeHtml(u.display_name)}</td><td>${escapeHtml(u.department || "")}</td><td>${escapeHtml(u.role_names || "")}</td><td>${u.is_active ? "\u542f\u7528" : "\u505c\u7528"}${u.must_change_password ? " / 待改密" : ""}</td><td class="mini-actions"><button data-edit-user="${u.id}">\u7f16\u8f91</button><button data-reset-user-password="${u.id}">重置密码</button><button data-toggle-user="${u.id}" data-active="${u.is_active ? 0 : 1}">${u.is_active ? "\u505c\u7528" : "\u542f\u7528"}</button><button class="danger" data-delete-user="${u.id}">\u5220\u9664</button></td></tr>`).join("")}</tbody></table>`;
    document.querySelectorAll("[data-edit-user]").forEach(btn => btn.addEventListener("click", () => editUser(Number(btn.dataset.editUser))));
    document.querySelectorAll("[data-reset-user-password]").forEach(btn => btn.addEventListener("click", () => resetUserPassword(Number(btn.dataset.resetUserPassword))));
    document.querySelectorAll("[data-toggle-user]").forEach(btn => btn.addEventListener("click", () => toggleUser(Number(btn.dataset.toggleUser), Number(btn.dataset.active) === 1)));
    document.querySelectorAll("[data-delete-user]").forEach(btn => btn.addEventListener("click", () => deleteUser(Number(btn.dataset.deleteUser))));
  }

  async function loadDepartments() {
    const rows = await systemApi("/api/system/departments");
    system.boot.departments = rows;
    document.getElementById("deptHost").innerHTML = `<table class="flow-table"><thead><tr><th>\u90e8\u95e8</th><th>\u63cf\u8ff0</th><th>\u64cd\u4f5c</th></tr></thead><tbody>${rows.map(dep => `<tr><td>${escapeHtml(dep.name)}</td><td>${escapeHtml(dep.description || "")}</td><td class="mini-actions"><button data-edit-dept="${dep.id}">\u7f16\u8f91</button>${hasPerm("edit_department") ? `<button class="danger" data-delete-dept="${dep.id}">\u5220\u9664</button>` : ""}</td></tr>`).join("")}</tbody></table>`;
    document.querySelectorAll("[data-edit-dept]").forEach(btn => btn.addEventListener("click", () => editDepartment(Number(btn.dataset.editDept))));
    document.querySelectorAll("[data-delete-dept]").forEach(btn => btn.addEventListener("click", () => deleteDepartment(Number(btn.dataset.deleteDept))));
  }

  async function saveDepartment() {
    await systemApi("/api/system/departments", { method: "POST", body: JSON.stringify({ id: document.getElementById("deptId").value, name: document.getElementById("deptName").value, description: document.getElementById("deptDesc").value }) });
    toast("\u90e8\u95e8\u5df2\u4fdd\u5b58");
    document.getElementById("deptId").value = "";
    document.getElementById("deptName").value = "";
    document.getElementById("deptDesc").value = "";
    system.boot = null;
    await loadSystemBoot();
    renderSystemMain();
  }

  function editDepartment(departmentId) {
    const dep = (system.boot.departments || []).find(item => item.id === departmentId);
    if (!dep) return;
    document.getElementById("deptId").value = dep.id;
    document.getElementById("deptName").value = dep.name || "";
    document.getElementById("deptDesc").value = dep.description || "";
  }

  async function deleteDepartment(departmentId) {
    if (!confirm("\u786e\u8ba4\u5220\u9664\u8be5\u90e8\u95e8\uff1f\u7528\u6237\u90e8\u95e8\u5c06\u6e05\u7a7a\u3002")) return;
    await systemApi(`/api/system/departments/${departmentId}`, { method: "DELETE" });
    toast("\u90e8\u95e8\u5df2\u5220\u9664");
    system.boot = null;
    await loadSystemBoot();
    renderSystemMain();
  }

  async function saveNewUser() {
    const roles = [...document.getElementById("newRoles").selectedOptions].map(option => option.value);
    await systemApi("/api/system/users", { method: "POST", body: JSON.stringify({ id: document.getElementById("editUserId").value, username: document.getElementById("newUsername").value, display_name: document.getElementById("newDisplayName").value, password: document.getElementById("newPassword").value, department: document.getElementById("newDepartment").value, is_active: document.getElementById("newActive").value === "1", roles }) });
    toast("\u7528\u6237\u5df2\u4fdd\u5b58");
    document.getElementById("editUserId").value = "";
    document.getElementById("newPassword").value = "";
    loadUsers();
  }

  async function importUsers() {
    const file = document.getElementById("userImportFile").files?.[0];
    if (!file) throw new Error("请先选择填写好的用户导入模板");
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetch("/api/system/users/import", { method: "POST", body: formData });
    const data = await response.json();
    if (!response.ok || data.success === false) throw new Error(data.error || "导入失败");
    toast(`导入完成：新增 ${data.imported || 0}，更新 ${data.updated || 0}${(data.errors || []).length ? "；部分行有提示，请检查控制台" : ""}`);
    if ((data.errors || []).length) console.warn("用户导入提示", data.errors);
    loadUsers();
  }

  function editUser(userId) {
    const user = (system.boot.users || []).find(item => item.id === userId);
    if (!user) return;
    document.getElementById("editUserId").value = user.id;
    document.getElementById("newUsername").value = user.username;
    document.getElementById("newDisplayName").value = user.display_name;
    document.getElementById("newPassword").value = "";
    document.getElementById("newDepartment").value = user.department || "";
    document.getElementById("newActive").value = user.is_active ? "1" : "0";
    const codes = String(user.role_codes || "").split(",");
    [...document.getElementById("newRoles").options].forEach(option => { option.selected = codes.includes(option.value); });
  }

  async function toggleUser(userId, active) {
    await systemApi(`/api/system/users/${userId}/status`, { method: "POST", body: JSON.stringify({ is_active: active }) });
    toast(active ? "\u7528\u6237\u5df2\u542f\u7528" : "\u7528\u6237\u5df2\u505c\u7528");
    loadUsers();
  }

  async function deleteUser(userId) {
    if (!confirm("\u786e\u8ba4\u5220\u9664\u8be5\u7528\u6237\uff1f\u5386\u53f2\u6d41\u7a0b\u4f1a\u4fdd\u7559\uff0c\u4f46\u8be5\u7528\u6237\u5173\u8054\u4f1a\u7f6e\u7a7a\u3002")) return;
    await systemApi(`/api/system/users/${userId}`, { method: "DELETE" });
    toast("\u7528\u6237\u5df2\u5220\u9664");
    loadUsers();
  }

  async function resetUserPassword(userId) {
    openModal({
      title: "重置用户密码",
      body: `<div class="form-grid"><label class="wide">新密码<input id="resetUserPasswordInput" type="password"></label><p class="hint wide">是否强制该用户下次登录修改密码，按当前密码策略执行。</p></div>`,
      okText: "确认重置",
      onOk: async () => {
        await systemApi(`/api/system/users/${userId}/reset-password`, {
          method: "POST",
          body: JSON.stringify({ password: document.getElementById("resetUserPasswordInput").value }),
        });
        toast("密码已重置");
      },
    });
  }

  async function loadPasswordPolicy() {
    const policy = await systemApi("/api/system/password-policy");
    document.getElementById("passwordPolicySettings").innerHTML = `<div class="form-grid">
      <label>最小位数<input id="policyMinLength" type="number" min="1" value="${Number(policy.min_length || 6)}"></label>
      <label><input id="policyDigit" type="checkbox" ${policy.require_digit ? "checked" : ""}> 必须包含数字</label>
      <label><input id="policyLower" type="checkbox" ${policy.require_lower ? "checked" : ""}> 必须包含小写字母</label>
      <label><input id="policyUpper" type="checkbox" ${policy.require_upper ? "checked" : ""}> 必须包含大写字母</label>
      <label><input id="policySymbol" type="checkbox" ${policy.require_symbol ? "checked" : ""}> 必须包含字符/符号</label>
      <label class="wide"><input id="policyForceFirstLogin" type="checkbox" ${policy.force_change_on_first_login ? "checked" : ""}> 首次登录或管理员重置后强制修改密码</label>
    </div>`;
    document.getElementById("savePasswordPolicy").addEventListener("click", savePasswordPolicy);
  }

  async function savePasswordPolicy() {
    await systemApi("/api/system/password-policy", {
      method: "POST",
      body: JSON.stringify({
        min_length: document.getElementById("policyMinLength").value,
        require_digit: document.getElementById("policyDigit").checked,
        require_lower: document.getElementById("policyLower").checked,
        require_upper: document.getElementById("policyUpper").checked,
        require_symbol: document.getElementById("policySymbol").checked,
        force_change_on_first_login: document.getElementById("policyForceFirstLogin").checked,
      }),
    });
    toast("密码策略已保存");
  }

  function dataValidationChecked(id) {
    return Boolean(document.getElementById(id)?.checked);
  }

  function dataValidationValue(id, fallback = "") {
    const el = document.getElementById(id);
    return el ? el.value : fallback;
  }

  function dataValidationCheckHtml(id, checked, label) {
    return `<label class="compact-check"><input id="${id}" type="checkbox" ${checked ? "checked" : ""}> <span>${label}</span></label>`;
  }

  function dataValidationNumberHtml(label, id, value, attrs = "") {
    return `<label class="validation-number">${label}<input id="${id}" type="number" ${attrs} value="${Number(value)}"></label>`;
  }

  function dataValidationOverview(settings) {
    const items = [
      ["总开关", settings.enabled ? "已启用" : "已关闭"],
      ["项目号", `${settings.project_code?.enabled ? "启用" : "关闭"} · 最长 ${Number(settings.project_code?.max_length || 50)} 字符`],
      ["批次号", `${settings.batch_no?.enabled ? "启用" : "关闭"} · ${settings.batch_no?.required ? "必填" : "可为空"}`],
      ["半成品/成品编号", `${settings.serial_no?.enabled ? "启用" : "关闭"} · 最长 ${Number(settings.serial_no?.max_length || 64)} 字符`],
      ["流程数量", settings.workflow_bounds?.enabled ? "限制超库存/超申请/超合格" : "已关闭"],
    ];
    return `<div class="validation-overview">${items.map(([label, value]) => `<div class="validation-pill"><strong>${label}</strong><span>${value}</span></div>`).join("")}</div>`;
  }

  function dataValidationTextRuleHtml(prefix, title, settings, defaultLength, requiredLabel = "") {
    return `<details class="settings-section" open><summary>${title}</summary><div class="settings-inner"><div class="form-grid validation-form">
      ${dataValidationCheckHtml(`${prefix}Enabled`, settings.enabled, "启用校验")}
      ${requiredLabel ? dataValidationCheckHtml(`${prefix}Required`, settings.required, requiredLabel) : ""}
      ${dataValidationNumberHtml("最大长度", `${prefix}MaxLength`, settings.max_length || defaultLength, 'min="1" max="200"')}
      ${dataValidationCheckHtml(`${prefix}AllowControl`, settings.allow_control_chars, "允许换行/制表符")}
    </div></div></details>`;
  }

  async function loadDataValidationSettings() {
    const settings = await systemApi("/api/system/data-validation");
    const project = settings.project_code || {};
    const batch = settings.batch_no || {};
    const serial = settings.serial_no || {};
    const materialCode = settings.material_code || {};
    const quantity = settings.quantity || {};
    const price = settings.price || {};
    const makerUser = settings.maker_user || {};
    const workflowBounds = settings.workflow_bounds || {};
    document.getElementById("dataValidationSettings").innerHTML = `
      <div class="validation-settings">
      ${dataValidationOverview(settings)}
      <div class="validation-note warn">关闭编号唯一或流程数量边界后，系统会允许重复编号或超量办理。建议只在历史数据清理、临时导入时短时间关闭。</div>
      <details class="settings-section" open><summary>总开关</summary><div class="settings-inner"><div class="form-grid validation-form">
        ${dataValidationCheckHtml("dvEnabled", settings.enabled, "启用数据校验")}
      </div></div></details>
      ${dataValidationTextRuleHtml("dvProject", "项目号", project, 50)}
      ${dataValidationTextRuleHtml("dvBatch", "批次号", batch, 64, "批次号必填")}
      <details class="settings-section" open><summary>半成品/成品编号</summary><div class="settings-inner"><div class="form-grid validation-form">
        ${dataValidationCheckHtml("dvSerialEnabled", serial.enabled, "启用校验")}
        ${dataValidationCheckHtml("dvSerialRequired", serial.required, "编号必填")}
        ${dataValidationNumberHtml("最大长度", "dvSerialMaxLength", serial.max_length || 64, 'min="1" max="200"')}
        ${dataValidationCheckHtml("dvSerialAllowControl", serial.allow_control_chars, "允许换行/制表符")}
        ${dataValidationCheckHtml("dvSerialCountWithin", serial.count_within_acceptance, "编号数量不超过验收数量")}
        ${dataValidationCheckHtml("dvSerialUniquePayload", serial.unique_in_payload, "本次提交内编号不重复")}
        ${dataValidationCheckHtml("dvSerialUniqueDb", serial.unique_in_database, "系统内编号不重复")}
      </div></div></details>
      <details class="settings-section"><summary>物料编号</summary><div class="settings-inner"><div class="form-grid validation-form">
        ${dataValidationCheckHtml("dvMatCodeEnabled", materialCode.enabled, "启用校验")}
        ${dataValidationNumberHtml("固定长度", "dvMatCodeLength", materialCode.length || 14, 'min="1" max="64"')}
        ${dataValidationCheckHtml("dvMatCodeDigits", materialCode.digits_only, "只能是数字")}
      </div></div></details>
      <details class="settings-section"><summary>数量</summary><div class="settings-inner"><div class="form-grid validation-form">
        ${dataValidationCheckHtml("dvQtyEnabled", quantity.enabled, "启用校验")}
        ${dataValidationNumberHtml("最小值", "dvQtyMin", quantity.min_value ?? 0, 'min="0" step="0.000001"')}
        ${dataValidationNumberHtml("最大小数位", "dvQtyDecimals", quantity.max_decimals ?? 6, 'min="0" max="8"')}
      </div></div></details>
      <details class="settings-section"><summary>单价</summary><div class="settings-inner"><div class="form-grid validation-form">
        ${dataValidationCheckHtml("dvPriceEnabled", price.enabled, "启用校验")}
        ${dataValidationNumberHtml("最小值", "dvPriceMin", price.min_value ?? 0, 'min="0" step="0.0001"')}
        ${dataValidationNumberHtml("最大小数位", "dvPriceDecimals", price.max_decimals ?? 4, 'min="0" max="8"')}
      </div></div></details>
      <details class="settings-section"><summary>制作者与流程数量</summary><div class="settings-inner"><div class="form-grid validation-form">
        ${dataValidationCheckHtml("dvMakerEnabled", makerUser.enabled, "制作者必须为启用用户")}
        ${dataValidationCheckHtml("dvWorkflowBoundsEnabled", workflowBounds.enabled, "流程数量不超过库存/申请/合格/未归还数量")}
      </div></div></details>
      </div>
    `;
    document.getElementById("saveDataValidation").onclick = saveDataValidationSettings;
  }

  async function saveDataValidationSettings() {
    const payload = {
      enabled: dataValidationChecked("dvEnabled"),
      project_code: {
        enabled: dataValidationChecked("dvProjectEnabled"),
        max_length: dataValidationValue("dvProjectMaxLength", 50),
        allow_control_chars: dataValidationChecked("dvProjectAllowControl"),
      },
      batch_no: {
        enabled: dataValidationChecked("dvBatchEnabled"),
        required: dataValidationChecked("dvBatchRequired"),
        max_length: dataValidationValue("dvBatchMaxLength", 64),
        allow_control_chars: dataValidationChecked("dvBatchAllowControl"),
      },
      serial_no: {
        enabled: dataValidationChecked("dvSerialEnabled"),
        required: dataValidationChecked("dvSerialRequired"),
        max_length: dataValidationValue("dvSerialMaxLength", 64),
        allow_control_chars: dataValidationChecked("dvSerialAllowControl"),
        count_within_acceptance: dataValidationChecked("dvSerialCountWithin"),
        unique_in_payload: dataValidationChecked("dvSerialUniquePayload"),
        unique_in_database: dataValidationChecked("dvSerialUniqueDb"),
      },
      material_code: {
        enabled: dataValidationChecked("dvMatCodeEnabled"),
        length: dataValidationValue("dvMatCodeLength", 14),
        digits_only: dataValidationChecked("dvMatCodeDigits"),
      },
      quantity: {
        enabled: dataValidationChecked("dvQtyEnabled"),
        min_value: dataValidationValue("dvQtyMin", 0),
        max_decimals: dataValidationValue("dvQtyDecimals", 6),
      },
      price: {
        enabled: dataValidationChecked("dvPriceEnabled"),
        min_value: dataValidationValue("dvPriceMin", 0),
        max_decimals: dataValidationValue("dvPriceDecimals", 4),
      },
      maker_user: { enabled: dataValidationChecked("dvMakerEnabled") },
      workflow_bounds: { enabled: dataValidationChecked("dvWorkflowBoundsEnabled") },
    };
    const saveButton = document.getElementById("saveDataValidation");
    if (saveButton) saveButton.disabled = true;
    try {
      const result = await systemApi("/api/system/data-validation", { method: "POST", body: JSON.stringify(payload) });
      toast("数据校验已保存");
      if (system.boot) system.boot.data_validation_settings = result.settings || payload;
      await loadDataValidationSettings();
    } finally {
      if (saveButton) saveButton.disabled = false;
    }
  }

  async function loadWorkflowSettings() {
    const settings = await systemApi("/api/system/workflow-settings");
    const permissions = await systemApi("/api/system/role-permissions");
    system.boot.role_permissions = permissions.permissions || {};
    document.getElementById("workflowSettings").innerHTML = `
      <details class="settings-section" open><summary>流程开关</summary><div class="settings-inner"><div class="form-grid">
        <label><input id="setClaimSameDept" type="checkbox" ${settings.claim_leader_same_department ? "checked" : ""}> 申领审批默认按当前用户部门筛选领导</label>
        <label><input id="setManualApprovalLeader" type="checkbox" ${settings.allow_manual_approval_leader ? "checked" : ""}> 允许手动选择审批领导</label>
        <label><input id="setAccLeaderLock" type="checkbox" ${settings.acceptance_leader_locked_after_first_inspect ? "checked" : ""}> 验收员首次指定领导后锁定</label>
        <label><input id="setMultiClaim" type="checkbox" ${settings.allow_multi_claim_leaders ? "checked" : ""}> 申领允许多个领导候选</label>
        <label><input id="setSingleInbound" type="checkbox" ${settings.single_item_inbound_enabled ? "checked" : ""}> 入库流程允许单个物料单独入库</label>
        <label class="wide"><input id="setTemporaryInventoryEnabled" type="checkbox" ${settings.temporary_inventory_enabled ? "checked" : ""}> 启用临时库功能<span class="hint">关闭后将隐藏临时库页面及临时库相关流程、待办和通知，已有数据不会被删除。</span></label>
        <label><input id="setRequireMaterialPhoto" type="checkbox" ${settings.acceptance_material_photo_required ? "checked" : ""}> \u9a8c\u6536\u63d0\u4ea4\u65f6\u7269\u6599\u7167\u7247\u5fc5\u586b</label>
        <label><input id="setRequireAcceptanceDocument" type="checkbox" ${settings.acceptance_document_required ? "checked" : ""}> \u9a8c\u6536\u63d0\u4ea4\u65f6\u8d44\u6599\u5fc5\u586b</label>
        <label><input id="setRequireTemporaryPhoto" type="checkbox" ${settings.temporary_inventory_material_photo_required ? "checked" : ""}> 临时批次入库时物料照片必填</label>
        <label><input id="setRequireTemporaryDocument" type="checkbox" ${settings.temporary_inventory_document_required ? "checked" : ""}> 临时批次入库时物料资料必填</label>
        <label>回收站保留时间<select id="setRecycleDays">${[7, 15, 30, 60, 90].map(day => `<option value="${day}" ${Number(settings.recycle_retention_days || 30) === day ? "selected" : ""}>${day} 天</option>`).join("")}</select></label>
        <label>通知自动清理时间<select id="setNotificationDays">${[7, 15, 30, 90, 180].map(day => `<option value="${day}" ${Number(settings.notification_retention_days || 90) === day ? "selected" : ""}>${day} 天</option>`).join("")}</select></label>
        <label>重复验收验证时间<select id="setDuplicateAcceptanceDays">${[7, 15, 30, 90].map(day => `<option value="${day}" ${Number(settings.duplicate_acceptance_check_days || 7) === day ? "selected" : ""}>${day} 天</option>`).join("")}</select></label>
        <label>盘点提醒日<input id="setReminderDay" type="number" min="1" max="31" value="${settings.default_stocktake_reminder_day || 25}"></label>
        <label class="wide">项目代号（每行一个）<textarea id="setProjectCodes" rows="3">${escapeHtml((settings.project_codes || []).join("\n"))}</textarea></label>
      </div></div></details>
      <details class="settings-section" open><summary>文案设置</summary><div class="settings-inner"><div class="form-grid">
        <label class="wide">物料小助手开场白设置<textarea id="setAiWelcome" rows="3">${escapeHtml(settings.ai_welcome_message || aiWelcomeMessage())}</textarea></label>
        <label class="wide">入库通知文案<textarea id="setInboundNotice" rows="3">${escapeHtml(settings.notification_inbound_template || "有新的物料：“{name}”“{brand_model}”“{spec}”入库了，请按需领取。")}</textarea></label>
        <label class="wide">验收人入库通知文案<textarea id="setInboundParticipantNotice" rows="3">${escapeHtml(settings.notification_inbound_participant_template || "您验收的物料：“{name}”“{brand_model}”“{spec}”已入库，请按需领取。")}</textarea></label>
      </div></div></details>
      <details class="settings-section"><summary>首页与入口</summary><div class="settings-inner">
        <h3>物料态势指标</h3><div class="form-grid">${dashboardMetricChecks(settings.dashboard_metrics || [])}</div>
        <h3>仓库料卡系统入口可见角色</h3><div class="form-grid">${cardButtonRoleChecks(settings.card_button_roles || [])}</div>
      </div></details>
      <details class="settings-section"><summary>流程步骤可办理人</summary><div class="settings-inner">${workflowStepAssigneeTable(settings.workflow_step_assignees || {})}</div></details>
      <details class="settings-section" open><summary>角色权限</summary><div class="settings-inner">${rolePermissionTable(permissions.permission_keys || [])}</div></details>
    `;
    bindRolePermissionChoices();
    bindWorkflowStepAssigneeBuilders();
    document.getElementById("saveWorkflowSettings").addEventListener("click", saveWorkflowSettings);
  }

  async function saveWorkflowSettings() {
    const previousTemporaryEnabled = Boolean(system.boot?.workflow_settings?.temporary_inventory_enabled);
    const temporaryToggle = document.getElementById("setTemporaryInventoryEnabled");
    const saveButton = document.getElementById("saveWorkflowSettings");
    if (saveButton) saveButton.disabled = true;
    try {
      await systemApi("/api/system/role-permissions", { method: "POST", body: JSON.stringify({ permissions: collectRolePermissions() }) });
      await systemApi("/api/system/workflow-settings", { method: "POST", body: JSON.stringify({
        claim_leader_same_department: document.getElementById("setClaimSameDept").checked,
        acceptance_leader_locked_after_first_inspect: document.getElementById("setAccLeaderLock").checked,
        default_stocktake_reminder_day: document.getElementById("setReminderDay").value,
        allow_manual_approval_leader: document.getElementById("setManualApprovalLeader").checked,
        allow_multi_claim_leaders: document.getElementById("setMultiClaim").checked,
        single_item_inbound_enabled: document.getElementById("setSingleInbound").checked,
        temporary_inventory_enabled: temporaryToggle.checked,
        acceptance_material_photo_required: document.getElementById("setRequireMaterialPhoto").checked,
        acceptance_document_required: document.getElementById("setRequireAcceptanceDocument").checked,
        temporary_inventory_material_photo_required: document.getElementById("setRequireTemporaryPhoto").checked,
        temporary_inventory_document_required: document.getElementById("setRequireTemporaryDocument").checked,
        recycle_retention_days: document.getElementById("setRecycleDays").value,
        notification_retention_days: document.getElementById("setNotificationDays").value,
        duplicate_acceptance_check_days: document.getElementById("setDuplicateAcceptanceDays").value,
        project_codes: document.getElementById("setProjectCodes").value.split(/\n|,|，|；/).map(item => item.trim()).filter(Boolean),
        ai_welcome_message: document.getElementById("setAiWelcome").value,
        notification_inbound_template: document.getElementById("setInboundNotice").value,
        notification_inbound_participant_template: document.getElementById("setInboundParticipantNotice").value,
        dashboard_metrics: [...document.querySelectorAll("[data-dashboard-setting]:checked")].map(input => input.value).slice(0, 3),
        card_button_roles: [...document.querySelectorAll("[data-card-role]:checked")].map(input => input.value),
        workflow_step_assignees: collectWorkflowStepAssignees(),
      }) });
      system.boot = null;
      await loadSystemBoot();
      if (!system.boot.workflow_settings?.temporary_inventory_enabled && ["temporaryInventory", "temporaryTransfers"].includes(system.view)) {
        system.view = "query";
        temporaryInventory.clear();
        temporaryTransfers.clear();
      }
      toast("流程设置已保存");
      const home = document.getElementById("home");
      if (home) window.renderSystemDashboard(home);
    } catch (error) {
      if (temporaryToggle) temporaryToggle.checked = previousTemporaryEnabled;
      toast(error.message || "流程设置保存失败");
    } finally {
      if (saveButton) saveButton.disabled = false;
    }
  }

  async function loadBackupSettings() {
    const host = document.getElementById("backupSettings");
    try {
      const data = await systemApi("/api/system/backup-settings");
      renderBackupSettings(data.settings || {}, data.backups || []);
    } catch (error) {
      if (host) host.innerHTML = `<div class="empty-state">备份与恢复加载失败：${escapeHtml(error.message)}</div>`;
    }
  }

  function renderBackupSettings(settings, backups) {
    const host = document.getElementById("backupSettings");
    if (!host) return;
    host.innerHTML = `
      <div class="form-grid">
        <label><input id="backupEnabled" type="checkbox" ${settings.enabled ? "checked" : ""}> 启用定时备份</label>
        <label>备份频率（小时）<input id="backupFrequency" type="number" min="1" value="${Number(settings.frequency_hours || 24)}"></label>
        <label class="wide">备份位置<input id="backupDir" value="${escapeAttr(settings.backup_dir || "")}"></label>
        <label>保留份数<input id="backupRetention" type="number" min="1" value="${Number(settings.retention_count || 30)}"></label>
        <p class="hint wide">上次备份：${escapeHtml(settings.last_backup_at || "暂无")} ${settings.last_backup_file ? `；文件：${escapeHtml(settings.last_backup_file)}` : ""}${settings.last_error ? `；错误：${escapeHtml(settings.last_error)}` : ""}</p>
      </div>
      <div class="flow-tools">
        <button id="runBackupNow" class="primary">立即备份</button>
        <button id="refreshBackups">刷新备份列表</button>
        <input id="restoreBackupFile" type="file" accept=".db,application/octet-stream">
        <button id="restoreBackupUpload" class="danger">上传并恢复</button>
      </div>
      ${backupTable(backups)}
    `;
    document.getElementById("runBackupNow").addEventListener("click", runBackupNow);
    document.getElementById("refreshBackups").addEventListener("click", loadBackupSettings);
    document.getElementById("restoreBackupUpload").addEventListener("click", restoreBackupUpload);
    host.querySelectorAll("[data-backup-download]").forEach(button => {
      button.addEventListener("click", () => { window.location.href = `/api/system/backups/download?filename=${encodeURIComponent(button.dataset.backupDownload)}`; });
    });
    host.querySelectorAll("[data-backup-restore]").forEach(button => {
      button.addEventListener("click", () => restoreBackupByName(button.dataset.backupRestore));
    });
  }

  function backupTable(backups) {
    return `<table class="flow-table"><thead><tr><th>备份文件</th><th>时间</th><th>大小</th><th>操作</th></tr></thead><tbody>${(backups || []).map(item => `<tr><td>${escapeHtml(item.filename)}</td><td>${escapeHtml(item.created_at || "")}</td><td>${formatFileSize(item.size || 0)}</td><td class="mini-actions"><button data-backup-download="${escapeAttr(item.filename)}">下载</button><button class="danger" data-backup-restore="${escapeAttr(item.filename)}">恢复</button></td></tr>`).join("") || `<tr><td colspan="4">暂无备份文件</td></tr>`}</tbody></table>`;
  }

  async function loadRecycleBin() {
    const host = document.getElementById("recycleBin");
    if (!host) return;
    try {
      const data = await systemApi("/api/system/recycle-bin");
      system.recycleRows = data.items || [];
      renderRecycleBin(data.retention_days || 30);
    } catch (error) {
      host.innerHTML = `<div class="empty-state">回收站加载失败：${escapeHtml(error.message)}</div>`;
    }
  }

  function renderRecycleBin(retentionDays) {
    const host = document.getElementById("recycleBin");
    if (!host) return;
    const rows = system.recycleRows || [];
    const visible = pageRows("recycle", rows, 20);
    host.innerHTML = `<p class="hint">当前保留 ${Number(retentionDays || 30)} 天，过期后自动清理。</p><table class="flow-table"><thead><tr><th>类型</th><th>对象</th><th>删除人</th><th>删除时间</th><th>自动清理时间</th><th>操作</th></tr></thead><tbody>${visible.map(row => `<tr><td>${recycleTypeLabel(row.target_type)}</td><td>${escapeHtml(row.title || row.target_id || "")}</td><td>${escapeHtml(row.deleted_by_name || "")}</td><td>${escapeHtml(row.deleted_at || "")}</td><td>${escapeHtml(row.purge_after || "")}</td><td class="mini-actions"><button data-recycle-restore="${row.id}">恢复</button><button class="danger" data-recycle-delete="${row.id}">彻底删除</button></td></tr>`).join("") || `<tr><td colspan="6">回收站暂无数据</td></tr>`}</tbody></table>${paginationHtml("recycle", rows, 20)}`;
    host.querySelectorAll("[data-recycle-restore]").forEach(button => button.addEventListener("click", () => restoreRecycleEntry(Number(button.dataset.recycleRestore))));
    host.querySelectorAll("[data-recycle-delete]").forEach(button => button.addEventListener("click", () => deleteRecycleEntry(Number(button.dataset.recycleDelete))));
    bindPagination(host, "recycle", () => renderRecycleBin(retentionDays));
  }

  function recycleTypeLabel(type) {
    const labels = {
      workflow: "流程",
      material: "物料",
      shelf: "货架",
      stocktake: "盘点",
      department: "部门",
      semifinished_inventory: "半成品库存",
      defective_semifinished: "不合格半成品",
      finished_inventory: "成品库存",
      defective_finished: "不合格成品",
    };
    return labels[type] || type || "";
  }

  async function restoreRecycleEntry(id) {
    if (!confirm("确认恢复该回收站条目？")) return;
    await systemApi(`/api/system/recycle-bin/${id}/restore`, { method: "POST", body: JSON.stringify({}) });
    toast("已恢复");
    await loadRecycleBin();
  }

  async function deleteRecycleEntry(id) {
    if (!confirm("确认彻底删除该回收站条目？此操作不可恢复。")) return;
    await systemApi(`/api/system/recycle-bin/${id}`, { method: "DELETE" });
    toast("已彻底删除");
    await loadRecycleBin();
  }

  async function saveBackupSettings() {
    const data = await systemApi("/api/system/backup-settings", {
      method: "POST",
      body: JSON.stringify({
        enabled: document.getElementById("backupEnabled").checked,
        backup_dir: document.getElementById("backupDir").value,
        frequency_hours: document.getElementById("backupFrequency").value,
        retention_count: document.getElementById("backupRetention").value,
      }),
    });
    toast("备份设置已保存");
    renderBackupSettings(data.settings || {}, data.backups || []);
  }

  async function runBackupNow() {
    const data = await systemApi("/api/system/backups/run", { method: "POST", body: JSON.stringify({}) });
    toast("备份已完成");
    renderBackupSettings(data.settings || {}, data.backups || []);
  }

  async function restoreBackupByName(filename) {
    if (!confirm(`确认恢复备份 ${filename}？当前数据库会先生成一份恢复前备份。`)) return;
    if (!confirm("恢复会覆盖当前业务数据，请再次确认。")) return;
    await systemApi("/api/system/backups/restore", { method: "POST", body: JSON.stringify({ filename, confirm: true }) });
    toast("备份已恢复，请刷新页面重新加载数据");
    system.boot = null;
    await loadSystemBoot();
    renderSystemMain();
  }

  async function restoreBackupUpload() {
    const file = document.getElementById("restoreBackupFile").files?.[0];
    if (!file) throw new Error("请先选择备份数据库文件");
    if (!confirm(`确认上传并恢复 ${file.name}？当前数据库会先生成恢复前备份。`)) return;
    if (!confirm("恢复会覆盖当前业务数据，请再次确认。")) return;
    const formData = new FormData();
    formData.append("file", file);
    const response = await fetch("/api/system/backups/restore", { method: "POST", body: formData });
    const data = await response.json();
    if (!response.ok || data.success === false) throw new Error(data.error || "恢复失败");
    toast("备份已恢复，请刷新页面重新加载数据");
    system.boot = null;
    await loadSystemBoot();
    renderSystemMain();
  }

  async function loadAuditLogs() {
    const params = new URLSearchParams();
    const keyword = document.getElementById("auditKeyword")?.value || "";
    const dateFrom = document.getElementById("auditFrom")?.value || "";
    const dateTo = document.getElementById("auditTo")?.value || "";
    if (keyword) params.set("keyword", keyword);
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    const data = await systemApi(`/api/system/audit-logs?${params.toString()}`);
    document.getElementById("auditLogList").innerHTML = auditLogTable(data.items || []);
  }

  function auditLogTable(rows) {
    return `<table class="flow-table"><thead><tr><th>时间</th><th>账号</th><th>操作</th><th>对象</th><th>结果</th><th>IP</th></tr></thead><tbody>${(rows || []).map(row => `<tr><td>${escapeHtml(row.created_at || "")}</td><td>${escapeHtml(row.username || "")}</td><td>${escapeHtml(row.action || "")}</td><td>${escapeHtml(row.target_type || "")} ${escapeHtml(row.target_id || "")}</td><td>${escapeHtml(row.summary || "")}</td><td>${escapeHtml(row.ip_address || "")}</td></tr>`).join("") || `<tr><td colspan="6">暂无审计记录</td></tr>`}</tbody></table>`;
  }

  function formatFileSize(bytes) {
    const value = Number(bytes || 0);
    if (value > 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
    if (value > 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${value} B`;
  }

  const workflowStepDefinitions = [
    ["acceptance", "物料验收", [["acceptance", "验收"], ["leader_acceptance", "领导签字"], ["inbound", "入库"]]],
    ["claim", "申领/出库", [["leader_claim", "申领审批"], ["outbound", "出库"]]],
    ["borrow", "借用申请", [["leader_borrow", "审批领导审批"], ["borrow_outbound", "仓库出库"]]],
    ["borrow_return", "借用归还", [["return_inbound", "仓库验收入库"]]],
    ["common_material", "常用物料申请", [["leader_common_material", "审批"]]],
    ["supply", "供货申请", [["leader_supply", "审批"], ["supply_outbound", "出库"]]],
    ["supply_return", "供货回寄", [["supply_return_inbound", "回寄验收"]]],
    ["supply_extension", "供货延期", [["leader_supply_extension", "延期审批"]]],
    ["semifinished", "半成品验收", [["acceptance", "验收"], ["leader_acceptance", "领导签字"], ["inbound", "入库"]]],
    ["finished", "成品验收", [["acceptance", "验收"], ["leader_acceptance", "领导签字"], ["inbound", "入库"]]],
  ];

  function workflowStepAssigneeTable(settings) {
    return workflowStepDefinitions.map(([formType, formLabel, steps]) => {
      const rows = steps.map(([stepCode, stepLabel]) => {
        const raw = settings?.[formType]?.[stepCode] || {};
        const config = Array.isArray(raw) ? { roles: [], users: raw } : { roles: raw.roles || [], users: raw.users || raw.user_ids || [] };
        const selectedRoles = new Set((config.roles || []).map(String));
        const selectedUsers = (config.users || []).map(Number).filter(Boolean);
        return `<tr><td>${stepLabel}</td><td>${workflowStepAssigneeBuilder(formType, stepCode, selectedRoles, selectedUsers)}</td></tr>`;
      }).join("");
      return `<details class="settings-section"><summary>${escapeHtml(formLabel)}</summary><div class="settings-inner"><table class="flow-table"><thead><tr><th>步骤</th><th>可办理角色和指定账号（都留空表示不限制）</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
    }).join("");
  }

  function workflowStepAssigneeBuilder(formType, stepCode, selectedRoles, selectedUsers) {
    const roles = (system.boot.roles || []).map(role => `<label><input type="checkbox" data-step-role value="${escapeAttr(role.code)}" ${selectedRoles.has(String(role.code)) ? "checked" : ""}> ${escapeHtml(role.name)}</label>`).join("");
    return `<div class="assignee-builder" data-step-builder="${formType}:${stepCode}">
      <div class="assignee-roles">${roles}</div>
      <input data-step-user-search placeholder="搜索姓名 / 账号 / 部门">
      <select data-step-user-select size="4">${workflowStepUserOptions("")}</select>
      <button type="button" data-step-add-user>添加账号</button>
      <div class="assignee-selected" data-step-selected-users>${workflowStepSelectedUsers(selectedUsers)}</div>
    </div>`;
  }

  function workflowStepUserOptions(keyword) {
    const text = String(keyword || "").trim().toLowerCase();
    return (system.boot.users || []).filter(user => {
      const haystack = `${user.display_name || ""} ${user.username || ""} ${user.department || ""}`.toLowerCase();
      return !text || haystack.includes(text);
    }).map(user => `<option value="${user.id}">${escapeHtml(user.display_name)} · ${escapeHtml(user.department || "")} · ${escapeHtml(user.username || "")}</option>`).join("");
  }

  function workflowStepSelectedUsers(ids) {
    return (ids || []).map(id => {
      const user = (system.boot.users || []).find(item => Number(item.id) === Number(id));
      if (!user) return "";
      return `<button type="button" class="assignee-chip" data-step-user-id="${user.id}">${escapeHtml(user.display_name)} ×</button>`;
    }).join("");
  }

  function bindWorkflowStepAssigneeBuilders() {
    document.querySelectorAll("[data-step-builder]").forEach(builder => {
      const search = builder.querySelector("[data-step-user-search]");
      const select = builder.querySelector("[data-step-user-select]");
      const selectedHost = builder.querySelector("[data-step-selected-users]");
      const selectedIds = () => [...selectedHost.querySelectorAll("[data-step-user-id]")].map(button => Number(button.dataset.stepUserId));
      const refreshSelect = () => { select.innerHTML = workflowStepUserOptions(search.value); };
      const addSelected = () => {
        const userId = Number(select.value || 0);
        if (!userId || selectedIds().includes(userId)) return;
        selectedHost.insertAdjacentHTML("beforeend", workflowStepSelectedUsers([userId]));
        bindSelectedRemove();
      };
      const bindSelectedRemove = () => {
        selectedHost.querySelectorAll("[data-step-user-id]").forEach(button => {
          button.onclick = () => button.remove();
        });
      };
      search.addEventListener("input", refreshSelect);
      builder.querySelector("[data-step-add-user]").addEventListener("click", addSelected);
      select.addEventListener("dblclick", addSelected);
      bindSelectedRemove();
    });
  }

  function collectWorkflowStepAssignees() {
    const result = {};
    workflowStepDefinitions.forEach(([formType, , steps]) => {
      result[formType] = {};
      steps.forEach(([stepCode]) => { result[formType][stepCode] = { roles: [], users: [] }; });
    });
    document.querySelectorAll("[data-step-builder]").forEach(builder => {
      const [formType, stepCode] = builder.dataset.stepBuilder.split(":");
      result[formType] = result[formType] || {};
      result[formType][stepCode] = {
        roles: [...builder.querySelectorAll("[data-step-role]:checked")].map(input => input.value),
        users: [...builder.querySelectorAll("[data-step-user-id]")].map(button => Number(button.dataset.stepUserId)).filter(Boolean),
      };
    });
    return result;
  }

  function rolePermissionTable(keys) {
    const labels = {
      view_query: "\u67e5\u770b\u7269\u6599\u67e5\u8be2",
      view_flow: "\u67e5\u770b\u6d41\u7a0b\u4e2d\u5fc3",
      view_outbound: "\u67e5\u770b\u51fa\u5e93\u6a21\u5757",
      view_stats: "\u67e5\u770b\u7edf\u8ba1\u6a21\u5757",
      view_stocktake: "\u67e5\u770b\u76d8\u70b9\u6a21\u5757",
      view_borrow: "查看借用模块",
      view_logs: "查看日志中心",
      view_recycle: "查看回收站权限",
      view_my_inspections: "查看我的验收",
      view_my_started: "查看我的发起",
      start_acceptance: "\u9a8c\u6536\u6d41\u7a0b\u53d1\u8d77\u6743",
      start_claim: "\u7533\u9886\u6d41\u7a0b\u53d1\u8d77\u6743",
      start_borrow: "借用流程发起权",
      start_stocktake: "\u53d1\u8d77\u76d8\u70b9\u5355",
      add_material: "添加物料",
      edit_acceptance: "\u4fee\u6539\u9a8c\u6536\u5355",
      edit_claim: "\u4fee\u6539\u7533\u9886\u5355",
      edit_borrow: "修改借用单",
      edit_outbound: "\u4fee\u6539\u51fa\u5e93\u5355",
      edit_stocktake: "\u4fee\u6539\u76d8\u70b9\u5355",
      edit_department: "\u4fee\u6539\u90e8\u95e8",
      edit_material: "\u4fee\u6539\u7269\u6599",
      delete_material_attachment: "删除物料附件",
      read_semifinished_inventory: "\u534a\u6210\u54c1\u5e93\u53ea\u8bfb",
      write_semifinished_inventory: "\u4fee\u6539\u534a\u6210\u54c1\u5e93",
      read_finished_inventory: "\u6210\u54c1\u5e93\u53ea\u8bfb",
      write_finished_inventory: "\u4fee\u6539\u6210\u54c1\u5e93",
      view_temporary_inventory: "查看临时库",
      manage_temporary_inventory: "管理临时库",
    };
    const exclusiveGroup = key => key.includes("_semifinished_inventory") ? "semifinished" : (key.includes("_finished_inventory") ? "finished" : "");
    const groups = [
      ["模块查看", ["view_query", "view_flow", "view_outbound", "view_stats", "view_stocktake", "view_borrow", "view_logs", "view_recycle", "view_my_inspections", "view_my_started"]],
      ["流程发起", ["start_acceptance", "start_claim", "start_borrow", "start_stocktake", "add_material"]],
      ["办理修改", ["edit_acceptance", "edit_claim", "edit_borrow", "edit_outbound", "edit_stocktake"]],
      ["库存权限", ["read_semifinished_inventory", "write_semifinished_inventory", "read_finished_inventory", "write_finished_inventory", "view_temporary_inventory", "manage_temporary_inventory"]],
      ["基础资料", ["edit_department", "edit_material", "delete_material_attachment"]],
    ];
    const usedKeys = new Set(groups.flatMap(group => group[1]));
    const extraKeys = (keys || []).filter(key => !usedKeys.has(key));
    if (extraKeys.length) groups.push(["其他权限", extraKeys]);
    const permissionInput = (role, key) => {
      const group = exclusiveGroup(key);
      const mode = key.startsWith("read_") ? "read" : (key.startsWith("write_") ? "write" : "");
      const extra = group && mode ? ` data-inventory-mode="${role.code}:${group}:${mode}"` : "";
      return `<label class="permission-option"><input type="checkbox" data-role-perm="${role.code}:${key}"${extra} ${system.boot.role_permissions?.[role.code]?.[key] ? "checked" : ""} ${role.code === "admin" ? "disabled" : ""}> <span>${escapeHtml(labels[key] || key)}</span></label>`;
    };
    const cards = (system.boot.roles || []).map(role => `<details class="permission-card" open><summary>${escapeHtml(role.name)}${role.code === "admin" ? " · 全权限" : ""}</summary><div class="permission-card-body">${groups.map(([groupLabel, groupKeys]) => {
      const visibleKeys = groupKeys.filter(key => (keys || []).includes(key));
      if (!visibleKeys.length) return "";
      return `<div class="permission-group"><div class="permission-group-title">${escapeHtml(groupLabel)}</div><div class="permission-options">${visibleKeys.map(key => permissionInput(role, key)).join("")}</div></div>`;
    }).join("")}</div></details>`).join("");
    return `<div class="permission-cards">${cards}</div>`;
  }

  function bindRolePermissionChoices() {
    document.querySelectorAll("[data-role-perm$=':manage_temporary_inventory']").forEach(input => {
      input.addEventListener("change", () => {
        if (!input.checked) return;
        const role = input.dataset.rolePerm.split(":")[0];
        const viewInput = document.querySelector(`[data-role-perm="${role}:view_temporary_inventory"]`);
        if (viewInput) viewInput.checked = true;
      });
    });
    document.querySelectorAll("[data-inventory-mode]").forEach(input => {
      input.addEventListener("change", () => {
        if (!input.checked) return;
        const [role, inventory, mode] = input.dataset.inventoryMode.split(":");
        const otherMode = mode === "read" ? "write" : "read";
        const other = document.querySelector(`[data-inventory-mode="${role}:${inventory}:${otherMode}"]`);
        if (other) other.checked = false;
      });
    });
  }

  function dashboardMetricChecks(selected) {
    const selectedSet = new Set(selected.length ? selected : ["total_amount", "month_in", "month_out"]);
    const labels = { total_amount: "总金额", month_in: "月入库", month_out: "月出库", today_in: "今日入库", today_out: "今日出库", materials: "总物料数" };
    return Object.entries(labels).map(([key, label]) => `<label><input type="checkbox" data-dashboard-setting value="${key}" ${selectedSet.has(key) ? "checked" : ""}> ${label}</label>`).join("");
  }

  function cardButtonRoleChecks(selected) {
    const selectedSet = new Set(selected.length ? selected : ["admin", "warehouse"]);
    return (system.boot.roles || []).map(role => `<label><input type="checkbox" data-card-role value="${role.code}" ${selectedSet.has(role.code) ? "checked" : ""}> ${escapeHtml(role.name)}</label>`).join("");
  }

  function collectRolePermissions() {
    const permissions = {};
    (system.boot.roles || []).forEach(role => { permissions[role.code] = {}; });
    document.querySelectorAll("[data-role-perm]").forEach(input => {
      const [role, key] = input.dataset.rolePerm.split(":");
      permissions[role] = permissions[role] || {};
      permissions[role][key] = input.checked;
    });
    Object.values(permissions).forEach(rolePerms => {
      if (rolePerms.write_semifinished_inventory) rolePerms.read_semifinished_inventory = false;
      if (rolePerms.write_finished_inventory) rolePerms.read_finished_inventory = false;
      if (rolePerms.manage_temporary_inventory) rolePerms.view_temporary_inventory = true;
    });
    return permissions;
  }

  async function loadAiConfig() {
    const config = await systemApi("/api/ai/config");
    document.getElementById("aiConfig").innerHTML = `<div class="form-grid"><label>模型地址<input id="aiBaseUrl" value="${escapeAttr(config.base_url || "")}" placeholder="https://.../v1"></label><label>API Key<input id="aiKey" type="password" placeholder="${config.has_api_key ? "已保存，留空不改" : "请输入"}"></label><label>模型名称<select id="aiModel">${aiModelOptions([], config.model || "")}</select></label><label style="align-self:end;"><button id="connectAiModels" type="button">连接并获取模型</button></label><label class="wide">Skill 路径<input id="aiSkill" value="${escapeAttr(config.skill_path || "")}"></label><label class="wide">数据库 API 地址<input id="dbApi" value="${escapeAttr(config.database_api || "/api")}"></label><p class="hint wide" id="aiModelStatus">填写模型地址和 API Key 后点击连接，选择模型名称再保存。</p></div>`;
    document.getElementById("saveAiConfig").addEventListener("click", saveAiConfig);
    document.getElementById("connectAiModels").addEventListener("click", connectAiModels);
  }

  function aiModelOptions(models, current) {
    const rows = (models || []).slice();
    if (current && !rows.some(model => model.id === current)) rows.unshift({ id: current, owned_by: "已保存" });
    if (!rows.length) return `<option value="">请先连接模型服务</option>`;
    return rows.map(model => `<option value="${escapeAttr(model.id)}" ${model.id === current ? "selected" : ""}>${escapeHtml(model.id)}${model.owned_by ? ` · ${escapeHtml(model.owned_by)}` : ""}</option>`).join("");
  }

  async function connectAiModels() {
    const button = document.getElementById("connectAiModels");
    const status = document.getElementById("aiModelStatus");
    const select = document.getElementById("aiModel");
    const current = select.value;
    button.disabled = true;
    status.textContent = "连接中...";
    try {
      const data = await systemApi("/api/ai/models", {
        method: "POST",
        body: JSON.stringify({
          ai_base_url: document.getElementById("aiBaseUrl").value,
          ai_api_key: document.getElementById("aiKey").value,
        }),
      });
      document.getElementById("aiBaseUrl").value = data.base_url || document.getElementById("aiBaseUrl").value;
      select.innerHTML = aiModelOptions(data.models || [], current);
      status.textContent = `连接成功，找到 ${(data.models || []).length} 个模型。`;
      toast("模型列表已加载");
    } catch (error) {
      status.textContent = error.message;
      toast(error.message);
    } finally {
      button.disabled = false;
    }
  }

  async function saveAiConfig() {
    await systemApi("/api/ai/config", { method: "POST", body: JSON.stringify({ ai_base_url: document.getElementById("aiBaseUrl").value, ai_model: document.getElementById("aiModel").value, ai_api_key: document.getElementById("aiKey").value, ai_skill_path: document.getElementById("aiSkill").value, database_api: document.getElementById("dbApi").value }) });
    toast("\u7269\u6599\u5c0f\u52a9\u624b\u914d\u7f6e\u5df2\u4fdd\u5b58");
  }

  async function askAi() {
    const data = await systemApi("/api/ai/chat", { method: "POST", body: JSON.stringify({ question: document.getElementById("aiQuestion").value }) });
    renderAiMessage(document.getElementById("aiAnswer"), data.answer || "");
  }

  function itemsTable(items) {
    return `<table class="flow-table"><thead><tr><th>物料</th><th>数量</th><th>到货/合格</th><th>单价</th></tr></thead><tbody>${items.map(i => `<tr><td>${escapeHtml(i.material_code)}<br>${escapeHtml(i.material_name)}<br><small>${escapeHtml(i.brand_model || "")} ${escapeHtml(i.spec || "")}</small></td><td>${formatQty(i.request_quantity)}</td><td>${formatQty(i.arrival_quantity)} / ${formatQty(i.qualified_quantity)}</td><td>${money(i.unit_price)}</td></tr>`).join("")}</tbody></table>`;
  }

  function materialTable(rows) {
    return `<table class="flow-table"><thead><tr><th>编号</th><th>名称</th><th>品牌型号</th><th>规格</th><th>余量</th><th>位置</th></tr></thead><tbody>${rows.map(m => `<tr><td>${escapeHtml(m.material_code)}</td><td>${escapeHtml(m.name)}</td><td>${escapeHtml(m.brand_model || "")}</td><td>${escapeHtml(m.spec || "")}</td><td>${formatQty(m.quantity)} ${escapeHtml(m.unit || "")}</td><td>${escapeHtml(m.shelf_name || "")} ${m.layer_number || ""} ${escapeHtml(m.zone_name || "")}</td></tr>`).join("") || `<tr><td colspan="6">暂无数据</td></tr>`}</tbody></table>`;
  }

  function flowItemsTable(form) {
    const items = form.items || [];
    if (isProductionFlow(form)) {
      const item = items[0] || {};
      return `${productionHeaderTable(form, item)}${productionComponentsTable(item)}${productionSerialsTable(item)}<h3>验收结果</h3><table class="flow-table"><thead><tr><th>验收/合格/不合格</th><th>外观/功能/性能</th><th>成本价</th><th>位置</th></tr></thead><tbody>${items.map(i => `<tr><td>${formatQty(i.arrival_quantity)} / ${formatQty(i.qualified_quantity)} / ${formatQty(i.unqualified_quantity)}</td><td>${formatQty(i.data?.appearance_ok_quantity || 0)} / ${formatQty(i.data?.function_ok_quantity || 0)} / ${formatQty(i.data?.performance_ok_quantity || 0)}</td><td>${money(i.unit_price || 0)}</td><td>${escapeHtml(locationText(i.data || {}))}</td></tr>`).join("")}</tbody></table>${flowTotalHtml(items, "arrival_quantity")}`;
    }
    if (form.form_type === "borrow" || form.form_type === "borrow_return") {
      return `<table class="flow-table"><thead><tr><th>类型</th><th>来源</th><th>编号</th><th>名称</th><th>品牌</th><th>规格</th><th>申请数量</th><th>出库/入库数量</th></tr></thead><tbody>${items.map(i => `<tr><td>${borrowItemTypeLabel(i.data?.borrow_item_type || (i.material_id ? "material" : ""))}</td><td>${ClaimSourceUI.sourceBadge(i.stock_source)}</td><td>${escapeHtml(i.material_code || "")}</td><td>${escapeHtml(i.material_name || "")}</td><td>${escapeHtml(i.brand_model || "无")}</td><td>${escapeHtml(i.spec || "")}</td><td>${formatQty(i.request_quantity || 0)} ${escapeHtml(i.unit || "")}</td><td>${formatQty(i.outbound_quantity || i.approved_quantity || 0)} ${escapeHtml(i.unit || "")}</td></tr>`).join("")}</tbody></table>`;
    }
    if (form.form_type === "claim") {
      return `<table class="flow-table"><thead><tr><th>物料</th><th>来源</th><th>申领数量</th><th>该来源库存</th><th>批次库存</th><th>已出库批次</th></tr></thead><tbody>${items.map(i => `<tr><td>${escapeHtml(i.material_code)}<br>${escapeHtml(i.material_name)}<br><small>${escapeHtml(i.brand_model || "")} ${escapeHtml(i.spec || "")}</small></td><td>${ClaimSourceUI.sourceBadge(i.stock_source)}</td><td>${formatQty(i.request_quantity)} ${escapeHtml(i.unit || "")}</td><td>${formatQty(i.stock_quantity || 0)} ${escapeHtml(i.unit || "")}</td><td>${batchSummaryHtml(i.batches || [])}</td><td>${batchSummaryHtml(i.data?.consumed_batches || [])}</td></tr>`).join("")}</tbody></table>${claimOutboundTotalHtml(items)}`;
    }
    return `<table class="flow-table"><thead><tr><th>物料</th><th>采购申请人</th><th>采购/到货/合格</th><th>入库批次</th><th>单价</th></tr></thead><tbody>${items.map(i => `<tr><td>${escapeHtml(i.material_code)}<br>${escapeHtml(i.material_name)}<br><small>${escapeHtml(i.brand_model || "")} ${escapeHtml(i.spec || "")}</small></td><td>${escapeHtml(i.purchase_applicant || i.data?.purchase_applicant || "")}</td><td>${formatQty(i.request_quantity)} / ${formatQty(i.arrival_quantity)} / ${formatQty(i.qualified_quantity)}</td><td>${escapeHtml(i.data?.inbound_batch_no || expectedInboundBatchNo(i))}</td><td>${money(i.unit_price)}</td></tr>`).join("")}</tbody></table>${flowTotalHtml(items, "arrival_quantity")}`;
  }

  function claimOutboundTotalHtml(items) {
    const total = (items || []).reduce((sum, item) => {
      const consumed = item?.data?.consumed_batches;
      if (Array.isArray(consumed) && consumed.length) {
        return sum + consumed.reduce(
          (batchTotal, batch) => batchTotal
            + (Number(batch.quantity || 0) * Number(batch.unit_price || 0)),
          0,
        );
      }
      // Before the warehouse action there is no actual batch price. Keep the
      // legacy item price as a compatibility fallback without inventing a cost.
      return sum + (Number(item.outbound_quantity || 0) * Number(item.unit_price || 0));
    }, 0);
    return `<p style="text-align:right;margin-top:8px;font-weight:bold;color:#1e40af">合计金额：${money(total)}</p>`;
  }

  function flowTotalHtml(items, qtyField) {
    const total = (items || []).reduce((sum, i) => sum + (Number(i[qtyField] || 0) * Number(i.unit_price || 0)), 0);
    return `<p style="text-align:right;margin-top:8px;font-weight:bold;color:#1e40af">合计金额：${money(total)}</p>`;
  }

  function expectedInboundBatchNo(item) {
    const date = new Date();
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const d = String(date.getDate()).padStart(2, "0");
    return `${y}${m}${d}${item.material_code || ""}`;
  }

  function itemsTableV2(items) {
    return `<table class="flow-table"><thead><tr><th>物料</th><th>申请/到货/合格</th><th>库存总量</th><th>批次库存</th><th>单价</th></tr></thead><tbody>${(items || []).map(i => `<tr><td>${escapeHtml(i.material_code)}<br>${escapeHtml(i.material_name)}<br><small>${escapeHtml(i.brand_model || "")} ${escapeHtml(i.spec || "")}</small></td><td>${formatQty(i.request_quantity)} / ${formatQty(i.arrival_quantity)} / ${formatQty(i.qualified_quantity)}</td><td>${formatQty(i.stock_quantity || 0)} ${escapeHtml(i.unit || "")}</td><td>${batchSummaryHtml(i.batches || i.data?.consumed_batches || [])}</td><td>${money(i.unit_price)}</td></tr>`).join("")}</tbody></table>`;
  }

  function materialTableV2(rows) {
    const canEdit = canModifyMaterial();
    const canDelete = canDeleteMaterial();
    return `<table class="flow-table"><thead><tr><th>\u7f16\u53f7</th><th>\u540d\u79f0</th><th>\u54c1\u724c\u578b\u53f7</th><th>\u89c4\u683c</th><th>\u91c7\u8d2d\u7533\u8bf7\u4eba</th><th>\u5e93\u5b58\u603b\u91cf</th><th>\u6279\u6b21\u4fe1\u606f</th><th>\u4f4d\u7f6e</th><th>\u64cd\u4f5c</th></tr></thead><tbody>${rows.map(m => `<tr><td>${escapeHtml(m.material_code)}</td><td>${escapeHtml(m.name)}</td><td>${escapeHtml(m.brand_model || "")}</td><td>${escapeHtml(m.spec || "")}</td><td>${escapeHtml(m.purchase_applicant || "")}</td><td>${formatQty(m.quantity)} ${escapeHtml(m.unit || "")}</td><td>${escapeHtml(m.batch_summary || "")}</td><td>${escapeHtml(m.shelf_name || "")} ${m.layer_number || ""} ${escapeHtml(m.zone_name || "")}</td><td class="mini-actions"><button type="button" data-view-material="${m.id}">\u67e5\u770b\u8be6\u60c5</button>${canEdit ? `<button type="button" data-edit-material="${m.id}">\u4fee\u6539</button>` : ""}${canDelete ? `<button type="button" class="danger" data-delete-material="${m.id}">\u5220\u9664</button>` : ""}</td></tr>`).join("") || `<tr><td colspan="9">\u6682\u65e0\u6570\u636e</td></tr>`}</tbody></table>`;
  }

  function batchSummaryHtml(batches) {
    return (batches || []).map(batch => `${escapeHtml(batch.batch_no || "")}：${formatQty(batch.quantity || 0)}`).join("<br>") || "";
  }

  function productionComponentsText(item) {
    const mats = item.data?.estimated_material_components || item.data?.material_components || item.data?.components || [];
    const semis = item.data?.estimated_semifinished_components || item.data?.semifinished_components || [];
    const matText = (mats || []).map(c => `${escapeHtml(c.material_code || "")} ${escapeHtml(c.material_name || c.name || "")} ${escapeHtml(c.batch_no || "")} 单台${formatQty(c.per_unit_quantity || 0)}`).join("<br>");
    const semiText = (semis || []).map(c => `${escapeHtml(c.name || "")} 单台${formatQty(c.per_unit_quantity || 0)}`).join("<br>");
    return [matText, semiText].filter(Boolean).join("<br>");
  }

  function productionComponentsTable(item) {
    const mats = item.data?.estimated_material_components || item.data?.material_components || item.data?.components || [];
    const semis = item.data?.estimated_semifinished_components || item.data?.semifinished_components || [];
    const matRows = (mats || []).map(c => `<tr><td>物料</td><td>${escapeHtml(c.material_code || "")} ${escapeHtml(c.material_name || c.name || "")}</td><td>${escapeHtml(c.batch_no || "")}</td><td>${formatQty(c.per_unit_quantity || 0)}</td><td>${money(c.unit_cost || 0)}</td></tr>`).join("");
    const semiRows = (semis || []).map(c => `<tr><td>半成品</td><td>${escapeHtml(c.name || "")}</td><td>${escapeHtml(c.spec || "")}</td><td>${formatQty(c.per_unit_quantity || 0)}</td><td>${money(c.unit_cost || 0)}</td></tr>`).join("");
    const rows = `${matRows}${semiRows}`;
    return `<table class="flow-table"><thead><tr><th>类型</th><th>名称</th><th>批次/规格</th><th>单台用量</th><th>单价</th></tr></thead><tbody>${rows || `<tr><td colspan="5">暂无组成明细</td></tr>`}</tbody></table>`;
  }

  function productionSerialsTable(item) {
    const rows = item.data?.serial_items || [];
    if (!rows.length) return "";
    return `<h3>单件编号</h3><table class="flow-table"><thead><tr><th>编号</th><th>结果</th><th>异常情况</th></tr></thead><tbody>${rows.map(row => `<tr><td>${escapeHtml(row.serial_no || "")}</td><td>${row.qualified ? "合格" : "不合格"}</td><td>${escapeHtml((row.abnormal_conditions || []).join("、"))}</td></tr>`).join("")}</tbody></table>`;
  }

  function recordsTable(rows) {
    return `<table class="flow-table"><thead><tr><th>日期</th><th>单号</th><th>物料</th><th>批次</th><th>数量</th><th>金额</th><th>仓库</th></tr></thead><tbody>${rows.map(r => `<tr><td>${formatDateCn(r.operation_date)}</td><td>${escapeHtml(r.form_no || "")}</td><td>${escapeHtml(r.material_code)} ${escapeHtml(r.material_name)}</td><td>${escapeHtml(r.batch_no || "")}</td><td>${formatQty(r.quantity)}</td><td>${money(r.amount || 0)}</td><td>${r.warehouse_type ? (r.warehouse_type === "rd" ? "研发材料库" : "办公用品库") : ""}</td></tr>`).join("") || `<tr><td colspan="7">暂无数据</td></tr>`}</tbody></table>`;
  }

  function stocktakeTable(form) {
    return `<h2>${escapeHtml(form.form_no || "")}</h2><p>\u76d8\u70b9\u8303\u56f4\uff1a${formatDateCn(form.date_from)} \u81f3 ${formatDateCn(form.date_to)}\uff0c\u76d8\u70b9\u4eba\uff1a${escapeHtml(form.checker_signature || "")}\uff0c\u65e5\u671f\uff1a${formatDateCn(form.checker_date)}</p><p>\u76d1\u76d8\u7b7e\u5b57\uff1a${escapeHtml(form.supervisor_signature || "\u5f85\u7b7e\u5b57")}\uff0c\u65e5\u671f\uff1a${formatDateCn(form.supervisor_date)}</p><table class="flow-table"><thead><tr><th>\u7269\u6599</th><th>\u4f4d\u7f6e</th><th>\u8d26\u9762\u5e93\u5b58</th><th>\u5e93\u5b58\u91d1\u989d</th><th>\u671f\u95f4\u5165\u5e93</th><th>\u671f\u95f4\u51fa\u5e93</th></tr></thead><tbody>${(form.items || []).map(i => `<tr><td>${escapeHtml(i.material_code)} ${escapeHtml(i.material_name)}</td><td>${escapeHtml(i.location_text || "")}</td><td>${formatQty(i.book_quantity)}</td><td>${money(i.stock_amount)}</td><td>${formatQty(i.period_in)}</td><td>${formatQty(i.period_out)}</td></tr>`).join("")}</tbody></table>`;
    return `<h2>${form.form_no}</h2><p>盘点范围：${form.date_from} 至 ${form.date_to}，盘点人：${escapeHtml(form.checker_signature || "")}，日期：${form.checker_date || ""}</p><table class="flow-table"><thead><tr><th>物料</th><th>位置</th><th>账面库存</th><th>库存金额</th><th>期间入库</th><th>期间出库</th></tr></thead><tbody>${form.items.map(i => `<tr><td>${escapeHtml(i.material_code)} ${escapeHtml(i.material_name)}</td><td>${escapeHtml(i.location_text || "")}</td><td>${formatQty(i.book_quantity)}</td><td>${money(i.stock_amount)}</td><td>${formatQty(i.period_in)}</td><td>${formatQty(i.period_out)}</td></tr>`).join("")}</tbody></table><p>监盘人签字：________________ 日期：__________</p>`;
  }

  function exportTableCsv(hostId, filename) {
    const text = [...document.querySelectorAll(`#${hostId} table tr`)].map(tr => [...tr.children].map(td => `"${td.textContent.replaceAll('"', '""')}"`).join(",")).join("\n");
    const blob = new Blob([text], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function printElement(hostId) {
    const content = document.getElementById(hostId).innerHTML;
    printHtml("打印", content);
  }

  function printHtml(title, content) {
    const win = window.open("", "_blank");
    win.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(title)}</title><style>@page{margin:14mm}body{font-family:Arial,'Microsoft YaHei',sans-serif;padding:0}.flow-table{width:100%;border-collapse:collapse}.flow-table th,.flow-table td{border:1px solid #999;padding:6px;text-align:left}section{break-inside:avoid-page;page-break-inside:avoid}</style></head><body>${content}</body></html>`);
    win.document.close();
    let printed = false;
    const runPrint = () => {
      if (printed) return;
      printed = true;
      win.focus();
      win.print();
      setTimeout(() => win.close(), 250);
    };
    win.onload = runPrint;
    setTimeout(runPrint, 120);
  }

  function renderInventoryTabs(container, tabs, onTabChange) {
    const div = document.createElement("div");
    div.className = "inventory-tabs";
    tabs.forEach(tab => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.dataset.tab = tab.key;
      btn.textContent = tab.label;
      if (tab.key === "qualified") btn.classList.add("active");
      btn.addEventListener("click", () => {
        div.querySelectorAll("button").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        onTabChange(tab.key);
      });
      div.appendChild(btn);
    });
    container.appendChild(div);
  }

  function detailHistoryModal(itemName) {
    return `<div class="history-modal">
      <h3>变更历史 — ${escapeHtml(itemName)}</h3>
      <table class="flow-table">
        <thead><tr><th>时间</th><th>操作类型</th><th>操作人</th><th>变更类型</th><th>变更详情</th><th>是否正常使用</th></tr></thead>
        <tbody id="historyTableBody"><tr><td colspan="6" class="empty-state">暂无变更记录</td></tr></tbody>
      </table>
      <div class="load-more" style="display:none;text-align:center;margin-top:12px;"><button class="btn-secondary" id="loadMoreHistory">加载更多</button></div>
    </div>`;
  }

  window.viewItemHistory = function(itemType, itemRefId) {
    let currentPage = 1;
    const limit = 20;

    fetch(`/api/items/${itemType}/${itemRefId}/history?page=${currentPage}&limit=${limit}`)
      .then(r => r.json())
      .then(data => {
        if (data.error) { toast(data.error); return; }

        const item = data.item;
        const history = data.history || [];
        const total = data.total || 0;

        let rowsHtml = '';
        if (history.length === 0) {
          rowsHtml = '<tr><td colspan="6" class="empty-state">暂无变更记录</td></tr>';
        } else {
          history.forEach(h => {
            const changeBadge = h.change_type === '软件'
              ? '<span class="badge badge-blue">软件</span>'
              : (h.change_type === '硬件' ? '<span class="badge badge-green">硬件</span>' : '');
            const normalUseBadge = h.normal_use === '是'
              ? '<span class="badge badge-ok">正常</span>'
              : (h.normal_use === '否' ? '<span class="badge badge-warn">异常</span>' : '');

            rowsHtml += `<tr>
              <td>${escapeHtml(h.date || '')}</td>
              <td>${escapeHtml(h.event_type || '')}</td>
              <td>${escapeHtml(h.operator || '')}</td>
              <td>${changeBadge} ${escapeHtml(h.change_type || '')}</td>
              <td>${escapeHtml(h.change_detail || h.version_after || h.summary || '')}</td>
              <td>${normalUseBadge}</td>
            </tr>`;
          });
        }

        const body = `<div class="history-modal">
          <h3>变更历史 — ${escapeHtml(item.name || item.product_name || '')}</h3>
          <table class="flow-table">
            <thead><tr><th>时间</th><th>操作类型</th><th>操作人</th><th>变更类型</th><th>详情</th><th>是否正常</th></tr></thead>
            <tbody id="historyTableBody">${rowsHtml}</tbody>
          </table>
          ${total > currentPage * limit ? `<div style="text-align:center;margin-top:12px" id="loadMoreRow"><button class="btn-secondary" id="loadMoreHistoryBtn">加载更多</button></div>` : ''}
        </div>`;

        openModal({
          title: `变更历史 — ${escapeHtml(item.name || item.product_name || '')}`,
          body: body,
          hideOk: true,
          onReady: () => {
            const loadMoreBtn = document.getElementById('loadMoreHistoryBtn');
            if (loadMoreBtn) {
              loadMoreBtn.addEventListener('click', () => {
                currentPage++;
                loadMoreHistory(itemType, itemRefId, currentPage, limit);
              });
            }
          }
        });
      })
      .catch(error => toast('加载历史记录失败：' + error.message));
  };

  window.loadMoreHistory = function(itemType, itemRefId, page, limit) {
    fetch(`/api/items/${itemType}/${itemRefId}/history?page=${page}&limit=${limit}`)
      .then(r => r.json())
      .then(data => {
        const history = data.history || [];
        const total = data.total || 0;
        const tbody = document.getElementById('historyTableBody');
        if (!tbody) return;

        history.forEach(h => {
          const changeBadge = h.change_type === '软件'
            ? '<span class="badge badge-blue">软件</span>'
            : (h.change_type === '硬件' ? '<span class="badge badge-green">硬件</span>' : '');
          const normalUseBadge = h.normal_use === '是'
            ? '<span class="badge badge-ok">正常</span>'
            : (h.normal_use === '否' ? '<span class="badge badge-warn">异常</span>' : '');

          const row = document.createElement('tr');
          row.innerHTML = `<td>${escapeHtml(h.date || '')}</td>
            <td>${escapeHtml(h.event_type || '')}</td>
            <td>${escapeHtml(h.operator || '')}</td>
            <td>${changeBadge} ${escapeHtml(h.change_type || '')}</td>
            <td>${escapeHtml(h.change_detail || h.version_after || h.summary || '')}</td>
            <td>${normalUseBadge}</td>`;
          tbody.appendChild(row);
        });

        const loadMoreRow = document.getElementById('loadMoreRow');
        if (loadMoreRow && total <= page * limit) {
          loadMoreRow.remove();
        }
      })
      .catch(error => toast('加载更多失败：' + error.message));
  };
})();
