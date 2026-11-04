(() => {
  const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const warehouseLabel = value => ({ office: "办公用品库", rd: "研发材料库" })[value] || value;
  const categoryValue = (row, level) => {
    if (level === "major") return String(row.major_code || row.category || "").trim();
    if (level === "middle") return String(row.middle_code || "").trim();
    return String(row.small_code || "").trim();
  };
  const quantityOf = (row, key) => Number(row[key] ?? row.quantity ?? 0);
  const materialWarehouseCode = row => String(row.material_code || "").slice(2, 4);
  const unitPriceOf = (row, quantityKey) => {
    const hasDirectPrice = row.unit_price !== undefined && row.unit_price !== null && row.unit_price !== "";
    const directPrice = Number(row.unit_price);
    if (hasDirectPrice && Number.isFinite(directPrice)) return directPrice;
    const quantity = quantityOf(row, quantityKey);
    const amount = Number(row.amount ?? 0);
    return quantity > 0 && Number.isFinite(amount) ? amount / quantity : 0;
  };
  const amountOf = (row, quantityKey) => {
    const directAmount = Number(row.amount);
    if (Number.isFinite(directAmount)) return directAmount;
    return unitPriceOf(row, quantityKey) * quantityOf(row, quantityKey);
  };
  const options = (rows, getter) => [...new Set((rows || []).map(getter).filter(Boolean))].sort((a, b) => String(a).localeCompare(String(b), "zh-CN", { numeric: true }));
  const optionHtml = (rows, getter, selected, emptyLabel) => `<option value="">${escape(emptyLabel)}</option>${options(rows, getter).map(value => `<option value="${escape(value)}" ${value === selected ? "selected" : ""}>${escape(value)}</option>`).join("")}`;

  function apply(rows, state, quantityKey = "quantity") {
    const filters = state.filters || {};
    const filtered = (rows || []).filter(row => {
      const materialWarehouse = materialWarehouseCode(row);
      const visibleQuantity = quantityOf(row, quantityKey) + Number(row.temporary_quantity || 0);
      const usageText = String(row.usage_kinds || row.usage_kind || "");
      const projectText = String(row.purchase_projects || row.purchase_project || "");
      return (!filters.hideZero || visibleQuantity > 0)
        && (!filters.usage || usageText.split(",").map(item => item.trim()).includes(filters.usage))
        && (!filters.project || projectText.split(",").map(item => item.trim()).includes(filters.project))
        && (!filters.warehouse || materialWarehouse === filters.warehouse)
        && (!filters.major || categoryValue(row, "major") === filters.major)
        && (!filters.middle || categoryValue(row, "middle") === filters.middle)
        && (!filters.small || categoryValue(row, "small") === filters.small);
    });
    const mode = state.sort || "code";
    return filtered.slice().sort((left, right) => {
      if (mode === "quantity") return quantityOf(right, quantityKey) - quantityOf(left, quantityKey) || String(left.material_code || "").localeCompare(String(right.material_code || ""), "zh-CN", { numeric: true });
      if (mode === "unit_price") return unitPriceOf(right, quantityKey) - unitPriceOf(left, quantityKey) || String(left.material_code || "").localeCompare(String(right.material_code || ""), "zh-CN", { numeric: true });
      if (mode === "amount") return amountOf(right, quantityKey) - amountOf(left, quantityKey) || String(left.material_code || "").localeCompare(String(right.material_code || ""), "zh-CN", { numeric: true });
      const leftValue = mode === "name" ? (left.name || left.material_name || "") : (left.material_code || "");
      const rightValue = mode === "name" ? (right.name || right.material_name || "") : (right.material_code || "");
      return String(leftValue).localeCompare(String(rightValue), "zh-CN", { numeric: true });
    });
  }

  function toolbarHtml(key) {
    return `<span class="inventory-list-controls" data-inventory-controls="${escape(key)}"><button type="button" class="primary query-search-button" data-inventory-sort-toggle="${escape(key)}">排序方式</button><select data-inventory-sort="${escape(key)}" hidden><option value="name">名称排序</option><option value="code">序号排序</option><option value="quantity">数量排序</option><option value="unit_price">单价排序</option><option value="amount">金额排序</option></select><button type="button" class="primary query-search-button" data-inventory-filter="${escape(key)}">筛选</button></span>`;
  }

  function bind(root, key, state, getRows, onApply, options = {}) {
    const host = root || document;
    const quantityKey = options.quantityKey || "quantity";
    const controls = host.querySelector(`[data-inventory-controls="${CSS.escape(key)}"]`);
    if (!controls) return;
    const sortSelect = controls.querySelector(`[data-inventory-sort="${CSS.escape(key)}"]`);
    sortSelect.value = state.sort || "code";
    controls.querySelector(`[data-inventory-sort-toggle="${CSS.escape(key)}"]`)?.addEventListener("click", () => { sortSelect.hidden = !sortSelect.hidden; if (!sortSelect.hidden) sortSelect.focus(); });
    sortSelect.addEventListener("change", () => { state.sort = sortSelect.value; sortSelect.hidden = true; onApply(apply(getRows(), state, quantityKey)); });
    controls.querySelector(`[data-inventory-filter="${CSS.escape(key)}"]`)?.addEventListener("click", () => openFilter(key, state, getRows, onApply, quantityKey));
  }

  function openFilter(key, state, getRows, onApply, quantityKey) {
    const rows = getRows() || [];
    const filters = state.filters || {};
    const id = value => `${key}${value}`;
    const projects = [...new Set(rows.flatMap(row => String(row.purchase_projects || row.purchase_project || "").split(",").map(item => item.trim()).filter(Boolean)))].sort((a, b) => a.localeCompare(b, "zh-CN"));
    window.openModal({
      title: "库存筛选",
      okText: "应用筛选",
      body: `<div class="form-grid inventory-filter-form"><label class="compact-check wide"><input id="${id("HideZero")}" type="checkbox" ${filters.hideZero ? "checked" : ""}> 隐藏 0 库存物料</label><label>用料形式<select id="${id("Usage")}"><option value="">全部</option><option value="研发用料" ${filters.usage === "研发用料" ? "selected" : ""}>研发用料</option><option value="生产用料" ${filters.usage === "生产用料" ? "selected" : ""}>生产用料</option></select></label><label>归属项目<select id="${id("Project")}"><option value="">全部项目</option>${projects.map(value => `<option value="${escape(value)}" ${value === filters.project ? "selected" : ""}>${escape(value)}</option>`).join("")}</select></label><label>物料库房<select id="${id("Warehouse")}"><option value="">全部库房</option><option value="10" ${filters.warehouse === "10" ? "selected" : ""}>办公用品库</option><option value="20" ${filters.warehouse === "20" ? "selected" : ""}>研发材料库</option></select></label><label>物料大类<select id="${id("Major")}">${optionHtml(rows, row => categoryValue(row, "major"), filters.major, "全部大类")}</select></label><label>物料中类<select id="${id("Middle")}">${optionHtml(rows, row => categoryValue(row, "middle"), filters.middle, "全部中类")}</select></label><label>物料小类<select id="${id("Small")}">${optionHtml(rows, row => categoryValue(row, "small"), filters.small, "全部小类")}</select></label></div>`,
      onOk: () => {
        state.filters = { hideZero: document.getElementById(id("HideZero")).checked, usage: document.getElementById(id("Usage")).value, project: document.getElementById(id("Project")).value, warehouse: document.getElementById(id("Warehouse")).value, major: document.getElementById(id("Major")).value, middle: document.getElementById(id("Middle")).value, small: document.getElementById(id("Small")).value };
        onApply(apply(getRows(), state, quantityKey));
      },
    });
  }

  window.WarehouseInventoryListControls = { apply, toolbarHtml, bind, warehouseLabel };
})();
