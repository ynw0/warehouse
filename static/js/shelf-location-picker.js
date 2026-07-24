(() => {
  const escape = value => String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[char]);

  function zones(layer) {
    return (layer?.zones || []).map(zone => ({
      name: typeof zone === "object" ? String(zone.name || "") : String(zone || ""),
      note: typeof zone === "object" ? String(zone.note || "") : "",
    })).filter(zone => zone.name);
  }

  function render(shelves, key, options = {}) {
    const allowEmpty = Boolean(options.allowEmpty);
    const selected = options.selected || {};
    const shelfOptions = (shelves || []).map(shelf =>
      `<option value="${Number(shelf.id)}" ${Number(selected.shelf_id) === Number(shelf.id) ? "selected" : ""}>${escape(shelf.name)}</option>`
    ).join("");
    const prefix = allowEmpty ? '<option value="">请选择货架号</option>' : "";
    return `<label>${escape(options.shelfLabel || "货架")}<select data-shelf="${escape(key)}" data-shelf-location-shelf="${escape(key)}" data-location-allow-empty="${allowEmpty ? "1" : "0"}" onchange="window.WarehouseShelfLocation.update(this, window._shelfData || [])">${prefix}${shelfOptions}</select></label>
      <label>${escape(options.layerLabel || "层")}<select data-layer="${escape(key)}" data-shelf-location-layer="${escape(key)}"></select></label>
      <label>${escape(options.zoneLabel || "分区")}<select data-zone="${escape(key)}" data-shelf-location-zone="${escape(key)}"></select></label>`;
  }
  function mount(host, shelves, key, options = {}) {
    if (!host) return null;
    host.innerHTML = render(shelves, key, options);
    bind(host, shelves, { [key]: options.selected || {} });
    return controls(host, key);
  }


  function controls(root, key) {
    const scope = root || document;
    const encoded = CSS.escape(String(key));
    return {
      shelf: scope.querySelector(`[data-shelf-location-shelf="${encoded}"]`),
      layer: scope.querySelector(`[data-shelf-location-layer="${encoded}"]`),
      zone: scope.querySelector(`[data-shelf-location-zone="${encoded}"]`),
    };
  }

  function update(shelfSelect, shelves, preferred = {}) {
    if (!shelfSelect) return;
    const key = shelfSelect.dataset.shelfLocationShelf;
    const scope = shelfSelect.closest(".modal") || document;
    const { layer, zone } = controls(scope, key);
    if (!layer || !zone) return;
    const allowEmpty = shelfSelect.dataset.locationAllowEmpty === "1";
    const shelf = (shelves || []).find(item => Number(item.id) === Number(shelfSelect.value));
    if (!shelf) {
      layer.innerHTML = '<option value="">请选择货架层</option>';
      zone.innerHTML = '<option value="">请选择货架分区</option>';
      layer.disabled = true;
      zone.disabled = true;
      return;
    }
    const layerOptions = (shelf.layers || []).map(item =>
      `<option value="${Number(item.layer_number)}">${Number(item.layer_number)} 层</option>`
    ).join("");
    layer.innerHTML = `${allowEmpty ? '<option value="">请选择货架层</option>' : ""}${layerOptions}`;
    layer.disabled = !(shelf.layers || []).length;
    if (preferred.layer_number && (shelf.layers || []).some(item => Number(item.layer_number) === Number(preferred.layer_number))) {
      layer.value = String(preferred.layer_number);
    }
    const updateZones = () => {
      const selectedLayer = (shelf.layers || []).find(item => Number(item.layer_number) === Number(layer.value));
      const zoneOptions = zones(selectedLayer).map(item =>
        `<option value="${escape(item.name)}">${escape(item.note ? `${item.name} 区 · ${item.note}` : `${item.name} 区`)}</option>`
      ).join("");
      zone.innerHTML = `${allowEmpty ? '<option value="">请选择货架分区</option>' : ""}${zoneOptions}`;
      zone.disabled = !zones(selectedLayer).length;
      if (preferred.zone_name && zones(selectedLayer).some(item => item.name === preferred.zone_name)) {
        zone.value = preferred.zone_name;
      }
    };
    updateZones();
    layer.onchange = updateZones;
  }

  function bind(root, shelves, preferredByKey = {}) {
    const scope = root || document;
    scope.querySelectorAll("[data-shelf-location-shelf]").forEach(shelf => {
      const key = shelf.dataset.shelfLocationShelf;
      const preferred = preferredByKey[key] || {};
      update(shelf, shelves, preferred);
      if (!shelf.dataset.shelfLocationBound) {
        shelf.dataset.shelfLocationBound = "1";
        shelf.addEventListener("change", () => update(shelf, shelves));
      }
    });
  }

  function value(key, root, shelves) {
    const { shelf, layer, zone } = controls(root, key);
    const shelfRow = (shelves || []).find(item => Number(item.id) === Number(shelf?.value));
    return {
      shelf_id: shelf?.value || "",
      layer_number: layer?.value || "",
      zone_name: zone?.value || "",
      shelf: shelfRow || null,
    };
  }

  window.WarehouseShelfLocation = { render, mount, bind, update, value };
})();
