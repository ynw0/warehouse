(() => {
  function newToken() {
    return `att${Date.now()}${Math.random().toString(16).slice(2)}`;
  }

  function normalizedType(type) {
    const text = String(type || "").toLowerCase();
    if (text === "photo") return "material_photo";
    if (["certificate", "invoice", "other"].includes(text)) return "document";
    return text === "material_photo" ? "material_photo" : "document";
  }

  function typeLabel(type) {
    return normalizedType(type) === "material_photo" ? "Material photo" : "Document";
  }

  function updateFileLabel(input) {
    const label = input?.closest(".attachment-file-pick")?.querySelector("[data-file-label]");
    if (!label) return;
    const files = input.files || [];
    const fallback = input.dataset.emptyLabel || "Select files";
    label.textContent = files.length === 1 ? files[0].name : (files.length > 1 ? `${files.length} files selected` : fallback);
  }

  function bindFilePickLabels(root = document) {
    root.querySelectorAll(".attachment-file-pick input[type='file']").forEach(input => {
      if (input.dataset.filePickBound) return;
      input.dataset.filePickBound = "1";
      input.addEventListener("change", () => updateFileLabel(input));
      updateFileLabel(input);
    });
  }

  async function uploadFiles({ files, token = "", materialId = 0, materialBatchId = 0, batchId = 0, workflowFormId = 0, workflowItemId = 0, attachmentType = "document", remark = "" }) {
    if (!files || !files.length) throw new Error("Choose attachments to upload");
    const resolvedBatchId = Number(materialBatchId || batchId || 0);
    const form = new FormData();
    [...files].forEach(file => form.append("files", file));
    if (token) form.append("token", token);
    if (materialId) form.append("material_id", String(materialId));
    if (resolvedBatchId) form.append("material_batch_id", String(resolvedBatchId));
    if (workflowFormId) form.append("workflow_form_id", String(workflowFormId));
    if (workflowItemId) form.append("workflow_item_id", String(workflowItemId));
    form.append("attachment_type", normalizedType(attachmentType));
    form.append("remark", remark || "");
    const response = await fetch("/api/material-attachments", { method: "POST", body: form });
    if (response.status === 401) {
      window.location.href = "/login";
      throw new Error("Please sign in first");
    }
    let data = {};
    try { data = await response.json(); } catch (error) {}
    if (!response.ok || data.success === false) throw new Error(data.error || "Attachment upload failed");
    return data;
  }

  window.WarehouseAttachmentUpload = { newToken, normalizedType, typeLabel, updateFileLabel, bindFilePickLabels, uploadFiles };
})();
