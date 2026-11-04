(() => {
  const api = (url, options = {}) => fetch(url, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options
  }).then(async response => {
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.success === false) throw new Error(data.error || "操作失败");
    return data;
  });
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const qty = value => Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 4 });
  const showError = error => window.alert(error?.message || String(error));

  const itemTypeLabel = type => ({ material: "物料", semifinished: "半成品", finished: "成品" }[type] || type || "");
  const itemLabel = row => [row.item_name || row.name, row.item_code || row.material_code, row.brand_model, row.spec]
    .filter(value => String(value || "").trim()).join(" · ");

  function openPageModal({ title, body, okText, onOk }) {
    const modal = document.getElementById("modal");
    if (!modal) throw new Error("页面弹窗未初始化");
    const close = () => { modal.classList.remove("open"); modal.innerHTML = ""; modal.onclick = null; };
    modal.innerHTML = `<div class="dialog" role="dialog" aria-modal="true" aria-label="${esc(title)}"><div class="dialog-head"><h3>${esc(title)}</h3><button type="button" id="pageModalClose" aria-label="关闭">×</button></div><div class="dialog-body">${body}<p class="hint" id="pageModalError" hidden></p></div><div class="dialog-foot"><button type="button" id="pageModalCancel">取消</button><button type="button" class="primary" id="pageModalConfirm">${esc(okText || "确认")}</button></div></div>`;
    modal.classList.add("open");
    modal.onclick = event => { if (event.target === modal) close(); };
    modal.querySelector("#pageModalClose").addEventListener("click", close);
    modal.querySelector("#pageModalCancel").addEventListener("click", close);
    modal.querySelector("#pageModalConfirm").addEventListener("click", async event => {
      const button = event.currentTarget;
      const errorBox = modal.querySelector("#pageModalError");
      try {
        button.disabled = true;
        await onOk();
        close();
      } catch (error) {
        errorBox.hidden = false;
        errorBox.textContent = error?.message || String(error);
        button.disabled = false;
      }
    });
  }
  function bindChoiceSearch({ inputId, hiddenId, resultsId, endpoint, queryKey, queryExtra, responseKey, filter, onSelect, onClear }) {
    const input = document.getElementById(inputId);
    const hidden = document.getElementById(hiddenId);
    const results = document.getElementById(resultsId);
    let timer = null;
    let requestId = 0;
    let selectedRow = null;
    const hide = () => { results.style.display = "none"; results.innerHTML = ""; };
    const position = count => {
      const rect = input.getBoundingClientRect();
      const maxWidth = Math.min(Math.max(rect.width, 420), window.innerWidth - 24);
      results.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - maxWidth - 12))}px`;
      results.style.top = `${Math.min(rect.bottom + 4, window.innerHeight - 120)}px`;
      results.style.width = `${maxWidth}px`;
      results.style.maxHeight = `${Math.min(Math.max(180, count * 58 + 8), Math.max(window.innerHeight * 0.72, 260))}px`;
      results.style.display = "block";
    };
    const clear = () => { selectedRow = null; hidden.value = ""; hide(); if (onClear) onClear(); };
    const run = async () => {
      const keyword = input.value.trim();
      const currentRequest = ++requestId;
      results.textContent = "搜索中...";
      results.style.display = "block";
      try {
        const extra = queryExtra ? "&" + queryExtra() : ""; const data = await api(`${endpoint}?${queryKey}=${encodeURIComponent(keyword)}${extra}`);
        if (currentRequest !== requestId) return;
        const rows = (Array.isArray(data) ? data : (data[responseKey] || [])).filter(row => !filter || filter(row));
        if (!rows.length) { results.textContent = "没有匹配的物料"; return; }
        results.innerHTML = rows.slice(0, 100).map((row, index) => {
          const code = row.item_code || row.material_code || "";
          const name = row.item_name || row.name || "";
          const detail = [row.brand_model, row.spec, row.purchase_applicant ? `采购申请人 ${row.purchase_applicant}` : "", row.item_type ? itemTypeLabel(row.item_type) : "", row.available_quantity != null ? `可用 ${qty(row.available_quantity)} ${row.unit || ""}` : ""].filter(value => String(value || "").trim()).join(" · ");
          return `<button class="material-option" type="button" data-choice-index="${index}">${esc(code)} · ${esc(name)}<small>${esc(detail)}</small></button>`;
        }).join("");
        position(Math.min(rows.length, 100));
        results.querySelectorAll("[data-choice-index]").forEach(button => button.addEventListener("click", () => {
          const row = rows[Number(button.dataset.choiceIndex)];
          selectedRow = row;
          hidden.value = row.id ?? row.item_ref_id ?? row.material_id ?? "";
          input.value = row.item_name || row.name || "";
          hide();
          if (onSelect) onSelect(row);
        }));
      } catch (error) {
        if (currentRequest === requestId) { results.textContent = error.message; results.style.display = "block"; }
      }
    };
    input.addEventListener("input", () => { clear(); clearTimeout(timer); timer = window.setTimeout(run, 220); });
    input.addEventListener("focus", run);
    input.addEventListener("keydown", event => { if (event.key === "Escape") clear(); });
    document.addEventListener("click", event => { if (!input.contains(event.target) && !results.contains(event.target)) hide(); });
    // 草稿回填已选物品，不重新搜索
    const restore = (row) => {
      if (!row) {
        clear();
        return;
      }
      selectedRow = row;
      hidden.value = row.id ?? row.item_ref_id ?? row.material_id ?? "";
      input.value = row.item_name || row.name || "";
      hide();
      if (onSelect) onSelect(row);
    };
    return { clear, run, selected: () => selectedRow, restore };
  }
  async function renderDefectiveInventory(main) {
    main.innerHTML = `<div class="module-content" data-page-title="不良品物料"><div class="work-panel"><div class="work-head"><h2>转入不良品</h2><button id="refreshDefective">刷新</button></div><div class="work-body"><div class="form-grid"><label class="material-search extended-material-choice">物料名称/编号/品牌型号/技术规格<input id="defectiveMaterialSearch" autocomplete="off" placeholder="输入关键词近似搜索"><input id="defectiveMaterialId" type="hidden"><div class="material-results" id="defectiveMaterialResults"></div></label><label>转移数量<input id="defectiveQuantity" type="number" min="0.0001" step="any"></label><label class="wide">原因<input id="defectiveReason" placeholder="报废、损坏或质量异常"></label><div class="wide submit-flow-actions"><button class="primary flow-submit-button" id="transferDefective">转入不良品</button></div></div></div></div><div class="work-panel"><div class="work-head"><h2>不良品台账</h2></div><div class="work-body" id="defectiveList">加载中...</div></div></div>`;
    const selector = bindChoiceSearch({ inputId: "defectiveMaterialSearch", hiddenId: "defectiveMaterialId", resultsId: "defectiveMaterialResults", endpoint: "/api/materials/search", queryKey: "keyword", responseKey: "items" });
    const load = async () => {
      try {
        const data = await api("/api/defective-inventory");
        const rows = data.items || [];
        document.getElementById("defectiveList").innerHTML = rows.length ? `<div class="table-wrapper"><table class="flow-table"><thead><tr><th>类型</th><th>编号</th><th>物料</th><th>剩余数量</th><th>原因</th><th>处理</th></tr></thead><tbody>${rows.map(row => `<tr><td><span class="flow-type-badge type-transfer">${esc(itemTypeLabel(row.item_type))}</span></td><td>${esc(row.item_code)}</td><td><strong>${esc(row.item_name)}</strong></td><td>${qty(row.remaining_quantity)} ${esc(row.unit)}</td><td>${esc(row.reason)}</td><td>${Number.isInteger(Number(row.id)) && row.remaining_quantity > 0 ? `<button class="handle-button" type="button" data-dispose="${esc(row.id)}">处置</button>` : `<span class="status-badge status-completed">已结案</span>`}</td></tr>`).join("")}</tbody></table></div>` : "暂无不良品记录";
        document.querySelectorAll("[data-dispose]").forEach(button => button.addEventListener("click", () => {
          const recordId = button.dataset.dispose;
          openPageModal({
            title: "处置不良品",
            okText: "确认处置",
            body: `<div class="form-grid"><label>处置方式<select id="defectiveDisposalAction"><option value="repair">修复后回库</option><option value="scrap">报废</option><option value="source_return">退回来源</option></select></label><label class="wide">处置原因<textarea id="defectiveDisposalReason" rows="3" placeholder="请填写本次处置原因"></textarea></label></div>`,
            onOk: async () => {
              const action = document.getElementById("defectiveDisposalAction").value;
              const reason = document.getElementById("defectiveDisposalReason").value.trim();
              if (!reason) throw new Error("请填写处置原因");
              await api(`/api/defective-inventory/${encodeURIComponent(recordId)}/dispositions`, { method: "POST", body: JSON.stringify({ action, reason }) });
              await load();
            }
          });
        }));
      } catch (error) { document.getElementById("defectiveList").textContent = error.message; }
    };
    document.getElementById("refreshDefective").addEventListener("click", load);
    document.getElementById("transferDefective").addEventListener("click", async () => {
      try {
        if (!document.getElementById("defectiveMaterialId").value) throw new Error("请先搜索并选择物料");
        await api("/api/defective-inventory/transfers", { method: "POST", body: JSON.stringify({ material_id: document.getElementById("defectiveMaterialId").value, quantity: document.getElementById("defectiveQuantity").value, reason: document.getElementById("defectiveReason").value }) });
        selector.clear();
        await load();
      } catch (error) { showError(error); }
    });
    await load();
  }

  async function renderCommonMaterials(main) {
    const lowStockOnly = new URLSearchParams(window.location.search).get("low_stock") === "1";
    main.innerHTML = `<div class="module-content" data-page-title="常用物料申请"><div class="work-panel"><div class="work-head"><h2>发起申请</h2><button id="refreshCommonMaterials">刷新</button></div><div class="work-body"><div class="form-grid"><label class="material-search extended-material-choice">物料名称/编号/品牌型号/技术规格<input id="commonMaterialSearch" autocomplete="off" placeholder="输入关键词近似搜索"><input id="commonMaterialId" type="hidden"><div class="material-results" id="commonMaterialResults"></div></label><label>预警数量<input id="commonWarningQuantity" type="number" min="0.0001" step="any"></label><label class="wide">申请理由<input id="commonReason"></label><div class="wide submit-flow-actions"><button class="primary flow-submit-button" id="submitCommonMaterial">提交申请</button></div></div></div></div><div class="work-panel"><div class="work-head"><h2>${lowStockOnly ? "低库存常用物料" : "已启用常用物料"}</h2></div><div class="work-body" id="commonMaterialList">加载中...</div></div></div>`;
    const selector = bindChoiceSearch({ inputId: "commonMaterialSearch", hiddenId: "commonMaterialId", resultsId: "commonMaterialResults", endpoint: "/api/materials/search", queryKey: "keyword", responseKey: "items" });
    const load = async () => {
      try {
        const data = await api("/api/common-materials");
        const rows = (data.items || []).filter(row => !lowStockOnly || row.below_warning);
        document.getElementById("commonMaterialList").innerHTML = rows.length ? `<div class="table-wrapper"><table class="flow-table"><thead><tr><th>编号</th><th>物料</th><th>当前正式可用</th><th>预警数量</th><th>负责人</th><th>状态</th><th>操作</th></tr></thead><tbody>${rows.map(row => `<tr><td>${esc(row.material_code)}</td><td><strong>${esc(row.name)}</strong></td><td>${qty(row.current_quantity)} ${esc(row.unit)}</td><td>${qty(row.warning_quantity)} ${esc(row.unit)}</td><td>${esc(row.owner_name)}</td><td><span class="status-badge ${row.below_warning ? "status-transfer-warning" : "status-completed"}">${row.below_warning ? "低库存" : "库存正常"}</span></td><td class="mini-actions"><button type="button" data-close-common="${row.id}">关闭</button><button type="button" class="danger" data-delete-common="${row.id}">删除</button></td></tr>`).join("")}</tbody></table></div>` : (lowStockOnly ? "暂无低库存常用物料" : "暂无常用物料");
        document.querySelectorAll("[data-close-common]").forEach(button => button.addEventListener("click", async () => {
          if (!window.confirm("确认关闭该常用物料？")) return;
          try {
            await api(`/api/common-materials/${button.dataset.closeCommon}/close`, { method: "POST", body: JSON.stringify({}) });
            await load();
          } catch (error) { showError(error); }
        }));
        document.querySelectorAll("[data-delete-common]").forEach(button => button.addEventListener("click", async () => {
          if (!window.confirm("确认删除该常用物料？")) return;
          try {
            await api(`/api/common-materials/${button.dataset.deleteCommon}`, { method: "DELETE" });
            await load();
          } catch (error) { showError(error); }
        }));
      } catch (error) { document.getElementById("commonMaterialList").textContent = error.message; }
    };
    document.getElementById("refreshCommonMaterials").addEventListener("click", load);
    document.getElementById("submitCommonMaterial").addEventListener("click", async () => {
      try {
        if (!document.getElementById("commonMaterialId").value) throw new Error("请先搜索并选择物料");
        await api("/api/common-material-applications", { method: "POST", body: JSON.stringify({ material_id: document.getElementById("commonMaterialId").value, warning_quantity: document.getElementById("commonWarningQuantity").value, reason: document.getElementById("commonReason").value }) });
        window.alert("申请已提交");
        selector.clear();
        await load();
      } catch (error) { showError(error); }
    });
    await load();
  }

  async function renderSupplies(main) {
    main.innerHTML = `<div class="module-content" data-page-title="供货申请"><div class="work-panel"><div class="work-head"><h2>发起供货</h2><button id="refreshSupplies">刷新</button></div><div class="work-body"><div class="form-grid"><label>收货公司<input id="supplyCompany"></label><label>收货姓名<input id="supplyName"></label><label>收货电话<input id="supplyPhone"></label><label>供货方式<select id="supplyMethod"><option value="试用">试用</option><option value="销售">销售</option></select></label><label>状态<select id="supplyGoodsStatus"><option value="">请选择</option><option>样机</option><option>产品</option><option>备件</option><option>展品</option></select></label><label>申请人电话<input id="supplyContactPhone"></label><label>预计结清日期（结清货款或归还设备）<input id="supplyCloseDate" type="date"></label><label class="wide">收货地址<input id="supplyAddress"></label><label>物品类型<select id="supplyItemType"><option value="material">物料</option><option value="semifinished">半成品</option><option value="finished">成品</option></select></label><label class="material-search extended-material-choice">物料名称/编号/品牌型号/技术规格<input id="supplyItemSearch" autocomplete="off" placeholder="输入编号/名称/品牌型号/技术规格"><input id="supplyItemRef" type="hidden"><div class="material-results" id="supplyItemResults"></div></label><label>物料编号<input id="supplyItemCode" readonly></label><label>品牌型号<input id="supplyItemBrand" readonly></label><label>技术规格<input id="supplyItemSpec" readonly></label><label>寄件数量<input id="supplyQuantity" type="number" min="0.0001" step="any"></label><label class="wide">申请理由<input id="supplyReason"></label><div class="wide submit-flow-actions"><button class="primary flow-submit-button" id="submitSupply">提交供货申请</button><button type="button" class="primary flow-submit-button" id="saveSupplyDraft">保存草稿</button></div></div></div></div><div class="work-panel"><div class="work-head"><h2>供货台账</h2></div><div class="work-body" id="supplyList">加载中...</div></div></div>`;
    const drafts = window.WarehouseDrafts;
    const hydrate = drafts?.consumeHydrate?.("supply");
    const itemType = document.getElementById("supplyItemType");
    const selector = bindChoiceSearch({ inputId: "supplyItemSearch", hiddenId: "supplyItemRef", resultsId: "supplyItemResults", endpoint: "/api/supplies/item-choices", queryKey: "q", queryExtra: () => "item_type=" + encodeURIComponent(itemType.value), responseKey: "items", filter: row => row.item_type === itemType.value, onSelect: row => { itemType.value = row.item_type; document.getElementById("supplyItemCode").value = row.item_code || row.material_code || ""; document.getElementById("supplyItemBrand").value = row.brand_model || ""; document.getElementById("supplyItemSpec").value = row.spec || ""; }, onClear: () => { document.getElementById("supplyItemCode").value = ""; document.getElementById("supplyItemBrand").value = ""; document.getElementById("supplyItemSpec").value = ""; } });
    const clearSelectedItem = () => selector.clear();
    itemType.addEventListener("change", clearSelectedItem);
    if (hydrate) {
      const recipient = hydrate.recipient && typeof hydrate.recipient === "object" ? hydrate.recipient : {};
      document.getElementById("supplyCompany").value = recipient.company || hydrate.company || "";
      document.getElementById("supplyName").value = recipient.name || "";
      document.getElementById("supplyPhone").value = recipient.phone || "";
      document.getElementById("supplyAddress").value = recipient.address || "";
      document.getElementById("supplyCloseDate").value = String(hydrate.expected_close_date || "").slice(0, 10);
      document.getElementById("supplyReason").value = hydrate.reason || "";
      document.getElementById("supplyContactPhone").value = hydrate.contact_phone || "";
      if (hydrate.supply_method) document.getElementById("supplyMethod").value = hydrate.supply_method;
      document.getElementById("supplyGoodsStatus").value = hydrate.goods_status || "";
      const item = Array.isArray(hydrate.items) ? hydrate.items[0] : null;
      if (item) {
        if (item.item_type) itemType.value = item.item_type;
        document.getElementById("supplyQuantity").value = item.quantity || "";
        if (item.goods_status) document.getElementById("supplyGoodsStatus").value = item.goods_status;
        const refId = Number(item.item_ref_id || 0);
        if (refId) {
          selector.restore({
            item_type: item.item_type || itemType.value,
            item_ref_id: refId,
            item_name: item.item_name || item.name || "",
            item_code: item.item_code || item.material_code || "",
            brand_model: item.brand_model || "",
            spec: item.spec || "",
          });
        } else if (item.item_name) {
          document.getElementById("supplyItemSearch").value = item.item_name;
        }
      }
    }
    const load = async () => {
      try {
        const data = await api("/api/supplies");
        const rows = data.items || [];
        document.getElementById("supplyList").innerHTML = rows.length ? `<div class="table-wrap"><table><thead><tr><th>单号</th><th>收货方</th><th>状态</th><th>供货方式</th><th>预计结清日期（结清货款或归还设备）</th><th>明细</th><th>操作</th></tr></thead><tbody>${rows.map(row => {
          const method = row.supply_method || "试用";
          const action = method === "销售"
            ? (row.status === "external_open" ? `<button type="button" data-supply-payment="${esc(row.form_id)}">货款结清</button>` : "")
            : (row.status === "shipping" || row.status === "external_open" ? `<button type="button" data-supply-return="${esc(row.form_id)}">登记回寄</button>` : "");
          const statusText = ({ pending: "待处理", shipping: "出库中", voucher: "交接凭证", external_open: "外部待结清", completed: "已办结", rejected: "已驳回", cancelled: "已取消", leader_supply: "供货审批", supply_handover_print: "打印回执单", supply_outbound: "供货出库", supply_voucher_upload: "上传交接凭证", supply_voucher_review: "查阅交接凭证" })[row.form_status || row.status] || row.form_status || row.status || "";
          return `<tr><td>${esc(row.form_no)}</td><td>${esc(row.recipient_company)} / ${esc(row.recipient_name)} / ${esc(row.recipient_phone)}</td><td>${esc(statusText)}</td><td>${esc(method)}</td><td>${esc(row.expected_close_date)}</td><td>${(row.items || []).map(item => `${esc(item.item_name)}${item.goods_status ? `（${esc(item.goods_status)}）` : ""} ${qty(item.shipped_quantity)}/${qty(item.approved_quantity)}，待结 ${qty(item.outstanding_quantity)}`).join("<br>")}</td><td>${action}</td></tr>`;
        }).join("")}</tbody></table></div>` : "暂无供货记录";
        document.querySelectorAll("[data-supply-return]").forEach(button => button.addEventListener("click", () => {
          const order = rows.find(row => Number(row.form_id) === Number(button.dataset.supplyReturn));
          if (order && window.openSupplyTrialReturn) window.openSupplyTrialReturn(order);
        }));
        document.querySelectorAll("[data-supply-payment]").forEach(button => button.addEventListener("click", () => {
          const order = rows.find(row => Number(row.form_id) === Number(button.dataset.supplyPayment));
          if (order && window.openSupplyPaymentSettlement) window.openSupplyPaymentSettlement(order);
        }));
      } catch (error) { document.getElementById("supplyList").textContent = error.message; }
    };
    document.getElementById("refreshSupplies").addEventListener("click", load);
    document.getElementById("saveSupplyDraft").addEventListener("click", () => {
      if (!drafts?.saveCurrentDraft) {
        showError(new Error("草稿箱未初始化"));
        return;
      }
      drafts.saveCurrentDraft("supply").catch(showError);
    });
    document.getElementById("submitSupply").addEventListener("click", async () => {
      try {
        const selectedItem = selector.selected();
        if (!selectedItem || selectedItem.item_type !== itemType.value) throw new Error("请先从搜索结果中选择物品");
        const goodsStatus = document.getElementById("supplyGoodsStatus").value;
        const contactPhone = document.getElementById("supplyContactPhone").value.trim();
        if (!goodsStatus) throw new Error("请选择货物状态");
        if (!contactPhone) throw new Error("请填写申请人电话");
        const payload = {
          recipient: {
            company: document.getElementById("supplyCompany").value,
            name: document.getElementById("supplyName").value,
            phone: document.getElementById("supplyPhone").value,
            address: document.getElementById("supplyAddress").value,
          },
          supply_method: document.getElementById("supplyMethod").value,
          contact_phone: contactPhone,
          expected_close_date: document.getElementById("supplyCloseDate").value,
          reason: document.getElementById("supplyReason").value,
          items: [{
            item_type: itemType.value,
            item_ref_id: Number(selectedItem.item_ref_id),
            quantity: Number(document.getElementById("supplyQuantity").value),
            goods_status: goodsStatus,
            item_name: selectedItem.item_name || selectedItem.name || "",
            item_code: selectedItem.item_code || selectedItem.material_code || "",
            brand_model: selectedItem.brand_model || "",
            spec: selectedItem.spec || "",
          }],
        };
        if (drafts?.submitWorkflowPayload) {
          await drafts.submitWorkflowPayload("supply", "/api/supplies", payload);
        } else {
          await api("/api/supplies", { method: "POST", body: JSON.stringify(payload) });
        }
        window.alert("申请已提交");
        clearSelectedItem();
        await load();
      } catch (error) { showError(error); }
    });
    await load();
  }

  window.WarehouseExtendedViews = { renderDefectiveInventory, renderCommonMaterials, renderSupplies };
})();