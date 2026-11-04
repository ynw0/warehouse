(() => {
  function create({ system, api, formatQty, moduleShell }) {
    const state = {
      page: 1,
      pageSize: 20,
      requestPageSize: 100,
      rows: [],
      records: [],
      materialChoices: [],
      loading: false,
      rawRows: [],
      listControlState: { sort: "code", filters: { hideZero: true } },
    };

    const escape = value => window.escapeHtml(value ?? "");
    const attr = value => escape(value).replace(/`/g, "&#96;");
    const canManage = () => Boolean(system.boot?.user_permissions?.manage_temporary_inventory);
    const canTransfer = () => Boolean(system.boot?.user_permissions?.transfer_temporary_inventory);
    const enabled = () => Boolean(
      system.boot?.workflow_settings?.temporary_inventory_enabled
      && system.boot?.user_permissions?.view_temporary_inventory
    );
    const operationKey = prefix => `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;

    function clear() {
      state.page = 1;
      state.rows = [];
      state.records = [];
      state.materialChoices = [];
    }

    function reloadRows() {
      state.page = 1;
      loadRows();
    }

    function render(main) {
      if (!enabled()) {
        clear();
        main.innerHTML = '<div class="work-panel"><div class="work-body"><div class="empty-state">临时库功能已关闭或当前账号无查看权限</div></div></div>';
        return;
      }
      main.innerHTML = moduleShell("临时库管理", `
        <div class="work-panel">
          <div class="work-head"><h2>查询临时物料和批次</h2>${canManage() ? '<button id="temporaryAddBatch" class="primary">新增临时批次</button>' : ""}</div>
          <div class="work-body">
            <div class="form-grid">
              <label class="wide">关键词<input id="temporaryKeyword" placeholder="物料编号 / 名称 / 品牌型号 / 技术规格 / 采购申请人"></label>
              <div class="wide inventory-query-actions">
                <button id="temporarySearch" class="primary query-search-button">查询</button>
                ${window.WarehouseInventoryListControls?.toolbarHtml("temporaryInventory") || ""}
              </div>
            </div>
            <div id="temporaryInventoryResult"></div>
          </div>
        </div>
      `);
      document.getElementById("temporarySearch").addEventListener("click", reloadRows);
      document.getElementById("temporaryKeyword").addEventListener("keydown", event => {
        if (event.key === "Enter") reloadRows();
      });
      document.getElementById("temporaryAddBatch")?.addEventListener("click", openAddBatch);
      loadRows();
      window.WarehouseInventoryListControls?.bind(
        main,
        "temporaryInventory",
        state.listControlState,
        () => state.rawRows,
        reloadRows,
        { quantityKey: "temporary_quantity" },
      );
    }

    async function fetchAllRows(query) {
      const items = [];
      let page = 1;
      let pages = 1;
      do {
        query.set("page", String(page));
        const data = await api(`/api/temporary-inventory?${query}`);
        items.push(...(data.items || []));
        pages = Number(data.pages || 1);
        page += 1;
      } while (page <= pages);
      return items;
    }

    async function loadRows() {
      if (!enabled() || state.loading) return;
      state.loading = true;
      const host = document.getElementById("temporaryInventoryResult");
      if (host) host.innerHTML = '<div class="empty-state">加载中...</div>';
      try {
        const query = new URLSearchParams({
          q: document.getElementById("temporaryKeyword")?.value.trim() || "",
          page_size: String(state.requestPageSize),
          material_warehouse_code: state.listControlState.filters.warehouse || "",
          inventory_status: "available",
          include_zero: state.listControlState.filters.hideZero ? "0" : "1",
        });
        state.rawRows = await fetchAllRows(query);
        state.rows = window.WarehouseInventoryListControls?.apply(state.rawRows, state.listControlState, "temporary_quantity") || state.rawRows;
        renderRows();
      } catch (error) {
        if (host) host.innerHTML = `<div class="empty-state">${escape(error.message)}</div>`;
        if (error.message === "临时库功能已关闭") handleDisabled();
      } finally {
        state.loading = false;
      }
    }

    function renderRows() {
      const host = document.getElementById("temporaryInventoryResult");
      if (!host) return;
      const totalPages = Math.max(1, Math.ceil(state.rows.length / state.pageSize));
      state.page = Math.min(state.page, totalPages);
      const start = (state.page - 1) * state.pageSize;
      const rows = state.rows.slice(start, start + state.pageSize);
      host.innerHTML = `
        <div class="table-scroll"><table class="flow-table temporary-table">
          <thead><tr><th>物料编号</th><th>名称</th><th>规格</th><th>仓库类型</th><th>临时库存</th><th>批次</th><th>状态</th><th>位置</th><th>操作</th></tr></thead>
          <tbody>${rows.map(row => `
            <tr>
              <td>${escape(row.material_code)}</td>
              <td>${escape(row.material_name)}</td>
              <td>${escape(row.spec || row.brand_model || "")}</td>
              <td>${escape(warehouseLabel(row.warehouse_type))}</td>
              <td><strong>${formatQty(row.temporary_quantity)}</strong> ${escape(row.unit)}</td>
              <td>${Number(row.batch_count || 0)}</td>
              <td><span class="source-badge">临时库</span> <span class="status-badge">可用</span></td>
              <td>${escape(row.location || "-")}</td>
              <td class="mini-actions">
                <button data-temporary-batches="${Number(row.material_id)}">批次详情</button>
                ${canManage() ? `<button data-temporary-edit="${Number(row.material_id)}">编辑物料</button><button class="danger" data-temporary-delete="${Number(row.material_id)}">删除物料</button>` : ""}
                ${canTransfer() ? `<button data-temporary-promote="${Number(row.material_id)}">转移到正式库</button>` : ""}
              </td>
            </tr>
          `).join("") || '<tr><td colspan="9">暂无临时库存</td></tr>'}</tbody>
        </table></div>
        <div class="temporary-pagination">
          <span>共 ${state.rows.length} 项</span>
          <button id="temporaryPrev" ${state.page <= 1 ? "disabled" : ""}>上一页</button>
          <span>${state.page} / ${totalPages}</span>
          <button id="temporaryNext" ${state.page >= totalPages ? "disabled" : ""}>下一页</button>
        </div>
      `;
      host.querySelectorAll("[data-temporary-batches]").forEach(button => {
        button.addEventListener("click", () => openBatches(Number(button.dataset.temporaryBatches)));
      });
      host.querySelectorAll("[data-temporary-edit]").forEach(button => {
        button.addEventListener("click", () => window.editMaterialFromQuery(Number(button.dataset.temporaryEdit), "temporary"));
      });
      host.querySelectorAll("[data-temporary-delete]").forEach(button => {
        button.addEventListener("click", () => deleteTemporaryMaterial(state.rows.find(row => Number(row.material_id) === Number(button.dataset.temporaryDelete))));
      });
      host.querySelectorAll("[data-temporary-promote]").forEach(button => {
        button.addEventListener("click", () => promoteTemporaryMaterial(
          state.rows.find(row => Number(row.material_id) === Number(button.dataset.temporaryPromote))
        ).catch(error => window.toast(error.message || "批次详情加载失败")));
      });
      document.getElementById("temporaryPrev")?.addEventListener("click", () => {
        state.page = Math.max(1, state.page - 1);
        renderRows();
      });
      document.getElementById("temporaryNext")?.addEventListener("click", () => {
        state.page = Math.min(totalPages, state.page + 1);
        renderRows();
      });
    }

    async function loadRecords() {
      if (!enabled()) return;
      const host = document.getElementById("temporaryRecords");
      if (!host) return;
      try {
        const data = await api("/api/temporary-inventory/records?page_size=30");
        state.records = data.items || [];
        host.innerHTML = `<div class="table-scroll"><table class="flow-table">
          <thead><tr><th>日期</th><th>物料</th><th>批次</th><th>类型</th><th>数量</th><th>结余</th><th>操作人</th><th>原因</th></tr></thead>
          <tbody>${state.records.map(row => `<tr>
            <td>${escape(row.operation_date)}</td>
            <td>${escape(row.material_code)} ${escape(row.material_name)}</td>
            <td>${escape(row.batch_no || "-")}</td>
            <td>${escape(businessLabel(row.business_type))}</td>
            <td>${row.operation_type === "out" ? "-" : "+"}${formatQty(row.quantity)}</td>
            <td>${formatQty(row.balance_after)}</td>
            <td>${escape(row.operator_name || "-")}</td>
            <td>${escape(row.remark || "")}</td>
          </tr>`).join("") || '<tr><td colspan="8">暂无临时库存流水</td></tr>'}</tbody>
        </table></div>`;
      } catch (error) {
        host.innerHTML = `<div class="empty-state">${escape(error.message)}</div>`;
        if (error.message === "临时库功能已关闭") handleDisabled();
      }
    }

    async function searchMaterialChoices() {
      const input = document.getElementById("temporaryMaterialKeyword");
      const list = document.getElementById("temporaryMaterialResults");
      if (!input || !list) return;
      const keyword = input.value.trim();
      try {
        const result = await api(`/api/temporary-inventory/material-choices?q=${encodeURIComponent(keyword)}&limit=100`);
        state.materialChoices = (result.items || []).slice(0, 100);
        if (!state.materialChoices.length) {
          list.style.display = "none";
          list.innerHTML = "";
          return;
        }
        list.innerHTML = state.materialChoices.map((item, index) => `
          <button class="material-option" type="button" data-temporary-material-index="${index}">
            ${escape(item.material_code)} · ${escape(item.name)}
            <small>${escape(item.brand_model || "")} ${escape(item.spec || "")} · 临时库 ${formatQty(item.temporary_quantity)} · ${escape(item.unit || "")}</small>
          </button>
        `).join("");
        const rect = input.getBoundingClientRect();
        const width = Math.min(Math.max(rect.width, 420), window.innerWidth - 24);
        list.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))}px`;
        list.style.top = `${Math.min(rect.bottom + 4, window.innerHeight - 120)}px`;
        list.style.width = `${width}px`;
        list.style.maxHeight = `${Math.min(Math.max(160, state.materialChoices.length * 58 + 8), Math.max(220, window.innerHeight * .65))}px`;
        list.style.display = "block";
        list.querySelectorAll("[data-temporary-material-index]").forEach(button => {
          button.addEventListener("click", () => {
            const item = state.materialChoices[Number(button.dataset.temporaryMaterialIndex)];
            document.getElementById("temporaryMaterialId").value = item.id;
            input.value = `${item.material_code} · ${item.name}`;
            document.getElementById("temporaryMaterialSelection").textContent =
              `已选择：${item.material_code} · ${item.name}`;
            list.style.display = "none";
            list.innerHTML = "";
          });
        });
      } catch (error) {
        list.style.display = "none";
        list.innerHTML = "";
        document.getElementById("temporaryMaterialSelection").textContent = error.message || "物料搜索失败";
      }
    }


    async function promoteTemporaryMaterial(row) {
      if (!row) return;
      const data = await api(`/api/temporary-inventory/materials/${Number(row.material_id)}/batches`);
      const batches = data.items || [];
      window.openModal({
        title: "转移到正式库",
        body: `<div class="transfer-confirm"><div><span>物料</span><strong>${escape(row.material_code)} · ${escape(row.material_name)}</strong></div><p class="transfer-warning">仅勾选的普通临时批次会转入正式库；领用退库批次不可转移。</p></div>
          <div class="table-scroll"><table class="flow-table"><thead><tr><th>选择</th><th>批次号</th><th>日期</th><th>数量</th><th>仓库类型</th><th>转移状态</th></tr></thead><tbody>
          ${batches.map(batch => {
            const blocked = Boolean(batch.transfer_blocked) || Number(batch.quantity || 0) <= 0 || batch.inventory_status !== "available";
            const status = batch.transfer_block_reason === "claim_return" ? "领用退库 · 不可转移" : (blocked ? "不可转移" : "可转移");
            return `<tr><td><input type="checkbox" data-promote-batch="${Number(batch.id)}" data-version="${Number(batch.version || 0)}" ${blocked ? "disabled" : ""}></td><td>${escape(batch.batch_no)}</td><td>${escape(batch.received_date)}</td><td>${formatQty(batch.quantity)} ${escape(row.unit || "")}</td><td>${escape(warehouseLabel(batch.warehouse_type))}</td><td>${escape(status)}</td></tr>`;
          }).join("") || '<tr><td colspan="6">暂无临时批次</td></tr>'}
          </tbody></table></div>`,
        okText: "确认转正式库",
        onOk: async () => {
          const selected = [...document.querySelectorAll("[data-promote-batch]:checked")].map(input => ({
            id: Number(input.dataset.promoteBatch),
            version: Number(input.dataset.version),
          }));
          if (!selected.length) throw new Error("请至少选择一个可转移批次");
          const result = await api(`/api/temporary-inventory/materials/${Number(row.material_id)}/promote`, {
            method: "POST",
            body: JSON.stringify({ batches: selected }),
          });
          window.toast(`已转为正式库：${Number(result.converted_batches || 0)} 个批次`);
          await loadRows();
        },
      });
    }

    async function deleteTemporaryMaterial(row) {
      if (!row || !confirm(`确认删除临时库物料“${row.material_name}”？仅允许删除没有正式库记录的物料。`)) return;
      try { await api(`/api/temporary-inventory/materials/${Number(row.material_id)}`, { method: "DELETE" }); window.toast("临时物料已删除"); await loadRows(); }
      catch (error) { window.toast(error.message || "删除临时物料失败"); }
    }

    function openAddBatch() {
      let materialMode = "existing";
      let locationShelves = [];
      let locationReady = Promise.resolve();
      const uploadToken = window.WarehouseAttachmentUpload?.newToken?.() || operationKey("temporary-attachment");
      const inboundOperationKey = operationKey("temporary-inbound");
      const newMaterialOperationKey = operationKey("temporary-new-material");
      let createdBatch = null;
      const existingFields = () => `
        <label class="wide material-search">物料
          <input id="temporaryMaterialKeyword" autocomplete="off" placeholder="输入编号 / 名称 / 规格 / 采购申请人">
          <input id="temporaryMaterialId" type="hidden">
          <div id="temporaryMaterialResults" class="material-results"></div>
          <small id="temporaryMaterialSelection" class="hint">输入后将即时显示匹配物料；可选择零库存物料。</small>
        </label>`;
      const newFields = () => `
        <label>物料编号<input id="temporaryNewCode" placeholder="例如 10200101010010"></label>
        <label>物料名称<input id="temporaryNewName" placeholder="必填"></label>
        <label>品牌型号<input id="temporaryNewBrand"></label>
        <label>技术规格<input id="temporaryNewSpec"></label>
        <label>单位<input id="temporaryNewUnit" value="个"></label>
        <label>类别名称<input id="temporaryNewCategory"></label>`;
      const commonFields = () => `
        <label>数量<input id="temporaryQuantity" type="number" min="0.000001" step="any"></label>
        <label>总金额<input id="temporaryTotalAmount" type="number" min="0" step="0.01" value="0" required></label>
        <label>单价<input id="temporaryUnitPrice" type="number" step="0.0001" value="0" readonly></label>
        <label>入库日期<input id="temporaryReceivedDate" type="date" value="${new Date().toISOString().slice(0, 10)}"></label>
        <div class="wide form-grid" id="temporaryShelfLocation"></div>
        <label class="wide">备注<input id="temporaryRemark" value="临时库手工入库"></label>`;
      const attachmentFields = () => {
        const settings = system.boot?.workflow_settings || {};
        const photoRequired = settings.temporary_inventory_material_photo_required ? '<span class="badge required">必填</span>' : "";
        const documentRequired = settings.temporary_inventory_document_required ? '<span class="badge required">必填</span>' : "";
        return `<div class="wide attachment-panel"><div class="attachment-panel-head"><h3>批次附件</h3><span class="hint">入库成功后自动绑定本批次</span></div><div class="attachment-upload-grid"><div class="attachment-upload-box photo"><div class="attachment-upload-title"><strong>物料照片</strong>${photoRequired}</div><div class="attachment-upload-controls"><label class="attachment-file-pick"><input id="temporaryPhotoFiles" data-empty-label="选择图片" type="file" multiple accept="image/*"><span data-file-label>选择图片</span></label></div></div><div class="attachment-upload-box document"><div class="attachment-upload-title"><strong>物料资料</strong>${documentRequired}</div><div class="attachment-upload-controls"><label class="attachment-file-pick"><input id="temporaryDocumentFiles" data-empty-label="选择资料" type="file" multiple accept="image/*,.pdf,.doc,.docx,.xls,.xlsx,.csv,.txt"><span data-file-label>选择资料</span></label></div></div></div></div>`;
      };
      const selectedFiles = id => document.getElementById(id)?.files || [];
      const validateAttachments = () => {
        const settings = system.boot?.workflow_settings || {};
        if (settings.temporary_inventory_material_photo_required && !selectedFiles("temporaryPhotoFiles").length) throw new Error("请上传至少一张物料照片");
        if (settings.temporary_inventory_document_required && !selectedFiles("temporaryDocumentFiles").length) throw new Error("请上传至少一份物料资料");
      };
      const uploadBatchAttachments = async (materialId, batchId) => {
        const uploader = window.WarehouseAttachmentUpload;
        if (!uploader?.uploadFiles) throw new Error("附件上传模块未加载");
        const tasks = [["temporaryPhotoFiles", "material_photo"], ["temporaryDocumentFiles", "document"]]
          .filter(([id]) => selectedFiles(id).length)
          .map(([id, attachmentType]) => uploader.uploadFiles({ files: selectedFiles(id), token: uploadToken, materialId, batchId, attachmentType }));
        if (tasks.length) await Promise.all(tasks);
      };
      const renderSource = () => {
        const host = document.getElementById("temporaryMaterialSource");
        host.innerHTML = materialMode === "existing" ? existingFields() : newFields();
        if (materialMode === "existing") {
          const input = document.getElementById("temporaryMaterialKeyword");
          let timer = 0;
          input.addEventListener("input", () => {
            document.getElementById("temporaryMaterialId").value = "";
            document.getElementById("temporaryMaterialSelection").textContent = "正在搜索全部物料…";
            window.clearTimeout(timer);
            timer = window.setTimeout(searchMaterialChoices, 180);
          });
          input.addEventListener("keydown", event => {
            if (event.key === "Escape") {
              const list = document.getElementById("temporaryMaterialResults");
              list.style.display = "none";
            }
          });
          document.addEventListener("click", event => {
            if (!document.getElementById("temporaryMaterialSource")?.contains(event.target)) {
              const list = document.getElementById("temporaryMaterialResults");
              if (list) list.style.display = "none";
            }
          }, { once: true });
          input.focus();
        }
      };
      const batchPayload = () => {
        const location = window.WarehouseShelfLocation.value("temporary", document, locationShelves);
        return {
          quantity: document.getElementById("temporaryQuantity").value,
          total_amount: document.getElementById("temporaryTotalAmount").value,
          received_date: document.getElementById("temporaryReceivedDate").value,
          shelf_id: location.shelf_id,
          layer_number: location.layer_number,
          zone_name: location.zone_name,
          remark: document.getElementById("temporaryRemark").value,
        };
      };
      window.openModal({
        title: "新增临时批次",
        body: `<div class="form-grid temporary-form">
          <label class="wide">物料录入方式
            <select id="temporaryMaterialMode">
              <option value="existing">搜索并选择已有物料</option>
              <option value="new">直接录入新物料</option>
            </select>
          </label>
          <div class="wide form-grid" id="temporaryMaterialSource"></div>
          ${commonFields()}
          ${attachmentFields()}
        </div>`,
        okText: "确认入库",
        onReady: () => {
          document.getElementById("temporaryMaterialMode").addEventListener("change", event => {
            materialMode = event.target.value;
            renderSource();
          });
          renderSource();
          window.WarehouseAmountInput?.bind({
            quantityInput: document.getElementById("temporaryQuantity"),
            totalAmountInput: document.getElementById("temporaryTotalAmount"),
            unitPriceInput: document.getElementById("temporaryUnitPrice"),
          });
          window.WarehouseAttachmentUpload?.bindFilePickLabels(document.querySelector(".modal") || document);
          locationReady = api("/api/shelves").then(shelves => {
            locationShelves = shelves || [];
            if (!locationShelves.length) throw new Error("暂无可用货架，请先在料卡系统中创建货架");
            window._shelfData = locationShelves;
            const host = document.getElementById("temporaryShelfLocation");
            window.WarehouseShelfLocation.mount(host, locationShelves, "temporary", {
              allowEmpty: true, shelfLabel: "货架号", layerLabel: "货架层", zoneLabel: "货架分区",
            });
          });
          locationReady.catch(error => {
            window.toast(error.message || "货架位置加载失败");
          });
        },
        onOk: async () => {
          await locationReady;
          validateAttachments();
          if (!createdBatch) {
            const payload = batchPayload();
            const shelf = locationShelves.find(item => Number(item.id) === Number(payload.shelf_id));
            if (!shelf || !payload.layer_number || !payload.zone_name) {
              throw new Error("请依次选择货架号、货架层和货架分区");
            }
            payload.warehouse_type = shelf.warehouse_type;
            if (materialMode === "existing") {
              const materialId = Number(document.getElementById("temporaryMaterialId").value || 0);
              if (!materialId) throw new Error("请先搜索并选择已有物料，或切换为直接录入新物料");
              const result = await api("/api/temporary-inventory/batches", {
                method: "POST",
                body: JSON.stringify({ ...payload, material_id: materialId, operation_key: inboundOperationKey }),
              });
              createdBatch = { materialId, batchId: Number(result.batch_id || 0) };
            } else {
              const result = await api("/api/temporary-inventory/materials", {
                method: "POST",
                body: JSON.stringify({
                  ...payload,
                  material_code: document.getElementById("temporaryNewCode").value.trim(),
                  name: document.getElementById("temporaryNewName").value.trim(),
                  brand_model: document.getElementById("temporaryNewBrand").value.trim(),
                  spec: document.getElementById("temporaryNewSpec").value.trim(),
                  unit: document.getElementById("temporaryNewUnit").value.trim(),
                  category_name: document.getElementById("temporaryNewCategory").value.trim(),
                  operation_key: newMaterialOperationKey,
                }),
              });
              createdBatch = { materialId: Number(result.material?.id || 0), batchId: Number(result.batch_id || 0) };
            }
            if (!createdBatch.materialId || !createdBatch.batchId) throw new Error("临时批次创建结果不完整");
          }
          await uploadBatchAttachments(createdBatch.materialId, createdBatch.batchId);
          window.toast("临时批次已入库");
          await loadRows();
        },
      });
    }

    async function openBatches(materialId) {
      const data = await api(`/api/temporary-inventory/materials/${materialId}/batches`);
      window.openModal({
        title: "临时批次详情",
        hideOk: true,
        body: batchTable(data.items || [], false),
      });
    }

    function batchAttachmentHtml(batch) {
      const attachments = batch.attachments || [];
      if (!attachments.length) return '<span class="hint">暂无附件</span>';
      return attachments.map(item => item.is_image
        ? `<a class="attachment-card compact" href="${attr(item.download_url)}" target="_blank" rel="noopener"><img src="${attr(item.download_url)}" alt="${attr(item.original_name || "物料照片")}"></a>`
        : `<a class="attachment-chip" href="${attr(item.download_url)}" target="_blank" rel="noopener">${escape(item.original_name || "资料")}</a>`
      ).join("");
    }

    function batchTable(batches) {
      return `<div class="table-scroll"><table class="flow-table"><thead><tr><th>批次号</th><th>日期</th><th>实际数量</th><th>每单位数量</th><th>归属项目</th><th>用料形式</th><th>仓库类型</th><th>层/分区</th><th>附件</th><th>来源</th><th>状态</th><th>转移限制</th></tr></thead>` +
        `<tbody>${batches.map(batch => `<tr><td>${escape(batch.batch_no)}</td><td>${escape(batch.received_date)}</td><td>${formatQty(batch.quantity)}</td><td>${formatQty(batch.pack_quantity || 1)}</td><td>${escape(batch.purchase_project || "")}</td><td>${escape(batch.usage_kind || "研发用料")}</td><td>${escape(warehouseLabel(batch.warehouse_type))}</td><td>${Number(batch.layer_number || 1)} / ${escape(batch.zone_name || "")}</td><td><div class="attachment-gallery">${batchAttachmentHtml(batch)}</div></td><td><span class="source-badge">临时库</span></td><td>可用</td><td>${batch.transfer_block_reason === "claim_return" ? '<span class="status-badge">领用退库 · 不可转移</span>' : "可转移"}</td></tr>`).join("") || '<tr><td colspan="12">暂无批次</td></tr>'}</tbody>` +
      `</table></div>`;
    }


    const acceptanceStatusText = {
      acceptance: "待验收",
      leader_acceptance: "待领导验收",
      inbound: "待入库",
      completed: "已完成",
      applicant_revision: "退回修改",
      rejected: "已驳回",
    };

    function renderFlows(main) {
      if (!enabled()) {
        clear();
        main.innerHTML = '<div class="work-panel"><div class="work-body"><div class="empty-state">临时库功能已关闭或当前账号无查看权限</div></div></div>';
        return;
      }
      main.innerHTML = moduleShell("临时库流程管理", `
        <div class="work-panel">
          <div class="work-head"><h2>没有采购订单的验收流程</h2><button id="temporaryFlowRefresh" type="button">刷新</button></div>
          <div class="work-body" id="temporaryFlowResult"><div class="empty-state">加载中...</div></div>
        </div>
      `);
      document.getElementById("temporaryFlowRefresh").addEventListener("click", loadFlows);
      loadFlows();
    }

    async function loadFlows() {
      const host = document.getElementById("temporaryFlowResult");
      if (!host) return;
      try {
        const data = await api("/api/temporary-inventory/acceptance-forms");
        const rows = data.items || [];
        host.innerHTML = `<div class="table-scroll"><table class="flow-table"><thead><tr><th>验收单号</th><th>标题</th><th>申请人</th><th>状态</th><th>物料</th><th>操作</th></tr></thead><tbody>${
          rows.map(row => `<tr>
            <td>${escape(row.form_no)}</td>
            <td>${escape(row.title)}</td>
            <td>${escape(row.applicant_name)}</td>
            <td>${escape(acceptanceStatusText[row.status] || row.status || "")}</td>
            <td>${escape((row.materials || []).join("、"))}</td>
            <td>${flowAction(row)}</td>
          </tr>`).join("") || '<tr><td colspan="6">暂无没有采购订单的验收流程</td></tr>'
        }</tbody></table></div>`;
        host.querySelectorAll("[data-promote-form]").forEach(button => {
          button.addEventListener("click", () => promoteFlow(button.dataset.promoteForm));
        });
      } catch (error) {
        if (error.message === "临时库功能已关闭") handleDisabled();
        else host.innerHTML = `<div class="empty-state">${escape(error.message)}</div>`;
      }
    }

    function flowAction(row) {
      if (row.promoted) return `已转移${row.promoted_at ? " " + escape(row.promoted_at) : ""}`;
      if (!row.inbound_ready) return "待入库";
      if (!canTransfer()) return "";
      return `<button type="button" data-promote-form="${Number(row.id)}">转移到正式库</button>`;
    }

    async function promoteFlow(formId) {
      if (!confirm("确认把这张验收单下的物料全部转移到正式库？")) return;
      try {
        await api(`/api/temporary-inventory/acceptance-forms/${Number(formId)}/promote`, { method: "POST", body: "{}" });
        window.toast("已转移到正式库");
        await loadFlows();
      } catch (error) {
        if (error.payload?.splittable && confirm(`${error.message}\n是否拆分申领单，把临时库物料拆出来？`)) {
          try {
            await api(`/api/temporary-inventory/acceptance-forms/${Number(formId)}/split-claims`, { method: "POST", body: "{}" });
            window.toast("已拆分申领单");
            await loadFlows();
          } catch (splitError) {
            window.toast(splitError.message);
          }
          return;
        }
        window.toast(error.message);
      }
    }

    function renderLedger(main) {
      if (!enabled()) { main.innerHTML = '<div class="empty-state">临时库功能已关闭或当前账号无查看权限</div>'; return; }
      main.innerHTML = `<div class="module-title"><div><h1>临时库存流水</h1><p>审计中心 · 临时库入库、调整与结余记录</p></div></div><div class="work-panel temporary-panel"><div class="work-head"><h2>流水明细</h2><button id="temporaryRefreshRecords">刷新</button></div><div class="work-body" id="temporaryRecords"></div></div>`;
      document.getElementById("temporaryRefreshRecords").addEventListener("click", loadRecords);
      loadRecords();
    }

    function handleDisabled() {
      clear();
      system.view = "query";
      const main = document.getElementById("systemMain");
      if (main) main.innerHTML = '<div class="work-panel"><div class="work-body"><div class="empty-state">临时库功能已关闭</div></div></div>';
    }

    function warehouseLabel(value) {
      return String(value || "").split(",").map(item => ({ office: "办公库", rd: "研发库" })[item] || item).join("、");
    }

    function businessLabel(value) {
      return ({
        temporary_manual_inbound: "手工入库",
        temporary_manual_adjust_in: "调整增加",
        temporary_manual_adjust_out: "调整减少",
        claim_return_inbound: "领用退库",
      })[value] || value || "";
    }

    return { render, renderFlows, renderLedger, clear, enabled, reload: loadRows };
  }

  window.WarehouseTemporaryInventory = { create };
})();
