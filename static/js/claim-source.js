(() => {
  "use strict";

  function sourceLabel(source) {
    return source === "temporary" ? "临时库" : "正式库";
  }

  function sourceBadge(source) {
    const normalized = source === "temporary" ? "temporary" : "formal";
    return `<span class="stock-source-badge ${normalized}">${sourceLabel(normalized)}</span>`;
  }

  function availabilityText(material, temporaryEnabled, formatQty) {
    const formal = Number(material?.formal_available_quantity || 0);
    const temporary = temporaryEnabled ? Number(material?.temporary_available_quantity || 0) : 0;
    const total = Number(material?.total_available_quantity ?? material?.quantity ?? (formal + temporary));
    const format = typeof formatQty === "function" ? formatQty : value => String(value);
    if (!temporaryEnabled) return `正式库 ${format(formal)} · 合计 ${format(total)}`;
    return `正式库 ${format(formal)} · 临时库 ${format(temporary)} · 合计 ${format(total)}`;
  }

  function allocationEstimate(material, requested, temporaryEnabled, formatQty) {
    const quantity = Math.max(0, Number(requested || 0));
    if (!material || !quantity) return "";
    const formalAvailable = Number(material.formal_available_quantity || 0);
    const temporaryAvailable = temporaryEnabled ? Number(material.temporary_available_quantity || 0) : 0;
    const formal = Math.min(quantity, formalAvailable);
    const temporary = Math.min(Math.max(0, quantity - formal), temporaryAvailable);
    const shortage = Math.max(0, quantity - formal - temporary);
    const format = typeof formatQty === "function" ? formatQty : value => String(value);
    const parts = [];
    if (formal > 0) parts.push(`正式库 ${format(formal)}`);
    if (temporary > 0) parts.push(`临时库 ${format(temporary)}`);
    if (shortage > 0) parts.push(`不足 ${format(shortage)}`);
    return `预计分配：${parts.join("，")}`;
  }

  function groupClaimRevisionItems(items) {
    const groups = new Map();
    (items || []).forEach(item => {
      const key = item.allocation_group_key || item.data?.allocation_group_key || `legacy-item:${item.id}`;
      if (!groups.has(key)) {
        groups.set(key, {
          ...item,
          allocation_group_key: key,
          request_quantity: 0,
        });
      }
      const grouped = groups.get(key);
      const requested = item.requested_quantity_snapshot ?? item.data?.requested_quantity_snapshot;
      if (requested != null && requested !== "") {
        grouped.request_quantity = Number(requested || 0);
      } else {
        grouped.request_quantity += Number(item.request_quantity || 0);
      }
    });
    return [...groups.values()];
  }

  window.ClaimSourceUI = {
    allocationEstimate,
    availabilityText,
    groupClaimRevisionItems,
    sourceBadge,
    sourceLabel,
  };
})();
