(() => {
  "use strict";

  const DASHBOARD_REFRESH_INTERVAL = 30_000;
  const MAX_PARTICLES = 35;
  const MIN_PARTICLES = 30;
  const PARTICLE_FPS = 24;
  const PARTICLE_FRAME_INTERVAL = 1000 / PARTICLE_FPS;
  const PARTICLE_RESIZE_DEBOUNCE = 200;
  const state = { lastData: null };
  const workflowLabels = {
    acceptance: "待验收",
    leader_acceptance: "验收审批",
    applicant_revision: "申请人修改",
    inbound: "待入库",
    leader_claim: "领用审批",
    outbound: "待出库",
    leader_borrow: "借用审批",
    borrow_outbound: "借用待出库",
    return_inbound: "归还确认",
    temporary_transfer: "临时库转正式库",
    awaiting_purchase: "等待采购处理",
    acceptance_in_progress: "验收处理中",
    acceptance_failed: "验收失败",
    formal_inbound_partial: "正式入库不足",
    formal_inbound_complete: "正式入库完成，等待结算",
    exception: "异常",
    stocktake_supervisor: "待监盘签字",
    leader_common_material: "常用物料审批",
    leader_supply: "供货审批",
    supply_outbound: "供货待出库",
    supply_return_inbound: "回寄待验收",
    leader_supply_extension: "供货延期审批",
    external_open: "外部待结清"
  };

  function number(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function formatNumber(value) {
    return String(Math.round(number(value)));
  }

  function formatMoney(value) {
    return `¥${number(value).toFixed(2)}`;
  }

  function readValue(data, path) {
    return path.split(".").reduce((current, key) => current && current[key], data);
  }

  function setText(field, value) {
    document.querySelectorAll(`[data-field="${field}"]`).forEach(element => {
      if (field === "summary.today_in_out" || field === "summary.month_in_out") {
        element.textContent = String(value || "0 / 0");
      } else if (field === "summary.total_amount") {
        element.textContent = formatMoney(value);
      } else if (field.endsWith("_ratio")) {
        element.textContent = `${number(value).toFixed(1)}%`;
      } else {
        element.textContent = formatNumber(value);
      }
    });
  }

  function updateFields(data) {
    document.querySelectorAll("[data-field]").forEach(element => {
      const field = element.dataset.field;
      if (field === "summary.today_in_out" || field === "summary.month_in_out") return;
      setText(field, readValue(data, field));
    });

    const summary = data.summary || {};
    setText("summary.today_in_out", `${formatNumber(summary.today_inbound)} / ${formatNumber(summary.today_outbound)}`);
    setText("summary.month_in_out", `${formatNumber(summary.month_inbound)} / ${formatNumber(summary.month_outbound)}`);

    const inventory = data.inventory || {};
    const total = number(inventory.total_stock);
    setText("inventory.formal_ratio", total ? number(inventory.formal_stock) / total * 100 : 0);
    setText("inventory.temporary_ratio", total ? number(inventory.temporary_stock) / total * 100 : 0);
  }

  function clear(element) {
    while (element.firstChild) element.removeChild(element.firstChild);
  }

  function emptyState(message) {
    const empty = document.createElement("div");
    empty.className = "dashboard-empty";
    empty.textContent = message;
    return empty;
  }

  function renderCategories(categories) {
    const host = document.getElementById("categoryBars");
    if (!host) return;
    clear(host);
    if (!Array.isArray(categories) || !categories.length) {
      host.append(emptyState("暂无数据"));
      return;
    }
    const max = Math.max(1, ...categories.map(item => number(item.stock_quantity)));
    categories.forEach(item => {
      const row = document.createElement("div");
      row.className = "category-row";
      const name = document.createElement("span");
      name.className = "category-name";
      name.textContent = String(item.name || "未分类");
      const track = document.createElement("div");
      track.className = "category-track";
      const fill = document.createElement("span");
      fill.className = "category-fill";
      fill.style.width = `${Math.max(0, number(item.stock_quantity)) / max * 100}%`;
      track.append(fill);
      const value = document.createElement("strong");
      value.className = "category-value";
      value.textContent = formatNumber(item.stock_quantity);
      row.append(name, track, value);
      host.append(row);
    });
  }

  function renderTrend(trend) {
    const host = document.getElementById("trendChart");
    if (!host) return;
    clear(host);
    const rows = Array.isArray(trend) ? trend : [];
    const max = Math.max(1, ...rows.flatMap(row => [number(row.inbound), number(row.outbound)]));
    rows.forEach(row => {
      const group = document.createElement("div");
      group.className = "trend-group";
      group.dataset.label = String(row.label || "");
      const tooltip = document.createElement("span");
      tooltip.className = "trend-tooltip";
      tooltip.textContent = `${row.month || ""}\n入库：${formatNumber(row.inbound)}\n出库：${formatNumber(row.outbound)}`;
      const inbound = document.createElement("span");
      inbound.className = "trend-bar inbound";
      inbound.style.height = number(row.inbound) ? `${Math.max(4, number(row.inbound) / max * 100)}%` : "1px";
      const outbound = document.createElement("span");
      outbound.className = "trend-bar outbound";
      outbound.style.height = number(row.outbound) ? `${Math.max(4, number(row.outbound) / max * 100)}%` : "1px";
      group.append(tooltip, inbound, outbound);
      host.append(group);
    });
  }

  function renderTodos(todos) {
    const host = document.getElementById("todoList");
    if (!host) return;
    clear(host);
    if (!Array.isArray(todos) || !todos.length) {
      host.append(emptyState("暂无数据"));
      return;
    }
    todos.forEach(todo => {
      const row = document.createElement("a");
      row.className = "todo-row";
      row.href = "/?view=todo";
      row.target = "_top";
      const main = document.createElement("span");
      main.className = "todo-main";
      const formNo = document.createElement("strong");
      formNo.className = "todo-no";
      formNo.textContent = String(todo.form_no || "待办流程");
      const title = document.createElement("span");
      title.className = "todo-title";
      title.textContent = String(todo.title || todo.form_type || "");
      main.append(formNo, title);
      const step = document.createElement("span");
      step.className = "todo-step";
      const rawStep = String(todo.step_code || "");
      step.textContent = workflowLabels[rawStep] || rawStep || "待处理";
      row.append(main, step);
      host.append(row);
    });
  }

  function renderStocktake(check) {
    const date = document.getElementById("stocktakeDate");
    const status = document.getElementById("stocktakeStatus");
    if (!date || !status) return;
    if (!check || !check.next_date || check.status === "unset") {
      date.textContent = "下一次盘点日期：暂未设置";
      status.textContent = "暂未设置";
      status.className = "stocktake-status";
      return;
    }
    date.textContent = `下一次盘点日期：${check.next_date}`;
    const remaining = number(check.days_remaining);
    if (check.status === "overdue") {
      status.textContent = `已逾期 ${Math.abs(remaining)} 天`;
      status.className = "stocktake-status overdue";
    } else if (check.status === "due") {
      status.textContent = "今日盘点";
      status.className = "stocktake-status due";
    } else {
      status.textContent = `距离盘点还有 ${remaining} 天`;
      status.className = "stocktake-status";
    }
  }

  function renderAlerts(alerts) {
    const host = document.getElementById("alertList");
    if (!host) return;
    clear(host);
    if (!Array.isArray(alerts) || !alerts.length) {
      host.append(emptyState("当前无库存预警"));
      return;
    }
    alerts.forEach(alert => {
      const links = {
        common_low_stock: { href: "/?view=commonMaterials&low_stock=1", title: "查看低库存常用物料" },
        borrow_overdue: { href: "/?view=myBorrow&overdue=1", title: "查看逾期借用物料" }
      };
      const link = links[alert.action];
      const row = document.createElement(link ? "a" : "div");
      row.className = `alert-row${alert.level === "critical" ? " critical" : ""}${link ? " alert-link" : ""}`;
      if (link) { row.href = link.href; row.target = "_top"; row.title = link.title; }
      const indicator = document.createElement("i");
      indicator.className = "alert-indicator";
      const text = document.createElement("span");
      text.className = "alert-text";
      text.textContent = String(alert.text || "");
      const date = document.createElement("time");
      date.className = "alert-date";
      date.textContent = String(alert.date || "");
      row.append(indicator, text, date);
      host.append(row);
    });
  }

  function renderDashboard(data) {
    const temporaryEnabled = Boolean(data.settings && data.settings.temporary_warehouse_enabled);
    document.body.classList.toggle("temporary-disabled", !temporaryEnabled);
    updateFields(data);
    renderCategories(data.categories);
    renderTrend(data.trend);
    renderTodos(data.todos);
    renderStocktake(data.inventory_check);
    renderAlerts(data.alerts);
  }

  function updateClock() {
    const now = new Date();
    const date = now.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", weekday: "short" });
    const time = now.toLocaleTimeString("zh-CN", { hour12: false });
    const clock = document.getElementById("dashboardClock");
    if (clock) clock.textContent = `${date}  ${time}`;
  }

  async function loadDashboard() {
    try {
      const response = await fetch("/api/dashboard/overview", { headers: { Accept: "application/json" }, cache: "no-store" });
      if (!response.ok) throw new Error(`dashboard request failed: ${response.status}`);
      const data = await response.json();
      if (!data || typeof data !== "object") throw new Error("dashboard response is not an object");
      state.lastData = data;
      renderDashboard(data);
    } catch (error) {
      console.error("dashboard refresh failed", error);
    }
  }

  let dashboardRoot = null;
  let clockTimer = null;
  let dashboardRefreshTimer = null;
  let particleCanvas = null;
  let particleContext = null;
  let particles = [];
  let particleAnimationId = null;
  let lastParticleFrameTime = 0;
  let particleResizeTimer = null;
  let particleResizeHandler = null;
  let particleWidth = 0;
  let particleHeight = 0;
  let visibilityListenerAttached = false;

  function resizeParticleCanvas() {
    if (!particleCanvas || !particleContext) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    particleWidth = window.innerWidth;
    particleHeight = window.innerHeight;
    particleCanvas.width = Math.round(particleWidth * dpr);
    particleCanvas.height = Math.round(particleHeight * dpr);
    particleCanvas.style.width = `${particleWidth}px`;
    particleCanvas.style.height = `${particleHeight}px`;
    particleContext.setTransform(dpr, 0, 0, dpr, 0, 0);

    const particleCount = Math.min(
      MAX_PARTICLES,
      Math.max(MIN_PARTICLES, Math.floor(particleWidth * particleHeight / 30_000))
    );
    particles = Array.from({ length: particleCount }, () => ({
      x: Math.random() * particleWidth,
      y: Math.random() * particleHeight,
      vx: (Math.random() - .5) * .22,
      vy: -.12 - Math.random() * .28,
      r: Math.random() * 1.4 + .5,
      alpha: Math.random() * .38 + .12,
      phase: Math.random() * Math.PI * 2
    }));
  }

  function scheduleParticleResize() {
    if (particleResizeTimer !== null) window.clearTimeout(particleResizeTimer);
    particleResizeTimer = window.setTimeout(() => {
      particleResizeTimer = null;
      resizeParticleCanvas();
    }, PARTICLE_RESIZE_DEBOUNCE);
  }

  function updateAndDrawParticles(frameElapsed) {
    if (!particleContext || !particleCanvas) return;
    const frameScale = Math.min(frameElapsed / (1000 / 60), 3);
    particleContext.clearRect(0, 0, particleWidth, particleHeight);
    particleContext.shadowColor = "rgba(49,209,255,.58)";
    particleContext.shadowBlur = 6;
    particles.forEach(particle => {
      particle.x += particle.vx * frameScale;
      particle.y += particle.vy * frameScale;
      particle.phase += .025 * frameScale;
      if (particle.y < -10) {
        particle.y = particleHeight + 10;
        particle.x = Math.random() * particleWidth;
      }
      if (particle.x < -10) particle.x = particleWidth + 10;
      if (particle.x > particleWidth + 10) particle.x = -10;
      const blink = .55 + Math.sin(particle.phase) * .35;
      particleContext.beginPath();
      particleContext.arc(particle.x, particle.y, particle.r, 0, Math.PI * 2);
      particleContext.fillStyle = `rgba(49,209,255,${particle.alpha * blink})`;
      particleContext.fill();
    });
    particleContext.shadowBlur = 0;
  }

  function particleAnimationLoop(timestamp) {
    if (document.hidden || particleAnimationId === null) return;
    particleAnimationId = window.requestAnimationFrame(particleAnimationLoop);
    const frameElapsed = timestamp - lastParticleFrameTime;
    if (frameElapsed < PARTICLE_FRAME_INTERVAL) return;
    lastParticleFrameTime = timestamp;
    updateAndDrawParticles(frameElapsed);
  }

  function startParticleAnimation() {
    if (document.hidden || particleAnimationId !== null || !particleContext) return;
    lastParticleFrameTime = performance.now();
    particleAnimationId = window.requestAnimationFrame(particleAnimationLoop);
  }

  function stopParticleAnimation() {
    if (particleAnimationId === null) return;
    window.cancelAnimationFrame(particleAnimationId);
    particleAnimationId = null;
  }

  function initializeParticles() {
    particleCanvas = document.getElementById("particleCanvas");
    if (!particleCanvas || !particleCanvas.getContext) return;
    particleContext = particleCanvas.getContext("2d");
    if (!particleContext) return;
    particleResizeHandler = scheduleParticleResize;
    window.addEventListener("resize", particleResizeHandler);
    resizeParticleCanvas();
  }

  function destroyParticles() {
    stopParticleAnimation();
    if (particleResizeTimer !== null) window.clearTimeout(particleResizeTimer);
    if (particleResizeHandler) window.removeEventListener("resize", particleResizeHandler);
    particleCanvas = null;
    particleContext = null;
    particles = [];
    particleResizeTimer = null;
    particleResizeHandler = null;
    particleWidth = 0;
    particleHeight = 0;
  }

  function stopDashboardRefresh() {
    if (dashboardRefreshTimer !== null) {
      window.clearInterval(dashboardRefreshTimer);
      dashboardRefreshTimer = null;
    }
  }

  function startDashboardRefresh() {
    stopDashboardRefresh();
    if (document.hidden || !dashboardRoot) return;
    dashboardRefreshTimer = window.setInterval(() => {
      if (!document.hidden) loadDashboard();
    }, DASHBOARD_REFRESH_INTERVAL);
  }

  function handleDashboardVisibilityChange() {
    if (!dashboardRoot) return;
    if (document.hidden) {
      dashboardRoot.classList.add("is-paused");
      stopParticleAnimation();
      stopDashboardRefresh();
      return;
    }

    dashboardRoot.classList.remove("is-paused");
    startParticleAnimation();
    loadDashboard().finally(startDashboardRefresh);
  }

  function ensureVisibilityListener() {
    if (visibilityListenerAttached) return;
    document.addEventListener("visibilitychange", handleDashboardVisibilityChange);
    visibilityListenerAttached = true;
  }

  function unmount() {
    if (clockTimer !== null) window.clearInterval(clockTimer);
    stopDashboardRefresh();
    destroyParticles();
    clockTimer = null;
    dashboardRoot = null;
  }

  function mount() {
    unmount();
    dashboardRoot = document.querySelector("[data-dashboard-page]");
    if (!dashboardRoot) return;
    ensureVisibilityListener();
    updateClock();
    clockTimer = window.setInterval(updateClock, 1_000);
    initializeParticles();
    if (document.hidden) {
      dashboardRoot.classList.add("is-paused");
      return;
    }
    startParticleAnimation();
    loadDashboard().finally(startDashboardRefresh);
  }

  window.WarehouseDashboard = { mount, unmount };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      if (document.querySelector("[data-dashboard-page]")) mount();
    }, { once: true });
  } else if (document.querySelector("[data-dashboard-page]")) {
    mount();
  }
})();
