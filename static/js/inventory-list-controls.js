(() => {
  const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
  const warehouseLabel = value => ({ office: "办公用品库", rd: "研发材料库" })[value] || value;
  const categoryValue = (row, level) => {
    if (level === "major") return String(row.major_code || row.category || "").trim();
    if (level === "middle") return String(row.middle_code || "").trim();
    return String(row.small_code || "").trim();
  };
  const quantityOf = (row, key) => Number(row[key] ?? row.quantity ?? 0);
  const options = (rows, getter) => [...new Set((rows || []).map(getter).filter(Boolean))].sort((a, b) => String(a).localeCompare(String(b), "zh-CN", { numeric: true }));
  const optionHtml = (rows, getter, selected, emptyLabel) => `<option value="">${escape(emptyLabel)}</option>${options(rows, getter).map(value => `<option value="${escape(value)}" ${value === selected ? "selected" : ""}>${escape(value)}</option>`).join("")}`;

  function apply(rows, state, quantityKey = "quantity") {
    const filters = state.filters || {};
    const filtered = (rows || []).filter(row => {
      const warehouse = String(row.warehouse_type || "");
      return (!filters.hideZero || quantityOf(row, quantityKey) > 0)
        && (!filters.warehouse || warehouse.split(",").includes(filters.warehouse))
        && (!filters.major || categoryValue(row, "major") === filters.major)
        && (!filters.middle || categoryValue(row, "middle") === filters.middle)
        && (!filters.small || categoryValue(row, "small") === filters.small);
    });
    const mode = state.sort || "code";
    return filtered.slice().sort((left, right) => {
      if (mode === "quantity") return quantityOf(right, quantityKey) - quantityOf(left, quantityKey) || String(left.material_code || "").localeCompare(String(right.material_code || ""), "zh-CN", { numeric: true });
      const leftValue = mode === "name" ? (left.name || left.material_name || "") : (left.material_code || "");
      const rightValue = mode === "name" ? (right.name || right.material_name || "") : (right.material_code || "");
      return String(leftValue).localeCompare(String(rightValue), "zh-CN", { numeric: true });
    });
  }

  function toolbarHtml(key) {
    return `<span class="inventory-list-controls" data-inventory-controls="${escape(key)}"><button type="button" class="primary query-search-button" data-inventory-sort-toggle="${escape(key)}">排序方式</button><select data-inventory-sort="${escape(key)}" hidden><option value="name">名称排序</option><option value="code">序号排序</option><option value="quantity">数量排序</option></select><button type="button" class="primary query-search-button" data-inventory-filter="${escape(key)}">筛选</button></span>`;
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
    window.openModal({
      title: "库存筛选",
      okText: "应用筛选",
      body: `<div class="form-grid inventory-filter-form"><label class="compact-check wide"><input id="${id("HideZero")}" type="checkbox" ${filters.hideZero ? "checked" : ""}> 隐藏 0 库存物料</label><label>库房分类<select id="${id("Warehouse")}"><option value="">全部库房</option><option value="office" ${filters.warehouse === "office" ? "selected" : ""}>办公用品库</option><option value="rd" ${filters.warehouse === "rd" ? "selected" : ""}>研发材料库</option></select></label><label>物料大类<select id="${id("Major")}">${optionHtml(rows, row => categoryValue(row, "major"), filters.major, "全部大类")}</select></label><label>物料中类<select id="${id("Middle")}">${optionHtml(rows, row => categoryValue(row, "middle"), filters.middle, "全部中类")}</select></label><label>物料小类<select id="${id("Small")}">${optionHtml(rows, row => categoryValue(row, "small"), filters.small, "全部小类")}</select></label></div>`,
      onOk: () => {
        state.filters = { hideZero: document.getElementById(id("HideZero")).checked, warehouse: document.getElementById(id("Warehouse")).value, major: document.getElementById(id("Major")).value, middle: document.getElementById(id("Middle")).value, small: document.getElementById(id("Small")).value };
        onApply(apply(getRows(), state, quantityKey));
      },
    });
  }

  window.WarehouseInventoryListControls = { apply, toolbarHtml, bind, warehouseLabel };
})();
