(() => {
  function create({ system, api, formatQty, onInventoryChanged }) {
    const state = { page: 1, pages: 1, status: "", assignedToMe: false, loading: false };
    const escape = value => window.escapeHtml(value ?? "");
    const canProcess = () => Boolean(system.boot?.user_permissions?.process_temporary_transfer);
    const canRequest = () => Boolean(system.boot?.user_permissions?.transfer_temporary_inventory);
    const featureEnabled = () => Boolean(system.boot?.workflow_settings?.temporary_inventory_enabled);
    const enabled = () => featureEnabled() && (canProcess() || canRequest());
    const requestKey = prefix => `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    const labels = {
      awaiting_purchase: "等待采购处理",
      acceptance_in_progress: "验收处理中",
      acceptance_failed: "验收失败",
      formal_inbound_partial: "正式入库不足",
      formal_inbound_complete: "正式入库完成",
      reserving: "正在预留正式库存",
      auto_claim_creating: "正在创建自动领用",
      auto_claim_pending: "等待自动领用完成",
      auto_claim_exception: "自动领用异常",
      completed: "已完成",
      paused: "已暂停",
      exception: "异常",
      cancelled: "已取消",
    };

    function clear() {
      state.page = 1;
      state.pages = 1;
      state.status = "";
      state.assignedToMe = false;
    }

    function badge(task) {
      const status = task.status || "";
      return `<span class="transfer-status transfer-status-${escape(status)}">${escape(task.status_label || labels[status] || status)}</span>`;
    }

    function render(main) {
      if (!enabled()) {
        clear();
        main.innerHTML = '<div class="work-panel"><div class="work-body"><div class="empty-state">临时库功能已关闭或当前账号无转移任务权限</div></div></div>';
        return;
      }
      main.innerHTML = `
        <div class="module-title"><div><h1>临时物料转正式库</h1><p>采购认领、验收关联与正式入库进度</p></div></div>
        <div class="work-panel transfer-panel">
          <div class="work-head"><h2>转移任务</h2><button id="transferRefresh">刷新</button></div>
          <div class="work-body">
            <div class="temporary-filters">
              <label>状态<select id="transferStatus"><option value="">全部状态</option>
                ${Object.entries(labels).map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}
              </select></label>
              ${canProcess() ? '<label class="compact-check"><input id="transferAssigned" type="checkbox"> 只看分配给我</label>' : ""}
              <button id="transferSearch" class="primary">查询</button>
            </div>
            <div id="transferTaskResult"><div class="empty-state">加载中...</div></div>
          </div>
        </div>`;
      document.getElementById("transferStatus").value = state.status;
      if (document.getElementById("transferAssigned")) {
        document.getElementById("transferAssigned").checked = state.assignedToMe;
      }
      document.getElementById("transferRefresh").addEventListener("click", loadTasks);
      document.getElementById("transferSearch").addEventListener("click", () => {
        state.status = document.getElementById("transferStatus").value;
        state.assignedToMe = Boolean(document.getElementById("transferAssigned")?.checked);
        state.page = 1;
        loadTasks();
      });
      loadTasks();
    }

    async function loadTasks() {
      if (!enabled() || state.loading) return;
      state.loading = true;
      const host = document.getElementById("transferTaskResult");
      if (host) host.innerHTML = '<div class="empty-state">加载中...</div>';
      try {
        const query = new URLSearchParams({
          page: String(state.page),
          page_size: "20",
          status: state.status,
          assigned_to_me: state.assignedToMe ? "1" : "0",
        });
        const result = await api(`/api/temporary-inventory/transfers?${query}`);
        state.pages = Number(result.pages || 1);
        renderRows(host, result);
      } catch (error) {
        if (host) host.innerHTML = `<div class="empty-state">${escape(error.message)}</div>`;
      } finally {
        state.loading = false;
      }
    }

    function actions(task) {
      const allowed = new Set(task.available_actions || []);
      const id = Number(task.id);
      return [
        allowed.has("claim") ? `<button class="primary" data-transfer-action="claim" data-transfer-id="${id}">认领</button>` : "",
        allowed.has("start_acceptance") ? `<button class="primary" data-transfer-action="start_acceptance" data-transfer-id="${id}">发起验收</button>` : "",
        allowed.has("cancel") ? `<button data-transfer-action="cancel" data-transfer-id="${id}">取消</button>` : "",
        allowed.has("retry") ? `<button data-transfer-action="retry" data-transfer-id="${id}">重试同步</button>` : "",
        allowed.has("process_auto_claims") ? `<button class="primary" data-transfer-action="process_auto_claims" data-transfer-id="${id}">处理自动领用</button>` : "",
        allowed.has("retry_auto_claims") ? `<button data-transfer-action="retry_auto_claims" data-transfer-id="${id}">重试自动领用</button>` : "",
      ].join("");
    }

    function renderRows(host, result) {
      if (!host) return;
      host.innerHTML = `
        <div class="table-scroll"><table class="flow-table transfer-table">
          <thead><tr><th>转移编号</th><th>物料</th><th>临时现存</th><th>待结算</th><th>目标 / 已入库</th><th>采购处理人</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>${(result.items || []).map(task => `<tr>
            <td>${escape(task.transfer_no)}</td>
            <td><strong>${escape(task.material_name)}</strong><small>${escape(task.material_code)} · ${escape(task.spec || task.brand_model || "")}</small></td>
            <td>${formatQty(task.temporary_quantity_snapshot)}</td>
            <td>${formatQty(task.obligation_quantity_snapshot)}</td>
            <td>${formatQty(task.target_acceptance_quantity)} / ${formatQty(task.accepted_quantity)}</td>
            <td>${escape(task.assigned_buyer_name || "待认领")}</td>
            <td>${badge(task)}</td>
            <td class="mini-actions"><button data-transfer-detail="${Number(task.id)}">详情</button>${actions(task)}</td>
          </tr>`).join("") || '<tr><td colspan="8">暂无转移任务</td></tr>'}</tbody>
        </table></div>
        <div class="temporary-pagination">
          <span>共 ${Number(result.total || 0)} 项</span>
          <button id="transferPrev" ${state.page <= 1 ? "disabled" : ""}>上一页</button>
          <span>${state.page} / ${state.pages}</span>
          <button id="transferNext" ${state.page >= state.pages ? "disabled" : ""}>下一页</button>
        </div>`;
      host.querySelectorAll("[data-transfer-detail]").forEach(button => {
        button.addEventListener("click", () => openDetail(Number(button.dataset.transferDetail)));
      });
      host.querySelectorAll("[data-transfer-action]").forEach(button => {
        button.addEventListener("click", () => runAction(button.dataset.transferAction, Number(button.dataset.transferId), button));
      });
      document.getElementById("transferPrev")?.addEventListener("click", () => {
        state.page = Math.max(1, state.page - 1);
        loadTasks();
      });
      document.getElementById("transferNext")?.addEventListener("click", () => {
        state.page = Math.min(state.pages, state.page + 1);
        loadTasks();
      });
    }

    async function openCreate(row) {
      if (!featureEnabled() || !canRequest()) {
        window.toast("当前账号无权发起转移");
        return;
      }
      const result = await api(`/api/temporary-inventory/transfers/preview?material_id=${Number(row.material_id)}`);
      const preview = result.preview;
      const blocked = preview.has_active_temporary_borrows
        ? "存在未归还临时借用，暂不能转移"
        : (preview.active_transfer_task_id ? "该物料已有进行中的转移任务" : "");
      window.openModal({
        title: "转移到正式库",
        body: `<div class="transfer-confirm">
          <div><span>物料</span><strong>${escape(preview.material.material_code)} · ${escape(preview.material.name)}</strong></div>
          <div><span>临时现存数量</span><strong>${formatQty(preview.temporary_quantity)}</strong></div>
          <div><span>历史待结算领用量</span><strong>${formatQty(preview.obligation_quantity)}</strong></div>
          <div class="transfer-total"><span>系统目标验收数量</span><strong>${formatQty(preview.target_acceptance_quantity)}</strong></div>
          <p class="${blocked ? "transfer-warning" : "transfer-ok"}">${escape(blocked || "未发现未归还临时借用，可以发起转移。")}</p>
        </div>`,
        okText: "确认发起",
        hideOk: !preview.can_transfer,
        onOk: async () => {
          const created = await api("/api/temporary-inventory/transfers", {
            method: "POST",
            body: JSON.stringify({
              material_id: Number(row.material_id),
              idempotency_key: requestKey(`temporary-transfer-${Number(row.material_id)}`),
            }),
          });
          window.toast(`转移任务 ${created.task.transfer_no} 已创建并通知采购`);
          await Promise.resolve(onInventoryChanged?.());
        },
      });
    }

    async function openDetail(taskId) {
      const [result, settlementResult] = await Promise.all([
        api(`/api/temporary-inventory/transfers/${taskId}`),
        api(`/api/temporary-inventory/transfers/${taskId}/settlement`),
      ]);
      const task = result.task;
      const settlement = settlementResult.settlement || {};
      window.openModal({
        title: `转移任务 ${escape(task.transfer_no)}`,
        hideOk: true,
        body: `<div class="transfer-detail-grid">
          <div><span>物料</span><strong>${escape(task.material_code)} · ${escape(task.material_name)}</strong></div>
          <div><span>状态</span>${badge(task)}</div>
          <div><span>临时现存快照</span><strong>${formatQty(task.temporary_quantity_snapshot)}</strong></div>
          <div><span>待结算快照</span><strong>${formatQty(task.obligation_quantity_snapshot)}</strong></div>
          <div><span>目标验收</span><strong>${formatQty(task.target_acceptance_quantity)}</strong></div>
          <div><span>已正式入库</span><strong>${formatQty(task.accepted_quantity)}</strong></div>
          <div><span>采购处理人</span><strong>${escape(task.assigned_buyer_name || "待认领")}</strong></div>
          <div><span>发起人</span><strong>${escape(task.requested_by_name || "")}</strong></div>
          <div class="wide"><span>异常 / 说明</span><strong>${escape(task.error_message || "-")}</strong></div>
        </div><h3>关联验收</h3>
        <div class="transfer-link-list">${(task.acceptance_links || []).map(link => `
          <div><span>${escape(link.acceptance_form_no)}</span><span>${escape(link.acceptance_form_status)}</span><strong>${formatQty(link.linked_quantity)}</strong></div>
        `).join("") || '<div class="empty-state">尚未发起验收</div>'}</div>
        <h3>后续结算</h3>
        <div class="transfer-link-list">
          <div><span>正式预留</span><strong>${formatQty(settlement.reservation_totals?.reserved_quantity)}</strong><span>已消耗 ${formatQty(settlement.reservation_totals?.consumed_quantity)}</span></div>
          ${(settlement.auto_claims || []).map(claim => `<div>
            <span>${escape(claim.applicant_name)}</span>
            <strong>${formatQty(claim.quantity)}</strong>
            <span>${escape(claim.status)} · ${escape(claim.form_no || "待创建流程")}</span>
          </div>`).join("") || '<div class="empty-state">无需创建历史领用结算流程</div>'}
          <div><span>临时批次</span><strong>${settlement.temporary_batches_closed ? "已关闭" : "保持锁定"}</strong></div>
        </div>`,
      });
    }

    async function runAction(action, taskId, button) {
      if (button.disabled) return;
      button.disabled = true;
      try {
        if (action === "claim") {
          await api(`/api/temporary-inventory/transfers/${taskId}/claim`, { method: "POST", body: "{}" });
          window.toast("任务已认领");
        } else if (action === "start_acceptance") {
          openAcceptance(taskId);
          return;
        } else if (action === "cancel") {
          openCancel(taskId);
          return;
        } else if (action === "retry") {
          await api(`/api/temporary-inventory/transfers/${taskId}/retry`, { method: "POST", body: "{}" });
          window.toast("任务状态已同步");
        } else if (action === "process_auto_claims") {
          await api(`/api/temporary-inventory/transfers/${taskId}/process-auto-claims`, { method: "POST", body: "{}" });
          window.toast("自动领用结算已开始处理");
        } else if (action === "retry_auto_claims") {
          await api(`/api/temporary-inventory/transfers/${taskId}/retry-auto-claims`, { method: "POST", body: "{}" });
          window.toast("自动领用流程已重新创建");
        }
        await loadTasks();
      } finally {
        button.disabled = false;
      }
    }

    function validatorOptions() {
      const currentUserId = Number(system.boot?.user?.id || 0);
      return (system.boot?.users || []).map(user => {
        const selected = Number(user.id) === currentUserId ? "selected" : "";
        return `<option value="${Number(user.id)}" ${selected}>${escape(user.display_name)} · ${escape(user.department || "未分部门")}</option>`;
      }).join("");
    }

    function openAcceptance(taskId) {
      window.openModal({
        title: "发起关联验收",
        body: `<div class="form-grid temporary-form">
          <label>验收单价<input id="transferAcceptancePrice" type="number" min="0" step="0.0001" value="0"></label>
          <label class="wide">验收人员<select id="transferAcceptanceValidators" multiple size="5">${validatorOptions()}</select></label>
          <label class="wide">采购申请人<input id="transferPurchaseApplicant" value="${escape(system.boot?.user?.display_name || "")}"></label>
        </div><p class="hint">物料和验收数量由转移任务固定，提交值不能覆盖系统计算结果。</p>`,
        okText: "创建验收流程",
        onOk: async () => {
          const validatorIds = [...document.getElementById("transferAcceptanceValidators").selectedOptions]
            .map(option => Number(option.value)).filter(Boolean);
          const result = await api(`/api/temporary-inventory/transfers/${taskId}/start-acceptance`, {
            method: "POST",
            body: JSON.stringify({
              idempotency_key: requestKey(`transfer-acceptance-${taskId}`),
              validator_ids: validatorIds,
              unit_price: document.getElementById("transferAcceptancePrice").value,
              purchase_applicant: document.getElementById("transferPurchaseApplicant").value,
            }),
          });
          window.toast(`验收流程 ${result.task.acceptance_form_no || ""} 已创建`);
          await loadTasks();
        },
      });
    }

    function openCancel(taskId) {
      window.openModal({
        title: "取消转移任务",
        body: '<label>取消原因<textarea id="transferCancelReason" placeholder="请填写取消原因"></textarea></label>',
        okText: "确认取消",
        onOk: async () => {
          await api(`/api/temporary-inventory/transfers/${taskId}/cancel`, {
            method: "POST",
            body: JSON.stringify({ reason: document.getElementById("transferCancelReason").value }),
          });
          window.toast("转移任务已取消，临时批次已解锁");
          await Promise.all([loadTasks(), Promise.resolve(onInventoryChanged?.())]);
        },
      });
    }

    return { render, clear, enabled, openCreate, openDetail, loadTasks };
  }

  window.WarehouseTemporaryTransfer = { create };
})();
