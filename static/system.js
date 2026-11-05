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
    myClaims: { title: "我已领用", icon: "hand-package" },
    myInspections: { title: "我的验收", icon: "clipboard-check" }, mySupplies: { title: "我的供货", icon: "package-open" }, myStarted: { title: "我的发起", icon: "file-pen" }, drafts: { title: "草稿箱", icon: "file-pen" },
    acceptance: { title: "物料验收", icon: "shield-check" }, semifinished: { title: "半成品验收", icon: "shield-check" }, finished: { title: "成品验收", icon: "shield-check" },
    claim: { title: "研发用料申领", icon: "hand-package" }, semifinishedClaim: { title: "半成品领用", icon: "hand-package" }, productionClaim: { title: "生产用料申领", icon: "factory" }, specialClaim: { title: "特殊用料", icon: "spark" }, borrow: { title: "借用申请", icon: "package-open" }, myBorrow: { title: "我的借用", icon: "package-open" }, outbound: { title: "物料出库", icon: "package-open" }, purchaseRequest: { title: "采购申请", icon: "cart" }, purchaseSummary: { title: "汇总采购申请", icon: "clipboard-list" }, purchaseHistory: { title: "历史采购申请", icon: "history" }, purchaseCoding: { title: "申请物料编码", icon: "file-pen" }, projects: { title: "项目设置", icon: "folder" },
    query: { title: "物料查询", icon: "search-file" }, temporaryInventory: { title: "临时库管理", icon: "boxes" }, temporaryFlows: { title: "临时库流程管理", icon: "workflow" }, temporaryLedger: { title: "临时库存流水", icon: "history" }, semifinishedInventory: { title: "半成品库", icon: "boxes" }, finishedInventory: { title: "成品库", icon: "boxes" },
    flow: { title: "流程中心", icon: "workflow" }, supplyLedger: { title: "供货台账", icon: "package-open" }, stats: { title: "统计", icon: "chart" }, stocktake: { title: "盘点", icon: "scan" }, logs: { title: "日志中心", icon: "history" }, recycle: { title: "回收站", icon: "trash" }, admin: { title: "系统设置", icon: "settings" },
    defectiveInventory: { title: "不良品物料", icon: "shield-check" }, commonMaterials: { title: "常用物料", icon: "bell" }, supplies: { title: "供货管理", icon: "package-open" },
  };
  const myClaims = window.WarehouseMyClaims.create({ api: systemApi, formatQty: formatInputQty, moduleShell });
  temporaryInventory = window.WarehouseTemporaryInventory.create({
    system,
    api: systemApi,
    formatQty: formatInputQty,
    moduleShell,
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
              ["mySupplies", "我的供货", canView("mySupplies")],
              ["drafts", "草稿箱", canView("drafts")],
              ["myStarted", "我的发起", canView("myStarted")],
            ])}
            ${navGroup("验收中心", "shield-check", [
              ["acceptance", "物料验收", canView("acceptance")],
              ["semifinished", "半成品验收", canView("semifinished")],
              ["finished", "成品验收", canView("finished")],
            ])}
            ${navGroup("领用中心", "hand-package", [
              ["claim", "研发用料申领", canView("claim")],
              ["semifinishedClaim", "半成品领用", canView("semifinishedClaim")],
              ["productionClaim", "生产用料申领", canView("productionClaim")],
              ["specialClaim", "特殊用料", canView("specialClaim")],
              ["myClaims", "我已领用", canView("myClaims")],
              ["borrow", "借用申请", canView("borrow")],
              ["myBorrow", "我的借用", canView("myBorrow")],
              ["outbound", "物料出库", canView("outbound")],
              ["supplies", "供货管理", canView("supplies")],
            ])}
            ${navGroup("采购中心", "cart", [
              ["purchaseRequest", "采购申请", canView("purchaseRequest")],
              ["purchaseSummary", "汇总采购申请", canView("purchaseSummary")],
              ["purchaseHistory", "历史采购申请", canView("purchaseHistory")],
              ["purchaseCoding", "申请物料编码", canView("purchaseCoding")],
            ])}
            ${navGroup("临时库管理", "boxes", [
              ["temporaryInventory", "临时库管理", canView("temporaryInventory")],
              ["temporaryFlows", "临时库流程管理", canView("temporaryFlows")],
            ])}
            ${navGroup("库存中心", "boxes", [
              ["query", "物料查询", canView("query")],
              ["semifinishedInventory", "半成品库", canView("semifinishedInventory")],
              ["finishedInventory", "成品库", canView("finishedInventory")],
              ["defectiveInventory", "不良品物料", canView("defectiveInventory")],
              ["commonMaterials", "常用物料", canView("commonMaterials")],
              ["__card", "仓库料卡系统", canViewCardSystem()],
            ])}
            ${navGroup("审计中心", "search-file", [
              ["flow", "流程中心", canView("flow")],
              ["supplyLedger", "供货台账", canView("supplyLedger")],
              ["stats", "统计", canView("stats")],
              ["stocktake", "盘点", canView("stocktake")],
              ["logs", "日志中心", canView("logs")],
              ["temporaryLedger", "临时库存流水", canView("temporaryLedger")],
            ])}
            ${canView("projects") ? navButton("projects", "项目设置") : ""}
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
      if (["temporaryInventory", "temporaryFlows", "temporaryLedger"].includes(system.view)) system.view = "query";
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
      supplyLedger: renderSupplyLedger,
      acceptance: renderAcceptance,
      claim: main => { system.claimUsageKind = "研发用料"; renderClaim(main); },
      semifinishedClaim: renderSemifinishedClaim,
      productionClaim: main => { system.claimUsageKind = "生产用料"; renderClaim(main); },
      specialClaim: main => window.WarehouseInventoryFlow.renderSpecial(main, { system, api: systemApi, escapeHtml, toast }),
      purchaseRequest: main => window.WarehouseInventoryFlow.renderPurchase(main, { system, api: systemApi, escapeHtml, toast }),
      purchaseSummary: main => window.WarehouseInventoryFlow.renderPurchaseSummary(main, { system, api: systemApi, escapeHtml, toast }),
      purchaseHistory: main => window.WarehouseInventoryFlow.renderPurchaseHistory(main, { system, api: systemApi, escapeHtml, toast, openView: view => { system.view = view; renderSystemMain(); } }),
      purchaseCoding: main => window.WarehouseInventoryFlow.renderPurchaseCoding(main, { system, api: systemApi, escapeHtml, toast }),
      mySupplies: renderMySupplies,
      projects: main => window.WarehouseInventoryFlow.renderProjects(main, { system, api: systemApi, escapeHtml, toast }),
      myClaims: myClaims.render,
      borrow: renderBorrow,
      myBorrow: renderMyBorrow,
      myInspections: renderMyInspections,
      myStarted: renderMyStarted,
      drafts: renderDrafts,
      outbound: renderOutbound,
      semifinished: renderSemifinished,
      finished: renderFinished,
      semifinishedInventory: renderSemifinishedInventory,
      finishedInventory: renderFinishedInventory,
      query: renderQuery,
      temporaryInventory: temporaryInventory.render,
      temporaryFlows: temporaryInventory.renderFlows,
      temporaryLedger: temporaryInventory.renderLedger,
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
    if (view === "myClaims" || view === "mySupplies") return Boolean(system.boot?.user);
    if (view === "acceptance" || view === "semifinished" || view === "finished") {
      return hasPerm("start_acceptance");
    }
    if (view === "claim" || view === "productionClaim" || view === "semifinishedClaim") {
      return hasPerm("start_claim");
    }
    if (view === "specialClaim") return hasPerm("start_special_claim");
    if (view === "purchaseRequest") return hasPerm("start_purchase_request");
    if (view === "purchaseSummary" || view === "purchaseHistory" || view === "purchaseCoding") return hasPerm("manage_purchase_summary");
    if (view === "projects") return hasPerm("view_projects") || hasPerm("manage_projects");
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
    if (view === "drafts") {
      return hasPerm("start_acceptance") || hasPerm("start_claim") || hasPerm("start_borrow") || hasPerm("start_supply");
    }
    if (view === "outbound") {
      return hasPerm("view_outbound");
    }
    if (view === "temporaryInventory" || view === "temporaryFlows") {
      return Boolean(
        system.boot?.workflow_settings?.temporary_inventory_enabled
        && hasPerm("view_temporary_inventory")
      );
    }
    if (view === "temporaryLedger") {
      return Boolean(system.boot?.workflow_settings?.temporary_inventory_enabled && hasPerm("view_temporary_inventory"));
    }
    if (view === "supplyLedger") return hasRole("admin") || hasPerm("view_supply") || hasPerm("view_flow");
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
    return system.boot?.workflow_settings?.ai_welcome_message || "你好，我可以帮你查库存、查流程和准备单据。";
  }

  function aiFloatingHtml() {
    return window.WarehouseAssistant.html(aiWelcomeMessage());
  }

  function setupAiFloating() {
    if (!window.WarehouseAssistant) return;
    window.WarehouseAssistant.mount({
      system,
      applySuggestion: applyAiSuggestion,
      openWorkflow: id => openFlowDetail(id, true),
      makeDraggable,
    });
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
      const project = row.querySelector("[data-purchase-project]");
      if (project && item.purchase_project) project.value = item.purchase_project;
      const materialId = row.querySelector("[data-material-id]");
      if (materialId && item.material_id) materialId.value = item.material_id;
      const purchase = row.querySelector("[data-purchase]");
      if (purchase) purchase.value = item.purchase_quantity || item.quantity || "";
      const arrival = row.querySelector("[data-arrival]");
      if (arrival) arrival.value = item.arrival_quantity || item.quantity || "";
      const totalAmount = row.querySelector("[data-total-amount]");
      if (totalAmount) totalAmount.value = Number(item.unit_price || 0) * Number(item.arrival_quantity || item.quantity || 0);
      row._syncAmountInput?.();
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

  let todoReloadTimer = 0;

  function scheduleTodoReload(notify = false) {
    window.clearTimeout(todoReloadTimer);
    todoReloadTimer = window.setTimeout(() => {
      reloadTodoList({ notify }).catch(error => console.warn("todo reload failed", error));
    }, 200);
  }

  async function reloadTodoList({ notify = false } = {}) {
    const data = await systemApi("/api/todos");
    const todos = data.items || [];
    const oldKeys = system.todoKeys || new Set();
    const newItems = todos.filter(item => !oldKeys.has(todoKey(item)));
    if (system.boot) system.boot.todos = todos;
    system.todoKeys = new Set(todos.map(todoKey));
    document.title = todos.length ? `(${todos.length}) 仓库物料系统` : "仓库物料系统";
    if (notify && newItems.length) {
      newItems.slice(0, 3).forEach(item => showDesktopNotice("待办流程", item.notification_body || `${item.form_no || ""} ${workflowStepLabel(item.step_code || "")}`.trim(), `todo:${todoKey(item)}`));
    }
    if (system.view === "todo") {
      const todoList = document.getElementById("todoList");
      if (todoList) {
        todoList.innerHTML = todoTable(todos);
        bindTodoButtons(todoList);
      }
    }
    return todos;
  }

  function applyRealtimeTodos(data) {
    if (system.view === "todo") {
      scheduleTodoReload(true);
      return;
    }
    const todos = data.items || [];
    const oldKeys = system.todoKeys || new Set();
    const newItems = todos.filter(item => !oldKeys.has(todoKey(item)));
    if (system.boot) system.boot.todos = todos;
    system.todoKeys = new Set(todos.map(todoKey));
    document.title = todos.length ? `(${todos.length}) 仓库物料系统` : "仓库物料系统";
    if (newItems.length) {
      newItems.slice(0, 3).forEach(item => showDesktopNotice("待办流程", item.notification_body || `${item.form_no || ""} ${workflowStepLabel(item.step_code || "")}`.trim(), `todo:${todoKey(item)}`));
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
        await loadNotifications();
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

  function consumeDraftHydrate(formType) {
    const pending = system.pendingDraftHydrate;
    if (!pending) {
      system.currentDraftId = 0;
      return null;
    }
    if (pending.form_type !== formType) {
      system.pendingDraftHydrate = null;
      system.currentDraftId = 0;
      return null;
    }
    system.pendingDraftHydrate = null;
    system.currentDraftId = Number(pending.id || 0);
    return pending.payload || {};
  }

  function draftViewForType(formType) {
    return ({ acceptance: "acceptance", semifinished: "semifinished", finished: "finished", claim: "claim", borrow: "borrow", supply: "supplies" })[formType] || "";
  }

  function setDateField(id, value) {
    const input = document.getElementById(id);
    if (!input) return;
    const text = String(value || "").slice(0, 10);
    input.value = text;
    const display = document.getElementById(`${id}Text`);
    if (display) display.value = formatDateCn(text);
  }

  function claimPayload() {
    const items = [...document.querySelectorAll("#claimRows .inline-row")].map(row => ({
      material_id: row.querySelector("[data-material-id]").value,
      material_code: row.querySelector("[data-code]")?.value || "",
      material_name: row.querySelector("[data-search]")?.value || "",
      brand_model: row.querySelector("[data-brand]")?.value || "",
      spec: row.querySelector("[data-spec]")?.value || "",
      request_quantity: row.querySelector("[data-qty]").value,
      purchase_project: row.querySelector("[data-purchase-project]")?.value || "",
    }));
    return {
      title: document.getElementById("claimTitle")?.value || "",
      leader_id: system.selectedClaimLeaders[0],
      leader_ids: system.selectedClaimLeaders,
      flow_attribute: document.getElementById("claimFlowAttribute")?.value || "实物交接",
      purpose: document.getElementById("claimPurpose")?.value || "",
      rd_item_kind: document.getElementById("claimRdKind")?.value || "",
      project_code: document.getElementById("claimProjectCode")?.value || "",
      purchase_project: items.find(item => item.purchase_project)?.purchase_project || "",
      usage_kind: system.claimUsageKind || "研发用料",
      items: items.map(item => ({ ...item, usage_kind: system.claimUsageKind || "研发用料" })),
    };
  }

  function borrowPayload() {
    const items = collectBorrowRows();
    return {
      title: document.getElementById("borrowTitle")?.value || "",
      expected_return_date: document.getElementById("borrowReturnDate")?.value || "",
      leader_id: system.selectedBorrowLeader,
      purpose: document.getElementById("borrowPurpose")?.value || "",
      rd_item_kind: document.getElementById("borrowRdKind")?.value || "",
      project_code: document.getElementById("borrowProjectCode")?.value || "",
      purchase_project: items.find(item => item.purchase_project)?.purchase_project || "",
      usage_kind: document.getElementById("borrowUsageKind")?.value || "研发用料",
      items: items.map(item => ({ ...item, usage_kind: document.getElementById("borrowUsageKind")?.value || "研发用料" })),
    };
  }

  // 供货草稿与提交共用同一报文，额外带展示字段方便回填
  function supplyPayload() {
    return {
      recipient: {
        company: document.getElementById("supplyCompany")?.value || "",
        name: document.getElementById("supplyName")?.value || "",
        phone: document.getElementById("supplyPhone")?.value || "",
        address: document.getElementById("supplyAddress")?.value || "",
      },
      supply_method: document.getElementById("supplyMethod")?.value || "",
      contact_phone: document.getElementById("supplyContactPhone")?.value || "",
      goods_status: document.getElementById("supplyGoodsStatus")?.value || "",
      expected_close_date: document.getElementById("supplyCloseDate")?.value || "",
      reason: document.getElementById("supplyReason")?.value || "",
      items: [{
        item_type: document.getElementById("supplyItemType")?.value || "",
        item_ref_id: Number(document.getElementById("supplyItemRef")?.value || 0),
        quantity: document.getElementById("supplyQuantity")?.value || "",
        goods_status: document.getElementById("supplyGoodsStatus")?.value || "",
        item_name: document.getElementById("supplyItemSearch")?.value || "",
        item_code: document.getElementById("supplyItemCode")?.value || "",
        brand_model: document.getElementById("supplyItemBrand")?.value || "",
        spec: document.getElementById("supplyItemSpec")?.value || "",
      }],
    };
  }

  function collectDraftPayload(formType) {
    if (formType === "acceptance") {
      const { items } = acceptanceItemsWithRows();
      const purchaseOrderChoice = document.querySelector("input[name='acceptancePurchaseOrder']:checked");
      return {
        title: document.getElementById("accTitle")?.value || "",
        has_purchase_order: purchaseOrderChoice ? purchaseOrderChoice.value === "yes" : true,
        items,
      };
    }
    if (formType === "claim") return claimPayload();
    if (formType === "borrow") return borrowPayload();
    if (formType === "semifinished") return semifinishedPayload();
    if (formType === "finished") return finishedPayload();
    if (formType === "supply") return supplyPayload();
    return {};
  }

  async function saveCurrentDraft(formType) {
    const payload = collectDraftPayload(formType);
    const body = JSON.stringify({ form_type: formType, payload });
    const id = Number(system.currentDraftId || 0);
    const data = id
      ? await systemApi(`/api/drafts/${id}`, { method: "PUT", body })
      : await systemApi("/api/drafts", { method: "POST", body });
    system.currentDraftId = Number(data.draft?.id || 0);
    toast("草稿已保存");
  }

  async function submitWorkflowPayload(formType, url, payload) {
    const draftId = Number(system.currentDraftId || 0);
    if (draftId) {
      await systemApi(`/api/drafts/${draftId}`, { method: "PUT", body: JSON.stringify({ form_type: formType, payload }) });
      const result = await systemApi(`/api/drafts/${draftId}/submit`, { method: "POST" });
      system.currentDraftId = 0;
      return result;
    }
    return systemApi(url, { method: "POST", body: JSON.stringify(payload) });
  }

  window.WarehouseDrafts = {
    consumeHydrate: consumeDraftHydrate,
    saveCurrentDraft,
    submitWorkflowPayload,
  };

  function hydrateItemRows(hostId, type, items) {
    const host = document.getElementById(hostId);
    if (!host) return;
    host.innerHTML = "";
    const rows = Array.isArray(items) ? items : [];
    if (!rows.length) {
      addItemRow(host, type);
      return;
    }
    rows.forEach(item => {
      addItemRow(host, type);
      const row = host.lastElementChild;
      applyMaterialSelection(row, {
        id: item.material_id || "",
        material_code: item.material_code || "",
        name: item.material_name || item.name || "",
        brand_model: item.brand_model || "",
        spec: item.spec || "",
        purchase_applicant: item.purchase_applicant || "",
        purchase_project: item.purchase_project || "",
        quantity: item.stock_quantity,
        total_available_quantity: item.total_available_quantity,
      });
      if (type === "claim") {
        const qty = row.querySelector("[data-qty]");
        if (qty) qty.value = item.request_quantity || "";
        updateClaimAllocationPreview(row);
      } else {
        const purchase = row.querySelector("[data-purchase]");
        const arrival = row.querySelector("[data-arrival]");
        const amount = row.querySelector("[data-total-amount]");
        if (purchase) purchase.value = item.purchase_quantity || item.request_quantity || "";
        if (arrival) arrival.value = item.arrival_quantity || "";
        if (amount) amount.value = item.total_amount || "";
        row._syncAmountInput?.();
      }
    });
  }

  function hydrateAcceptanceForm(payload) {
    const title = document.getElementById("accTitle");
    if (title) title.value = payload.title || "";
    const hasPurchaseOrder = payload.has_purchase_order;
    if (hasPurchaseOrder === false) {
      const noOrder = document.querySelector("input[name='acceptancePurchaseOrder'][value='no']");
      if (noOrder) noOrder.checked = true;
    } else if (hasPurchaseOrder === true) {
      const yesOrder = document.querySelector("input[name='acceptancePurchaseOrder'][value='yes']");
      if (yesOrder) yesOrder.checked = true;
    }
    hydrateItemRows("accRows", "acceptance", payload.items || []);
  }

  function hydrateClaimForm(payload) {
    const title = document.getElementById("claimTitle");
    if (title) title.value = payload.title || "";
    hydrateItemRows("claimRows", "claim", payload.items || []);
    const flowAttribute = document.getElementById("claimFlowAttribute");
    if (flowAttribute) flowAttribute.value = payload.flow_attribute === "流程流转" ? "流程流转" : "实物交接";
    updateClaimFlowAttributeHint();
    const purpose = document.getElementById("claimPurpose");
    if (purpose && payload.purpose) purpose.value = payload.purpose;
    const rdKind = document.getElementById("claimRdKind");
    if (rdKind && payload.rd_item_kind) rdKind.value = payload.rd_item_kind;
    const projectCode = document.getElementById("claimProjectCode");
    if (projectCode && (payload.project_code || payload.purchase_project)) {
      projectCode.value = payload.project_code || payload.purchase_project || "";
    }
    updatePurposeFields("claim");
  }

  function hydrateBorrowForm(payload) {
    const title = document.getElementById("borrowTitle");
    if (title) title.value = payload.title || "";
    setDateField("borrowReturnDate", payload.expected_return_date || "");
    if (payload.leader_id) system.selectedBorrowLeader = Number(payload.leader_id || 0);
    const borrowHost = document.getElementById("borrowRows");
    if (borrowHost) {
      borrowHost.innerHTML = "";
      (payload.items || []).forEach(item => addBorrowRow(item));
    }
    const purpose = document.getElementById("borrowPurpose");
    if (purpose && payload.purpose) purpose.value = payload.purpose;
    const rdKind = document.getElementById("borrowRdKind");
    if (rdKind && payload.rd_item_kind) rdKind.value = payload.rd_item_kind;
    const projectCode = document.getElementById("borrowProjectCode");
    if (projectCode && (payload.project_code || payload.purchase_project)) {
      projectCode.value = payload.project_code || payload.purchase_project || "";
    }
    updatePurposeFields("borrow");
  }

  function hydrateMaterialComponents(hostId, components, pool, qtyId, costId, peerHostId = "") {
    const host = document.getElementById(hostId);
    if (!host) return;
    host.innerHTML = "";
    const rows = Array.isArray(components) ? components : [];
    if (!rows.length) {
      addMaterialComponentRow(host, pool, qtyId, costId, peerHostId);
      return;
    }
    rows.forEach(component => {
      addMaterialComponentRow(host, pool, qtyId, costId, peerHostId);
      const row = host.lastElementChild;
      const item = (pool || []).find(entry => Number(entry.material_id) === Number(component.material_id));
      if (item) applyProductionMaterial(row, item, component.batch_ids);
      else if (row.querySelector("[data-prod-material]")) row.querySelector("[data-prod-material]").value = component.material_id || "";
      const perUnit = row.querySelector("[data-prod-per-unit]");
      if (perUnit) perUnit.value = component.per_unit_quantity || 1;
    });
    refreshComponentRows(hostId, qtyId, costId, peerHostId);
  }

  function hydrateSemifinishedComponents(hostId, components, pool, qtyId, costId, peerHostId = "") {
    const host = document.getElementById(hostId);
    if (!host) return;
    host.innerHTML = "";
    const rows = Array.isArray(components) ? components : [];
    if (!rows.length) {
      addSemifinishedComponentRow(host, pool, qtyId, costId, peerHostId);
      return;
    }
    rows.forEach(component => {
      addSemifinishedComponentRow(host, pool, qtyId, costId, peerHostId);
      const row = host.lastElementChild;
      const item = (pool || []).find(entry => Number(entry.id) === Number(component.semifinished_inventory_id));
      if (item) applyProductionSemi(row, item);
      else if (row.querySelector("[data-prod-semi]")) row.querySelector("[data-prod-semi]").value = component.semifinished_inventory_id || "";
      const perUnit = row.querySelector("[data-prod-per-unit]");
      if (perUnit) perUnit.value = component.per_unit_quantity || 1;
    });
    refreshComponentRows(hostId, qtyId, costId, peerHostId);
  }

  function hydrateSemifinishedForm(payload) {
    const title = document.getElementById("semiTitle");
    if (title) title.value = payload.title || "";
    const name = document.getElementById("semiName");
    if (name) name.value = payload.name || "";
    const spec = document.getElementById("semiSpec");
    if (spec) spec.value = payload.spec || "";
    const qty = document.getElementById("semiQty");
    if (qty) qty.value = payload.acceptance_quantity || "";
    const unit = document.getElementById("semiUnit");
    if (unit && payload.unit) unit.value = payload.unit;
    const maker = document.getElementById("semiMakerId");
    if (maker && payload.maker_id) maker.value = payload.maker_id;
    setDateField("semiDate", payload.acceptance_date || "");
    const project = document.getElementById("semiProjectCode");
    if (project) project.value = payload.project_code || "";
  }

  function hydrateFinishedForm(payload) {
    const title = document.getElementById("finTitle");
    if (title) title.value = payload.title || "";
    const name = document.getElementById("finName");
    if (name) name.value = payload.name || payload.product_name || "";
    const spec = document.getElementById("finSpec");
    if (spec) spec.value = payload.spec || "";
    const qty = document.getElementById("finQty");
    if (qty) qty.value = payload.acceptance_quantity || "";
    const unit = document.getElementById("finUnit");
    if (unit && payload.unit) unit.value = payload.unit;
    const maker = document.getElementById("finMakerId");
    if (maker && payload.maker_id) maker.value = payload.maker_id;
    setDateField("finDate", payload.acceptance_date || "");
    const project = document.getElementById("finProjectCode");
    if (project) project.value = payload.project_code || "";
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
    main.innerHTML = moduleShell("待办流程", `<div class="work-panel todo-work-panel"><div class="work-head"><h2>我的待办</h2><button id="refreshTodo" class="refresh-button" type="button">${iconSvg("refresh", "button-icon")}<span>刷新</span></button></div><div class="work-body" id="todoList">加载中...</div></div>`);
    document.getElementById("refreshTodo").addEventListener("click", () => {
      reloadTodoList().catch(error => toast(error.message));
    });
    reloadTodoList().catch(error => toast(error.message));
  }

  function renderNotifications(main) {
    const read = Boolean(system.notificationReadView);
    const category = String(system.notificationCategory || "");
    const options = [
      ["", "全部"],
      ["inbound", "入库"],
      ["stock_warning", "库存预警"],
      ["supply", "供货"],
      ["transfer", "转借"],
    ];
    main.innerHTML = moduleShell("通知", `
      <div class="work-panel"><div class="work-head"><h2>${read ? "已读通知" : "当前通知"}</h2><div class="flow-tools"><label class="notification-filter">筛选<select id="notificationFilter">${options.map(([value, label]) => `<option value="${escapeHtml(value)}" ${value === category ? "selected" : ""}>${label}</option>`).join("")}</select></label><button id="markNotificationsRead" ${read ? "disabled" : ""}>一键已读</button><button id="toggleReadNotifications">${read ? "查看当前通知" : "已读通知"}</button><button id="refreshNotifications">刷新</button></div></div><div class="work-body" id="notificationList">加载中...</div></div>
    `);
    document.getElementById("toggleReadNotifications").addEventListener("click", () => {
      system.notificationReadView = !system.notificationReadView;
      renderNotifications(main);
    });
    document.getElementById("notificationFilter").addEventListener("change", event => {
      system.notificationCategory = event.target.value || "";
      loadNotifications();
    });
    document.getElementById("refreshNotifications").addEventListener("click", loadNotifications);
    document.getElementById("markNotificationsRead").addEventListener("click", markNotificationsRead);
    loadNotifications();
  }

  async function loadNotifications() {
    const params = new URLSearchParams({ read: system.notificationReadView ? "1" : "0" });
    if (system.notificationCategory) params.set("category", system.notificationCategory);
    const data = await systemApi(`/api/notifications?${params.toString()}`);
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
    const labels = { inbound: "入库", stock_warning: "库存预警", supply: "供货", transfer: "转借" };
    return `<table class="flow-table"><thead><tr><th>\u65f6\u95f4</th><th>分类</th><th>\u6807\u9898</th><th>\u5185\u5bb9</th><th>\u72b6\u6001</th></tr></thead><tbody>${(rows || []).map(row => `<tr><td>${escapeHtml(row.created_at || "")}</td><td>${escapeHtml(labels[row.category] || "")}</td><td>${escapeHtml(row.title || "")}</td><td>${escapeHtml(row.body || "")}</td><td>${read ? "\u5df2\u8bfb" : "\u672a\u8bfb"}</td></tr>`).join("") || `<tr><td colspan="5">${read ? "\u6682\u65e0\u5df2\u8bfb\u901a\u77e5" : "\u6682\u65e0\u5f53\u524d\u901a\u77e5"}</td></tr>`}</tbody></table>`;
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
    main.innerHTML = moduleShell("\u6d41\u7a0b\u4e2d\u5fc3", `<div class="work-panel"><div class="work-head"><h2>\u5168\u90e8\u6d41\u7a0b</h2></div><div class="work-body"><div class="form-grid"><label>\u5206\u7c7b<select id="flowType"><option value="">\u5168\u90e8</option><option value="acceptance">\u7269\u6599\u9a8c\u6536</option><option value="semifinished">\u534a\u6210\u54c1\u9a8c\u6536</option><option value="finished">\u6210\u54c1\u9a8c\u6536</option><option value="claim">\u7533\u9886/\u51fa\u5e93</option><option value="semifinished_claim">半成品领用</option><option value="claim_return">领用退库</option><option value="borrow">借用申请</option><option value="borrow_return">借用归还</option><option value="borrow_claim">借用转领用</option><option value="supply">供货管理</option></select></label><label>\u72b6\u6001<select id="flowStatus"><option value="">\u5168\u90e8</option><option value="acceptance">\u9a8c\u6536\u4e2d</option><option value="leader_acceptance">\u9a8c\u6536\u9886\u5bfc\u5ba1\u6279</option><option value="inbound">\u5f85\u5165\u5e93</option><option value="leader_claim">\u7533\u9886\u5ba1\u6279</option><option value="applicant_revision">\u53d1\u8d77\u4eba\u4fee\u6539</option><option value="outbound">\u5f85\u51fa\u5e93</option><option value="leader_borrow">借用审批</option><option value="borrow_outbound">借用待出库</option><option value="return_inbound">归还待入库</option><option value="completed">\u5df2\u529e\u7ed3</option></select></label>${system.boot?.workflow_settings?.temporary_inventory_enabled ? `<label>单据库别<select id="flowStockScope"><option value="">全部</option><option value="temporary">临时库单据</option><option value="formal">正式库单据</option></select></label>` : ""}</div><div class="form-grid" style="margin-top:8px"><label>名称<input id="flowMaterialName" placeholder="物料/成品/半成品名称模糊搜索"></label><label>编号<input id="flowMaterialCode" placeholder="物料/成品/半成品编号模糊搜索"></label><label>\u54c1\u724c\u578b\u53f7<input id="flowBrandModel" placeholder="\u54c1\u724c\u578b\u53f7\u6a21\u7cca\u641c\u7d22"></label><label>\u6280\u672f\u89c4\u683c<input id="flowSpec" placeholder="技术规格/成品半成品规格参数模糊搜索"></label><label>申请人<input id="flowApplicant" placeholder="申请人姓名/账号/部门"></label><label>\u9a8c\u6536\u4eba<input id="flowInspector" placeholder="\u9a8c\u6536\u4eba\u59d3\u540d\u6a21\u7cca\u641c\u7d22"></label><label>\u6d41\u7a0b\u5355\u53f7<input id="flowFormNo" placeholder="\u6d41\u7a0b\u5355\u53f7\u6a21\u7cca\u641c\u7d22"></label></div><div style="display:flex;gap:8px;margin-top:8px"><button id="runFlowFilter" class="primary">\u7b5b\u9009</button><button id="clearFlowFilter">\u6e05\u7a7a</button></div><div class="flow-tools"><button id="exportFlowFiltered">\u5bfc\u51fa\u5f53\u524d\u7b5b\u9009</button><button id="exportFlowChecked">\u5bfc\u51fa\u52fe\u9009</button><button id="printFlowFiltered">\u6253\u5370\u5f53\u524d\u7b5b\u9009</button><button id="printFlowChecked">\u6253\u5370\u52fe\u9009</button><button id="deleteFlowChecked" class="danger">\u5220\u9664\u52fe\u9009</button></div><div id="flowCenterList"></div></div></div>`);
    document.getElementById("runFlowFilter").addEventListener("click", () => {
      system.pages.flowCenter = 1;
      loadFlowCenter();
    });
    document.getElementById("clearFlowFilter").addEventListener("click", () => {
      const fields = ["flowMaterialName", "flowMaterialCode", "flowBrandModel", "flowSpec", "flowApplicant", "flowInspector", "flowFormNo"];
      fields.forEach(id => { const el = document.getElementById(id); if (el) el.value = ""; });
      document.getElementById("flowType").value = "";
      document.getElementById("flowStatus").value = "";
      const flowStockScope = document.getElementById("flowStockScope");
      if (flowStockScope) flowStockScope.value = "";
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
      <div class="work-panel"><div class="work-head"><h2>发起验收</h2></div>
        <div class="work-body">
          <div class="form-grid">
            <div class="wide acceptance-title-row"><label>验收标题<input id="accTitle" placeholder="例如：研发物料到货验收"></label><div class="acceptance-title-actions"><button type="button" id="downloadAcceptanceTemplate">下载导入模板</button><button type="button" id="importAcceptanceRows">导入</button><input id="acceptanceImportFile" type="file" accept=".csv,text/csv" hidden></div></div>
            <div class="wide acceptance-order-row">${system.boot?.workflow_settings?.temporary_inventory_enabled ? `<div class="acceptance-purchase-order"><span class="acceptance-purchase-order-label">是否有采购订单</span><div class="acceptance-purchase-order-options"><label><input type="radio" name="acceptancePurchaseOrder" value="yes" checked> 是</label><label><input type="radio" name="acceptancePurchaseOrder" value="no"> 否（按临时库物料验收）</label></div></div>` : "<span></span>"}<button type="button" id="addAcceptanceRow">添加行</button></div>
            <div class="wide" id="accRows"></div>
            <div class="wide submit-flow-actions"><button class="primary flow-submit-button" id="submitAcceptance">提交验收流程</button><button type="button" class="primary flow-submit-button" id="saveAcceptanceDraft">保存草稿</button></div>
          </div>
        </div>
      </div>
      <div class="work-panel"><div class="work-head"><h2>验收流程</h2><button id="refreshAcceptance">刷新</button></div><div class="work-body" id="accList"></div></div>
    `);
    const hydrate = consumeDraftHydrate("acceptance");
    const rows = document.getElementById("accRows");
    const addRow = () => addItemRow(rows, "acceptance");
    document.getElementById("addAcceptanceRow").addEventListener("click", addRow);
    document.getElementById("downloadAcceptanceTemplate").addEventListener("click", () => { window.location.href = "/api/acceptance/import-template"; });
    document.getElementById("importAcceptanceRows").addEventListener("click", () => document.getElementById("acceptanceImportFile").click());
    document.getElementById("acceptanceImportFile").addEventListener("change", async event => {
      const file = event.target.files?.[0];
      event.target.value = "";
      if (!file) return;
      const text = await file.text();
      const result = await systemApi("/api/acceptance/import", { method: "POST", body: JSON.stringify({ text }) });
      rows.innerHTML = "";
      (result.items || []).forEach(item => {
        addRow();
        const row = rows.lastElementChild;
        row.querySelector("[data-material-id]").value = item.material_id || "";
        row.querySelector("[data-code]").value = item.material_code || "";
        row.querySelector("[data-search]").value = item.material_name || "";
        row.querySelector("[data-brand]").value = item.brand_model || "";
        row.querySelector("[data-spec]").value = item.spec || "";
        row.querySelector("[data-purchase-applicant]").value = item.purchase_applicant || "";
        row.querySelector("[data-purchase-project]").value = item.purchase_project || "";
        row.querySelector("[data-usage-kind]").value = item.usage_kind || "研发用料";
        row.querySelector("[data-pack]").value = item.pack_quantity || 1;
        row.querySelector("[data-arrival-unit]").value = item.arrival_unit || "";
        row.querySelector("[data-purchase]").value = item.purchase_quantity || "";
        row.querySelector("[data-arrival]").value = item.arrival_quantity || "";
        row.querySelector("[data-total-amount]").value = item.total_amount || "";
        row._syncAmountInput?.();
      });
    });
    document.getElementById("saveAcceptanceDraft").addEventListener("click", () => saveCurrentDraft("acceptance").catch(error => toast(error.message)));
    document.getElementById("submitAcceptance").addEventListener("click", startAcceptanceSubmit);
    document.getElementById("refreshAcceptance").addEventListener("click", loadAcceptanceList);
    const acceptanceSeed = system.acceptancePrefill;
    system.acceptancePrefill = null;
    if (acceptanceSeed) {
      document.getElementById("accTitle").value = acceptanceSeed.title || "";
      (acceptanceSeed.items || []).forEach(item => {
        addRow();
        const row = rows.lastElementChild;
        row.querySelector("[data-material-id]").value = item.material_id || "";
        row.querySelector("[data-purchase-item]").value = item.purchase_item_id || "";
        row.querySelector("[data-code]").value = item.material_code || "";
        row.querySelector("[data-search]").value = item.material_name || "";
        row.querySelector("[data-brand]").value = item.brand_model || "";
        row.querySelector("[data-spec]").value = item.spec || "";
        row.querySelector("[data-purchase-applicant]").value = item.purchase_applicant || "";
        row.querySelector("[data-purchase-project]").value = item.purchase_project || "";
        row.querySelector("[data-usage-kind]").value = item.usage_kind || "研发用料";
        row.querySelector("[data-pack]").value = item.pack_quantity || 1;
        row.querySelector("[data-arrival-unit]").value = item.arrival_unit || item.unit || "";
        row.querySelector("[data-purchase]").value = item.purchase_quantity || item.request_quantity || "";
        row.querySelector("[data-arrival]").value = item.arrival_quantity || item.request_quantity || "";
      });
      if (!rows.children.length) addRow();
    } else if (hydrate) {
      hydrateAcceptanceForm(hydrate);
    } else {
      addRow();
      fillNextFormNo("accTitle", "YS");
    }
    loadAcceptanceList();
  }

  function renderClaim(main) {
    const claimTitleText = system.claimUsageKind === "生产用料" ? "生产用料申领" : "研发用料申领";
    main.innerHTML = moduleShell(claimTitleText, `
      <div class="work-panel"><div class="work-head"><h2>发起申领</h2><button id="addClaimRow">添加行</button></div>
        <div class="work-body">
          <div class="form-grid">
            <div class="wide claim-title-row">
              <label>申领标题<input id="claimTitle" placeholder="例如：综合部办公用品申领"></label>
              <label class="compact-field">流程属性<select id="claimFlowAttribute"><option value="实物交接" selected>实物交接</option><option value="流程流转">流程流转</option></select></label>
            </div>
            <p class="hint wide" id="claimFlowAttributeHint" hidden>无实物交接，用于第三方集成采购组织等特定场景</p>
            <div class="wide claim-purpose-row">
              <label class="compact-field">用途<select id="claimPurpose"><option value="研发" selected>研发</option><option value="办公">办公</option></select></label>
              <label class="compact-field" data-claim-rd>研发类型<select id="claimRdKind"><option value="项目物料">项目物料</option><option value="辅料">辅料</option></select></label>
              <label class="compact-field" data-claim-project>项目代号<select id="claimProjectCode">${projectCodeOptions()}</select></label>
            </div>
            <p class="hint wide">领用临时库物料时，单次只能领用一种物料</p>
            <div class="wide" id="claimRows"></div>
            <div class="wide submit-flow-actions"><button class="primary flow-submit-button" id="submitClaim">提交申领流程</button><button type="button" class="primary flow-submit-button" id="saveClaimDraft">保存草稿</button></div>
          </div>
        </div>
      </div>
      <div class="work-panel"><div class="work-head"><h2>申领流程</h2><button id="refreshClaim">刷新</button></div><div class="work-body" id="claimList"></div></div>
    `);
    const hydrate = consumeDraftHydrate("claim");
    const rows = document.getElementById("claimRows");
    const addRow = () => {
      if ([...document.querySelectorAll("#claimRows .inline-row")].some(rowClaimsTemporary)) {
        toast("领用临时库物料时，单次只能领用一种物料");
        return;
      }
      addItemRow(rows, "claim");
    };
    document.getElementById("addClaimRow").addEventListener("click", addRow);
    document.getElementById("saveClaimDraft").addEventListener("click", () => saveCurrentDraft("claim").catch(error => toast(error.message)));
    document.getElementById("submitClaim").addEventListener("click", chooseClaimLeadersAndSubmit);
    document.getElementById("refreshClaim").addEventListener("click", loadClaimList);
    document.getElementById("claimPurpose").addEventListener("change", () => updatePurposeFields("claim"));
    document.getElementById("claimFlowAttribute")?.addEventListener("change", updateClaimFlowAttributeHint);
    updateClaimFlowAttributeHint();
    document.getElementById("claimRdKind").addEventListener("change", () => updatePurposeFields("claim"));
    updatePurposeFields("claim");
    if (hydrate) {
      hydrateClaimForm(hydrate);
    } else {
      addRow();
      fillNextFormNo("claimTitle", "CK");
    }
    loadClaimList();
  }

  function renderSemifinishedClaim(main) {
    const dept = currentUser()?.department || "";
    const leaderUsers = approvalLeaderUsers("semifinished_claim", "leader_semifinished_claim", dept);
    main.innerHTML = moduleShell("半成品领用", `
      <div class="work-panel"><div class="work-head"><h2>发起半成品领用</h2></div>
        <div class="work-body">
          <div class="form-grid">
            <label class="wide">领用标题<input id="semiClaimTitle" placeholder="半成品领用"></label>
            <label>项目代号<select id="semiClaimProject">${projectCodeOptions()}</select></label>
            <label>来源<select id="semiClaimSource"><option value="qualified">合格</option><option value="defective">不合格</option><option value="scrapped">报废</option></select></label>
            <label class="wide">半成品<input id="semiClaimSearch" placeholder="搜索名称、规格或流水号"><div id="semiClaimResults" class="material-results"></div></label>
            <label>数量<input id="semiClaimQty" type="number" min="0" step="any" value="1"></label>
            <label>审批领导<select id="semiClaimLeader">${leaderUsers.map(user => `<option value="${user.id}">${escapeHtml(user.display_name)}</option>`).join("")}</select></label>
            <div class="wide"><button class="primary" id="submitSemiClaim" type="button">提交半成品领用</button></div>
            <p class="hint wide" id="semiClaimPicked">请先选择项目代号并搜索半成品</p>
          </div>
        </div>
      </div>
    `);
    fillNextFormNo("semiClaimTitle", "BC");
    let picked = null;
    const results = document.getElementById("semiClaimResults");
    const search = document.getElementById("semiClaimSearch");
    async function loadItems() {
      const project = document.getElementById("semiClaimProject").value;
      if (!project) {
        results.style.display = "none";
        return;
      }
      const source = document.getElementById("semiClaimSource").value;
      const data = await systemApi(`/api/semifinished-claims/items?keyword=${encodeURIComponent(search.value.trim())}&purchase_project=${encodeURIComponent(project)}`);
      const items = (data.items || []).filter(item => item.source_kind === source);
      if (!items.length) {
        results.innerHTML = `<div class="material-option">没有可领用的半成品</div>`;
        results.style.display = "block";
        return;
      }
      results.innerHTML = items.map((item, index) => `<button type="button" class="material-option" data-semi-claim-index="${index}">${escapeHtml(item.serial_no || "")} ${escapeHtml(item.name || "")}<small>${escapeHtml(item.spec || "")} · 可领 ${formatQty(item.available_quantity)} ${escapeHtml(item.unit || "")}</small></button>`).join("");
      results.style.display = "block";
      results.querySelectorAll("[data-semi-claim-index]").forEach(button => {
        button.addEventListener("click", () => {
          picked = items[Number(button.dataset.semiClaimIndex)];
          search.value = `${picked.serial_no || ""} ${picked.name || ""}`.trim();
          document.getElementById("semiClaimQty").value = picked.whole_item ? "1" : formatInputQty(picked.available_quantity);
          document.getElementById("semiClaimQty").disabled = Boolean(picked.whole_item);
          document.getElementById("semiClaimPicked").textContent = `${picked.serial_no || ""} ${picked.name || ""}，可领 ${formatQty(picked.available_quantity)}`;
          results.style.display = "none";
        });
      });
    }
    let timer = 0;
    search.addEventListener("input", () => {
      picked = null;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => loadItems().catch(error => toast(error.message)), 160);
    });
    search.addEventListener("focus", () => loadItems().catch(error => toast(error.message)));
    document.getElementById("semiClaimProject").addEventListener("change", () => { picked = null; loadItems().catch(error => toast(error.message)); });
    document.getElementById("semiClaimSource").addEventListener("change", () => { picked = null; loadItems().catch(error => toast(error.message)); });
    document.getElementById("submitSemiClaim").addEventListener("click", async () => {
      if (!picked) {
        toast("请选择半成品");
        return;
      }
      const leaderId = document.getElementById("semiClaimLeader").value;
      if (!leaderId) {
        toast("请选择审批领导");
        return;
      }
      try {
        await systemApi("/api/semifinished-claims", {
          method: "POST",
          body: JSON.stringify({
            project_code: document.getElementById("semiClaimProject").value,
            leader_id: leaderId,
            items: [{
              source_kind: picked.source_kind,
              source_id: picked.source_id,
              quantity: document.getElementById("semiClaimQty").value,
            }],
          }),
        });
        toast("半成品领用已提交");
        picked = null;
        search.value = "";
        fillNextFormNo("semiClaimTitle", "BC");
      } catch (error) {
        toast(error.message || "提交失败");
      }
    });
  }

  function renderBorrow(main) {
    const hydrate = consumeDraftHydrate("borrow");
    const dept = currentUser()?.department || "";
    const leaderUsers = approvalLeaderUsers("borrow", "leader_borrow", dept);
    const validLeaderIds = new Set(leaderUsers.map(user => Number(user.id)));
    if (hydrate?.leader_id) system.selectedBorrowLeader = Number(hydrate.leader_id);
    if (!validLeaderIds.has(Number(system.selectedBorrowLeader || 0))) system.selectedBorrowLeader = Number(leaderUsers[0]?.id || 0);
    main.innerHTML = moduleShell("借用申请", `
      <div class="work-panel"><div class="work-head"><h2>发起借用</h2><button id="addBorrowRow" type="button">添加行</button></div>
        <div class="work-body">
          <div class="form-grid">
            <label class="wide">借用标题<input id="borrowTitle" placeholder="例如：项目调试物料借用"></label>
            <div class="wide borrow-meta-row">
              <label class="compact-field">用途<select id="borrowPurpose"><option value="研发" selected>研发</option><option value="办公">办公</option></select></label>
              <label class="compact-field" data-borrow-rd>研发类型<select id="borrowRdKind"><option value="项目物料">项目物料</option><option value="辅料">辅料</option></select></label>
              <label class="compact-field" data-borrow-project>项目代号<select id="borrowProjectCode">${projectCodeOptions()}</select></label>
              <label class="compact-field">用料形式<select id="borrowUsageKind"><option value="研发用料">研发用料</option><option value="生产用料">生产用料</option></select></label>
              <label class="compact-field short-date-field">预计归还日期${datePickerHtml("borrowReturnDate")}</label>
            </div>
            <div class="wide" id="borrowRows"></div>
            <div class="wide submit-flow-actions"><button class="primary flow-submit-button" id="submitBorrow">提交借用申请</button><button type="button" class="primary flow-submit-button" id="saveBorrowDraft">保存草稿</button></div>
          </div>
        </div>
      </div>
      <div class="work-panel"><div class="work-head"><h2>我的借用流程</h2><button id="refreshBorrowFlows">刷新</button></div><div class="work-body" style="padding-top:0"><div class="search-bar"><input type="text" id="borrowMakerFilter" placeholder="搜索制作人..." class="search-input"><input type="text" id="borrowProjectFilter" placeholder="搜索所属项目..." class="search-input"><button id="clearBorrowFiltersBtn">清除</button></div><div id="borrowFlowList"></div></div></div>
    `);
    document.getElementById("addBorrowRow").addEventListener("click", () => addBorrowRow());
    document.getElementById("borrowPurpose")?.addEventListener("change", () => updatePurposeFields("borrow"));
    document.getElementById("borrowRdKind")?.addEventListener("change", () => updatePurposeFields("borrow"));
    updatePurposeFields("borrow");
    document.getElementById("saveBorrowDraft").addEventListener("click", () => saveCurrentDraft("borrow").catch(error => toast(error.message)));
    document.getElementById("submitBorrow").addEventListener("click", chooseBorrowLeaderAndSubmit);
    document.getElementById("refreshBorrowFlows").addEventListener("click", () => fetchBorrowApplications("", ""));
    document.getElementById("borrowMakerFilter")?.addEventListener("input", debouncedFilterBorrow);
    document.getElementById("borrowProjectFilter")?.addEventListener("input", debouncedFilterBorrow);
    document.getElementById("clearBorrowFiltersBtn")?.addEventListener("click", clearBorrowFilters);
    bindDatePickers(main);
    if (hydrate) {
      hydrateBorrowForm(hydrate);
      if (!document.querySelector("#borrowRows .inline-row")) addBorrowRow();
    } else {
      fillNextFormNo("borrowTitle", "JY");
      addBorrowRow();
    }
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

  function renderDrafts(main) {
    main.innerHTML = moduleShell("草稿箱", `
      <div class="work-panel"><div class="work-head"><h2>草稿箱</h2><button id="refreshDrafts">刷新</button></div>
        <div class="work-body" id="draftList">加载中...</div>
      </div>
    `);
    document.getElementById("refreshDrafts").addEventListener("click", loadDrafts);
    loadDrafts();
  }

  async function loadDrafts() {
    const host = document.getElementById("draftList");
    if (!host) return;
    const data = await systemApi("/api/drafts");
    system.draftRows = data.drafts || [];
    const rows = system.draftRows;
    host.innerHTML = `<div class="table-wrapper"><table class="flow-table"><thead><tr><th>类型</th><th>标题</th><th>更新时间</th><th>操作</th></tr></thead><tbody>${rows.map(row => `
      <tr>
        <td>${escapeHtml(flowTypeLabel(row.form_type))}</td>
        <td>${escapeHtml(row.title || "-")}</td>
        <td>${escapeHtml(row.updated_at || "")}</td>
        <td class="mini-actions"><button type="button" data-draft-continue="${row.id}">继续填写</button><button type="button" class="danger" data-draft-delete="${row.id}">删除</button></td>
      </tr>
    `).join("") || `<tr><td colspan="4">暂无草稿</td></tr>`}</tbody></table></div>`;
    host.querySelectorAll("[data-draft-continue]").forEach(button => {
      button.addEventListener("click", () => continueDraft(Number(button.dataset.draftContinue)).catch(error => toast(error.message)));
    });
    host.querySelectorAll("[data-draft-delete]").forEach(button => {
      button.addEventListener("click", () => deleteDraftRow(Number(button.dataset.draftDelete)).catch(error => toast(error.message)));
    });
  }

  async function continueDraft(draftId) {
    const data = await systemApi(`/api/drafts/${draftId}`);
    const draft = data.draft || {};
    const view = draftViewForType(draft.form_type);
    if (!view) {
      toast("草稿类型无法继续填写");
      return;
    }
    system.pendingDraftHydrate = { id: draft.id, form_type: draft.form_type, payload: draft.payload || {} };
    system.view = view;
    renderSystemMain();
  }

  async function deleteDraftRow(draftId) {
    if (!confirm("确认删除该草稿？")) return;
    await systemApi(`/api/drafts/${draftId}`, { method: "DELETE" });
    toast("草稿已删除");
    if (Number(system.currentDraftId) === Number(draftId)) system.currentDraftId = 0;
    loadDrafts();
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

  function activeProjectRows() {
    return (system.boot?.projects || []).filter(row => row.active !== false && row.active !== 0);
  }

  function projectCodeOptions(selected = "") {
    const codes = activeProjectRows().map(row => row.code).filter(code => code && code !== "办公");
    if (!codes.length) return `<option value="">未设置项目代号</option>`;
    return codes.map(code => `<option value="${escapeAttr(code)}" ${code === selected ? "selected" : ""}>${escapeHtml(code)}</option>`).join("");
  }

  function purchaseProjectOptions(selected = "") {
    const codes = activeProjectRows().map(row => row.code);
    const unique = [...new Set(["办公", ...codes].filter(Boolean))];
    return `<option value="">请选择</option>` + unique.map(code => `<option value="${escapeAttr(code)}" ${code === selected ? "selected" : ""}>${escapeHtml(code)}</option>`).join("");
  }

  function updateClaimFlowAttributeHint() {
    const hint = document.getElementById("claimFlowAttributeHint");
    if (!hint) return;
    hint.hidden = document.getElementById("claimFlowAttribute")?.value !== "流程流转";
  }

  function updatePurposeFields(prefix = "claim") {
    const isRd = document.getElementById(`${prefix}Purpose`)?.value === "研发";
    const isProjectMaterial = isRd && document.getElementById(`${prefix}RdKind`)?.value === "项目物料";
    document.querySelectorAll(`[data-${prefix}-rd]`).forEach(el => { el.style.display = isRd ? "" : "none"; });
    document.querySelectorAll(`[data-${prefix}-project]`).forEach(el => { el.style.display = isProjectMaterial ? "" : "none"; });
  }

  function fillPurposeFromPurchaseProject(prefix, purchaseProject) {
    const purpose = document.getElementById(`${prefix}Purpose`);
    const rdKind = document.getElementById(`${prefix}RdKind`);
    const projectCode = document.getElementById(`${prefix}ProjectCode`);
    if (purchaseProject === "办公") {
      if (purpose) purpose.value = "办公";
      if (rdKind) rdKind.value = "项目物料";
      if (projectCode) projectCode.value = "";
    } else if (purchaseProject) {
      if (purpose) purpose.value = "研发";
      if (rdKind) rdKind.value = "项目物料";
      if (projectCode) projectCode.value = purchaseProject;
    }
    updatePurposeFields(prefix);
  }

  function updateClaimPurposeFields() {
    updatePurposeFields("claim");
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
    const hydrate = consumeDraftHydrate("semifinished");
    system.selectedValidators = [];
    main.innerHTML = moduleShell("半成品验收", `
      <div class="work-panel"><div class="work-head"><h2>发起半成品验收</h2></div>
        <div class="work-body">
          <div class="form-grid">
            <div class="wide production-head-row">
              <label>验收单号/标题<input id="semiTitle"></label>
              <label>项目代号<select id="semiProjectCode">${purchaseProjectOptions()}</select></label>
            </div>
            <div class="wide production-meta-row">
              <label>验收数量<input id="semiQty" type="number" step="1" min="1" value="1"></label>
              <label>验收日期${datePickerHtml("semiDate", new Date().toISOString().slice(0, 10))}</label>
              <label>单位<input id="semiUnit" value="台"></label>
              <label>制作人<select id="semiMakerId"><option value="">请选择制作人</option></select></label>
            </div>
            <label>半成品名称<input id="semiName"></label>
            <label>规格参数<input id="semiSpec"></label>
            <div class="wide"><div class="flow-tools"><strong>单台所用物料</strong><button type="button" id="addSemiMaterial">添加物料</button></div><div id="semiMaterials"></div></div>
            <div class="wide"><span class="badge" id="semiCost">成本价：¥0.00 / 台</span></div>
            <div class="wide submit-flow-actions"><button class="primary flow-submit-button" id="submitSemi">提交半成品验收</button><button type="button" class="primary flow-submit-button" id="saveSemiDraft">保存草稿</button></div>
          </div>
        </div>
      </div>
      <div class="work-panel"><div class="work-head"><h2>半成品验收流程</h2><button id="refreshSemiFlows">刷新</button></div><div class="work-body" id="semiFlowList"></div></div>
    `);
    document.getElementById("semiQty").addEventListener("input", () => refreshComponentRows("semiMaterials", "semiQty", "semiCost"));
    document.getElementById("semiProjectCode")?.addEventListener("change", async () => {
      document.getElementById("semiMaterials").innerHTML = "";
      await loadProductionMaterialPool().catch(error => toast(error.message));
    });
    document.getElementById("addSemiMaterial").addEventListener("click", async () => {
      if (!document.getElementById("semiProjectCode")?.value) {
        toast("请先选择项目代号");
        return;
      }
      try {
        await loadProductionMaterialPool();
        addMaterialComponentRow(document.getElementById("semiMaterials"), system.productionMaterialPool, "semiQty", "semiCost");
      } catch (error) {
        toast(error.message);
      }
    });
    document.getElementById("saveSemiDraft").addEventListener("click", () => saveCurrentDraft("semifinished").catch(error => toast(error.message)));
    document.getElementById("submitSemi").addEventListener("click", startSemifinishedSubmit);
    document.getElementById("refreshSemiFlows").addEventListener("click", loadSemifinishedFlowList);
    document.getElementById("semiTitle").value = "";
    populateProductionMakerSelect("semiMakerId");
    bindDatePickers(main);
    if (hydrate) hydrateSemifinishedForm(hydrate);
    else fillNextFormNo("semiTitle", "BY");
    loadProductionMaterialPool()
      .catch(error => {
        console.warn("半成品验收物料池加载失败", error);
        system.productionMaterialPool = [];
      })
      .finally(() => {
        if (hydrate) hydrateMaterialComponents("semiMaterials", hydrate.components || [], system.productionMaterialPool, "semiQty", "semiCost");
      });
    loadSemifinishedFlowList();
  }

  function renderFinished(main) {
    const hydrate = consumeDraftHydrate("finished");
    system.selectedValidators = [];
    main.innerHTML = moduleShell("成品验收", `
      <div class="work-panel"><div class="work-head"><h2>发起成品验收</h2></div>
        <div class="work-body">
          <div class="form-grid">
            <div class="wide production-head-row">
              <label>验收单号/标题<input id="finTitle"></label>
              <label>项目代号<select id="finProjectCode">${purchaseProjectOptions()}</select></label>
            </div>
            <div class="wide production-meta-row">
              <label>验收数量<input id="finQty" type="number" step="1" min="1" value="1"></label>
              <label>验收日期${datePickerHtml("finDate", new Date().toISOString().slice(0, 10))}</label>
              <label>单位<input id="finUnit" value="台"></label>
              <label>制作人<select id="finMakerId"><option value="">请选择制作人</option></select></label>
            </div>
            <label>成品名称<input id="finName"></label>
            <label>规格参数<input id="finSpec"></label>
            <div class="wide"><div class="flow-tools"><strong>所用物料</strong><button type="button" id="addFinMaterial">添加物料</button></div><div id="finMaterials"></div></div>
            <div class="wide"><div class="flow-tools"><strong>所用半成品</strong><button type="button" id="addFinSemi">添加半成品</button></div><div id="finSemis"></div></div>
            <div class="wide"><span class="badge" id="finCost">成本价：¥0.00 / 台</span></div>
            <div class="wide submit-flow-actions"><button class="primary flow-submit-button" id="submitFin">提交成品验收</button><button type="button" class="primary flow-submit-button" id="saveFinDraft">保存草稿</button></div>
          </div>
        </div>
      </div>
      <div class="work-panel"><div class="work-head"><h2>成品验收流程</h2><button id="refreshFinFlows">刷新</button></div><div class="work-body" id="finFlowList"></div></div>
    `);
    document.getElementById("finProjectCode")?.addEventListener("change", async () => {
      document.getElementById("finMaterials").innerHTML = "";
      document.getElementById("finSemis").innerHTML = "";
      try {
        await loadProductionMaterialPool();
        await loadSemifinishedPool();
      } catch (error) {
        toast(error.message);
      }
    });
    ["finQty", "finName"].forEach(id => document.getElementById(id).addEventListener("input", () => {
      refreshComponentRows("finMaterials", "finQty", "finCost", "finSemis");
    }));
    const addMaterial = () => addMaterialComponentRow(document.getElementById("finMaterials"), system.productionMaterialPool, "finQty", "finCost", "finSemis");
    const addSemi = () => addSemifinishedComponentRow(document.getElementById("finSemis"), system.semifinishedPool, "finQty", "finCost", "finMaterials");
    document.getElementById("addFinMaterial").addEventListener("click", async () => {
      if (!document.getElementById("finProjectCode")?.value) {
        toast("请先选择项目代号");
        return;
      }
      try {
        await loadProductionMaterialPool();
        addMaterial();
      } catch (error) {
        toast(error.message);
      }
    });
    document.getElementById("addFinSemi").addEventListener("click", async () => {
      if (!document.getElementById("finProjectCode")?.value) {
        toast("请先选择项目代号");
        return;
      }
      try {
        await loadSemifinishedPool();
        addSemi();
      } catch (error) {
        toast(error.message);
      }
    });
    document.getElementById("saveFinDraft").addEventListener("click", () => saveCurrentDraft("finished").catch(error => toast(error.message)));
    document.getElementById("submitFin").addEventListener("click", startFinishedSubmit);
    document.getElementById("refreshFinFlows").addEventListener("click", loadFinishedFlowList);
    document.getElementById("finTitle").value = "";
    populateProductionMakerSelect("finMakerId");
    bindDatePickers(main);
    if (hydrate) hydrateFinishedForm(hydrate);
    else fillNextFormNo("finTitle", "CP");
    loadProductionMaterialPool()
      .catch(error => {
        console.warn("成品验收物料池加载失败", error);
        system.productionMaterialPool = [];
      })
      .finally(() => {
        if (hydrate) hydrateMaterialComponents("finMaterials", hydrate.components || hydrate.material_components || [], system.productionMaterialPool, "finQty", "finCost", "finSemis");
      });
    loadSemifinishedPool()
      .catch(error => {
        console.warn("成品验收半成品池加载失败", error);
        system.semifinishedPool = [];
      })
      .finally(() => {
        if (hydrate) hydrateSemifinishedComponents("finSemis", hydrate.semifinished_components || [], system.semifinishedPool, "finQty", "finCost", "finMaterials");
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
    runMaterialQuery();
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
            <button id="printStocktake" type="button">下载盘点报表</button>
          </div>
          <div class="print-area" id="stocktakeResult"></div>
        </div>
      </div>` : ""}
      <div class="work-panel"><div class="work-head"><h2>历史盘点</h2><button id="loadStocktakes">刷新</button></div><div class="work-body" id="stocktakeList"></div></div>
    `);
    document.getElementById("createStocktake")?.addEventListener("click", chooseStocktakeSupervisorAndCreate);
    document.getElementById("loadStocktakes").addEventListener("click", loadStocktakes);
    document.getElementById("printStocktake")?.addEventListener("click", () => {
      if (!system.currentStocktakeId) {
        toast("请先生成盘点单");
        return;
      }
      window.location.href = `/api/stocktakes/${system.currentStocktakeId}/report.xlsx`;
    });
    bindDatePickers(main);
    loadStocktakes();
  }

  function renderAdmin(main) {
    main.innerHTML = moduleShell("\u7cfb\u7edf\u8bbe\u7f6e", `
      <div class="work-panel"><div class="work-head"><h2>\u90e8\u95e8\u7ba1\u7406</h2><button id="saveDept" class="primary">\u4fdd\u5b58\u90e8\u95e8</button></div><div class="work-body"><input type="hidden" id="deptId"><div class="form-grid"><label>\u90e8\u95e8\u540d<input id="deptName"></label><label>\u63cf\u8ff0<input id="deptDesc"></label></div><div id="deptHost"></div></div></div>
      <div class="work-panel"><div class="work-head"><h2>\u8d26\u53f7\u4e0e\u89d2\u8272</h2><button id="saveUser" class="primary">\u4fdd\u5b58\u7528\u6237</button></div><div class="work-body"><input type="hidden" id="editUserId"><div class="form-grid"><label>\u8d26\u53f7<input id="newUsername"></label><label>\u59d3\u540d<input id="newDisplayName"></label><label>\u5bc6\u7801<input id="newPassword" type="password" placeholder="\u65b0\u5efa\u9ed8\u8ba4 123456\uff0c\u7f16\u8f91\u7559\u7a7a\u4e0d\u6539"></label><label>\u90e8\u95e8<select id="newDepartment">${departmentOptions()}</select></label><label>\u72b6\u6001<select id="newActive"><option value="1">\u542f\u7528</option><option value="0">\u505c\u7528</option></select></label><label class="wide">\u89d2\u8272<select id="newRoles" multiple size="6">${(system.boot.roles || []).map(r => `<option value="${r.code}">${escapeHtml(r.name)}</option>`).join("")}</select></label></div><div id="usersHost"></div></div></div>
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

  function populateProductionMakerSelect(selectId) {
    const select = document.getElementById(selectId);
    const user = currentUser();
    if (!select || !user) return;
    const users = (system.boot?.users || []).filter(item => item.is_active);
    select.innerHTML = '<option value="">请选择制作人</option>';
    users.forEach(item => {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.display_name || item.username;
      select.appendChild(option);
    });
    select.value = user.id;
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

  window.warehouseStepOptions = userOptionsForStep;

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
    return `<div class="form-grid material-detail-grid"><div class="wide"><h3>${escapeHtml(material.material_code || "")} ${escapeHtml(material.name || "")}</h3></div><div><span class="hint">品牌型号</span><strong>${escapeHtml(material.brand_model || "-")}</strong></div><div><span class="hint">技术规格</span><strong>${escapeHtml(material.spec || "-")}</strong></div><div><span class="hint">所属分类编码</span><strong>${escapeHtml(material.group_category_code || "-")}</strong></div><div><span class="hint">物品和服务分类代码</span><strong>${escapeHtml(material.group_service_code || "-")}</strong></div><div class="wide"><span class="hint">标准描述</span><strong>${escapeHtml(material.group_standard_description || "-")}</strong></div><div class="wide material-detail-line"><div><span class="hint">单位</span><strong>${escapeHtml(material.unit || "-")}</strong></div><div><span class="hint">位置</span><strong>${escapeHtml([material.shelf_name, material.layer_number, material.zone_name].filter(value => String(value || "").trim()).join(" ") || "-")}</strong></div></div><div class="wide material-detail-line"><div><span class="hint">正式库实际数量</span><strong>${formatQty(material.quantity || 0)}</strong></div><div><span class="hint">正式库账面数量</span><strong>${formatQty(material.book_quantity || 0)}</strong></div></div><div class="wide material-detail-line"><div><span class="hint">临时库实际数量</span><strong>${formatQty(material.temporary_quantity || 0)}</strong></div><div><span class="hint">临时库账面数量</span><strong>${formatQty(material.temporary_book_quantity || 0)}</strong></div></div><div class="wide"><h3>批次信息</h3>${batchSummaryHtml(material.batches || [], material.unit) || `<div class="empty-state">暂无批次</div>`}<h3>临时库批次</h3>${batchSummaryHtml(material.temporary_batches || [], material.unit) || `<div class="empty-state">暂无临时库批次</div>`}}</div><div class="wide"><h3>批次附件</h3><div id="materialDetailBatchAttachments">${materialBatchAttachmentsHtml(material, canDeleteMaterialAttachment())}</div></div>${uploadHtml}</div>`;
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
    row.className = `inline-row ${type === "claim" ? "claim" : "acceptance"}`;
    row.innerHTML = type === "claim"
      ? `<label class="material-search">物料<input data-search placeholder="输入编号/名称/规格/采购申请人"><div data-result-list class="material-results"></div><small data-stock-info class="hint"></small></label><label>编号<input data-code readonly></label><label>品牌型号<input data-brand readonly></label><label>技术规格<input data-spec readonly></label><label>领用数量<input data-qty type="number" step="1" min="0"><small data-allocation-info class="claim-allocation-preview"></small></label><button data-remove>删除</button><input type="hidden" data-material-id><input type="hidden" data-stock-quantity><input type="hidden" data-purchase-project>`
      : `<label class="material-search">物料<input data-search placeholder="输入编号/名称/规格"><div data-result-list class="material-results"></div></label><label class="material-search">编号<input data-code placeholder="输入编号近似搜索"><div data-result-list class="material-results"></div></label><label>品牌型号<input data-brand></label><label>技术规格<input data-spec></label><label>采购申请人<input data-purchase-applicant></label><label>采购申请项目<select data-purchase-project required>${purchaseProjectOptions()}</select></label><label>用料形式<select data-usage-kind><option value="研发用料">研发用料</option><option value="生产用料">生产用料</option></select></label><label>每单位数量<input data-pack type="number" min="1" step="1" value="1"></label><label>单位<input data-arrival-unit></label><label>采购数量<input data-purchase type="number" step="1"></label><label>到货数量<input data-arrival type="number" step="1"></label><label>总金额<input data-total-amount type="number" min="0" step="0.01" required></label><label>单价<input data-price type="number" step="0.0001" readonly></label><button data-remove>删除</button><input type="hidden" data-material-id><input type="hidden" data-purchase-item>`;
    row.querySelector("[data-remove]").addEventListener("click", () => row.remove());
    const bindSearch = input => {
      if (!input) return;
      const list = input.parentElement.querySelector("[data-result-list]");
      let timer = 0;
      input.addEventListener("input", () => {
        window.clearTimeout(timer);
        row.querySelector("[data-material-id]").value = "";
        timer = window.setTimeout(() => resolveMaterial(row, type === "claim", input, list).catch(error => toast(error.message)), 220);
      });
      input.addEventListener("focus", () => resolveMaterial(row, type === "claim", input, list).catch(error => toast(error.message)));
      input.addEventListener("keydown", event => {
        if (event.key === "Escape") hideMaterialResults(row);
      });
    };
    bindSearch(row.querySelector("[data-search]"));
    if (type !== "claim") bindSearch(row.querySelector("[data-code]"));
    row.querySelector("[data-qty]")?.addEventListener("input", () => updateClaimAllocationPreview(row));
    document.addEventListener("click", event => {
      if (!row.contains(event.target)) hideMaterialResults(row);
    });
    host.appendChild(row);
    if (type !== "claim") {
      row._syncAmountInput = window.WarehouseAmountInput?.bind({
        quantityInput: row.querySelector("[data-arrival]"),
        totalAmountInput: row.querySelector("[data-total-amount]"),
        unitPriceInput: row.querySelector("[data-price]"),
      });
    }
  }

  async function resolveMaterial(row, positiveOnly, input = row.querySelector("[data-search]"), list = input?.parentElement.querySelector("[data-result-list]")) {
    const keyword = input?.value.trim() || "";
    const results = positiveOnly
      ? await systemApi(`/api/claims/materials?usage_kind=${encodeURIComponent(system.view === "borrow" ? (document.getElementById("borrowUsageKind")?.value || "研发用料") : (system.claimUsageKind || "研发用料"))}${keyword ? `&keyword=${encodeURIComponent(keyword)}` : ""}`)
      : (keyword
        ? await systemApi(`/api/materials/search?keyword=${encodeURIComponent(keyword)}`)
        : await freshMaterialRowsForPicker());
    const filtered = positiveOnly
      ? results.filter(item => Number(item.total_available_quantity ?? item.quantity ?? 0) > 0)
      : results;
    if (!filtered.length) {
      hideMaterialResults(row);
      row.querySelector("[data-material-id]").value = "";
      return;
    }
    hideMaterialResults(row);
    row._materialResults = filtered;
    const temporaryEnabled = Boolean(system.boot?.workflow_settings?.temporary_inventory_enabled);
    list.innerHTML = filtered.map((material, index) => `
      <button class="material-option" type="button" data-material-index="${index}">
        ${escapeHtml(material.material_code)} · ${escapeHtml(material.name)}
        <small>${escapeHtml(material.brand_model || "")} ${escapeHtml(material.spec || "")} · 采购申请人 ${escapeHtml(material.purchase_applicant || "-")}${positiveOnly && material.purchase_project ? ` · ${escapeHtml(material.purchase_project)}` : ""} · ${positiveOnly ? escapeHtml(ClaimSourceUI.availabilityText(material, temporaryEnabled, formatQty)) : `总余量 ${formatQty(material.quantity || 0)}`} ${escapeHtml(material.unit || "")}${!positiveOnly && material.batch_summary ? ` · 批次 ${escapeHtml(material.batch_summary)}` : ""}</small>
      </button>
    `).join("");
    positionMaterialResults(row, list, filtered.length, input);
    resizeMaterialResults(list, filtered.length);
    list.style.display = "block";
    list.querySelectorAll("[data-material-index]").forEach(button => {
      button.addEventListener("click", () => applyMaterialSelection(row, filtered[Number(button.dataset.materialIndex)]));
    });
  }

  function applyMaterialSelection(row, material) {
    if (row.closest("#claimRows") && material.purchase_project) {
      const others = [...document.querySelectorAll("#claimRows .inline-row")].filter(other => other !== row);
      const conflict = others.some(other => {
        const value = other.querySelector("[data-purchase-project]")?.value || "";
        return value && value !== material.purchase_project;
      });
      if (conflict) {
        toast("一张申领单只允许一个采购申请项目");
        return;
      }
    }
    row._selectedMaterial = material;
    row.querySelector("[data-material-id]").value = material.id;
    row.querySelector("[data-search]").value = material.name || "";
    row.querySelector("[data-code]") && (row.querySelector("[data-code]").value = material.material_code);
    row.querySelector("[data-brand]") && (row.querySelector("[data-brand]").value = material.brand_model || "");
    row.querySelector("[data-spec]") && (row.querySelector("[data-spec]").value = material.spec || "");
    row.querySelector("[data-purchase-applicant]") && (row.querySelector("[data-purchase-applicant]").value = material.purchase_applicant || "");
    const projectInput = row.querySelector("[data-purchase-project]");
    if (projectInput && material.purchase_project) projectInput.value = material.purchase_project;
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
    if (row.closest("#claimRows") && material.purchase_project) {
      fillPurposeFromPurchaseProject("claim", material.purchase_project);
    }
    updateClaimAllocationPreview(row);
    if (row.closest("#claimRows")) keepOnlyTemporaryClaimRow(row);
  }

  function rowClaimsTemporary(row) {
    const material = row?._selectedMaterial;
    if (!material || !system.boot?.workflow_settings?.temporary_inventory_enabled) return false;
    const temporary = Number(material.temporary_available_quantity || 0);
    if (!(temporary > 0)) return false;
    const formal = Number(material.formal_available_quantity || 0);
    const quantity = Number(row.querySelector("[data-qty]")?.value || 0);
    if (quantity > 0) return quantity > formal + 1e-9;
    return formal <= 1e-9;
  }

  function keepOnlyTemporaryClaimRow(row) {
    if (!rowClaimsTemporary(row)) return;
    const extras = [...document.querySelectorAll("#claimRows .inline-row")].filter(other => other !== row);
    if (!extras.length) return;
    extras.forEach(other => other.remove());
    toast("领用临时库物料时，单次只能领用一种物料");
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
    if (row.closest("#claimRows")) keepOnlyTemporaryClaimRow(row);
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

  function hideMaterialResults(row, targetList = null) {
    const lists = targetList ? [targetList] : [...row.querySelectorAll("[data-result-list]")];
    lists.forEach(list => {
      list.style.display = "none";
      list.innerHTML = "";
    });
  }

  function resizeMaterialResults(list, count) {
    const top = Number.parseFloat(list.style.top || "0") || list.getBoundingClientRect().top;
    const viewportRoom = Math.max(180, window.innerHeight - top - 18);
    const ideal = Math.max(120, count * 58 + 8);
    list.style.maxHeight = `${Math.min(ideal, viewportRoom, Math.max(window.innerHeight * 0.72, 260))}px`;
  }

  function positionMaterialResults(row, list, count, sourceInput = null) {
    const input = sourceInput || row.querySelector("[data-search]") || row.querySelector("[data-prod-material-search]") || row.querySelector("[data-prod-semi-search]");
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
      borrow: ["借用申请", "type-borrow"], borrow_return: ["借用归还", "type-return"], borrow_claim: ["借用转领用", "type-issue"],
      common_material: ["常用物料申请", "type-common"], supply: ["供货申请", "type-supply"],
      supply_return: ["供货回寄", "type-return"], supply_extension: ["供货延期", "type-supply"],
      purchase_request: ["采购申请", "type-common"], purchase_summary: ["采购汇总", "type-common"],
      special_claim: ["特殊用料", "type-issue"], stocktake: ["盘点", "type-default"],
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
      leader_common_material: "status-pending", leader_supply: "status-pending", supply_handover_print: "status-pending", supply_outbound: "status-pending", supply_voucher_upload: "status-pending", supply_voucher_review: "status-pending", supply_return_inbound: "status-acceptance", leader_supply_extension: "status-pending", external_open: "status-transfer-warning",
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

  function bindFlowDetailButtons(host, readonly = false) {
    host.querySelectorAll("[data-detail]").forEach(button => button.addEventListener("click", async () => {
      button.disabled = true;
      try {
        await openFlowDetail(Number(button.dataset.detail), readonly);
      } catch (error) {
        console.error("流程详情加载失败", error);
        toast(`流程详情加载失败：${error.message || "未知错误"}`);
      } finally {
        button.disabled = false;
      }
    }));
  }

  function bindTodoButtons(host) {
    bindFlowDetailButtons(host);
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
      purchase_project: row.querySelector("[data-purchase-project]")?.value || "",
      usage_kind: row.querySelector("[data-usage-kind]")?.value || "研发用料",
      pack_quantity: row.querySelector("[data-pack]")?.value || 1,
      arrival_unit: row.querySelector("[data-arrival-unit]")?.value || "",
      purchase_quantity: row.querySelector("[data-purchase]").value,
      arrival_quantity: row.querySelector("[data-arrival]").value,
      total_amount: row.querySelector("[data-total-amount]").value,
      purchase_item_id: row.querySelector("[data-purchase-item]")?.value || "",
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
      const purchaseOrderChoice = document.querySelector("input[name='acceptancePurchaseOrder']:checked");
      const hasPurchaseOrder = purchaseOrderChoice ? purchaseOrderChoice.value === "yes" : true;
      await submitWorkflowPayload("acceptance", "/api/acceptance", { title: document.getElementById("accTitle").value, validator_ids: system.selectedValidators, has_purchase_order: hasPurchaseOrder, items });
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
      const payload = claimPayload();
      [...document.querySelectorAll("#claimRows .inline-row")].forEach(row => {
        const qty = Number(row.querySelector("[data-qty]").value || 0);
        const stockRaw = row.querySelector("[data-stock-quantity]")?.value;
        const stock = Number(stockRaw || 0);
        if (stockRaw != null && stockRaw !== "" && qty > stock + 1e-9) {
          throw new Error("\u7533\u9886\u6570\u91cf\u4e0d\u80fd\u5927\u4e8e\u5f53\u524d\u5e93\u5b58\u6570\u91cf");
        }
      });
      await submitWorkflowPayload("claim", "/api/claims", payload);
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

  function collectBorrowRows() {
    return [...document.querySelectorAll("#borrowRows .inline-row")].map(row => {
      const item = row._borrowItem || {};
      const quantity = row.querySelector("[data-qty]")?.value || item.request_quantity || item.quantity || 0;
      return {
        ...item,
        item_type: row.querySelector("[data-item-type]")?.value || item.item_type || "",
        item_ref_id: Number(row.querySelector("[data-item-ref]")?.value || item.item_ref_id || 0),
        purchase_project: row.querySelector("[data-purchase-project]")?.value || item.purchase_project || "",
        item_code: row.querySelector("[data-code]")?.value || item.item_code || "",
        item_name: row.querySelector("[data-search]")?.value || item.item_name || "",
        brand_model: row.querySelector("[data-brand]")?.value || item.brand_model || "",
        spec: row.querySelector("[data-spec]")?.value || item.spec || "",
        request_quantity: quantity,
        quantity,
      };
    }).filter(item => item.item_ref_id);
  }

  function placeBorrowResults(input, list) {
    const rect = input.getBoundingClientRect();
    list.style.left = `${Math.max(8, rect.left)}px`;
    list.style.top = `${rect.bottom + 4}px`;
    list.style.width = `${Math.max(rect.width, 320)}px`;
    list.style.display = "block";
  }

  function applyBorrowSelection(row, item, checkConflict = true) {
    const project = item.purchase_project || "";
    if (checkConflict && project) {
      const conflict = [...document.querySelectorAll("#borrowRows .inline-row")].some(other => {
        if (other === row) return false;
        const value = other.querySelector("[data-purchase-project]")?.value || "";
        return value && value !== project;
      });
      if (conflict) {
        toast("一张借用单只允许一个采购申请项目");
        return;
      }
    }
    row._borrowItem = item;
    row.querySelector("[data-search]").value = item.item_name || "";
    row.querySelector("[data-code]").value = item.item_code || "";
    row.querySelector("[data-brand]").value = item.brand_model || "";
    row.querySelector("[data-spec]").value = item.spec || "";
    row.querySelector("[data-kind]").value = borrowItemTypeLabel(item.item_type);
    row.querySelector("[data-item-type]").value = item.item_type || "material";
    row.querySelector("[data-item-ref]").value = item.item_ref_id || "";
    row.querySelector("[data-purchase-project]").value = project;
    const qty = row.querySelector("[data-qty]");
    const available = Number(item.available_quantity || 0);
    if (available > 0) qty.max = String(available);
    if (!qty.value) qty.value = formatInputQty(item.request_quantity || item.quantity || 1);
    const stock = row.querySelector("[data-stock-info]");
    if (stock) {
      const availableText = available ? `可借 ${formatInputQty(available)} ${item.unit || ""}` : "";
      stock.textContent = [project, availableText].filter(Boolean).join(" · ");
    }
    const list = row.querySelector("[data-result-list]");
    if (list) list.style.display = "none";
    if (checkConflict && project) fillPurposeFromPurchaseProject("borrow", project);
  }

  async function searchBorrowRow(row, input, list) {
    const keyword = input.value.trim();
    if (!keyword) {
      list.style.display = "none";
      return;
    }
    const data = await systemApi(`/api/borrow/items?keyword=${encodeURIComponent(keyword)}`);
    const usage = document.getElementById("borrowUsageKind")?.value || "";
    const items = (data.items || []).filter(item => !usage || !item.usage_kind || item.usage_kind === usage).slice(0, 12);
    if (!items.length) {
      list.style.display = "none";
      return;
    }
    list.innerHTML = items.map((item, index) => `
      <button class="material-option" type="button" data-borrow-index="${index}">
        ${escapeHtml(item.item_code || "")} · ${escapeHtml(item.item_name || "")}
        <small>${escapeHtml(borrowItemTypeLabel(item.item_type))} ${escapeHtml(item.brand_model || "")} ${escapeHtml(item.spec || "")}${item.purchase_project ? ` · ${escapeHtml(item.purchase_project)}` : ""} · 可借 ${escapeHtml(formatInputQty(item.available_quantity || 0))} ${escapeHtml(item.unit || "")}</small>
      </button>
    `).join("");
    placeBorrowResults(input, list);
    list.querySelectorAll("[data-borrow-index]").forEach(button => {
      button.addEventListener("click", () => applyBorrowSelection(row, items[Number(button.dataset.borrowIndex)]));
    });
  }

  function addBorrowRow(item = null) {
    const host = document.getElementById("borrowRows");
    if (!host) return;
    const row = document.createElement("div");
    row.className = "inline-row borrow";
    row.innerHTML = `<label class="material-search">物料<input data-search placeholder="输入编号/名称/规格"><div data-result-list class="material-results"></div><small data-stock-info class="hint"></small></label><label>编号<input data-code readonly></label><label>品牌型号<input data-brand readonly></label><label>技术规格<input data-spec readonly></label><label>类型<input data-kind readonly></label><label>借用数量<input data-qty type="number" step="1" min="0"></label><button type="button" data-remove>删除</button><input type="hidden" data-item-type><input type="hidden" data-item-ref><input type="hidden" data-purchase-project>`;
    row.querySelector("[data-remove]").addEventListener("click", () => row.remove());
    const input = row.querySelector("[data-search]");
    const list = row.querySelector("[data-result-list]");
    let timer = 0;
    input.addEventListener("input", () => {
      window.clearTimeout(timer);
      row.querySelector("[data-item-ref]").value = "";
      timer = window.setTimeout(() => searchBorrowRow(row, input, list).catch(error => toast(error.message)), 220);
    });
    input.addEventListener("keydown", event => {
      if (event.key === "Escape") list.style.display = "none";
    });
    document.addEventListener("click", event => {
      if (!row.contains(event.target)) list.style.display = "none";
    });
    host.appendChild(row);
    if (item && (item.item_ref_id || item.item_name || item.item_code)) applyBorrowSelection(row, item, false);
  }

  async function submitBorrow() {
    const payload = borrowPayload();
    const rows = (payload.items || []).map(item => ({
      item_type: item.item_type,
      item_ref_id: item.item_ref_id,
      quantity: item.quantity || item.request_quantity,
      purchase_project: item.purchase_project || "",
      usage_kind: payload.usage_kind || "研发用料",
    })).filter(item => Number(item.quantity || 0) > 0 && item.item_ref_id);
    if (!rows.length) {
      toast("请先加入借用明细");
      return;
    }
    await submitWorkflowPayload("borrow", "/api/borrows", {
      ...payload,
      leader_id: system.selectedBorrowLeader,
      items: rows,
    });
    toast("借用申请已提交");
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
        actions = `<button type="button" data-return-borrow="${item.id}">我要归还</button><button type="button" class="btn-transfer" data-transfer-borrow="${item.id}">我要转借</button>${item.item_type === "material" ? `<button type="button" class="btn-claim-borrow" data-claim-borrow="${item.id}">我要领用</button>` : ""}`;
      }
      return `<tr><td>${escapeHtml(item.borrow_no || "")}</td><td>${borrowItemTypeLabel(item.item_type)}</td><td>${ClaimSourceUI.sourceBadge(item.stock_source)}</td><td>${escapeHtml(item.item_code || "")}</td><td>${escapeHtml(item.item_name || "")}</td><td>${escapeHtml(item.brand_model || "无")}</td><td>${escapeHtml(item.spec || "")}</td><td>${formatQty(item.quantity || 0)} ${escapeHtml(item.unit || "")}</td><td>${formatQty(item.returned_quantity || 0)}</td><td>${formatQty(item.remaining_quantity || 0)}</td><td>${item.is_overdue ? `<span class="status-badge status-transfer-warning">${escapeHtml(item.expected_return_date || "")}</span>` : escapeHtml(item.expected_return_date || "-")}</td><td>${escapeHtml(item.outbound_date || "")}</td><td>${actions}</td></tr>`;
    }).join("") || `<tr><td colspan="13">${system.myBorrowOverdueOnly ? "暂无逾期借用物料" : "暂无借用中的物料"}</td></tr>`}</tbody></table>${paginationHtml("myBorrow", rows, 20)}`;
    host.querySelectorAll("[data-return-borrow]").forEach(button => button.addEventListener("click", () => openBorrowReturn(Number(button.dataset.returnBorrow))));
    host.querySelectorAll("[data-transfer-borrow]").forEach(button => button.addEventListener("click", () => initiateTransfer(Number(button.dataset.transferBorrow))));
    host.querySelectorAll("[data-claim-borrow]").forEach(button => button.addEventListener("click", () => claimBorrow(Number(button.dataset.claimBorrow))));
    host.querySelectorAll("[data-accept-transfer]").forEach(button => button.addEventListener("click", () => acceptTransfer(Number(button.dataset.acceptTransfer))));
    host.querySelectorAll("[data-reject-transfer]").forEach(button => button.addEventListener("click", () => rejectTransfer(Number(button.dataset.rejectTransfer))));
    bindPagination(host, "myBorrow", renderMyBorrowList);
  }

  function claimBorrow(recordId) {
    const item = (system.myBorrowRows || []).find(row => Number(row.id) === Number(recordId));
    if (!item) return;
    const remaining = Number(item.remaining_quantity || 0);
    openModal({
      title: "确认转为领用",
      placement: "top-center",
      body: `<div class="borrow-claim-confirm">
        <p>请确认是否将以下借用物料转为已领用：</p>
        <div class="borrow-claim-summary">
          <strong>${escapeHtml(item.item_code || "")} · ${escapeHtml(item.item_name || "该物料")}</strong>
          <span>未归还：${escapeHtml(formatQty(remaining))} ${escapeHtml(item.unit || "")}</span>
        </div>
        <label>领用数量<input id="borrowClaimQty" type="number" min="0" step="any" max="${remaining}" value="${formatInputQty(remaining)}"></label>
        <p class="borrow-claim-warning">转换后该数量记为已领用，如不留物料可再退库。</p>
      </div>`,
      okText: "确认领用",
      onOk: async () => {
        const quantity = Number(document.getElementById("borrowClaimQty")?.value);
        if (!(quantity > 0) || quantity > remaining + 1e-9) throw new Error("转领用数量不正确");
        await systemApi(`/api/borrows/${recordId}/claim`, {
          method: "POST",
          body: JSON.stringify({ quantity }),
        });
        toast("借用物料已转为已领用");
        await loadMyBorrow();
      },
    });
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
    bindFlowDetailButtons(host, true);
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
    const statusOptions = isProduction
      ? `<option value="完好">完好</option><option value="报废">报废</option><option value="异常">异常</option>`
      : `<option value="完好">完好</option><option value="损坏">损坏</option>`;

    let body = `<div class="form-grid">
      <p class="hint wide">未归还数量：${formatQty(record.remaining_quantity || 0)} ${escapeHtml(record.unit || "")}</p>
      <label>归还数量<input id="returnBorrowQty" type="number" step="1" min="0" max="${Number(record.remaining_quantity || 0)}" value="${formatInputQty(record.remaining_quantity || 0)}"></label>
      <label>归还状态
        <select id="returnStatus">${statusOptions}</select>
      </label>
      <label id="remarksLabel" style="display:none">备注说明<textarea id="returnRemarks" rows="2"></textarea></label>
      <label class="wide" id="returnWarehouseLabel">仓库验收入库人<select id="returnBorrowWarehouse">${userOptionsForStep("borrow_return", "return_inbound", "warehouse", "", warehouseId)}</select></label>`;

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
        const warehouseLabel = document.getElementById('returnWarehouseLabel');
        statusSelect.onchange = () => {
          const materialDamage = !isProduction && statusSelect.value === '损坏';
          remarksLabel.style.display = (statusSelect.value === '报废' || statusSelect.value === '异常') ? '' : 'none';
          if (warehouseLabel) warehouseLabel.style.display = materialDamage ? 'none' : '';
          if (materialDamage && !confirm("借用物料损坏需领用，如后续不留物料可再退库")) {
            statusSelect.value = "完好";
            if (warehouseLabel) warehouseLabel.style.display = '';
          }
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
        if (!isProduction && status === '损坏') {
          const quantity = parseFloat(document.getElementById('returnBorrowQty').value);
          await systemApi(`/api/borrows/${record.id}/claim`, {
            method: "POST",
            body: JSON.stringify({ quantity }),
          });
          toast("已转为领用");
          await loadMyBorrow();
          await refreshRealtimeTodos();
          return;
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
    const remaining = Number(record.remaining_quantity || 0);
    openModal({
      title: `转借 - ${escapeHtml(record.item_name || "")}`,
      body: `<div class="form-grid">
        <p class="hint wide">未归还数量：${formatQty(remaining)} ${escapeHtml(record.unit || "")}</p>
        <label>转借数量<input id="borrowTransferQty" type="number" min="0" step="any" max="${remaining}" value="${formatInputQty(remaining)}"></label>
      </div>`,
      okText: "选择接收人",
      onOk: () => {
        const quantity = Number(document.getElementById("borrowTransferQty")?.value);
        if (!(quantity > 0) || quantity > remaining + 1e-9) throw new Error("转借数量不正确");
        window.setTimeout(() => {
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
                body: JSON.stringify({ receiver_id: receiverId, quantity }),
              });
              toast("转借请求已发送");
              await refreshMyBorrows();
            },
          });
        }, 0);
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
    const stockScope = document.getElementById("flowStockScope")?.value || "";
    if (type) params.set("type", type);
    if (status) params.set("status", status);
    if (stockScope) params.set("stock_scope", stockScope);
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
        if (form.form_type === "acceptance" && workflowCan(form, "edit")) {
          const editButton = host.querySelector(`[data-edit-flow="${form.id}"]`);
          const correctButton = `<button type="button" data-correct-price="${form.id}">更正单价</button>`;
          if (editButton) editButton.insertAdjacentHTML("afterend", correctButton);
          else detail.insertAdjacentHTML("afterend", correctButton);
        }
        if (form.form_type === "semifinished" && form.status === "completed" && workflowCan(form, "edit")) {
          const editButton = host.querySelector(`[data-edit-flow="${form.id}"]`);
          const revokeButton = `<button type="button" data-revoke-semi="${form.id}">撤销</button>`;
          if (editButton) editButton.insertAdjacentHTML("afterend", revokeButton);
          else detail.insertAdjacentHTML("afterend", revokeButton);
        }
      });
    }
    document.getElementById("checkAllFlows")?.addEventListener("change", event => {
      host.querySelectorAll("[data-flow-check]").forEach(input => { input.checked = event.target.checked; });
    });
    bindFlowDetailButtons(host);
    host.querySelectorAll("[data-edit-flow]").forEach(btn => btn.addEventListener("click", () => editWorkflow(Number(btn.dataset.editFlow))));
    host.querySelectorAll("[data-revoke-semi]").forEach(btn => btn.addEventListener("click", () => revokeSemifinishedWorkflow(Number(btn.dataset.revokeSemi))));
    host.querySelectorAll("[data-correct-price]").forEach(btn => btn.addEventListener("click", async () => {
      if (btn.disabled) return;
      btn.disabled = true;
      try {
        await openAcceptancePriceCorrection(Number(btn.dataset.correctPrice));
      } finally {
        btn.disabled = false;
      }
    }));
    host.querySelectorAll("[data-delete-flow]").forEach(btn => btn.addEventListener("click", () => deleteWorkflow(Number(btn.dataset.deleteFlow))));
    if (pageKey) bindPagination(host, pageKey, () => renderFlowList(hostId, rows));
  }

  function workflowCan(form, action) {
    const isAcceptance = form.form_type === "acceptance" || form.form_type === "semifinished" || form.form_type === "finished";
    if (form.form_type === "borrow" || form.form_type === "borrow_return" || form.form_type === "borrow_claim") return hasPerm(`${action}_borrow`);
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
    if (type === "semifinished_claim") return "半成品领用";
    if (type === "claim_return") return "领用退库";
    if (type === "borrow") return "借用申请";
    if (type === "borrow_return") return "借用归还";
    if (type === "borrow_claim") return "借用转领用";
    if (type === "semifinished") return "半成品验收";
    if (type === "finished") return "成品验收";
    if (type === "purchase_request") return "采购申请";
    if (type === "purchase_summary") return "采购汇总";
    if (type === "special_claim") return "特殊用料";
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
      leader_semifinished_claim: "半成品领用审批",
      semifinished_outbound: "半成品待出库",
      damage_claim: "物料损坏转领用",
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
      supply_handover_print: "打印回执单",
      supply_outbound: "供货出库",
      supply_voucher_upload: "上传交接凭证",
      supply_voucher_review: "查阅交接凭证",
      supply_return_acceptance: "回寄验收",
      supply_return_inbound: "供货回寄验收入库",
      leader_supply_extension: "供货延期审批",
      external_open: "外部待结清",
      voucher: "交接凭证",
      shipping: "出库中",
      pending_summary: "待汇总",
      returned: "已退回",
      summarized: "已汇总",
      registered: "已登记",
      project_owner: "项目负责人审批",
      leader_special_claim: "特殊用料领导审批",
      special_outbound: "特殊用料出库",
      purchase_register: "库房赋码",
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
      leader_semifinished_claim: "半成品领用审批",
      semifinished_outbound: "半成品出库",
      damage_claim: "物料损坏转领用",
      leader_borrow: "借用审批",
      borrow_outbound: "借用出库",
      return_inbound: "归还验收入库",
      applicant_revision: "发起人修改",
      stocktake_supervisor: "监盘签字",
      supervisor: "监盘签字",
      completed: "已办结",
      rejected: "已驳回",
      cancelled: "已取消",
      leader_common_material: "常用物料审批",
      leader_supply: "供货审批",
      supply_handover_print: "打印回执单",
      supply_outbound: "供货出库",
      supply_voucher_upload: "上传交接凭证",
      supply_voucher_review: "查阅交接凭证",
      supply_return_acceptance: "回寄验收",
      supply_return_inbound: "供货回寄验收入库",
      leader_supply_extension: "供货延期审批",
      external_open: "外部待结清",
      project_owner: "项目负责人",
      leader_special_claim: "特殊用料领导审批",
      special_outbound: "特殊用料出库",
      purchase_register: "库房赋码",
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
      ${isProductionFlow(detail) ? `<label>制作人<select id="editFlowMaker"><option value="">请选择制作人</option>${userOptionsFrom((system.boot.users || []).filter(user => user.is_active), Number(detail.data?.maker_id || 0))}</select></label>` : ""}
      <div class="wide">${editWorkflowItemsHtml(detail)}</div>
      <div class="wide">${editWorkflowTasksHtml(detail)}</div>
    </div>`, okText: "保存修改", onReady: () => {
      document.getElementById("editFlowLeader").value = detail.leader_id || "";
      document.getElementById("editFlowWarehouse").value = detail.warehouse_user_id || "";
      const makerSelect = document.getElementById("editFlowMaker");
      if (makerSelect) makerSelect.value = detail.data?.maker_id || "";
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
      bindClaimOutboundQuantityEditors();
    }, onOk: async () => {
      await systemApi(`/api/workflows/${formId}`, { method: "PUT", body: JSON.stringify({
        title: document.getElementById("editFlowTitle").value,
        status: document.getElementById("editFlowStatus").value,
        current_step: document.getElementById("editFlowStep").value,
        leader_id: document.getElementById("editFlowLeader").value,
        warehouse_user_id: document.getElementById("editFlowWarehouse").value,
        maker_id: document.getElementById("editFlowMaker")?.value,
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

  function editWorkflowItemControl(key, label, item) {
    if (key === "purchase_project") {
      const selected = item.purchase_project || item.data?.purchase_project || "";
      return `<label>${label}<select data-edit-item-field="${key}">${batchProjectOptions(selected)}</select></label>`;
    }
    return `<label>${label}<input data-edit-item-field="${key}" value="${escapeAttr(item[key] ?? "")}"></label>`;
  }

  function editWorkflowItemFields(form) {
    const type = form.form_type;
    if (type === "acceptance") {
      return [
        ["request_quantity", "采购数量"],
        ["arrival_quantity", "到货数量"],
        ["qualified_quantity", "合格数量"],
        ["unqualified_quantity", "不合格数量"],
        ["approved_quantity", "核准入库"],
        ["unit_price", "单价"],
        ["purchase_project", "采购申请项目"],
      ];
    }
    if (type === "semifinished" || type === "finished") {
      return [
        ["arrival_quantity", "验收数量"],
        ["qualified_quantity", "合格数量"],
        ["unqualified_quantity", "不合格数量"],
      ];
    }
    if (type === "borrow") {
      return [
        ["request_quantity", "申请数量"],
        ["outbound_quantity", "出库数量"],
      ];
    }
    return [
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
  }

  function claimOutboundLineHtml(line) {
    return `<div class="form-grid" data-claim-line data-stock-record-id="${Number(line.stock_record_id || 0)}" data-batch-id="${Number(line.batch_id || 0)}"><label>批次<input value="${escapeAttr(line.batch_no || "")}" readonly></label><label>实际数量<input data-claim-actual type="number" min="0" step="1" value="${escapeAttr(formatInputQty(line.actual_quantity || 0))}"></label><label>每单位数量<input data-claim-pack type="number" min="1" step="1" value="${escapeAttr(formatInputQty(line.pack_quantity || 1))}"></label><label>账面数量<input data-claim-book type="number" min="0" step="1" value="${escapeAttr(formatInputQty(line.book_quantity || 0))}"></label></div>`;
  }

  function bindClaimOutboundQuantityEditors() {
    document.querySelectorAll("[data-claim-line]").forEach(line => {
      const actual = line.querySelector("[data-claim-actual]");
      const pack = line.querySelector("[data-claim-pack]");
      const book = line.querySelector("[data-claim-book]");
      if (!actual || !pack || !book) return;
      const syncActual = () => {
        if (actual.dataset.handEdited === "1") return;
        const bookQty = Number(book.value || 0);
        const packQty = Number(pack.value || 0);
        if (!Number.isFinite(bookQty) || !Number.isFinite(packQty)) return;
        actual.value = String(Number((bookQty * packQty).toFixed(4)));
      };
      actual.addEventListener("input", () => { actual.dataset.handEdited = "1"; });
      pack.addEventListener("input", syncActual);
      book.addEventListener("input", syncActual);
    });
  }

  function editWorkflowItemsHtml(form) {
    const claimLike = form.form_type === "claim" || form.form_type === "special_claim";
    const fields = claimLike ? [] : editWorkflowItemFields(form);
    const body = (form.items || []).map(item => {
      const title = [item.material_code, item.material_name].filter(Boolean).join(" ");
      if (claimLike) {
        const lines = item.outbound_lines || [];
        const lineHtml = lines.length
          ? lines.map(claimOutboundLineHtml).join("")
          : `<p class="hint">尚未出库，没有待消耗数量</p>`;
        return `<div class="work-panel" data-edit-flow-item="${item.id}"><div class="work-body"><p><strong>${escapeHtml(title || "物料")}</strong></p>${lineHtml}</div></div>`;
      }
      return `<div class="work-panel" data-edit-flow-item="${item.id}"><div class="work-body"><p><strong>${escapeHtml(title || item.material_name || "")}</strong></p><div class="form-grid">${fields.map(([key, label]) => editWorkflowItemControl(key, label, item)).join("")}</div><div style="margin-top:8px;display:flex;justify-content:flex-end"><button class="danger" data-delete-edit-item="${item.id}">删除此物料行</button></div></div></div>`;
    }).join("");
    return `<h3>表单明细</h3>${body || `<div class="empty-state">暂无明细</div>`}`;
  }

  function editWorkflowTasksHtml(form) {
    return `<h3>签字/办理记录</h3>${(form.tasks || []).map(task => `<div class="work-panel" data-edit-flow-task="${task.id}"><div class="work-body"><div class="form-grid"><label>环节<input value="${escapeAttr(workflowStepLabel(task.step_code || ""))}" readonly></label><label>办理人<select data-edit-task-field="assignee_id"><option value="">未指定</option>${userOptionsFrom(system.boot.users || [], Number(task.assignee_id || 0))}</select></label><label>状态<input data-edit-task-field="status" value="${escapeAttr(task.status || "")}"></label><label>意见<input data-edit-task-field="decision" value="${escapeAttr(task.decision || "")}"></label><label>签字<input data-edit-task-field="signature" value="${escapeAttr(task.signature || "")}"></label><label>日期<input data-edit-task-field="signed_at" value="${escapeAttr(task.signed_at || "")}"></label><label class="wide">备注<textarea data-edit-task-field="remark">${escapeHtml(task.data?.remark || "")}</textarea></label></div></div></div>`).join("") || `<div class="empty-state">暂无办理记录</div>`}`;
  }

  function collectEditWorkflowItems() {
    return [...document.querySelectorAll("[data-edit-flow-item]")].filter(row => !row.dataset.editFlowDeleted).map(row => {
      const item = { id: Number(row.dataset.editFlowItem) };
      const lines = [...row.querySelectorAll("[data-claim-line]")];
      if (lines.length) {
        item.outbound_lines = lines.map(line => ({
          stock_record_id: Number(line.dataset.stockRecordId),
          batch_id: Number(line.dataset.batchId),
          actual_quantity: line.querySelector("[data-claim-actual]")?.value,
          pack_quantity: line.querySelector("[data-claim-pack]")?.value,
          book_quantity: line.querySelector("[data-claim-book]")?.value,
        }));
        return item;
      }
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

  async function revokeSemifinishedWorkflow(formId) {
    if (!confirm("确认撤销该半成品验收？撤销后本单入库结果删除，流程退回待入库。")) return;
    await systemApi(`/api/production/semifinished/${formId}/revoke`, { method: "POST", body: "{}" });
    toast("半成品验收已撤销");
    refreshAfterFlowMutation();
  }

  function correctionDocumentText(items, key) {
    const numbers = [];
    (items || []).forEach(item => {
      (item[key] || []).forEach(number => {
        if (number && !numbers.includes(number)) numbers.push(number);
      });
    });
    return numbers.join("、");
  }

  async function openAcceptancePriceCorrection(formId) {
    let preview;
    try {
      preview = await systemApi(`/api/acceptance/${formId}/unit-price-correction`);
    } catch (error) {
      toast(error.message || "更正单价失败");
      return;
    }
    const items = preview.items || [];
    const outboundText = correctionDocumentText(items, "outbound_form_nos") || "无";
    const semifinishedText = correctionDocumentText(items, "semifinished_form_nos") || "无";
    const finishedText = correctionDocumentText(items, "finished_form_nos") || "无";
    openModal({
      title: `更正单价 ${preview.form_no || ""}`,
      body: `<div class="form-grid">
        <p class="hint wide">未修改的行不会保存。出库单金额会改成新单价，相关半成品和成品成本会重算。没有入库批次的行不能更正。</p>
        <p class="wide">受影响的出库单：${escapeHtml(outboundText)}</p>
        <p class="wide">将重算成本的半成品：${escapeHtml(semifinishedText)}</p>
        <p class="wide">将重算成本的成品：${escapeHtml(finishedText)}</p>
        ${items.map(item => `<div class="work-panel wide"><div class="work-body"><div class="form-grid">
          <label>物料<input value="${escapeAttr(`${item.material_code || ""} ${item.material_name || ""}`.trim())}" readonly></label>
          <label>当前单价<input value="${escapeAttr(formatInputQty(item.unit_price || 0))}" readonly></label>
          <label>新单价<input id="correctPrice${item.id}" type="number" min="0" step="0.0001" value="${escapeAttr(formatInputQty(item.unit_price || 0))}" ${item.correctable ? "" : "disabled"}></label>
          ${item.correctable ? "" : `<p class="hint wide">${escapeHtml(item.block_reason || "这条验收明细没有入库批次，不能更正单价")}</p>`}
        </div></div></div>`).join("") || `<div class="empty-state">暂无验收明细</div>`}
      </div>`,
      okText: "确认更正",
      onOk: async () => {
        const corrections = [];
        items.forEach(item => {
          const input = document.getElementById(`correctPrice${item.id}`);
          if (!input || input.disabled) return;
          const next = input.value.trim();
          if (next === "" || Math.abs(Number(next) - Number(item.unit_price || 0)) <= 1e-9) return;
          corrections.push({ id: item.id, unit_price: next });
        });
        if (!corrections.length) throw new Error("没有需要更正的单价");
        const result = await systemApi(`/api/acceptance/${formId}/correct-unit-price`, {
          method: "POST",
          body: JSON.stringify({ items: corrections }),
        });
        const changedOutbound = (result.outbound_form_nos || []).join("、");
        toast(changedOutbound ? `单价已更正，请重打出库单 ${changedOutbound}` : "单价已更正");
        await refreshAfterFlowMutation();
      },
    });
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
    const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8" });
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
    const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = filename; a.click(); URL.revokeObjectURL(a.href);
  }

  async function printFullFlowRows(rows) {
    const data = await fullFlowRows(rows.length ? rows : system.flowRows);
    const html = data.map(row => `<section style="page-break-inside:avoid;margin-bottom:18px;"><h2>${escapeHtml(row.form_type === "purchase_request" ? `${row.form_no} 中光学（杭州）物料采购申请` : `${row.form_no} ${row.title || ""}`)}</h2><p>类型：${flowTypeLabel(row.form_type)}，项目代号：${escapeHtml(flowProjectCode(row))}，状态：${escapeHtml(workflowStatusLabel(row.status))}，申请人：${escapeHtml(row.applicant_name || "")}，时间：${escapeHtml(row.created_at || "")}${row.form_type === "claim" ? `，流程属性：${escapeHtml(row.data?.flow_attribute || "实物交接")}` : ""}</p>${flowItemsTable(row)}${row.form_type === "purchase_summary" ? "<p>制表　　　　校对　　　　审核　　　　审批</p>" : ""}<h3>审批/办理记录</h3><table class="flow-table"><thead><tr><th>环节</th><th>办理人</th><th>意见</th><th>签字</th><th>日期</th><th>备注</th></tr></thead><tbody>${(row.tasks || []).map(t => `<tr><td>${escapeHtml(workflowStepLabel(t.step_code))}</td><td>${escapeHtml(t.assignee_name || "")}</td><td>${escapeHtml(t.decision || "")}</td><td>${escapeHtml(t.signature || "")}</td><td>${escapeHtml(t.signed_at || "")}</td><td>${escapeHtml(t.data?.remark || t.data?.return_reason || "")}</td></tr>`).join("")}</tbody></table></section>`).join("");
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
    let vouchers = [];
    if (form.form_type === "supply" && Number(form.data?.handover_required || 0)) {
      try {
        const voucherData = await systemApi(`/api/supplies/${form.id}/handover-vouchers`);
        vouchers = voucherData.items || [];
      } catch (error) {
        vouchers = [];
      }
    }
    const receiptButton = form.form_type === "supply" && Number(form.data?.handover_required || 0)
      ? `<button data-flow-action="supplyReceipt">打印回执单</button>` : "";
    const taskButtons = (readonly ? `<button data-flow-action="printDetail">打印</button>${receiptButton}` : flowActions(form));
    const leaderSignatures = (form.tasks || []).filter(task => task.step_code === "leader_acceptance" || task.step_code === "leader_claim" || task.step_code === "leader_borrow").map(task => task.signature).filter(Boolean).join("、");
    const showAcceptanceSignature = form.form_type === "acceptance" || form.form_type === "supply_return" || isProductionFlow(form);
    openModal({
      title: `${form.form_no} · ${form.title || ""}`,
      hideOk: true,
      body: `
        <div class="facts">
          <div class="fact"><span>状态</span><strong>${escapeHtml(workflowStatusLabel(form.status))}</strong></div>
          <div class="fact"><span>申请人</span><span>${escapeHtml(form.applicant_name || "")}</span></div>
          ${form.form_type === "supply" && form.data?.contact_phone ? `<div class="fact"><span>申请人电话</span><span>${escapeHtml(form.data.contact_phone)}</span></div>` : ""}
          ${form.form_type === "claim" ? `<div class="fact"><span>流程属性</span><span>${escapeHtml(form.data?.flow_attribute || "实物交接")}</span></div>` : ""}
          ${form.form_type === "borrow_claim" ? `<div class="fact"><span>关联借用单号</span><span>${escapeHtml(form.data?.borrow_form_no || "")}</span></div><div class="fact"><span>领用日期</span><span>${escapeHtml(form.data?.claimed_date || "")}</span></div>` : ""}
          ${isProductionFlow(form) ? `<div class="fact"><span>制作人</span><span>${escapeHtml(form.data?.maker || "未填写")}</span></div>` : ""}
          ${showAcceptanceSignature ? `<div class="fact"><span>验收签字</span><span>${escapeHtml(form.data?.acceptance_signatures || "")}</span></div>` : ""}
          <div class="fact"><span>领导签字</span><span>${escapeHtml(leaderSignatures || "")}</span></div>
        </div>
        ${flowItemsTable(form)}
        ${supplyVoucherHtml(vouchers)}
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
    const pendingTask = currentPendingTask(form);
    const isAdmin = (system.boot?.user?.role_codes || []).includes("admin");
    if (pendingTask && isProductionFlow(form) && form.status === "acceptance") buttons.push(`<button data-flow-action="productionInspect" class="primary">填写验收</button>`);
    if (pendingTask && isProductionFlow(form) && form.status === "leader_acceptance") buttons.push(`<button data-flow-action="productionLeader" class="primary">领导审批</button>`);
    if (pendingTask && isProductionFlow(form) && form.status === "inbound") buttons.push(`<button data-flow-action="productionInbound" class="good">办理入库</button>`);
    if (pendingTask && form.form_type === "acceptance" && form.status === "acceptance") buttons.push(`<button data-flow-action="inspect" class="primary">填写验收</button>`);
    if (pendingTask && form.form_type === "acceptance" && form.status === "leader_acceptance") buttons.push(`<button data-flow-action="leaderAcceptance" class="primary">领导审批</button>`);
    if (pendingTask && form.form_type === "acceptance" && form.status === "inbound") buttons.push(`<button data-flow-action="inbound" class="good">办理入库</button>`);
    if (pendingTask && form.form_type === "claim" && form.status === "leader_claim") buttons.push(`<button data-flow-action="leaderClaim" class="primary">领导审批</button>`);
    if (pendingTask && form.form_type === "claim" && form.status === "outbound") buttons.push(`<button data-flow-action="outbound" class="warn">办理出库</button>`);
    if (pendingTask && form.form_type === "semifinished_claim" && form.status === "leader_semifinished_claim") buttons.push(`<button data-flow-action="leaderSemiClaim" class="primary">领导审批</button>`);
    if (pendingTask && form.form_type === "semifinished_claim" && form.status === "semifinished_outbound") buttons.push(`<button data-flow-action="semiClaimOutbound" class="warn">办理出库</button>`);
    if (pendingTask && form.form_type === "claim_return" && form.status === "return_inbound") buttons.push(`<button data-flow-action="claimReturnInbound" class="good">退库入库</button>`);
    if (pendingTask && form.form_type === "borrow_return" && form.status === "damage_claim") buttons.push(`<button data-flow-action="borrowDamageClaim" class="warn">物料损坏转领用</button>`);
    if (pendingTask && form.form_type === "borrow" && form.status === "leader_borrow") buttons.push(`<button data-flow-action="leaderBorrow" class="primary">领导审批</button>`);
    if (pendingTask && form.form_type === "borrow" && form.status === "borrow_outbound") buttons.push(`<button data-flow-action="borrowOutbound" class="warn">办理出库</button>`);
    if (pendingTask && form.form_type === "borrow_return" && form.status === "return_inbound") buttons.push(`<button data-flow-action="borrowReturnInbound" class="good">验收入库</button>`);
    if (pendingTask && form.form_type === "common_material" && form.status === "leader_common_material") buttons.push('<button data-flow-action="leaderCommonMaterial" class="primary">常用物料审批</button>');
    if (pendingTask && form.form_type === "supply" && form.status === "leader_supply") buttons.push('<button data-flow-action="leaderSupply" class="primary">供货审批</button>');
    if (form.form_type === "supply" && Number(form.data?.handover_required || 0)) buttons.push('<button data-flow-action="supplyReceipt">打印回执单</button>');
    if (pendingTask && form.form_type === "supply" && form.status === "supply_handover_print") buttons.push('<button data-flow-action="supplyReceiptPrinted" class="primary">已打印</button>');
    if (pendingTask && form.form_type === "supply" && form.status === "supply_outbound") buttons.push('<button data-flow-action="supplyOutbound" class="warn">办理供货出库</button>');
    if (pendingTask && form.form_type === "supply" && form.status === "supply_voucher_upload") buttons.push('<button data-flow-action="supplyVoucherUpload" class="primary">上传交接凭证</button>');
    if (pendingTask && form.form_type === "supply" && form.status === "supply_voucher_review") buttons.push('<button data-flow-action="supplyVoucherReview" class="primary">查阅交接凭证</button>');
    if (pendingTask && form.form_type === "supply_return" && form.status === "supply_return_acceptance") buttons.push('<button data-flow-action="supplyReturnAccept" class="primary">验收</button>');
    if (pendingTask && form.form_type === "supply_return" && form.status === "supply_return_inbound") buttons.push('<button data-flow-action="supplyReturnInbound" class="good">办理入库</button>');
    if (pendingTask && form.form_type === "supply_extension" && form.status === "leader_supply_extension") buttons.push('<button data-flow-action="leaderSupplyExtension" class="primary">延期审批</button>');
    if (pendingTask && form.form_type === "special_claim" && (form.status === "project_owner" || form.status === "leader_special_claim")) buttons.push('<button data-flow-action="specialDecide" class="primary">审批</button>');
    if (pendingTask && form.form_type === "special_claim" && form.status === "special_outbound") buttons.push('<button data-flow-action="specialOutbound" class="warn">办理出库</button>');
    if (pendingTask && form.form_type === "purchase_summary" && form.status === "summarized") buttons.push('<button data-flow-action="purchaseRegister" class="good">申请物料编码</button>');
    if (pendingTask && form.status === "applicant_revision") buttons.push(`<button data-flow-action="returnedRevision" class="primary">重新提交</button>`);
    if (workflowCanReturn(form)) buttons.push(`<button data-flow-action="returnFlow" class="danger">退回</button>`);
    if (isAdmin && currentStepPendingTasks(form).length) buttons.push(`<button data-flow-action="reassignTask">改派当前待办</button>`);
    buttons.push(`<button data-flow-action="printDetail">打印</button>`);
    return buttons.join("");
  }

  async function handleFlowAction(form, action) {
    if (action === "supplyReceipt") return printSupplyReceipt(form);
    closeModal();
    if (action === "productionInspect") return inspectProductionFlow(form);
    if (action === "productionLeader") return leaderDecision(`/api/production/${form.form_type}/${form.id}/leader`, form);
    if (action === "productionInbound") return inboundProductionFlow(form);
    if (action === "inspect") return inspectFlow(form);
    if (action === "leaderAcceptance") return leaderDecision(`/api/acceptance/${form.id}/leader`, form);
    if (action === "inbound") return inboundFlowV2(form);
    if (action === "leaderClaim") return leaderDecision(`/api/claims/${form.id}/leader`, form);
    if (action === "outbound") return outboundFlowV2(form);
    if (action === "leaderSemiClaim") return leaderDecision(`/api/semifinished-claims/${form.id}/leader`, form);
    if (action === "semiClaimOutbound") return semiClaimOutboundFlow(form);
    if (action === "claimReturnInbound") return claimReturnInboundFlow(form);
    if (action === "borrowDamageClaim") return borrowDamageClaimFlow(form);
    if (action === "leaderBorrow") return leaderDecision(`/api/borrows/${form.id}/leader`, form);
    if (action === "leaderCommonMaterial") return leaderDecision("/api/common-material-applications/" + form.id + "/leader", form);
    if (action === "leaderSupply") return leaderDecision("/api/supplies/" + form.id + "/leader", form);
    if (action === "supplyReceiptPrinted") return confirmSupplyReceiptPrinted(form);
    if (action === "supplyVoucherUpload") return uploadSupplyVoucher(form);
    if (action === "supplyVoucherReview") return reviewSupplyVoucher(form);
    if (action === "leaderSupplyExtension") return leaderDecision("/api/supply-extensions/" + form.id + "/leader", form);
    if (action === "supplyReturnInbound") return supplyReturnInboundFlow(form);
    if (action === "borrowOutbound") return borrowOutboundFlow(form);
    if (action === "borrowReturnInbound") return borrowReturnInboundFlow(form);
    if (action === "returnedRevision") return editApplicantWorkflow(form);
    if (action === "returnFlow") return returnWorkflowFlow(form);
    if (action === "reassignTask") return reassignWorkflowTask(form);
    if (action === "printDetail") return printFullFlowRows([form]);
    if (action === "specialDecide") return decideSpecialClaim(form);
    if (action === "specialOutbound") return outboundSpecialClaim(form);
    if (action === "purchaseRegister") {
      closeModal();
      system.codingFormId = form.id;
      system.view = "purchaseCoding";
      renderSystemMain();
      return;
    }
    if (action === "supplyOutbound") return supplyOutboundFlow(form);
    if (action === "supplyReturnAccept") return acceptSupplyReturn(form);
  }

  async function decideSpecialClaim(form) {
    openModal({
      title: "特殊用料审批",
      okText: "同意",
      body: `<label class="wide">意见<textarea id="specialDecisionRemark"></textarea></label>`,
      onOk: async () => {
        await systemApi(`/api/special-claims/${form.id}/decide`, { method: "POST", body: JSON.stringify({ approved: true, remark: document.getElementById("specialDecisionRemark").value }) });
        toast("已同意");
        renderSystemMain();
      },
    });
  }

  async function outboundSpecialClaim(form) {
    const items = (form.items || []).map(item => ({ id: item.id, outbound_quantity: item.request_quantity }));
    await systemApi(`/api/special-claims/${form.id}/outbound`, { method: "POST", body: JSON.stringify({ items }) });
    toast("已出库");
    renderSystemMain();
  }

  function workflowStepCodes(type, form) {
    const supplySteps = form && Number(form.data?.handover_required || 0)
      ? ["leader_supply", "supply_handover_print", "supply_outbound", "supply_voucher_upload", "supply_voucher_review"]
      : ["leader_supply", "supply_outbound"];
    const map = {
      acceptance: ["acceptance", "leader_acceptance", "inbound"],
      claim: ["leader_claim", "outbound"],
      borrow: ["leader_borrow", "borrow_outbound"],
      semifinished_claim: ["leader_semifinished_claim", "semifinished_outbound"],
      claim_return: ["return_inbound"],
      borrow_return: ["return_inbound", "damage_claim"],
      borrow_claim: [],
      common_material: ["leader_common_material"],
      supply: supplySteps,
      supply_return: ["supply_return_acceptance", "supply_return_inbound"],
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

  function currentStepPendingTasks(form) {
    const step = form.current_step || form.status;
    return (form.tasks || []).filter(task => task.status === "pending" && task.step_code === step);
  }

  function reassignWorkflowTask(form) {
    const tasks = currentStepPendingTasks(form);
    const activeUsers = (system.boot?.users || []).filter(user => user.is_active);
    openModal({
      title: `改派 ${form.form_no}`,
      body: `<div class="form-grid">
        <label>当前待办<select id="reassignTaskId">${tasks.map(task => `<option value="${task.id}">${escapeHtml(workflowStepLabel(task.step_code))} · ${escapeHtml(task.assignee_name || "未指定")}</option>`).join("")}</select></label>
        <label>新的办理人<select id="reassignTargetUser"><option value="">请选择</option>${userOptionsFrom(activeUsers, 0)}</select></label>
      </div>`,
      okText: "确认改派",
      onOk: async () => {
        const taskId = Number(document.getElementById("reassignTaskId").value || 0);
        const targetUserId = Number(document.getElementById("reassignTargetUser").value || 0);
        if (!taskId || !targetUserId) throw new Error("请选择待办和新的办理人");
        await systemApi(`/api/workflows/${form.id}/tasks/${taskId}/reassign`, {
          method: "POST",
          body: JSON.stringify({ target_user_id: targetUserId }),
        });
        toast("待办已改派");
        await refreshAfterFlowMutation();
      },
    });
  }

  function workflowCanReturn(form) {
    const steps = workflowStepCodes(form.form_type, form);
    const current = form.current_step || form.status;
    return currentPendingTask(form) && steps.includes(current);
  }

  function returnWorkflowFlow(form) {
    const steps = workflowStepCodes(form.form_type, form);
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
    return `<table class="flow-table"><thead><tr><th>${productionNameLabel(form)}</th><th>项目代号</th><th>参数</th><th>验收数量</th><th>单位</th></tr></thead><tbody><tr><td>${escapeHtml(item.material_name || "")}</td><td>${escapeHtml(flowProjectCode(form) || item.data?.project_code || "")}</td><td>${escapeHtml(item.spec || "")}</td><td>${formatQty(item.arrival_quantity || item.request_quantity || 0)}</td><td>${escapeHtml(item.unit || "")}</td></tr></tbody></table><h3>${productionComponentLabel(form)}</h3>`;
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
    const params = new URLSearchParams({ name, count: String(count) });
    if (form.id) params.set("form_id", String(form.id));
    const data = await systemApi(`/api/production/${form.form_type}/serials?${params.toString()}`);
    if (token !== productionSerialPreviewToken) return;
    const existing = item.data?.serial_items || [];
    const serials = data.serials || [];
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
          <label>账面入库数量<input id="prodInboundQty" type="number" step="1" value="${formatInputQty(item.qualified_quantity || 0)}" readonly></label>
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
    const isSupply = form.form_type === "supply" || url.includes("/supplies/");
    const nextStep = isBorrow ? "borrow_outbound" : (isClaim ? "outbound" : (isSupply ? "supply_outbound" : "inbound"));
    const formType = isBorrow ? "borrow" : (isClaim ? "claim" : (isSupply ? "supply" : (form.form_type || "acceptance")));
    const warehouseId = defaultStepUserId(formType, nextStep, "warehouse");
    openModal({ title: "领导审批", body: `<div class="form-grid"><label>是否同意<select id="leaderDecision"><option>同意</option><option>不同意</option></select></label><label>${isClaim || isBorrow || isSupply ? "出库办理人" : "入库办理人"}<select id="leaderWarehouse">${userOptionsForStep(formType, nextStep, "warehouse", "", warehouseId)}</select></label><label>签字<input id="leaderSign" value="${escapeAttr(currentUser()?.display_name || "")}"></label><label class="wide">备注/意见<textarea id="leaderRemark"></textarea></label></div>`, okText: "提交审批", onReady: () => {
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
      openModal({ title: "办理入库", body: `<div class="form-grid">${form.items.map(item => `<div class="wide work-panel"><div class="work-body"><strong>${escapeHtml(item.material_name)}</strong><label>账面入库数量<input data-in="${item.id}" type="number" value="${item.qualified_quantity || item.arrival_quantity || 0}"></label><div class="form-grid">${shelfLocationSelects(shelvesData, item.id)}</div></div></div>`).join("")}<label class="wide">库管签字<input id="inSign" value="${escapeAttr(currentUser()?.display_name || "")}"></label></div>`, okText: "确认入库", onReady: () => {
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
        pack_quantity: document.querySelector(`[data-pack="${item.id}"]`).value,
        actual_quantity: document.querySelector(`[data-actual="${item.id}"]`).value,
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
          document.querySelector(`[data-pack="${id}"]`)?.setAttribute("disabled", "disabled");
          document.querySelector(`[data-actual="${id}"]`)?.setAttribute("disabled", "disabled");
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
        const pack = Number(item.data?.pack_quantity || item.pack_quantity || 1);
        const arrival = Number(item.arrival_quantity || 0);
        const actual = arrival * pack;
        return `<div class="wide work-panel" data-inbound-card="${item.id}"><div class="work-body"><strong data-inbound-title>${escapeHtml(item.material_name)}${done ? ` <span class="badge" data-inbound-done-badge>已入库</span>` : ""}</strong><p>入库批次：<b>${escapeHtml(item.data?.inbound_batch_no || expectedInboundBatchNo(item))}</b></p><label>每单位数量<input data-pack="${item.id}" type="number" min="1" step="1" value="${pack}" ${done ? "disabled" : ""}></label><label>账面入库数量<input data-in="${item.id}" type="number" value="${item.qualified_quantity || item.arrival_quantity || 0}" ${done ? "disabled" : ""}></label><label>实际数量<input data-actual="${item.id}" type="number" value="${actual}" ${done ? "disabled" : ""}></label><div class="form-grid">${shelfLocationSelects(shelvesData, item.id)}</div>${singleEnabled ? `<div class="inbound-single-actions"><button type="button" class="good" data-single-inbound="${item.id}" ${done ? "disabled" : ""}>单独入库</button></div>` : ""}</div></div>`;
      }).join("")}<label class="wide">库管签字<input id="inSign" value="${escapeAttr(currentUser()?.display_name || "")}"></label></div>`, okText: singleEnabled ? "一键入库" : "确认入库", onReady: () => {
        document.querySelectorAll("[data-shelf]").forEach(sel => { if (sel.tagName === "SELECT") window._updateShelfLocation(sel); });
        document.querySelectorAll("[data-pack]").forEach(input => input.addEventListener("input", () => {
          const item = (form.items || []).find(row => Number(row.id) === Number(input.dataset.pack));
          const actual = document.querySelector(`[data-actual="${input.dataset.pack}"]`);
          if (actual) actual.value = Number(input.value || 0) * Number(item?.arrival_quantity || 0);
        }));
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


  function supplyVoucherHtml(vouchers) {
    if (!vouchers || !vouchers.length) return "";
    return `<h3>交接凭证</h3><ul>${vouchers.map(row => `<li><a href="${escapeAttr(row.download_url || "")}" target="_blank" rel="noopener">${escapeHtml(row.original_name || "凭证")}</a></li>`).join("")}</ul>`;
  }

  function supplyReceiptDocument(receipt) {
    const cell = value => escapeHtml(value || "");
    const rows = (receipt.items || []).map(item => `<tr><td>${cell(item.index)}</td><td>${cell(item.name)}</td><td>${cell(item.unit)}</td><td>${cell(item.quantity)}</td><td>${cell(item.goods_status)}</td><td></td></tr>`).join("");
    const dateText = formatDateCn(receipt.provide_date || "");
    return `<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title></title><style>
      /* 页边距为 0 时，浏览器不再印出日期、标题和 about:blank 页码。 */
      @page { size: A4; margin: 0; }
      html, body { margin: 0; padding: 0; }
      body { padding: 16mm 18mm; color: #000; font-family: SimSun, "Songti SC", serif; }
      h1 { text-align: center; font-size: 22px; letter-spacing: 6px; font-weight: 600; }
      table { width: 100%; border-collapse: collapse; margin-top: 12px; }
      td, th { border: 1px solid #000; padding: 8px 10px; font-size: 14px; text-align: center; vertical-align: middle; }
      .label { width: calc(90px - 2em); white-space: nowrap; }
      .sign { margin-top: 28px; font-size: 15px; display: flex; flex-direction: column; align-items: flex-end; }
      .sign-company { line-height: 2.4; }
      .sign-tail { display: grid; grid-template-columns: auto 3em auto 2em auto 2em auto; line-height: 2.4; }
      .sign-who { grid-column: 1; grid-row: 1; }
      .sign-year { grid-column: 3; grid-row: 2; }
      .sign-month { grid-column: 5; grid-row: 2; }
      .sign-day { grid-column: 7; grid-row: 2; }
    </style></head><body>
      <h1>产品接收回执单</h1>
      <table>
        <tr><td class="label">提供单位</td><td>${cell(receipt.provider_company)}</td><td class="label">接收单位</td><td>${cell(receipt.recipient_company)}</td></tr>
        <tr><td class="label">联系人</td><td>${cell(receipt.provider_contact)}</td><td class="label">联系人</td><td>${cell(receipt.recipient_name)}</td></tr>
        <tr><td class="label">联系电话</td><td>${cell(receipt.provider_phone)}</td><td class="label">联系电话</td><td>${cell(receipt.recipient_phone)}</td></tr>
        <tr><td class="label">提供日期</td><td colspan="3">${dateText}</td></tr>
      </table>
      <table>
        <thead><tr><th>序号</th><th>产品名称及代号</th><th>计量单位</th><th>数量</th><th>状态</th><th>备注</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="sign"><div class="sign-company">接收单位：${cell(receipt.recipient_company)}</div><div class="sign-tail"><span class="sign-who">接收人：</span><span class="sign-year">年</span><span class="sign-month">月</span><span class="sign-day">日</span></div></div>
    </body></html>`;
  }

  async function printSupplyReceipt(form) {
    const receipt = await systemApi(`/api/supplies/${form.id}/handover-receipt`);
    const page = window.open("", "_blank");
    if (!page) throw new Error("浏览器拦截了回执单窗口");
    page.document.open();
    page.document.write(supplyReceiptDocument(receipt));
    page.document.close();
    page.focus();
    page.print();
  }

  async function confirmSupplyReceiptPrinted(form) {
    await systemApi(`/api/supplies/${form.id}/handover-print`, { method: "POST", body: "{}" });
    toast("已确认打印");
    await refreshAfterFlowMutation();
  }

  function uploadSupplyVoucher(form) {
    openModal({
      title: "上传交接凭证",
      okText: "上传",
      body: `<label class="wide">图片或 PDF<input id="supplyVoucherFiles" type="file" accept="image/*,.pdf" multiple></label>`,
      onOk: async () => {
        const input = document.getElementById("supplyVoucherFiles");
        if (!input?.files?.length) throw new Error("请选择交接凭证");
        const body = new FormData();
        [...input.files].forEach(file => body.append("files", file));
        await systemApi(`/api/supplies/${form.id}/handover-vouchers`, { method: "POST", body, headers: {} });
        toast("凭证已上传");
        await refreshAfterFlowMutation();
      },
    });
  }

  function reviewSupplyVoucher(form) {
    openModal({
      title: "查阅交接凭证",
      okText: "提交",
      body: `<div class="form-grid"><label>是否同意<select id="voucherDecision"><option>同意</option><option>退回</option></select></label><label class="wide">意见<textarea id="voucherRemark"></textarea></label></div>`,
      onOk: async () => {
        const decision = document.getElementById("voucherDecision").value;
        const remark = document.getElementById("voucherRemark").value.trim();
        if (decision === "退回" && !remark) throw new Error("退回时必须填写意见");
        await systemApi(`/api/supplies/${form.id}/voucher-review`, { method: "POST", body: JSON.stringify({ decision, remark }) });
        toast(decision === "同意" ? "已进入外部待结清" : "已退回重新上传");
        await refreshAfterFlowMutation();
      },
    });
  }

  async function supplyOutboundFlow(form) {
    const ledger = await systemApi("/api/supplies");
    const order = (ledger.items || []).find(row => Number(row.form_id) === Number(form.id));
    if (!order) {
      toast("供货台账不存在");
      return;
    }
    const blocks = [];
    for (const item of order.items || []) {
      const remain = Math.max(0, Number(item.approved_quantity || 0) - Number(item.shipped_quantity || 0));
      if (remain <= 0) continue;
      if (item.item_type === "material") {
        const batches = await systemApi(`/api/materials/${item.item_ref_id}/batches`);
        const rows = (Array.isArray(batches) ? batches : []).filter(batch => Number(batch.quantity || 0) > 0);
        blocks.push(`<div class="wide work-panel"><div class="work-body"><h3>${escapeHtml(item.item_code || "")} ${escapeHtml(item.item_name || "")}</h3><p>待出：${formatQty(remain)} ${escapeHtml(item.unit || "")}</p><label>本次出库数量<input data-supply-qty="${item.id}" type="number" min="0" max="${remain}" step="1" value="${formatInputQty(remain)}"></label><table class="flow-table"><thead><tr><th>出库</th><th>批次</th><th>批次库存</th></tr></thead><tbody>${rows.map(batch => `<tr><td><input data-supply-batch="${item.id}" data-batch-id="${batch.id}" type="number" min="0" max="${Number(batch.quantity || 0)}" step="1" value="0"></td><td>${escapeHtml(batch.batch_no || "")}</td><td>${formatQty(batch.quantity)}</td></tr>`).join("") || `<tr><td colspan="3">暂无可用批次</td></tr>`}</tbody></table></div></div>`);
      } else {
        const choices = await systemApi(`/api/supplies/item-choices?item_type=${encodeURIComponent(item.item_type)}&q=${encodeURIComponent(item.item_name || "")}`);
        const options = (choices.items || []).filter(row => row.item_type === item.item_type);
        blocks.push(`<div class="wide work-panel"><div class="work-body"><h3>${escapeHtml(item.item_name || "")}</h3><p>待出：${formatQty(remain)} ${escapeHtml(item.unit || "")}</p><label>本次出库数量<input data-supply-qty="${item.id}" type="number" min="0" max="${remain}" step="1" value="${formatInputQty(remain)}"></label><label>编号<select data-supply-serial="${item.id}">${options.map(row => `<option value="${row.item_ref_id}" data-code="${escapeAttr(row.item_code || "")}" ${Number(row.item_ref_id) === Number(item.item_ref_id) ? "selected" : ""}>${escapeHtml(row.item_code || "")} ${escapeHtml(row.item_name || "")}</option>`).join("")}</select></label></div></div>`);
      }
    }
    openModal({
      title: "办理供货出库",
      okText: "确认出库",
      body: `<div class="form-grid">${blocks.join("") || `<div class="wide">没有待出物品</div>`}<label>承运方<input id="supplyCarrier"></label><label>运单号<input id="supplyTracking"></label><label>出库日期${datePickerHtml("supplyShipDate", new Date().toISOString().slice(0, 10))}</label></div>`,
      onReady: () => bindDatePickers(document.querySelector(".modal") || document),
      onOk: async () => {
        const items = [...document.querySelectorAll("[data-supply-qty]")].map(input => {
          const serial = document.querySelector(`[data-supply-serial="${input.dataset.supplyQty}"]`);
          const option = serial?.selectedOptions?.[0];
          return {
            supply_item_id: Number(input.dataset.supplyQty),
            quantity: input.value,
            allocations: [...document.querySelectorAll(`[data-supply-batch="${input.dataset.supplyQty}"]`)].map(batch => ({ batch_id: Number(batch.dataset.batchId), quantity: batch.value })).filter(batch => Number(batch.quantity) > 0),
            item_ref_id: serial ? Number(serial.value) : undefined,
            item_code: option?.dataset.code || "",
          };
        }).filter(item => Number(item.quantity) > 0);
        await systemApi(`/api/supplies/${form.id}/shipments`, { method: "POST", body: JSON.stringify({ items, carrier: document.getElementById("supplyCarrier").value, tracking_no: document.getElementById("supplyTracking").value, shipped_at: document.getElementById("supplyShipDate").value }) });
        toast("供货出库完成");
        system.boot = null;
        await loadSystemBoot();
        renderSystemMain();
      },
    });
  }

  function acceptSupplyReturn(form) {
    openModal({
      title: "回寄验收",
      okText: "同意",
      body: `<p>接收日期：${escapeHtml(form.data?.received_date || "")}</p><p>货品信息：${escapeHtml(form.data?.goods_info || "")}</p>`,
      onOk: async () => {
        await systemApi(`/api/supply-returns/${form.id}/accept`, { method: "POST", body: JSON.stringify({}) });
        toast("验收已提交");
        system.boot = null;
        await loadSystemBoot();
        renderSystemMain();
      },
    });
  }

  function supplyReturnItemNeedsShelf(item) {
    const kind = item.data?.item_type || (item.material_id ? "material" : "");
    return kind === "material";
  }

  function supplyReturnInboundFlow(form) {
    const anyMaterial = (form.items || []).some(supplyReturnItemNeedsShelf);
    const shelves = typeof state !== "undefined" && Array.isArray(state.shelves) && state.shelves.length ? state.shelves : [];
    const ready = anyMaterial
      ? (shelves.length ? Promise.resolve(shelves) : systemApi("/api/shelves").then(data => { if (typeof state !== "undefined") state.shelves = data; return data; }))
      : Promise.resolve([]);
    ready.then(shelvesData => {
      window._shelfData = shelvesData;
      const rows = (form.items || []).map(item => {
        const shelf = supplyReturnItemNeedsShelf(item) ? `<div class="form-grid">${shelfLocationSelects(shelvesData, `return-${item.id}`)}</div>` : "";
        return `<div class="wide work-panel"><div class="work-body"><strong>${escapeHtml(item.material_name || "")}</strong><p>预计回寄：${formatQty(item.request_quantity || 0)}</p><label>实收数量<input data-return-received="${item.id}" data-supply-item="${item.data?.supply_item_id || ""}" data-return-material="${supplyReturnItemNeedsShelf(item) ? "1" : "0"}" type="number" min="0" step="1" value="${formatInputQty(item.request_quantity || 0)}"></label><label>完好数量<input data-return-good="${item.id}" type="number" min="0" step="1" value="${formatInputQty(item.request_quantity || 0)}"></label><label>不良数量<input data-return-bad="${item.id}" type="number" min="0" step="1" value="0"></label>${shelf}</div></div>`;
      }).join("");
      openModal({
        title: "回寄入库",
        okText: "确认入库",
        body: `<div class="form-grid">${rows}</div>`,
        onReady: () => {
          document.querySelectorAll("[data-shelf]").forEach(sel => { if (sel.tagName === "SELECT") window._updateShelfLocation(sel); });
        },
        onOk: async () => {
          const items = [...document.querySelectorAll("[data-return-received]")].map(input => {
            const key = `return-${input.dataset.returnReceived}`;
            const good = document.querySelector(`[data-return-good="${input.dataset.returnReceived}"]`).value;
            const location = input.dataset.returnMaterial === "1" ? {
              shelf_id: document.querySelector(`[data-shelf="${key}"]`)?.value || "",
              layer_number: document.querySelector(`[data-layer="${key}"]`)?.value || "",
              zone_name: document.querySelector(`[data-zone="${key}"]`)?.value || "",
            } : {};
            if (input.dataset.returnMaterial === "1" && Number(good) > 0 && !location.shelf_id) {
              throw new Error("物料完好回库必须选择货架");
            }
            return {
              supply_item_id: Number(input.dataset.supplyItem),
              received_quantity: input.value,
              good_quantity: good,
              defective_quantity: document.querySelector(`[data-return-bad="${input.dataset.returnReceived}"]`).value,
              location,
            };
          });
          await systemApi(`/api/supply-returns/${form.id}/inbound`, { method: "POST", body: JSON.stringify({ items }) });
          toast("回寄已入库");
          system.boot = null;
          await loadSystemBoot();
          renderSystemMain();
        },
      });
    }).catch(error => toast(error.message));
  }

  function supplyReturnGoodsText(item) {
    const kind = { material: "物料", semifinished: "半成品", finished: "成品" }[item.item_type] || "物品";
    const title = [item.item_code, item.item_name, item.brand_model, item.spec].map(value => String(value || "").trim()).filter(Boolean).join(" ");
    return `${kind} ${title}，数量 ${formatQty(item.outstanding_quantity || 0)} ${item.unit || ""}`.trim();
  }

  function supplySettlementLabel(status) {
    return status === "completed" ? "已结清" : "未结清";
  }

  function supplyFollowButton(row, returnAttr) {
    if (!hasPerm("view_supply")) return "";
    const method = row.supply_method || "试用";
    if (method === "销售") {
      return row.status === "external_open" ? `<button type="button" data-supply-payment="${row.form_id}">货款结清</button>` : "";
    }
    if (!["shipping", "external_open"].includes(row.status)) return "";
    return `<button type="button" data-${returnAttr}="${row.form_id}">登记回寄</button>`;
  }

  function bindSupplyFollow(rows, returnAttr) {
    const returnKey = returnAttr.replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
    document.querySelectorAll(`[data-${returnAttr}]`).forEach(button => button.addEventListener("click", () => {
      const order = rows.find(row => Number(row.form_id) === Number(button.dataset[returnKey]));
      if (order) openSupplyTrialReturn(order);
    }));
    document.querySelectorAll("[data-supply-payment]").forEach(button => button.addEventListener("click", () => {
      const order = rows.find(row => Number(row.form_id) === Number(button.dataset.supplyPayment));
      if (order) openSupplyPaymentSettlement(order);
    }));
  }

  function openSupplyPaymentSettlement(order) {
    openModal({
      title: "供货-销售 货款结清",
      okText: "确认结清",
      body: `<div class="form-grid"><div class="wide"><span class="hint">关联供货单号</span><strong>${escapeHtml(order.form_no || "")}</strong></div><label>货款结清日期${datePickerHtml("supplyPaymentDate", new Date().toISOString().slice(0, 10))}</label><label class="wide">备注<textarea id="supplyPaymentRemark"></textarea></label></div>`,
      onReady: () => bindDatePickers(document.querySelector(".modal") || document),
      onOk: async () => {
        const settled_at = document.getElementById("supplyPaymentDate").value;
        if (!settled_at) throw new Error("请填写货款结清日期");
        await systemApi(`/api/supplies/${order.form_id}/payment-settlement`, { method: "POST", body: JSON.stringify({ settled_at, remark: document.getElementById("supplyPaymentRemark").value.trim() }) });
        toast("货款已结清");
        renderSystemMain();
      },
    });
  }

  async function renderSupplyLedger(main) {
    main.innerHTML = `<div class="module-content" data-page-title="供货台账"><div class="work-panel"><div class="work-head"><h2>供货台账</h2><button type="button" id="refreshSupplyLedger">刷新</button></div><div class="work-body" id="supplyLedgerList">加载中...</div></div></div>`;
    const load = async () => {
      try {
        const data = await systemApi("/api/supplies");
        const rows = data.items || [];
        const body = rows.map(row => {
          const receipt = [row.recipient_company, row.recipient_name, row.recipient_phone, row.recipient_address].map(value => String(value || "").trim()).filter(Boolean).join(" / ");
          const detail = (row.items || []).map(item => `${escapeHtml(item.item_name || "")} 已出${formatQty(item.shipped_quantity)}/${formatQty(item.approved_quantity)}，待结 ${formatQty(item.outstanding_quantity)}`).join("<br>");
          return `<tr><td>${escapeHtml(row.form_no || "")}</td><td>${escapeHtml(receipt)}</td><td>${escapeHtml(workflowStatusLabel(row.form_status || row.status) || "")}</td><td>${escapeHtml(row.supply_method || "试用")}</td><td>${escapeHtml(row.expected_close_date || "")}</td><td>${supplySettlementLabel(row.status)}</td><td>${detail}</td><td>${supplyFollowButton(row, "ledger-return")}</td></tr>`;
        }).join("");
        document.getElementById("supplyLedgerList").innerHTML = rows.length
          ? `<div class="table-wrap"><table class="flow-table"><thead><tr><th>单号</th><th>收货信息</th><th>状态</th><th>供货方式</th><th>预计结清日期</th><th>结清状态</th><th>明细</th><th>操作</th></tr></thead><tbody>${body}</tbody></table></div>`
          : "暂无供货记录";
        bindSupplyFollow(rows, "ledger-return");
      } catch (error) {
        document.getElementById("supplyLedgerList").textContent = error.message;
      }
    };
    document.getElementById("refreshSupplyLedger").addEventListener("click", load);
    await load();
  }

  function openSupplyTrialReturn(order) {
    const lines = (order.items || []).filter(item => Number(item.outstanding_quantity || 0) > 0);
    const goods = lines.map(supplyReturnGoodsText).join("\n");
    openModal({
      title: "供货-试用 登记回寄",
      okText: "发起申请",
      body: `<div class="form-grid"><div class="wide"><span class="hint">关联供货单号</span><strong>${escapeHtml(order.form_no || "")}</strong></div><label>接收日期${datePickerHtml("supplyReturnDate", new Date().toISOString().slice(0, 10))}</label><label class="wide">货品信息<textarea id="supplyReturnGoods">${escapeHtml(goods)}</textarea></label></div>`,
      onReady: () => bindDatePickers(document.querySelector(".modal") || document),
      onOk: async () => {
        const received_date = document.getElementById("supplyReturnDate").value;
        const goods_info = document.getElementById("supplyReturnGoods").value.trim();
        if (!received_date) throw new Error("请填写接收日期");
        if (!goods_info) throw new Error("请填写货品信息");
        const items = lines.map(item => ({ supply_item_id: Number(item.id), expected_quantity: Number(item.outstanding_quantity || 0) }));
        if (!items.length) throw new Error("没有待回寄物品");
        const draft = { received_date, goods_info, items };
        window.setTimeout(() => openSupplyReturnValidators(order, draft), 0);
      },
    });
  }

  function openSupplyReturnValidators(order, draft) {
    const users = system.boot?.users || [];
    const selected = new Set(users.filter(user => String(user.role_codes || "").split(",").includes("warehouse")).map(user => Number(user.id)));
    const departments = system.boot?.departments || [];
    const grouped = departments.map(dep => ({
      name: dep.name,
      users: users.filter(user => user.department === dep.name),
    })).filter(group => group.users.length);
    const listed = new Set(grouped.flatMap(group => group.users.map(user => Number(user.id))));
    const rest = users.filter(user => !listed.has(Number(user.id)));
    if (rest.length) grouped.push({ name: "其他", users: rest });
    const body = grouped.map(group => `<div class="people-dept"><h3>${escapeHtml(group.name)}</h3>${group.users.map(user => `<label data-validator-row data-validator-text="${escapeAttr(`${user.display_name || ""} ${user.username || ""} ${user.department || ""}`.toLowerCase())}"><input type="checkbox" name="supplyReturnValidator" value="${user.id}" ${selected.has(Number(user.id)) ? "checked" : ""}>${escapeHtml(user.display_name || user.username || "")}<small>${escapeHtml(user.username || "")}</small></label>`).join("")}</div>`).join("");
    openModal({
      title: "选择验收人",
      okText: "确认选择",
      body: `<input id="supplyReturnValidatorSearch" placeholder="搜索姓名 / 账号 / 部门" style="margin-bottom:10px;"><div class="people-grid">${body || `<div class="empty-state">没有可选人员</div>`}</div>`,
      onReady: () => {
        document.getElementById("supplyReturnValidatorSearch")?.addEventListener("input", event => {
          const keyword = event.target.value.trim().toLowerCase();
          document.querySelectorAll("[data-validator-row]").forEach(label => { label.style.display = label.dataset.validatorText.includes(keyword) ? "flex" : "none"; });
        });
      },
      onOk: async () => {
        const ids = [...document.querySelectorAll('input[name="supplyReturnValidator"]:checked')].map(input => Number(input.value));
        if (ids.length < 3) throw new Error("请至少选择 3 名验收人");
        await systemApi(`/api/supplies/${order.form_id}/returns`, { method: "POST", body: JSON.stringify({ ...draft, validator_ids: ids }) });
        toast("回寄已登记");
        renderSystemMain();
      },
    });
  }

  async function renderMySupplies(main) {
    const method = system.supplyMineMethod || "试用";
    main.innerHTML = `<div class="module-content" data-page-title="我的供货"><div class="work-panel"><div class="work-head"><h2>我的供货</h2><span class="purchase-toolbar"><button type="button" id="mySupplyTrial" class="${method === "试用" ? "primary" : ""}">试用</button><button type="button" id="mySupplySale" class="${method === "销售" ? "primary" : ""}">销售</button></span></div><div class="work-body" id="mySupplyList">加载中...</div></div></div>`;
    document.getElementById("mySupplyTrial").addEventListener("click", () => { system.supplyMineMethod = "试用"; renderMySupplies(main); });
    document.getElementById("mySupplySale").addEventListener("click", () => { system.supplyMineMethod = "销售"; renderMySupplies(main); });
    const data = await systemApi("/api/supplies");
    const mine = (data.items || []).filter(row => Number(row.applicant_id) === Number(system.userId || 0) && (row.supply_method || "试用") === method);
    document.getElementById("mySupplyList").innerHTML = `<table class="flow-table"><thead><tr><th>单号</th><th>收货方</th><th>状态</th><th>明细</th><th></th></tr></thead><tbody>${mine.map(row => `<tr><td>${escapeHtml(row.form_no || "")}</td><td>${escapeHtml(row.recipient_company || "")}</td><td>${escapeHtml(workflowStatusLabel(row.form_status || row.status) || "")}</td><td>${(row.items || []).map(item => `${escapeHtml(item.item_name || "")} ${formatQty(item.shipped_quantity)}/${formatQty(item.approved_quantity)}`).join("<br>")}</td><td>${supplyFollowButton(row, "my-return")}</td></tr>`).join("") || `<tr><td colspan="5">暂无供货申请</td></tr>`}</tbody></table>`;
    bindSupplyFollow(mine, "my-return");
  }

  window.openSupplyTrialReturn = openSupplyTrialReturn;
  window.openSupplyPaymentSettlement = openSupplyPaymentSettlement;

  function outboundFlowV2(form) {
    const operationKey = newAttachmentToken();
    const pendingItems = (form.items || []).filter(item => Number(item.remaining_outbound_quantity ?? (Number(item.request_quantity || 0) - Number(item.outbound_quantity || 0))) > 1e-9);
    openModal({ title: "办理出库", body: `<div class="form-grid">${pendingItems.map(item => {
      const remaining = Number(item.remaining_outbound_quantity ?? (Number(item.request_quantity || 0) - Number(item.outbound_quantity || 0)));
      return `<div class="wide work-panel"><div class="work-body"><h3>${escapeHtml(item.material_code)} ${escapeHtml(item.material_name)} ${ClaimSourceUI.sourceBadge(item.stock_source)}</h3><p>申领数量：${formatQty(item.request_quantity)} ${escapeHtml(item.unit || "")}；已出：${formatQty(item.outbound_quantity || 0)}；待出：${formatQty(remaining)}；该来源可用库存：${formatQty(item.current_stock_quantity || 0)}</p><label>本次出库数量<input data-out="${item.id}" type="number" step="1" min="0" max="${remaining}" value="${formatInputQty(remaining)}"></label>${batchPickTable(item)}</div></div>`;
    }).join("")}<label>库管签字<input id="outSign" value="${escapeAttr(currentUser()?.display_name || "")}"></label></div>`, okText: "确认出库", onReady: () => {
      pendingItems.forEach(item => {
        const quantityInput = document.querySelector(`[data-out="${item.id}"]`);
        quantityInput?.addEventListener("input", () => {
          let remaining = Math.max(0, Number(quantityInput.value || 0));
          document.querySelectorAll(`[data-batch-item="${item.id}"]`).forEach(batchInput => {
            const take = Math.min(remaining, Number(batchInput.max || 0));
            batchInput.value = formatInputQty(take);
            remaining -= take;
          });
        });
      });
    }, onOk: async () => {
      const items = pendingItems.map(item => ({
        id: item.id,
        outbound_quantity: document.querySelector(`[data-out="${item.id}"]`).value,
        batches: [...document.querySelectorAll(`[data-batch-item="${item.id}"]`)].map(input => ({ batch_id: input.dataset.batchId, quantity: input.value })),
      }));
      const result = await systemApi(`/api/claims/${form.id}/outbound`, { method: "POST", body: JSON.stringify({ items, signature: document.getElementById("outSign").value, operation_key: operationKey }) });
      toast(result.partial_outbound ? "本次出库完成，流程仍有待出数量" : "出库完成，料卡已同步");
      await loadBootstrap();
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

  function semiClaimOutboundFlow(form) {
    const lines = (form.items || []).map(item => `<p>${escapeHtml(item.material_code || "")} ${escapeHtml(item.material_name || "")} · ${formatQty(item.request_quantity || 0)} ${escapeHtml(item.unit || "")}</p>`).join("");
    openModal({
      title: "办理半成品出库",
      body: `<div class="form-grid"><div class="wide">${lines}</div><label>库管签字<input id="semiClaimOutSign" value="${escapeAttr(currentUser()?.display_name || "")}"></label></div>`,
      okText: "确认出库",
      onOk: async () => {
        await systemApi(`/api/semifinished-claims/${form.id}/outbound`, {
          method: "POST",
          body: JSON.stringify({ signature: document.getElementById("semiClaimOutSign").value }),
        });
        toast("半成品已出库");
        await loadBootstrap();
      },
    });
  }

  function claimReturnInboundFlow(form) {
    const data = form.data || {};
    const condition = data.condition_status || "正常";
    openModal({
      title: "领用退库入库",
      body: `<div class="form-grid">
        <p class="wide">${escapeHtml((form.items || [])[0]?.material_name || "")} · ${escapeHtml(condition)} · ${formatQty(data.quantity || (form.items || [])[0]?.request_quantity || 0)}</p>
        ${data.remark ? `<p class="wide">备注：${escapeHtml(data.remark)}</p>` : ""}
        <label class="wide">审批意见<textarea id="claimReturnRemark" rows="2"></textarea></label>
        <div class="wide" style="display:flex;gap:8px;margin-top:12px">
          <button type="button" id="claimReturnApprove" class="primary" style="flex:1">确认入库</button>
          <button type="button" id="claimReturnReject" class="danger" style="flex:1">拒绝</button>
        </div>
      </div>`,
      hideOk: true,
      onReady: () => {
        async function submit(decision) {
          const remark = document.getElementById("claimReturnRemark").value;
          if (decision === "拒绝" && !remark.trim()) {
            toast("拒绝时必须填写审批意见");
            return;
          }
          try {
            await systemApi(`/api/claim-returns/${form.id}/inbound`, {
              method: "POST",
              body: JSON.stringify({ decision, remark }),
            });
            toast(decision === "拒绝" ? "已拒绝退库" : "退库入库完成");
            closeModal();
            await loadBootstrap();
          } catch (error) {
            toast(error.message || "操作失败");
          }
        }
        document.getElementById("claimReturnApprove").addEventListener("click", () => submit("同意"));
        document.getElementById("claimReturnReject").addEventListener("click", () => submit("拒绝"));
      },
    });
  }

  function borrowDamageClaimFlow(form) {
    const prompt = (form.data && form.data.warehouse_prompt) || "借用物料损坏需领用，如后续不留物料可再退库";
    openModal({
      title: "物料损坏转领用",
      body: `<p>${escapeHtml(prompt)}</p>`,
      okText: "确认",
      onOk: async () => {
        await systemApi(`/api/borrow-returns/${form.id}/damage-claim`, { method: "POST", body: "{}" });
        toast("已转为领用");
        await loadBootstrap();
      },
    });
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
        ${isMaterial ? `<label>核验状态<select id="borrowReturnVerified"><option value="完好">完好</option><option value="损坏">损坏</option></select></label>` : ""}
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
            const result = await systemApi(`/api/borrow-returns/${form.id}/inbound`, {
              method: "POST",
              body: JSON.stringify({
                return_quantity: document.getElementById("borrowReturnQty").value,
                inbound_date: document.getElementById("borrowReturnInboundDate").value,
                shelf_id: document.querySelector(`[data-shelf="borrowReturn"]`)?.value || "",
                layer_number: document.querySelector(`[data-layer="borrowReturn"]`)?.value || "",
                zone_name: document.querySelector(`[data-zone="borrowReturn"]`)?.value || "",
                signature: document.getElementById("borrowReturnSign").value,
                decision: decision,
                verified_status: document.getElementById("borrowReturnVerified")?.value || "",
              }),
            });
            toast(result.damage_claim ? "借用物料损坏需领用，如后续不留物料可再退库" : (decision === '同意' ? "借用归还入库完成" : "已拒绝归还"));
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
    const project = document.getElementById("semiProjectCode")?.value || document.getElementById("finProjectCode")?.value || "";
    if (!project) {
      system.productionMaterialPool = [];
      return system.productionMaterialPool;
    }
    const data = await systemApi(`/api/production/material-pool?purchase_project=${encodeURIComponent(project)}`);
    system.productionMaterialPool = data.items || [];
    return system.productionMaterialPool;
  }

  async function loadSemifinishedPool() {
    const project = document.getElementById("finProjectCode")?.value || "";
    if (!project) {
      system.semifinishedPool = [];
      return system.semifinishedPool;
    }
    const data = await systemApi(`/api/production/semifinished-pool?purchase_project=${encodeURIComponent(project)}`);
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
    row.innerHTML = `<label class="material-search">所用物料<input data-prod-material-search placeholder="搜索编号/名称/品牌/规格/批次/申领人"><div data-result-list class="material-results"></div></label><label>名称<input data-prod-name readonly></label><label>品牌<input data-prod-brand readonly></label><label>规格<input data-prod-spec readonly></label><label>单台用量<input data-prod-per-unit type="number" step="1" min="0" value="1"></label><label>总消耗<input data-prod-total readonly></label><span class="hint" data-prod-info></span><button type="button" data-remove>删除</button><input type="hidden" data-prod-material><div class="prod-batch-picks" data-prod-batch-picks></div>`;
    row.querySelector("[data-remove]").addEventListener("click", () => { row.remove(); refreshComponentRows(host.id, qtyId, costId, peerHostId); });
    const search = row.querySelector("[data-prod-material-search]");
    let timer = 0;
    search.addEventListener("input", () => {
      window.clearTimeout(timer);
      row.querySelector("[data-prod-material]").value = "";
      row.dataset.prodBatches = "[]";
      row.querySelector("[data-prod-batch-picks]").innerHTML = "";
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
      ? (pool || []).filter(item => `${item.material_code || ""} ${item.name || ""} ${item.brand_model || ""} ${item.spec || ""} ${(item.batches || []).map(batch => batch.batch_no || "").join(" ")} ${item.claim_applicant || ""}`.toLowerCase().includes(keyword))
      : (pool || []);
    if (!filtered.length) {
      hideMaterialResults(row);
      return;
    }
    list.innerHTML = filtered.map((item, index) => `
      <button class="material-option" type="button" data-prod-material-index="${index}">
        ${escapeHtml(item.material_code)} · ${escapeHtml(item.name)}
        <small>领用项目 ${escapeHtml(item.purchase_project || "办公")}${item.rd_item_kind === "辅料" ? " · 辅料" : ""} · 可用 ${formatQty(item.available_quantity)} ${escapeHtml(item.unit || "")} · ${(item.batches || []).length} 个批次 · 单价 ${money(item.unit_cost || 0)} · 申领人 ${escapeHtml(item.claim_applicant || "-")} · ${escapeHtml(item.brand_model || "")} ${escapeHtml(item.spec || "")}</small>
      </button>
    `).join("");
    positionMaterialResults(row, list, filtered.length);
    list.style.display = "block";
    list.querySelectorAll("[data-prod-material-index]").forEach(button => {
      button.addEventListener("click", () => applyProductionMaterial(row, filtered[Number(button.dataset.prodMaterialIndex)]));
    });
  }

  function applyProductionMaterial(row, item, selectedBatchIds) {
    row.querySelector("[data-prod-material]").value = item.material_id || "";
    row.querySelector("[data-prod-material-search]").value = `${item.material_code || ""} ${item.name || ""}`.trim();
    fillComponentInfo(row, { name: item.name || "", brand: item.brand_model || "无", spec: item.spec || "", batch: "" });
    row.dataset.unit = item.unit || "";
    renderProductionBatchPicks(row, item.batches || [], selectedBatchIds);
    hideMaterialResults(row);
    refreshComponentRows(row.parentElement.id, row.dataset.qtyId || "", row.dataset.costId || "", row.dataset.peerHostId || "");
  }

  function renderProductionBatchPicks(row, batches, selectedBatchIds) {
    const host = row.querySelector("[data-prod-batch-picks]");
    row.dataset.prodBatches = JSON.stringify(batches || []);
    if (!host) return;
    if (!(batches || []).length) {
      host.innerHTML = "";
      return;
    }
    const selected = selectedBatchIds == null ? null : new Set((selectedBatchIds || []).map(Number));
    host.innerHTML = (batches || []).map(batch => {
      const checked = selected ? selected.has(Number(batch.batch_id || 0)) : true;
      return `<label><input type="checkbox" data-prod-batch-id="${Number(batch.batch_id || 0)}"${checked ? " checked" : ""}> ${escapeHtml(batch.batch_no || "")} · 可用 ${formatQty(batch.available_quantity)} ${escapeHtml(batch.unit || row.dataset.unit || "")}</label>`;
    }).join("");
    host.querySelectorAll("[data-prod-batch-id]").forEach(input => {
      input.addEventListener("change", () => refreshComponentRows(row.parentElement.id, row.dataset.qtyId || "", row.dataset.costId || "", row.dataset.peerHostId || ""));
    });
  }

  function selectedProductionBatches(row) {
    const all = JSON.parse(row.dataset.prodBatches || "[]");
    const checked = new Set([...row.querySelectorAll("[data-prod-batch-id]:checked")].map(input => Number(input.dataset.prodBatchId)));
    return all.filter(batch => checked.has(Number(batch.batch_id || 0)));
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
    const batch = row.querySelector("[data-prod-batch-text]");
    if (batch) batch.value = values.batch;
  }

  function addSemifinishedComponentRow(host, pool, qtyId, costId, peerHostId = "") {
    const row = document.createElement("div");
    row.className = "component-row slim";
    row.dataset.qtyId = qtyId;
    row.dataset.costId = costId;
    row.dataset.peerHostId = peerHostId;
    row.innerHTML = `<label class="material-search">半成品<input data-prod-semi-search placeholder="搜索半成品名称/规格/制作人"><div data-result-list class="material-results"></div></label><label>名称<input data-prod-name readonly></label><label>品牌<input data-prod-brand readonly></label><label>规格<input data-prod-spec readonly></label><label>单台用量<input data-prod-per-unit type="number" step="1" min="0" value="1"></label><label>总消耗<input data-prod-total readonly></label><span class="hint" data-prod-info></span><button type="button" data-remove>删除</button><input type="hidden" data-prod-semi>`;
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
      list.innerHTML = `<div class="material-option">没有已领用未消耗的合格半成品</div>`;
      positionMaterialResults(row, list, 1);
      list.style.display = "block";
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
        const materialPicks = row.querySelector("[data-prod-batch-picks]");
        let available = Number((select ? option?.dataset.available : row.dataset.available) || 0);
        let unitCost = Number((select ? option?.dataset.cost : row.dataset.cost) || 0);
        if (materialPicks) {
          const selected = selectedProductionBatches(row);
          available = selected.reduce((sum, batch) => sum + Number(batch.available_quantity || 0), 0);
          const selectedQty = available;
          const selectedAmount = selected.reduce((sum, batch) => sum + Number(batch.available_quantity || 0) * Number(batch.unit_cost || 0), 0);
          unitCost = selectedQty > 0 ? selectedAmount / selectedQty : 0;
        }
        const unit = (select ? option?.dataset.unit : row.dataset.unit) || "";
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
    return [...document.querySelectorAll(`#${hostId} [data-prod-material]`)].map(input => {
      const row = input.closest(".component-row");
      return {
        material_id: input.value,
        per_unit_quantity: row.querySelector("[data-prod-per-unit]").value,
        batch_ids: selectedProductionBatches(row).map(batch => batch.batch_id),
      };
    }).filter(item => Number(item.material_id || 0) && Number(item.per_unit_quantity || 0) > 0);
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
      await submitWorkflowPayload("semifinished", "/api/production/semifinished/workflows", payload);
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
      await submitWorkflowPayload("finished", "/api/production/finished/workflows", payload);
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
    bindProductionInventoryDeleteActions();
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

  function semifinishedQualifiedActionsHtml(item) {
    const canWrite = hasPerm("write_semifinished_inventory");
    if (!canWrite) return "";
    const canDisassemble = Number(item.used_quantity || 0) <= 0 && Number(item.borrowed_quantity || 0) <= 0;
    return `<td class="mini-actions"><button type="button" onclick="viewItemHistory('semifinished',${item.id})">查看详情</button><button type="button" data-edit-semi-inventory="${item.id}">修改</button>${canDisassemble ? `<button type="button" data-disassemble-semi-inventory="${item.id}">拆解</button>` : ""}<button type="button" class="danger" data-delete-semi-inventory="${item.id}">删除</button></td>`;
  }

  function semifinishedQualifiedTableHtml(rows) {
    const canWrite = hasPerm("write_semifinished_inventory");
    const hasActions = canWrite;
    return `<h3>半成品库</h3><table class="flow-table"><thead><tr><th>编号</th><th>名称</th><th>规格</th><th>数量</th><th>已借用</th><th>用于成品</th><th>余量</th><th>状态</th><th>成本价</th><th>位置</th><th>日期</th>${hasActions ? `<th>操作</th>` : ""}</tr></thead><tbody>${(rows || []).map(item => `<tr><td>${escapeHtml(item.serial_no || "")}</td><td>${escapeHtml(item.name)}${item.project_code ? `<div class="hint">项目 ${escapeHtml(item.project_code)}</div>` : ""}${item._has_warning ? ` <div class="item-warning"><span class="item-warning-icon">⚠️</span> ${escapeHtml(item._change_type || '硬件')}异常</div>` : ''}</td><td>${escapeHtml(item.spec || "")}</td><td>${formatQty(item.quantity)} ${escapeHtml(item.unit || "")}</td><td>${formatQty(item.borrowed_quantity || 0)}</td><td>${formatQty(item.used_quantity || 0)}</td><td>${formatQty(item.remaining_quantity || 0)}</td><td>${escapeHtml(item.inventory_status_label || "在库")}</td><td>${money(item.cost_price || 0)}</td><td>${escapeHtml(locationText(item))}</td><td>${escapeHtml(item.acceptance_date || "")}</td>${hasActions ? semifinishedQualifiedActionsHtml(item) : ""}</tr>`).join("") || `<tr><td colspan="${hasActions ? 12 : 11}">暂无半成品库存</td></tr>`}</tbody></table>`;
  }

  function semifinishedDefectiveTableHtml(rows) {
    const canWrite = hasPerm("write_semifinished_inventory");
    const hasActions = canWrite;
    return `<h3>不合格半成品库</h3><table class="flow-table"><thead><tr><th>编号</th><th>半成品</th><th>规格</th><th>异常情况</th><th>状态</th><th>时间</th>${hasActions ? `<th>操作</th>` : ""}</tr></thead><tbody>${(rows || []).map(item => `<tr><td>${escapeHtml(item.serial_no || "")}</td><td>${escapeHtml(item.name || "")}</td><td>${escapeHtml(item.spec || "")}</td><td>${escapeHtml((item.abnormal_conditions || []).join("、"))}</td><td>${escapeHtml(item.inventory_status_label || "在库")}</td><td>${escapeHtml(item.created_at || "")}</td>${hasActions ? `<td class="mini-actions"><button type="button" onclick="viewItemHistory('defective_semifinished',${item.id})">查看详情</button>${canWrite ? `<button type="button" data-edit-defective-semi="${item.id}">修改</button><button type="button" class="danger" data-delete-defective-semi="${item.id}">删除</button>` : ""}</td>` : ""}</tr>`).join("") || `<tr><td colspan="${hasActions ? 7 : 6}">暂无不合格半成品</td></tr>`}</tbody></table>`;
  }

  function finishedQualifiedTableHtml(rows) {
    const canWrite = hasPerm("write_finished_inventory");
    const hasActions = canWrite;
    return `<h3>合格成品库</h3><table class="flow-table"><thead><tr><th>编号</th><th>成品</th><th>规格</th><th>数量</th><th>已借用</th><th>余量</th><th>状态</th><th>成本价</th><th>位置</th><th>日期</th>${hasActions ? `<th>操作</th>` : ""}</tr></thead><tbody>${(rows || []).map(item => `<tr><td>${escapeHtml(item.serial_no || "")}</td><td>${escapeHtml(item.product_name)}${item.project_code ? `<div class="hint">项目 ${escapeHtml(item.project_code)}</div>` : ""}${item._has_warning ? ` <div class="item-warning"><span class="item-warning-icon">⚠️</span> ${escapeHtml(item._change_type || '硬件')}异常</div>` : ''}</td><td>${escapeHtml(item.spec || "")}</td><td>${formatQty(item.quantity)} ${escapeHtml(item.unit || "")}</td><td>${formatQty(item.borrowed_quantity || 0)}</td><td>${formatQty(item.remaining_quantity ?? (Number(item.quantity || 0) - Number(item.borrowed_quantity || 0)))}</td><td>${escapeHtml(item.inventory_status_label || "在库")}</td><td>${money(item.cost_price || 0)}</td><td>${escapeHtml(locationText(item))}</td><td>${escapeHtml(item.acceptance_date || "")}</td>${hasActions ? `<td class="mini-actions"><button type="button" onclick="viewItemHistory('finished',${item.id})">查看详情</button>${canWrite ? `<button type="button" data-edit-finished-inventory="${item.id}">修改</button><button type="button" class="danger" data-delete-finished-inventory="${item.id}">删除</button>` : ""}</td>` : ""}</tr>`).join("") || `<tr><td colspan="${hasActions ? 11 : 10}">暂无合格成品库存</td></tr>`}</tbody></table>`;
  }

  function finishedDefectiveTableHtml(rows) {
    const canWrite = hasPerm("write_finished_inventory");
    const hasActions = canWrite;
    return `<h3>不合格成品库</h3><table class="flow-table"><thead><tr><th>流水号</th><th>成品</th><th>规格</th><th>异常情况</th><th>状态</th><th>时间</th>${hasActions ? `<th>操作</th>` : ""}</tr></thead><tbody>${(rows || []).map(item => `<tr><td>${escapeHtml(item.serial_no)}</td><td>${escapeHtml(item.product_name)}</td><td>${escapeHtml(item.spec || "")}</td><td>${escapeHtml((item.abnormal_conditions || []).join("、"))}</td><td>${escapeHtml(item.inventory_status_label || "在库")}</td><td>${escapeHtml(item.created_at || "")}</td>${hasActions ? `<td class="mini-actions"><button type="button" onclick="viewItemHistory('defective_finished',${item.id})">查看详情</button>${canWrite ? `<button type="button" data-edit-defective-finished="${item.id}">修改</button><button type="button" class="danger" data-delete-defective-finished="${item.id}">删除</button>` : ""}</td>` : ""}</tr>`).join("") || `<tr><td colspan="${hasActions ? 7 : 6}">暂无不合格成品</td></tr>`}</tbody></table>`;
  }

  function scrappedSemifinishedHtml(rows) {
    return `<h3>报废半成品</h3><table class="flow-table"><thead><tr><th>编号</th><th>名称</th><th>规格</th><th>报废原因</th><th>报废时间</th><th>状态</th><th>操作</th></tr></thead><tbody>${(rows || []).map(item => `<tr><td>${escapeHtml(item.serial_no || "")}</td><td>${escapeHtml(item.name || "")}</td><td>${escapeHtml(item.spec || "")}</td><td>${escapeHtml(item.scrap_reason || "")}</td><td>${escapeHtml(item.scrapped_at || item.created_at || "")}</td><td>${escapeHtml(item.inventory_status_label || "在库")}</td><td class="mini-actions"><button type="button" onclick="viewItemHistory('scrapped_semifinished',${item.id})">查看详情</button></td></tr>`).join("") || `<tr><td colspan="7">暂无报废半成品</td></tr>`}</tbody></table>`;
  }

  function scrappedFinishedHtml(rows) {
    return `<h3>报废成品</h3><table class="flow-table"><thead><tr><th>编号</th><th>成品名称</th><th>规格</th><th>报废原因</th><th>报废时间</th><th>状态</th><th>操作</th></tr></thead><tbody>${(rows || []).map(item => `<tr><td>${escapeHtml(item.serial_no || "")}</td><td>${escapeHtml(item.product_name || item.name || "")}</td><td>${escapeHtml(item.spec || "")}</td><td>${escapeHtml(item.scrap_reason || "")}</td><td>${escapeHtml(item.scrapped_at || item.created_at || "")}</td><td>${escapeHtml(item.inventory_status_label || "在库")}</td><td class="mini-actions"><button type="button" onclick="viewItemHistory('scrapped_finished',${item.id})">查看详情</button></td></tr>`).join("") || `<tr><td colspan="7">暂无报废成品</td></tr>`}</tbody></table>`;
  }

  function bindProductionInventoryDeleteActions() {
    document.querySelectorAll("[data-edit-semi-inventory]").forEach(btn => btn.addEventListener("click", () => editProductionInventory("semi", Number(btn.dataset.editSemiInventory))));
    document.querySelectorAll("[data-edit-defective-semi]").forEach(btn => btn.addEventListener("click", () => editProductionInventory("defectiveSemi", Number(btn.dataset.editDefectiveSemi))));
    document.querySelectorAll("[data-edit-finished-inventory]").forEach(btn => btn.addEventListener("click", () => editProductionInventory("finished", Number(btn.dataset.editFinishedInventory))));
    document.querySelectorAll("[data-edit-defective-finished]").forEach(btn => btn.addEventListener("click", () => editProductionInventory("defective", Number(btn.dataset.editDefectiveFinished))));
    document.querySelectorAll("[data-delete-semi-inventory]").forEach(btn => btn.addEventListener("click", () => deleteProductionInventory("semi", Number(btn.dataset.deleteSemiInventory))));
    document.querySelectorAll("[data-disassemble-semi-inventory]").forEach(btn => btn.addEventListener("click", () => disassembleSemifinishedInventory(Number(btn.dataset.disassembleSemiInventory))));
    document.querySelectorAll("[data-delete-defective-semi]").forEach(btn => btn.addEventListener("click", () => deleteProductionInventory("defectiveSemi", Number(btn.dataset.deleteDefectiveSemi))));
    document.querySelectorAll("[data-delete-finished-inventory]").forEach(btn => btn.addEventListener("click", () => deleteProductionInventory("finished", Number(btn.dataset.deleteFinishedInventory))));
    document.querySelectorAll("[data-delete-defective-finished]").forEach(btn => btn.addEventListener("click", () => deleteProductionInventory("defective", Number(btn.dataset.deleteDefectiveFinished))));
  }

  function shelfSelectHtml(selectedId, shelfName = "") {
    const shelves = typeof state !== "undefined" && Array.isArray(state.shelves) ? state.shelves : [];
    const known = shelves.some(shelf => Number(shelf.id) === Number(selectedId || 0));
    const current = selectedId && !known
      ? `<option value="${escapeAttr(selectedId)}" selected>${escapeHtml(shelfName || `货架${selectedId}`)}</option>`
      : "";
    return `<option value="">未指定</option>${current}${shelves.map(shelf => `<option value="${shelf.id}" ${Number(selectedId || 0) === Number(shelf.id) ? "selected" : ""}>${escapeHtml(shelf.name)}</option>`).join("")}`;
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

  function batchAmountInputValue(quantity, unitPrice) {
    const total = Number(quantity || 0) * Number(unitPrice || 0);
    if (!Number.isFinite(total)) return "0";
    return String(Number(total.toFixed(4)));
  }

  function normalizeBatchAmount(value) {
    const text = String(value ?? "").trim();
    if (!text) return "";
    const number = Number(text);
    if (!Number.isFinite(number)) return text;
    return String(Number(number.toFixed(4)));
  }

  function batchProjectOptions(selected) {
    const current = String(selected || "");
    let html = purchaseProjectOptions(current);
    if (current && !html.includes(`value="${escapeAttr(current)}"`)) {
      html += `<option value="${escapeAttr(current)}" selected>${escapeHtml(current)}</option>`;
    }
    return html;
  }

  let materialBatchLocationSeq = 0;

  function materialBatchLocationKey(batch) {
    if (batch.id) return `batch-${batch.id}`;
    materialBatchLocationSeq += 1;
    return `batch-new-${materialBatchLocationSeq}`;
  }

  function ensureLocationOption(select, value, label) {
    if (!select || value === undefined || value === null || value === "") return;
    const text = String(value);
    if (![...select.options].some(option => option.value === text)) {
      const option = document.createElement("option");
      option.value = text;
      option.textContent = label || text;
      select.appendChild(option);
    }
    select.value = text;
    select.disabled = false;
  }

  function bindMaterialShelfLocations(root) {
    const shelves = typeof state !== "undefined" && Array.isArray(state.shelves) ? state.shelves : [];
    window._shelfData = shelves;
    const scope = root || document.querySelector(".modal") || document;
    scope.querySelectorAll("[data-shelf-location-shelf]").forEach(select => {
      if (select.dataset.shelfLocationBound) return;
      const preferred = {
        layer_number: select.dataset.preferredLayer || "",
        zone_name: select.dataset.preferredZone || "",
      };
      window.WarehouseShelfLocation.update(select, shelves, preferred);
      const key = select.dataset.shelfLocationShelf;
      const layer = scope.querySelector(`[data-shelf-location-layer="${CSS.escape(key)}"]`);
      const zone = scope.querySelector(`[data-shelf-location-zone="${CSS.escape(key)}"]`);
      if (preferred.layer_number && layer && layer.value !== String(preferred.layer_number)) {
        ensureLocationOption(layer, preferred.layer_number, `${preferred.layer_number} 层`);
        layer.dispatchEvent(new Event("change"));
      }
      if (preferred.zone_name && zone && zone.value !== preferred.zone_name) {
        ensureLocationOption(zone, preferred.zone_name, `${preferred.zone_name} 区`);
      }
      select.dataset.shelfLocationBound = "1";
      select.addEventListener("change", () => window.WarehouseShelfLocation.update(select, shelves));
    });
  }

  function materialBatchRowHtml(batch = {}) {
    const locationKey = materialBatchLocationKey(batch);
    return `<tr data-material-batch-row data-batch-id="${batch.id || ""}" data-batch-version="${Number(batch.version || 0)}">
      <td><input data-batch-no value="${escapeAttr(batch.batch_no || "")}"></td>
      <td><input data-batch-date type="date" value="${escapeAttr(batch.received_date || "")}"></td>
      <td><input data-batch-qty type="number" step="1" min="0" value="${escapeAttr(formatInputQty(batch.quantity || 0))}"></td>
      <td><input data-batch-book type="number" step="1" min="0" value="${escapeAttr(formatInputQty(batch.book_quantity || 0))}"></td>
      <td><input data-batch-pack type="number" step="1" min="1" value="${escapeAttr(formatInputQty(batch.pack_quantity || 1))}"></td>
      <td><input data-batch-total-amount type="number" min="0" step="0.0001" value="${escapeAttr(batchAmountInputValue(batch.book_quantity, batch.unit_price))}"></td>
      <td><input data-batch-price type="number" step="0.0001" value="${escapeAttr(formatInputQty(batch.unit_price || 0))}" readonly></td>
      <td><select data-batch-shelf data-shelf="${locationKey}" data-shelf-location-shelf="${locationKey}" data-location-allow-empty="1" data-preferred-layer="${escapeAttr(batch.layer_number ?? "")}" data-preferred-zone="${escapeAttr(batch.zone_name || "")}">${shelfSelectHtml(batch.shelf_id, batch.shelf_name)}</select></td>
      <td><select data-batch-layer data-layer="${locationKey}" data-shelf-location-layer="${locationKey}"></select></td>
      <td><select data-batch-zone data-zone="${locationKey}" data-shelf-location-zone="${locationKey}"></select></td>
      <td><select data-batch-project>${batchProjectOptions(batch.purchase_project || "")}</select></td>
      <td><select data-batch-usage><option value="研发用料" ${(batch.usage_kind || "研发用料") === "研发用料" ? "selected" : ""}>研发用料</option><option value="生产用料" ${batch.usage_kind === "生产用料" ? "selected" : ""}>生产用料</option></select></td>
      <td><input data-batch-applicant value="${escapeAttr(batch.purchase_applicant || "")}"></td>
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
    document.querySelectorAll("[data-material-batch-row]").forEach(row => {
      const quantityInput = row.querySelector("[data-batch-qty]");
      const bookInput = row.querySelector("[data-batch-book]");
      const totalInput = row.querySelector("[data-batch-total-amount]");
      const priceInput = row.querySelector("[data-batch-price]");
      const writeTotalFromPrice = () => {
        const qty = Number(bookInput.value || 0);
        const price = Number(priceInput.value || 0);
        if (!Number.isFinite(qty) || !Number.isFinite(price)) return;
        totalInput.value = String(Number((qty * price).toFixed(4)));
      };
      const writePriceFromTotal = () => {
        const qty = Number(bookInput.value || 0);
        const total = Number(totalInput.value || 0);
        if (!(qty > 0) || !Number.isFinite(total)) return;
        priceInput.value = String(Number((total / qty).toFixed(4)));
      };
      quantityInput.oninput = () => updateMaterialBatchTotal();
      bookInput.oninput = writeTotalFromPrice;
      totalInput.oninput = writePriceFromTotal;
    });
    bindMaterialShelfLocations(document.querySelector(".modal"));
  }

  function materialBatchPayloadFromEditor(defaultLocation) {
    return [...document.querySelectorAll("[data-material-batch-row]")].map(row => ({
      id: row.dataset.batchId ? Number(row.dataset.batchId) : undefined,
      version: row.dataset.batchId ? Number(row.dataset.batchVersion || 0) : undefined,
      batch_no: row.querySelector("[data-batch-no]").value.trim(),
      received_date: row.querySelector("[data-batch-date]").value,
      quantity: row.querySelector("[data-batch-qty]").value,
      book_quantity: row.querySelector("[data-batch-book]").value,
      pack_quantity: row.querySelector("[data-batch-pack]").value,
      unit_price: row.querySelector("[data-batch-price]").value,
      total_amount: normalizeBatchAmount(row.querySelector("[data-batch-total-amount]").value),
      shelf_id: row.querySelector("[data-batch-shelf]").value || defaultLocation.shelf_id,
      layer_number: row.querySelector("[data-batch-layer]").value || defaultLocation.layer_number,
      zone_name: row.querySelector("[data-batch-zone]").value || defaultLocation.zone_name,
      source_form_no: row.querySelector("[data-batch-source]").value,
      purchase_project: row.querySelector("[data-batch-project]")?.value || "",
      usage_kind: row.querySelector("[data-batch-usage]")?.value || "研发用料",
      purchase_applicant: row.querySelector("[data-batch-applicant]")?.value || "",
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

  async function editMaterialFromQuery(materialId, stockSource = "formal") {
    const temporary = stockSource === "temporary";
    if (typeof state !== "undefined" && (!Array.isArray(state.shelves) || !state.shelves.length)) {
      const shelves = await systemApi("/api/shelves");
      if (Array.isArray(shelves)) state.shelves = shelves;
    }
    const material = await systemApi(`/api/materials/${materialId}`);
    const sourceBatches = temporary ? (material.temporary_batches || []) : (material.batches || []);
    const fallbackBatch = temporary || sourceBatches.length ? [] : [{ quantity: material.quantity || 0, shelf_id: material.shelf_id, layer_number: material.layer_number, zone_name: material.zone_name, received_date: new Date().toISOString().slice(0, 10), batch_no: material.material_code ? `00000000${material.material_code}` : "" }];
    const batches = [...sourceBatches, ...fallbackBatch];
    const batchSnapshotVersions = Object.fromEntries(
      sourceBatches.map(batch => [String(batch.id), Number(batch.version || 0)])
    );
    const quantityValue = temporary ? material.temporary_quantity : material.quantity;
    openModal({
      title: "修改物料",
      body: `<div class="form-grid">
        <label>物料编号<input id="editMatCode" value="${escapeAttr(material.material_code || "")}"></label>
        <label>名称<input id="editMatName" value="${escapeAttr(material.name || "")}"></label>
        <label>品牌型号<input id="editMatBrand" value="${escapeAttr(material.brand_model || "")}"></label>
        <label>技术规格<input id="editMatSpec" value="${escapeAttr(material.spec || "")}"></label>
        <label>采购申请人<input id="editMatApplicant" value="${escapeAttr(material.purchase_applicant || "")}"></label>
        <label>单位<input id="editMatUnit" value="${escapeAttr(material.unit || "")}"></label>
        <label>库存合计<input id="editMatQuantity" type="number" step="1" value="${Number(quantityValue || 0)}" readonly></label>
        <label>仓库码<input id="editMatWarehouseCode" value="${escapeAttr(material.warehouse_code || "")}"></label>
        <label>大类码<input id="editMatMajorCode" value="${escapeAttr(material.major_code || material.category || "")}"></label>
        <label>中类码<input id="editMatMiddleCode" value="${escapeAttr(material.middle_code || "")}"></label>
        <label>小类码<input id="editMatSmallCode" value="${escapeAttr(material.small_code || "")}"></label>
        <label>明细码<input id="editMatDetailCode" value="${escapeAttr(material.detail_code || "")}"></label>
        <label>分类名称<input id="editMatCategoryName" value="${escapeAttr(material.category_name || "")}"></label>
        <label>物料类型<input id="editMatType" value="${escapeAttr(material.material_type || "")}"></label>
        <label>图标<input id="editMatIcon" value="${escapeAttr(material.icon || "")}"></label>
        <label>默认货架<select id="editMatShelf" data-shelf="editMat" data-shelf-location-shelf="editMat" data-location-allow-empty="1" data-preferred-layer="${escapeAttr(material.layer_number ?? "")}" data-preferred-zone="${escapeAttr(material.zone_name || "")}">${shelfSelectHtml(material.shelf_id, material.shelf_name)}</select></label>
        <label>默认层<select id="editMatLayer" data-layer="editMat" data-shelf-location-layer="editMat"></select></label>
        <label>默认分区<select id="editMatZone" data-zone="editMat" data-shelf-location-zone="editMat"></select></label>
        <label>默认格位<input id="editMatSlot" type="number" value="${escapeAttr(material.slot_index || 0)}"></label>
        <div class="wide material-batch-editor">
          <div class="flow-tools"><button type="button" id="addMaterialBatch">新增批次</button></div>
          <table class="flow-table"><thead><tr><th>批次号</th><th>日期</th><th>实际数量</th><th>账面数量</th><th>每单位数量</th><th>库存总额</th><th>单价</th><th>货架</th><th>层</th><th>分区</th><th>采购项目</th><th>用料形式</th><th>采购申请人</th><th>来源</th><th>操作</th></tr></thead><tbody id="editMaterialBatchRows">${batches.map(materialBatchRowHtml).join("")}</tbody></table>
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
        const batchPayload = materialBatchPayloadFromEditor(defaultLocation);
        const currentBatchIds = new Set(batchPayload.filter(batch => batch.id).map(batch => String(batch.id)));
        const deleteBatchIds = Object.keys(batchSnapshotVersions).filter(id => !currentBatchIds.has(id)).map(Number);
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
            stock_source: temporary ? "temporary" : "formal",
            batches: batchPayload,
            batch_snapshot_versions: batchSnapshotVersions,
            delete_batch_ids: deleteBatchIds,
          }),
        });
        toast("已保存");
        if (temporary) {
          await temporaryInventory.reload();
        } else {
          await refreshMaterialsCache();
          await runMaterialQuery();
        }
      },
    });
  }

  window.editMaterialFromQuery = editMaterialFromQuery;

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

  async function disassembleSemifinishedInventory(id) {
    if (!id) return;
    if (!confirm("确认拆解该半成品库记录？")) return;
    await systemApi(`/api/production/semifinished-inventory/${id}/disassemble`, { method: "POST" });
    toast("已拆解");
    closeModal();
    if (document.getElementById("semiInventoryList")) loadSemifinishedInventoryModule();
    if (document.getElementById("semiList")) loadSemifinishedList();
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
    return `<h3>半成品库</h3><table class="flow-table"><thead><tr><th>编号</th><th>名称</th><th>规格</th><th>数量</th><th>已借用</th><th>用于成品</th><th>余量</th><th>状态</th><th>成本价</th><th>位置</th><th>日期</th>${hasActions ? `<th>操作</th>` : ""}</tr></thead><tbody>${(inventory || []).map(item => `<tr><td>${escapeHtml(item.serial_no || "")}</td><td>${escapeHtml(item.name)}${item.project_code ? `<div class="hint">项目 ${escapeHtml(item.project_code)}</div>` : ""}${item._has_warning ? ` <div class="item-warning"><span class="item-warning-icon">⚠️</span> ${escapeHtml(item._change_type || '硬件')}异常</div>` : ''}</td><td>${escapeHtml(item.spec || "")}</td><td>${formatQty(item.quantity)} ${escapeHtml(item.unit || "")}</td><td>${formatQty(item.borrowed_quantity || 0)}</td><td>${formatQty(item.used_quantity || 0)}</td><td>${formatQty(item.remaining_quantity || 0)}</td><td>${escapeHtml(item.inventory_status_label || "在库")}</td><td>${money(item.cost_price || 0)}</td><td>${escapeHtml(locationText(item))}</td><td>${escapeHtml(item.acceptance_date || "")}</td>${hasActions ? semifinishedQualifiedActionsHtml(item) : ""}</tr>`).join("") || `<tr><td colspan="${hasActions ? 12 : 11}">暂无半成品库存</td></tr>`}</tbody></table><h3>不合格半成品库</h3><table class="flow-table"><thead><tr><th>编号</th><th>半成品</th><th>规格</th><th>异常情况</th><th>状态</th><th>时间</th>${hasActions ? `<th>操作</th>` : ""}</tr></thead><tbody>${(defective || []).map(item => `<tr><td>${escapeHtml(item.serial_no || "")}</td><td>${escapeHtml(item.name || "")}</td><td>${escapeHtml(item.spec || "")}</td><td>${escapeHtml((item.abnormal_conditions || []).join("、"))}</td><td>${escapeHtml(item.inventory_status_label || "在库")}</td><td>${escapeHtml(item.created_at || "")}</td>${hasActions ? `<td class="mini-actions"><button type="button" onclick="viewItemHistory('defective_semifinished',${item.id})">查看详情</button>${canWrite ? `<button type="button" data-edit-defective-semi="${item.id}">修改</button><button type="button" class="danger" data-delete-defective-semi="${item.id}">删除</button>` : ""}</td>` : ""}</tr>`).join("") || `<tr><td colspan="${hasActions ? 7 : 6}">暂无不合格半成品</td></tr>`}</tbody></table>`;
  }

  function semifinishedAcceptanceHistoryHtml(acceptances) {
    return `<h3>验收记录</h3><table class="flow-table"><thead><tr><th>单号</th><th>半成品</th><th>验收/合格/不合格</th><th>外观/功能/性能</th><th>成本价</th><th>日期</th></tr></thead><tbody>${(acceptances || []).map(item => `<tr><td>${escapeHtml(item.acceptance_no)}</td><td>${escapeHtml(item.name)}<br><small>${escapeHtml(item.spec || "")}</small></td><td>${formatQty(item.acceptance_quantity)} / ${formatQty(item.qualified_quantity)} / ${formatQty(item.unqualified_quantity)}</td><td>${formatQty(item.appearance_ok_quantity)} / ${formatQty(item.function_ok_quantity)} / ${formatQty(item.performance_ok_quantity)}</td><td>${money(item.cost_price || 0)}</td><td>${escapeHtml(item.acceptance_date || "")}</td></tr>`).join("") || `<tr><td colspan="6">暂无验收记录</td></tr>`}</tbody></table>`;
  }

  function finishedInventoryHtml(qualified, defective) {
    const canWrite = hasPerm("write_finished_inventory");
    const hasActions = canWrite;
    return `<h3>合格成品库</h3><table class="flow-table"><thead><tr><th>编号</th><th>成品</th><th>规格</th><th>数量</th><th>已借用</th><th>余量</th><th>状态</th><th>成本价</th><th>位置</th><th>日期</th>${hasActions ? `<th>操作</th>` : ""}</tr></thead><tbody>${(qualified || []).map(item => `<tr><td>${escapeHtml(item.serial_no || "")}</td><td>${escapeHtml(item.product_name)}${item.project_code ? `<div class="hint">项目 ${escapeHtml(item.project_code)}</div>` : ""}${item._has_warning ? ` <div class="item-warning"><span class="item-warning-icon">⚠️</span> ${escapeHtml(item._change_type || '硬件')}异常</div>` : ''}</td><td>${escapeHtml(item.spec || "")}</td><td>${formatQty(item.quantity)} ${escapeHtml(item.unit || "")}</td><td>${formatQty(item.borrowed_quantity || 0)}</td><td>${formatQty(item.remaining_quantity ?? (Number(item.quantity || 0) - Number(item.borrowed_quantity || 0)))}</td><td>${escapeHtml(item.inventory_status_label || "在库")}</td><td>${money(item.cost_price || 0)}</td><td>${escapeHtml(locationText(item))}</td><td>${escapeHtml(item.acceptance_date || "")}</td>${hasActions ? `<td class="mini-actions"><button type="button" onclick="viewItemHistory('finished',${item.id})">查看详情</button>${canWrite ? `<button type="button" data-edit-finished-inventory="${item.id}">修改</button><button type="button" class="danger" data-delete-finished-inventory="${item.id}">删除</button>` : ""}</td>` : ""}</tr>`).join("") || `<tr><td colspan="${hasActions ? 11 : 10}">暂无合格成品库存</td></tr>`}</tbody></table><h3>不合格成品库</h3><table class="flow-table"><thead><tr><th>流水号</th><th>成品</th><th>规格</th><th>异常情况</th><th>状态</th><th>时间</th>${hasActions ? `<th>操作</th>` : ""}</tr></thead><tbody>${(defective || []).map(item => `<tr><td>${escapeHtml(item.serial_no)}</td><td>${escapeHtml(item.product_name)}</td><td>${escapeHtml(item.spec || "")}</td><td>${escapeHtml((item.abnormal_conditions || []).join("、"))}</td><td>${escapeHtml(item.inventory_status_label || "在库")}</td><td>${escapeHtml(item.created_at || "")}</td>${hasActions ? `<td class="mini-actions"><button type="button" onclick="viewItemHistory('defective_finished',${item.id})">查看详情</button>${canWrite ? `<button type="button" data-edit-defective-finished="${item.id}">修改</button><button type="button" class="danger" data-delete-defective-finished="${item.id}">删除</button>` : ""}</td>` : ""}</tr>`).join("") || `<tr><td colspan="${hasActions ? 7 : 6}">暂无不合格成品</td></tr>`}</tbody></table>`;
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
    const rows = keyword ? await systemApi(`/api/materials/search?keyword=${encodeURIComponent(keyword)}`) : await refreshMaterialsCache();
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
    system.currentStocktakeId = stocktake.stocktake?.id;
    await showStocktakeReport(system.currentStocktakeId, document.getElementById("stocktakeResult"));
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
    openModal({ title: form.form_no, hideOk: true, body: `<div class="print-area" id="stocktakeDetailPrint">加载中...</div><div class="mini-actions"><button type="button" id="printStocktakeDetail">下载盘点报表</button></div>`, onReady: () => {
      showStocktakeReport(stocktakeId, document.getElementById("stocktakeDetailPrint")).catch(error => toast(error.message));
      document.getElementById("printStocktakeDetail")?.addEventListener("click", () => {
        window.location.href = `/api/stocktakes/${stocktakeId}/report.xlsx`;
      });
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
    const button = document.getElementById("saveUser");
    button.disabled = true;
    try {
      const roles = [...document.getElementById("newRoles").selectedOptions].map(option => option.value);
      await systemApi("/api/system/users", { method: "POST", body: JSON.stringify({ id: document.getElementById("editUserId").value, username: document.getElementById("newUsername").value, display_name: document.getElementById("newDisplayName").value, password: document.getElementById("newPassword").value, department: document.getElementById("newDepartment").value, is_active: document.getElementById("newActive").value === "1", roles }) });
      toast("\u7528\u6237\u5df2\u4fdd\u5b58");
      document.getElementById("editUserId").value = "";
      document.getElementById("newUsername").value = "";
      document.getElementById("newDisplayName").value = "";
      document.getElementById("newPassword").value = "";
      document.getElementById("newDepartment").value = "";
      document.getElementById("newActive").value = "1";
      [...document.getElementById("newRoles").options].forEach(option => { option.selected = false; });
      await loadUsers();
    } catch (error) {
      toast(error.message || "\u7528\u6237\u4fdd\u5b58\u5931\u8d25");
    } finally {
      button.disabled = false;
    }
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
        <label class="wide">项目代号已改到「项目设置」。这里只保留旧配置<textarea id="setProjectCodes" rows="3">${escapeHtml((settings.project_codes || []).join("\n"))}</textarea></label>
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
      if (!system.boot.workflow_settings?.temporary_inventory_enabled && ["temporaryInventory", "temporaryFlows"].includes(system.view)) {
        system.view = "query";
        temporaryInventory.clear();
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
    ["semifinished_claim", "半成品领用", [["leader_semifinished_claim", "半成品领用审批"], ["semifinished_outbound", "半成品出库"]]],
    ["claim_return", "领用退库", [["return_inbound", "退库入库"]]],
    ["borrow_return", "借用归还", [["return_inbound", "仓库验收入库"], ["damage_claim", "物料损坏转领用"]]],
    ["borrow_claim", "借用转领用", []],
    ["common_material", "常用物料申请", [["leader_common_material", "审批"]]],
    ["supply", "供货申请", [["leader_supply", "审批"], ["supply_handover_print", "打印回执单"], ["supply_outbound", "出库"], ["supply_voucher_upload", "上传交接凭证"], ["supply_voucher_review", "查阅交接凭证"]]],
    ["supply_return", "供货回寄", [["supply_return_inbound", "回寄验收"]]],
    ["supply_extension", "供货延期", [["leader_supply_extension", "延期审批"]]],
    ["semifinished", "半成品验收", [["acceptance", "验收"], ["leader_acceptance", "领导签字"], ["inbound", "入库"]]],
    ["finished", "成品验收", [["acceptance", "验收"], ["leader_acceptance", "领导签字"], ["inbound", "入库"]]],
  ];

  function workflowStepAssigneeTable(settings) {
    return workflowStepDefinitions.map(([formType, formLabel, steps]) => {
      if (!steps.length) return "";
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
      transfer_temporary_inventory: "临时库转正式库",
      view_defective_inventory: "查看不良品物料",
      manage_defective_inventory: "管理不良品物料",
      start_common_material: "发起常用物料申请",
      view_supply: "查看供货管理",
      start_supply: "发起供货申请",
      edit_supply: "修改供货单",
      view_projects: "查看项目设置",
      manage_projects: "维护项目设置",
      start_purchase_request: "发起采购申请",
      manage_purchase_summary: "采购汇总与赋码",
      start_special_claim: "发起特殊用料",
    };
    const exclusiveGroup = key => key.includes("_semifinished_inventory") ? "semifinished" : (key.includes("_finished_inventory") ? "finished" : "");
    const groups = [
      ["模块查看", ["view_query", "view_flow", "view_outbound", "view_stats", "view_stocktake", "view_borrow", "view_logs", "view_recycle", "view_my_inspections", "view_my_started", "view_projects", "view_supply", "view_defective_inventory"]],
      ["流程发起", ["start_acceptance", "start_claim", "start_borrow", "start_stocktake", "add_material", "start_purchase_request", "start_special_claim", "start_common_material", "start_supply"]],
      ["办理修改", ["edit_acceptance", "edit_claim", "edit_borrow", "edit_outbound", "edit_stocktake", "manage_projects", "manage_purchase_summary", "edit_supply", "manage_defective_inventory"]],
      ["库存权限", ["read_semifinished_inventory", "write_semifinished_inventory", "read_finished_inventory", "write_finished_inventory", "view_temporary_inventory", "manage_temporary_inventory", "transfer_temporary_inventory"]],
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

  function aiProfileFields(profile) {
    const current = profile || {};
    const caps = current.capabilities || {};
    const labels = { off: "服务默认", none: "关闭", minimal: "极低", low: "低", medium: "中", high: "高", xhigh: "很高", max: "最高", ultra: "极致" };
    const options = current.thinking_options || (current.thinking_levels || ["off", "low", "medium", "high"]).map(id => ({
      id, label: labels[id] || id, enabled: true,
      fields: id === "off" ? {} : (current.thinking_style === "enable_thinking"
        ? { enable_thinking: true, reasoning_effort: id } : { reasoning_effort: id }),
    }));
    const detected = options.filter(item => item.enabled).map(item => item.label).join("、");
    return `<label>上下文长度<input id="aiContextTokens" type="number" min="1024" step="1" value="${Number(current.context_tokens || 32768)}"></label>
      <label>最大输出<input id="aiMaxOutputTokens" type="number" min="256" step="1" value="${Number(current.max_output_tokens || 4096)}"></label>
      <label><input id="aiCapTools" type="checkbox" ${caps.tools === false ? "" : "checked"}> 允许工具</label>
      <label><input id="aiCapVision" type="checkbox" ${caps.vision ? "checked" : ""}> 允许看图</label>
      <label><input id="aiCapReasoning" type="checkbox" ${caps.reasoning === false ? "" : "checked"}> 允许思考</label>
      <label class="wide">模型通用请求参数 JSON<input id="aiRequestParameters" value="${escapeAttr(JSON.stringify(current.request_parameters || {}))}" placeholder='{"temperature":0.3}'></label>
      <div class="wide ai-level-config">
        <div class="ai-level-heading"><strong>模型思考能力</strong><button type="button" id="aiDetectLevels">一键探测并保存</button></div>
        <p class="hint" id="aiLevelStatus">当前挡位：${escapeHtml(detected || "服务默认")}</p>
        <input type="hidden" id="aiDetectedOptions" value="${escapeAttr(JSON.stringify(options))}">
        <input type="hidden" id="aiDefaultThinking" value="${escapeAttr(current.default_thinking || "off")}">
      </div>`;
  }

  async function loadAiConfig() {
    const config = await systemApi("/api/ai/config");
    document.getElementById("aiConfig").innerHTML = `<div class="form-grid"><label>模型地址<input id="aiBaseUrl" value="${escapeAttr(config.base_url || "")}" placeholder="https://.../v1"></label><label>API Key<input id="aiKey" type="password" placeholder="${config.has_api_key ? "已保存，留空不改" : "请输入"}"></label><label>模型名称<select id="aiModel">${aiModelOptions([], config.model || "")}</select></label><label style="align-self:end;"><button id="connectAiModels" type="button">连接并获取模型</button></label><label class="wide">Skill 路径<input id="aiSkill" value="${escapeAttr(config.skill_path || "")}"></label><label class="wide">数据库 API 地址<input id="dbApi" value="${escapeAttr(config.database_api || "/api")}"></label>${aiProfileFields(config.profile)}<p class="hint wide" id="aiModelStatus">填写模型地址和 API Key 后点击连接，选择模型名称再保存。</p></div>`;
    document.getElementById("saveAiConfig").addEventListener("click", saveAiConfig);
    document.getElementById("connectAiModels").addEventListener("click", connectAiModels);
    document.getElementById("aiDetectLevels").addEventListener("click", detectAiLevels);
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
      select.onchange = () => {
        const metadata = (data.models || []).find(model => model.id === select.value);
        const context = metadata && (metadata.context_length || metadata.max_model_len || metadata.max_context_length);
        if (context) document.getElementById("aiContextTokens").value = context;
        if (metadata && metadata.max_output_tokens) document.getElementById("aiMaxOutputTokens").value = metadata.max_output_tokens;
      };
      select.onchange();
      status.textContent = `连接成功，找到 ${(data.models || []).length} 个模型。`;
      toast("模型列表已加载");
    } catch (error) {
      status.textContent = error.message;
      toast(error.message);
    } finally {
      button.disabled = false;
    }
  }

  async function detectAiLevels() {
    const button = document.getElementById("aiDetectLevels");
    const status = document.getElementById("aiLevelStatus");
    button.disabled = true;
    status.textContent = "正在探测模型，请稍候…";
    try {
      const data = await systemApi("/api/ai/model-detect", {
        method: "POST",
        body: JSON.stringify({
          ai_base_url: document.getElementById("aiBaseUrl").value,
          ai_api_key: document.getElementById("aiKey").value,
          model: document.getElementById("aiModel").value,
          request_parameters: JSON.parse(document.getElementById("aiRequestParameters").value || "{}"),
        }),
      });
      document.getElementById("aiDetectedOptions").value = JSON.stringify(data.thinking_options);
      document.getElementById("aiDefaultThinking").value = data.default_thinking;
      document.getElementById("aiCapReasoning").checked = true;
      const saved = await saveAiConfig();
      status.textContent = saved
        ? data.method + "：" + data.thinking_options.map(item => item.label).join("、")
        : "探测完成，保存失败，请检查模型设置。";
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  }

  async function saveAiConfig() {
    let options;
    let requestParameters;
    try {
      requestParameters = JSON.parse(document.getElementById("aiRequestParameters").value || "{}");
      options = JSON.parse(document.getElementById("aiDetectedOptions").value);
      if (!requestParameters || Array.isArray(requestParameters) || typeof requestParameters !== "object") {
        throw new Error("模型请求参数必须是 JSON 对象");
      }
    } catch (error) {
      toast(error.message);
      return;
    }
    const body = {
      ai_base_url: document.getElementById("aiBaseUrl").value,
      ai_model: document.getElementById("aiModel").value,
      ai_api_key: document.getElementById("aiKey").value,
      ai_skill_path: document.getElementById("aiSkill").value,
      database_api: document.getElementById("dbApi").value,
      ai_model_profile: {
        context_tokens: Number(document.getElementById("aiContextTokens").value),
        max_output_tokens: Number(document.getElementById("aiMaxOutputTokens").value),
        capabilities: {
          tools: document.getElementById("aiCapTools").checked,
          vision: document.getElementById("aiCapVision").checked,
          reasoning: document.getElementById("aiCapReasoning").checked,
        },
        thinking_options: options,
        request_parameters: requestParameters,
        default_thinking: document.getElementById("aiDefaultThinking").value.trim(),
      },
    };
    try {
      await systemApi("/api/ai/config", { method: "POST", body: JSON.stringify(body) });
      toast("物料小助手配置已保存");
      window.dispatchEvent(new Event("warehouse-ai-profile-updated"));
      return true;
    } catch (error) {
      toast(error.message);
      return false;
    }
  }

  async function askAi() {
    const host = document.getElementById("aiAnswer");
    const question = document.getElementById("aiQuestion").value.trim();
    if (!host) return;
    host.textContent = "回复中...";
    try {
      const response = await fetch("/api/ai/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      const type = response.headers.get("content-type") || "";
      if (!response.ok || !type.includes("ndjson")) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || "发送失败");
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let answer = "";
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";
        lines.forEach(line => {
          if (!line.trim()) return;
          const event = JSON.parse(line);
          if (event.type === "text") answer = event.text || "";
          if (event.type === "error") throw new Error(event.error || "模型返回失败");
        });
      }
      host.textContent = answer || "物料小助手未返回内容";
      applyAiSuggestion(answer);
    } catch (error) {
      host.textContent = error.message;
    }
  }

  function itemsTable(items) {
    return `<table class="flow-table"><thead><tr><th>物料</th><th>数量</th><th>到货/合格</th><th>单价</th></tr></thead><tbody>${items.map(i => `<tr><td>${escapeHtml(i.material_code)}<br>${escapeHtml(i.material_name)}<br><small>${escapeHtml(i.brand_model || "")} ${escapeHtml(i.spec || "")}</small></td><td>${formatQty(i.request_quantity)}</td><td>${formatQty(i.arrival_quantity)} / ${formatQty(i.qualified_quantity)}</td><td>${money(i.unit_price)}</td></tr>`).join("")}</tbody></table>`;
  }

  function materialTable(rows) {
    return `<table class="flow-table"><thead><tr><th>编号</th><th>名称</th><th>品牌型号</th><th>规格</th><th>余量</th><th>位置</th></tr></thead><tbody>${rows.map(m => `<tr><td>${escapeHtml(m.material_code)}</td><td>${escapeHtml(m.name)}</td><td>${escapeHtml(m.brand_model || "")}</td><td>${escapeHtml(m.spec || "")}</td><td>${formatQty(m.quantity)} ${escapeHtml(m.unit || "")}</td><td>${escapeHtml(m.shelf_name || "")} ${m.layer_number || ""} ${escapeHtml(m.zone_name || "")}</td></tr>`).join("") || `<tr><td colspan="6">暂无数据</td></tr>`}</tbody></table>`;
  }

  function flowProjectCode(form) {
    const item = (form.items || [])[0] || {};
    return String(form.data?.project_code || item.data?.project_code || "").trim();
  }

  function batchLineAmount(batches) {
    return (batches || []).reduce((sum, batch) => sum + Number(batch.quantity || 0) * Number(batch.unit_price || 0), 0);
  }

  function batchLineQuantity(batches) {
    return (batches || []).reduce((sum, batch) => sum + Number(batch.quantity || 0), 0);
  }

  function claimPriceBatches(item) {
    const consumed = item?.data?.consumed_batches;
    if (Array.isArray(consumed) && consumed.length) return consumed;
    return item.suggested_batches || [];
  }

  function claimItemAmount(item) {
    const batches = claimPriceBatches(item);
    if (batches.length) return batchLineAmount(batches);
    return Number(item.request_quantity || 0) * Number(item.unit_price || 0);
  }

  function claimItemUnitPrice(item) {
    const batches = claimPriceBatches(item);
    if (batches.length) {
      const qty = batchLineQuantity(batches);
      return qty > 0 ? batchLineAmount(batches) / qty : null;
    }
    if (item.unit_price == null || item.unit_price === "") return null;
    return Number(item.unit_price);
  }

  function borrowItemUnitPrice(item) {
    const outbound = item?.data?.borrow_outbound;
    const batches = outbound?.consumed_batches || item?.data?.consumed_batches;
    if (Array.isArray(batches) && batches.length) {
      const qty = batchLineQuantity(batches);
      return qty > 0 ? batchLineAmount(batches) / qty : null;
    }
    if (outbound && outbound.unit_price != null && outbound.unit_price !== "") {
      return Number(outbound.unit_price);
    }
    return null;
  }

  function moneyOrBlank(value) {
    if (value == null || value === "" || Number.isNaN(Number(value))) return "";
    return money(Number(value));
  }

  function flowItemsTable(form) {
    const items = form.items || [];
    if (isProductionFlow(form)) {
      const item = items[0] || {};
      return `${productionHeaderTable(form, item)}${productionComponentsTable(item)}${productionSerialsTable(item)}<h3>验收结果</h3><table class="flow-table"><thead><tr><th>验收/合格/不合格</th><th>外观/功能/性能</th><th>单价</th><th>金额</th><th>位置</th></tr></thead><tbody>${items.map(i => `<tr><td>${formatQty(i.arrival_quantity)} / ${formatQty(i.qualified_quantity)} / ${formatQty(i.unqualified_quantity)}</td><td>${formatQty(i.data?.appearance_ok_quantity || 0)} / ${formatQty(i.data?.function_ok_quantity || 0)} / ${formatQty(i.data?.performance_ok_quantity || 0)}</td><td>${money(i.unit_price || 0)}</td><td>${money(Number(i.arrival_quantity || 0) * Number(i.unit_price || 0))}</td><td>${escapeHtml(locationText(i.data || {}))}</td></tr>`).join("")}</tbody></table>${flowTotalHtml(items, "arrival_quantity")}`;
    }
    if (form.form_type === "borrow" || form.form_type === "borrow_return") {
      return `<table class="flow-table"><thead><tr><th>类型</th><th>编号</th><th>名称</th><th>品牌</th><th>规格</th><th>申请数量</th><th>出库/入库数量</th><th>单价</th><th>金额</th></tr></thead><tbody>${items.map(i => {
        const qty = Number(i.outbound_quantity || i.approved_quantity || i.request_quantity || 0);
        const unitPrice = borrowItemUnitPrice(i);
        const amount = unitPrice == null ? "" : money(unitPrice * qty);
        const claimed = Number(i.claimed_quantity || i.data?.claimed_quantity || 0);
        const claimedText = claimed > 0 ? ` 其中${formatQty(claimed)}个已转领用` : "";
        return `<tr><td>${borrowItemTypeLabel(i.data?.borrow_item_type || (i.material_id ? "material" : ""))}</td><td>${escapeHtml(i.material_code || "")}</td><td>${escapeHtml(i.material_name || "")}</td><td>${escapeHtml(i.brand_model || "无")}</td><td>${escapeHtml(i.spec || "")}</td><td>${formatQty(i.request_quantity || 0)} ${escapeHtml(i.unit || "")}${claimedText}</td><td>${formatQty(i.outbound_quantity || i.approved_quantity || 0)} ${escapeHtml(i.unit || "")}</td><td>${moneyOrBlank(unitPrice)}</td><td>${amount}</td></tr>`;
      }).join("")}</tbody></table>${borrowFlowTotalHtml(items)}`;
    }
    if (form.form_type === "borrow_claim") {
      const snapshot = form.data?.borrow_snapshot || {};
      const snapshotItems = snapshot.items || [];
      const snapshotTable = snapshotItems.length
        ? `<h3>原借用单</h3><table class="flow-table"><thead><tr><th>编号</th><th>名称</th><th>品牌</th><th>规格</th><th>申请数量</th><th>出库数量</th></tr></thead><tbody>${snapshotItems.map(i => `<tr><td>${escapeHtml(i.material_code || "")}</td><td>${escapeHtml(i.material_name || "")}</td><td>${escapeHtml(i.brand_model || "无")}</td><td>${escapeHtml(i.spec || "")}</td><td>${formatQty(i.request_quantity || 0)} ${escapeHtml(i.unit || "")}</td><td>${formatQty(i.outbound_quantity || 0)} ${escapeHtml(i.unit || "")}</td></tr>`).join("")}</tbody></table>`
        : "";
      return `${snapshotTable}<h3>本次转领用</h3><table class="flow-table"><thead><tr><th>编号</th><th>名称</th><th>品牌</th><th>规格</th><th>转领用数量</th></tr></thead><tbody>${items.map(i => `<tr><td>${escapeHtml(i.material_code || "")}</td><td>${escapeHtml(i.material_name || "")}</td><td>${escapeHtml(i.brand_model || "无")}</td><td>${escapeHtml(i.spec || "")}</td><td>${formatQty(i.request_quantity || 0)} ${escapeHtml(i.unit || "")}</td></tr>`).join("") || `<tr><td colspan="5">暂无明细</td></tr>`}</tbody></table>`;
    }
    if (form.form_type === "supply") {
      return `<table class="flow-table"><thead><tr><th>名称</th><th>品牌型号</th><th>规格</th><th>数量</th><th>单位</th><th>状态</th></tr></thead><tbody>${items.map(item => `<tr><td>${escapeHtml(item.material_name || "")}</td><td>${escapeHtml(item.brand_model || "")}</td><td>${escapeHtml(item.spec || "")}</td><td>${formatQty(item.request_quantity || 0)}</td><td>${escapeHtml(item.unit || "")}</td><td>${escapeHtml(item.data?.goods_status || "")}</td></tr>`).join("")}</tbody></table>`;
    }
    if (form.form_type === "claim") {
      return `<table class="flow-table"><thead><tr><th>物料</th><th>申领数量</th><th>剩余库存数量</th><th>已出库批次</th><th>单价</th><th>金额</th></tr></thead><tbody>${items.map(i => `<tr><td>${escapeHtml(i.material_code)}<br>${escapeHtml(i.material_name)}<br><small>${escapeHtml(i.brand_model || "")} ${escapeHtml(i.spec || "")}</small></td><td>${formatQty(i.request_quantity)} ${escapeHtml(i.unit || "")}</td><td>${batchSummaryHtml(i.batches || [], i.unit)}</td><td>${batchSummaryHtml(i.data?.consumed_batches || [], i.unit)}</td><td>${moneyOrBlank(claimItemUnitPrice(i))}</td><td>${money(claimItemAmount(i))}</td></tr>`).join("")}</tbody></table>${claimOutboundTotalHtml(items)}`;
    }
    return `<table class="flow-table"><thead><tr><th>物料</th><th>采购申请人</th><th>采购申请项目</th><th>采购/到货/合格</th><th>入库单号</th><th>入库批次</th><th>单价</th><th>金额</th></tr></thead><tbody>${items.map(i => `<tr><td>${escapeHtml(i.material_code)}<br>${escapeHtml(i.material_name)}<br><small>${escapeHtml(i.brand_model || "")} ${escapeHtml(i.spec || "")}</small></td><td>${escapeHtml(i.purchase_applicant || i.data?.purchase_applicant || "")}</td><td>${escapeHtml(i.purchase_project || i.data?.purchase_project || "")}</td><td>${formatQty(i.request_quantity)} / ${formatQty(i.arrival_quantity)} / ${formatQty(i.qualified_quantity)}</td><td>${escapeHtml(i.data?.inbound_no || "")}</td><td>${escapeHtml(i.data?.inbound_batch_no || expectedInboundBatchNo(i))}</td><td>${money(i.unit_price)}</td><td>${money(Number(i.arrival_quantity || 0) * Number(i.unit_price || 0))}</td></tr>`).join("")}</tbody></table>${flowTotalHtml(items, "arrival_quantity")}`;
  }

  function borrowFlowTotalHtml(items) {
    const total = (items || []).reduce((sum, item) => {
      const unitPrice = borrowItemUnitPrice(item);
      if (unitPrice == null) return sum;
      const qty = Number(item.outbound_quantity || item.approved_quantity || item.request_quantity || 0);
      return sum + unitPrice * qty;
    }, 0);
    const hasPrice = (items || []).some(item => borrowItemUnitPrice(item) != null);
    return hasPrice ? `<p style="text-align:right;margin-top:8px;font-weight:bold;color:#1e40af">总金额：${money(total)}</p>` : "";
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
      return sum + (item.suggested_batches || []).reduce(
        (batchTotal, batch) => batchTotal
          + (Number(batch.quantity || 0) * Number(batch.unit_price || 0)),
        0,
      );
    }, 0);
    return `<p style="text-align:right;margin-top:8px;font-weight:bold;color:#1e40af">总金额：${money(total)}</p>`;
  }

  function flowTotalHtml(items, qtyField) {
    const total = (items || []).reduce((sum, i) => sum + (Number(i[qtyField] || 0) * Number(i.unit_price || 0)), 0);
    return `<p style="text-align:right;margin-top:8px;font-weight:bold;color:#1e40af">总金额：${money(total)}</p>`;
  }

  function expectedInboundBatchNo(item) {
    const date = new Date();
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const d = String(date.getDate()).padStart(2, "0");
    return `${y}${m}${d}${item.material_code || ""}`;
  }

  function itemsTableV2(items) {
    return `<table class="flow-table"><thead><tr><th>物料</th><th>申请/到货/合格</th><th>库存总量</th><th>批次库存</th><th>单价</th></tr></thead><tbody>${(items || []).map(i => `<tr><td>${escapeHtml(i.material_code)}<br>${escapeHtml(i.material_name)}<br><small>${escapeHtml(i.brand_model || "")} ${escapeHtml(i.spec || "")}</small></td><td>${formatQty(i.request_quantity)} / ${formatQty(i.arrival_quantity)} / ${formatQty(i.qualified_quantity)}</td><td>${formatQty(i.stock_quantity || 0)} ${escapeHtml(i.unit || "")}</td><td>${batchSummaryHtml(i.batches || i.data?.consumed_batches || [], i.unit)}</td><td>${money(i.unit_price)}</td></tr>`).join("")}</tbody></table>`;
  }

  function materialTableV2(rows) {
    const canEdit = canModifyMaterial();
    const canDelete = canDeleteMaterial();
    return `<table class="flow-table"><thead><tr><th rowspan="2">编号</th><th colspan="2">集团物料编码</th><th rowspan="2">名称</th><th rowspan="2">品牌型号</th><th rowspan="2">规格</th><th rowspan="2">采购申请人</th><th rowspan="2">正式库实际数量</th><th rowspan="2">正式库账面数量</th><th rowspan="2">临时库实际数量</th><th rowspan="2">临时库账面数量</th><th rowspan="2">批次信息</th><th rowspan="2">位置</th><th rowspan="2">操作</th></tr><tr><th>所属分类编码</th><th>物品和服务分类代码</th></tr></thead><tbody>${rows.map(m => `<tr><td>${escapeHtml(m.material_code)}</td><td>${escapeHtml(m.group_category_code || "")}</td><td>${escapeHtml(m.group_service_code || "")}</td><td>${escapeHtml(m.name)}</td><td>${escapeHtml(m.brand_model || "")}</td><td>${escapeHtml(m.spec || "")}</td><td>${escapeHtml(m.purchase_applicants || "")}</td><td>${formatQty(m.quantity)} ${escapeHtml(m.unit || "")}</td><td>${formatQty(m.book_quantity || 0)}</td><td>${formatQty(m.temporary_quantity || 0)} ${escapeHtml(m.unit || "")}</td><td>${formatQty(m.temporary_book_quantity || 0)}</td><td>${escapeHtml(m.batch_summary || "")}</td><td>${escapeHtml(m.shelf_name || "")} ${m.layer_number || ""} ${escapeHtml(m.zone_name || "")}</td><td class="mini-actions"><button type="button" data-view-material="${m.id}">查看详情</button>${canEdit ? `<button type="button" data-edit-material="${m.id}">修改</button>` : ""}${canDelete ? `<button type="button" class="danger" data-delete-material="${m.id}">删除</button>` : ""}</td></tr>`).join("") || `<tr><td colspan="14">暂无数据</td></tr>`}</tbody></table>`;
  }

  function batchSummaryHtml(batches, unitFallback = "") {
    return (batches || []).map(batch => {
      const unit = escapeHtml(String(batch.unit || unitFallback || "").trim());
      const parts = [`实际${formatQty(batch.quantity || 0)}${unit}`];
      if (batch.book_quantity !== undefined && batch.book_quantity !== null && batch.book_quantity !== "") {
        parts.push(`账面${formatQty(batch.book_quantity)}${unit}`);
      }
      if (batch.pack_quantity !== undefined && batch.pack_quantity !== null && batch.pack_quantity !== "") {
        parts.push(`每单位数量${formatQty(batch.pack_quantity)}`);
      }
      return `${escapeHtml(batch.batch_no || "")}：${parts.join("，")}`;
    }).join("<br>") || "";
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

  function stocktakeNumberText(value) {
    if (value === null || value === undefined || value === "") return "";
    return formatQty(value);
  }

  function stocktakeReportHtml(report) {
    const sheets = report.sheets || [];
    return sheets.map(sheet => {
      const body = (sheet.rows || []).map(row => `<tr><td>${row.seq}</td><td>${escapeHtml(row.name || "")}</td><td>${escapeHtml(row.brand_model || "")}</td><td>${escapeHtml(row.spec || "")}</td><td>${escapeHtml(row.unit || "")}</td><td>${stocktakeNumberText(row.avg_price)}</td><td>${stocktakeNumberText(row.opening_qty)}</td><td>${stocktakeNumberText(row.opening_amount)}</td><td>${stocktakeNumberText(row.month_in_qty)}</td><td>${stocktakeNumberText(row.month_in_price)}</td><td>${stocktakeNumberText(row.month_in_amount)}</td><td>${stocktakeNumberText(row.ytd_in_qty)}</td><td>${stocktakeNumberText(row.ytd_in_amount)}</td><td>${stocktakeNumberText(row.month_out_qty)}</td><td>${stocktakeNumberText(row.month_out_amount)}</td><td>${stocktakeNumberText(row.ytd_out_qty)}</td><td>${stocktakeNumberText(row.ytd_out_amount)}</td><td>${stocktakeNumberText(row.close_qty)}</td><td>${stocktakeNumberText(row.close_amount)}</td><td>${row.age_months ?? ""}</td><td>${escapeHtml(row.project || "")}</td></tr>`).join("");
      return `<section class="stocktake-sheet"><h2>${escapeHtml(sheet.title || "")}</h2><p>${escapeHtml(sheet.org || "")}</p><table class="flow-table"><thead><tr><th>序号</th><th>产品名称</th><th>品牌型号</th><th>技术规格</th><th>单位</th><th>库存平均单价</th><th>上年结存数量</th><th>上年结存金额</th><th>本月收入数量</th><th>本月收入单价</th><th>本月收入金额</th><th>当年累计收入数量</th><th>当年累计收入金额</th><th>本月发出数量</th><th>本月发出金额</th><th>当年累计发出数量</th><th>当年累计发出金额</th><th>月末库存数量</th><th>月末库存金额</th><th>库龄（月）</th><th>入账项目</th></tr></thead><tbody>${body || `<tr><td colspan="21">没有盘点物料</td></tr>`}</tbody></table></section>`;
    }).join("") || `<div class="empty-state">没有可显示的月份</div>`;
  }

  async function showStocktakeReport(stocktakeId, host) {
    if (!host || !stocktakeId) return;
    const report = await systemApi(`/api/stocktakes/${stocktakeId}/report`);
    host.innerHTML = stocktakeReportHtml(report);
  }

  function stocktakeTable(form) {
    return `<h2>${escapeHtml(form.form_no || "")}</h2><p>\u76d8\u70b9\u8303\u56f4\uff1a${formatDateCn(form.date_from)} \u81f3 ${formatDateCn(form.date_to)}\uff0c\u76d8\u70b9\u4eba\uff1a${escapeHtml(form.checker_signature || "")}\uff0c\u65e5\u671f\uff1a${formatDateCn(form.checker_date)}</p><p>\u76d1\u76d8\u7b7e\u5b57\uff1a${escapeHtml(form.supervisor_signature || "\u5f85\u7b7e\u5b57")}\uff0c\u65e5\u671f\uff1a${formatDateCn(form.supervisor_date)}</p><table class="flow-table"><thead><tr><th>\u7269\u6599</th><th>\u4f4d\u7f6e</th><th>\u8d26\u9762\u5e93\u5b58</th><th>\u5e93\u5b58\u91d1\u989d</th><th>\u671f\u95f4\u5165\u5e93</th><th>\u671f\u95f4\u51fa\u5e93</th></tr></thead><tbody>${(form.items || []).map(i => `<tr><td>${escapeHtml(i.material_code)} ${escapeHtml(i.material_name)}</td><td>${escapeHtml(i.location_text || "")}</td><td>${formatQty(i.book_quantity)}</td><td>${money(i.stock_amount)}</td><td>${formatQty(i.period_in)}</td><td>${formatQty(i.period_out)}</td></tr>`).join("")}</tbody></table>`;
    return `<h2>${form.form_no}</h2><p>盘点范围：${form.date_from} 至 ${form.date_to}，盘点人：${escapeHtml(form.checker_signature || "")}，日期：${form.checker_date || ""}</p><table class="flow-table"><thead><tr><th>物料</th><th>位置</th><th>账面库存</th><th>库存金额</th><th>期间入库</th><th>期间出库</th></tr></thead><tbody>${form.items.map(i => `<tr><td>${escapeHtml(i.material_code)} ${escapeHtml(i.material_name)}</td><td>${escapeHtml(i.location_text || "")}</td><td>${formatQty(i.book_quantity)}</td><td>${money(i.stock_amount)}</td><td>${formatQty(i.period_in)}</td><td>${formatQty(i.period_out)}</td></tr>`).join("")}</tbody></table><p>监盘人签字：________________ 日期：__________</p>`;
  }

  function exportTableCsv(hostId, filename) {
    const text = [...document.querySelectorAll(`#${hostId} table tr`)].map(tr => [...tr.children].map(td => `"${td.textContent.replaceAll('"', '""')}"`).join(",")).join("\n");
    const blob = new Blob(["\uFEFF" + text], { type: "text/csv;charset=utf-8" });
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

        const item = data.item || {};
        const history = data.history || [];
        const total = data.total || 0;
        const componentRows = Array.isArray(item.components) ? item.components : null;
        const componentTable = componentRows
          ? `<h3>组成物料</h3><table class="flow-table"><thead><tr><th>编号</th><th>名称</th><th>批次</th><th>单台用量</th><th>原领用人</th></tr></thead><tbody>${
            componentRows.length
              ? componentRows.map(row => `<tr><td>${escapeHtml(row.material_code || "")}</td><td>${escapeHtml(row.material_name || "")}</td><td>${escapeHtml(row.batch_no || "")}</td><td>${formatQty(row.per_unit_quantity)}${row.unit ? ` ${escapeHtml(row.unit)}` : ""}</td><td>${escapeHtml(row.claim_applicant || "")}</td></tr>`).join("")
              : `<tr><td colspan="5">暂无组成明细</td></tr>`
          }</tbody></table>`
          : "";

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

        const canDisassembleSemi = itemType === "semifinished" && hasPerm("write_semifinished_inventory");
        const body = `<div class="history-modal">
          ${componentTable}
          <h3>变更历史 — ${escapeHtml(item.name || item.product_name || '')}</h3>
          <table class="flow-table">
            <thead><tr><th>时间</th><th>操作类型</th><th>操作人</th><th>变更类型</th><th>详情</th><th>是否正常</th></tr></thead>
            <tbody id="historyTableBody">${rowsHtml}</tbody>
          </table>
          ${total > currentPage * limit ? `<div style="text-align:center;margin-top:12px" id="loadMoreRow"><button class="btn-secondary" id="loadMoreHistoryBtn">加载更多</button></div>` : ''}
          ${canDisassembleSemi ? `<div class="mini-actions" style="margin-top:12px"><button type="button" id="disassembleSemiFromHistory">拆解</button></div>` : ""}
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
            const disassembleBtn = document.getElementById("disassembleSemiFromHistory");
            if (disassembleBtn) {
              disassembleBtn.addEventListener("click", () => disassembleSemifinishedInventory(itemRefId));
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
