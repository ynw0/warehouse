const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { webcrypto } = require("node:crypto");

(async () => {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, { value: "", textContent: "", innerHTML: "" });
    return elements.get(id);
  };
  const requests = [];
  let returnButton;
  const host = element("myClaimsResult");
  host.querySelector = id => element(id);
  host.querySelectorAll = () => {
    returnButton = { dataset: { claimReturn: "21" } };
    return [returnButton];
  };
  const window = { escapeHtml: value => String(value), toast() {}, openModal: value => { window.modal = value; } };
  vm.runInNewContext(fs.readFileSync("static/js/my-claims.js", "utf8"), {
    window, document: { getElementById: element }, crypto: webcrypto, URLSearchParams,
  });
  const row = {
    outbound_record_id: 21, form_no: "CK21", material_code: "102001", material_name: "物料",
    stock_source: "formal", batch_no: "B21", issued_quantity: 6, returned_quantity: 0,
    returnable_quantity: 6, inventory_status: "available", unit: "个",
  };
  const feature = window.WarehouseMyClaims.create({
    api: async (url, options) => {
      requests.push([url, options]);
      if (options) return { success: true };
      const page = Number(new URL(url, "http://test").searchParams.get("page"));
      return { items: Array.from({ length: page === 1 ? 20 : 1 }, () => row), total: 21, page, pages: 2 };
    },
    formatQty: String, moduleShell: (title, body) => body,
  });
  feature.render({});
  await new Promise(setImmediate);
  assert.equal((host.innerHTML.match(/data-claim-return=/g) || []).length, 20);
  element("#myClaimsNext").onclick();
  await new Promise(setImmediate);
  assert.equal((host.innerHTML.match(/data-claim-return=/g) || []).length, 1);
  returnButton.onclick();
  element("claimReturnQuantity").value = "7";
  await assert.rejects(window.modal.onOk, /可退数量/);
  element("claimReturnQuantity").value = "2";
  await window.modal.onOk();
  const post = requests.find(([, options]) => options);
  assert.equal(post[0], "/api/my-claims/21/return");
  assert.equal(JSON.parse(post[1].body).quantity, 2);
  assert.equal(JSON.parse(post[1].body).operation_key.length, 32);
  console.log("my claims: 20-row pagination, return dialog and quantity submission passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
