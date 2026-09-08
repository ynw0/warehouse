(() => {
  function create({ api, formatQty, moduleShell }) {
    let rows = [];
    let page = 1;
    let pages = 1;
    let query = "";
    const escape = value => window.escapeHtml(value ?? "");

    async function load() {
      const host = document.getElementById("myClaimsResult");
      if (!host) return;
      host.textContent = "加载中…";
      try {
        const data = await api("/api/my-claims?" + new URLSearchParams({ page, q: query }));
        if (host !== document.getElementById("myClaimsResult")) return;
        rows = data.items;
        page = data.page;
        pages = data.pages;
        host.innerHTML = `<div class="table-wrapper"><table class="flow-table">
          <thead><tr><th>领用单号</th><th>物料编号</th><th>物料名称</th><th>规格</th>
          <th>批次</th><th>库别</th><th>领用日期</th><th>已领数量</th><th>已退数量</th><th>可退数量</th><th>操作</th></tr></thead>
          <tbody>${rows.map(row => `<tr>
            <td>${escape(row.form_no)}</td><td>${escape(row.material_code)}</td>
            <td>${escape(row.material_name)}</td><td>${escape(row.spec || row.brand_model)}</td>
            <td>${escape(row.batch_no)}</td><td>${row.stock_source === "temporary" ? "临时库" : "正式库"}</td>
            <td>${escape(row.operation_date)}</td><td>${formatQty(row.issued_quantity)} ${escape(row.unit)}</td>
            <td>${formatQty(row.returned_quantity)}</td><td>${formatQty(row.returnable_quantity)}</td>
            <td><button data-claim-return="${row.outbound_record_id}" ${row.returnable_quantity <= 0 || row.inventory_status !== "available" ? "disabled" : ""}>${row.returnable_quantity <= 0 ? "已全部退库" : row.inventory_status == null ? "原批次缺失" : row.inventory_status !== "available" ? "批次处理中" : "退库"}</button></td>
          </tr>`).join("") || '<tr><td colspan="11">暂无领用记录</td></tr>'}</tbody></table></div>
          <div class="pager"><span>共 ${data.total} 条，第 ${page} / ${pages} 页</span>
          <button id="myClaimsPrev" ${page <= 1 ? "disabled" : ""}>上一页</button>
          <button id="myClaimsNext" ${page >= pages ? "disabled" : ""}>下一页</button></div>`;
        host.querySelector("#myClaimsPrev").onclick = () => { page -= 1; load(); };
        host.querySelector("#myClaimsNext").onclick = () => { page += 1; load(); };
        host.querySelectorAll("[data-claim-return]").forEach(button => {
          button.onclick = () => openReturn(rows.find(row => row.outbound_record_id === Number(button.dataset.claimReturn)));
        });
      } catch (error) {
        host.textContent = error.message;
      }
    }

    function openReturn(row) {
      const operationKey = Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, "0")).join("");
      window.openModal({
        title: "退库",
        body: `<div class="form-grid">
          <p class="wide">${escape(row.material_code)} · ${escape(row.material_name)}<br>
          领用单：${escape(row.form_no)} · 批次：${escape(row.batch_no)}<br>
          可退数量：${formatQty(row.returnable_quantity)} ${escape(row.unit)}</p>
          <p class="wide">退回物料统一进入临时库，需由库管确认后再转入正式库。</p>
          <label class="wide">退库数量<input id="claimReturnQuantity" type="number" min="0" max="${row.returnable_quantity}" step="any" value="${row.returnable_quantity}"></label>
        </div>`,
        okText: "确认退库",
        onOk: async () => {
          const quantity = Number(document.getElementById("claimReturnQuantity").value);
          if (!Number.isFinite(quantity) || quantity <= 0 || quantity > row.returnable_quantity) {
            throw new Error("请输入大于0且不超过可退数量的数值");
          }
          await api("/api/my-claims/" + row.outbound_record_id + "/return", {
            method: "POST", body: JSON.stringify({ quantity, operation_key: operationKey }),
          });
          window.toast("退库成功");
          await load();
        },
      });
    }

    function render(main) {
      page = 1;
      query = "";
      main.innerHTML = moduleShell("我已领用", `<div class="work-panel">
        <div class="work-head"><h2>我的领用物料</h2></div><div class="work-body">
        <div class="form-grid"><label class="wide">查询<input id="myClaimsQuery" placeholder="物料编号 / 名称 / 领用单号"></label></div>
        <button id="myClaimsSearch" class="primary">查询</button><div id="myClaimsResult"></div>
        </div></div>`);
      const search = () => { query = document.getElementById("myClaimsQuery").value.trim(); page = 1; load(); };
      document.getElementById("myClaimsSearch").onclick = search;
      document.getElementById("myClaimsQuery").onkeydown = event => { if (event.key === "Enter") search(); };
      load();
    }

    return { render };
  }
  window.WarehouseMyClaims = { create };
})();
