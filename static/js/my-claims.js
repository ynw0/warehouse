(() => {
  function create({ api, formatQty, moduleShell }) {
    let rows = [];
    let page = 1;
    let pages = 1;
    let query = "";
    const escape = value => window.escapeHtml(value ?? "");

    function returnLabel(row) {
      if (row.can_return) return "退库";
      if (Number(row.returnable_quantity || 0) <= 0) {
        return Number(row.consumed_quantity || 0) > 0 ? "已消耗" : "已全部退库";
      }
      if (row.inventory_status == null) return "原批次缺失";
      if (row.inventory_status !== "available") return "批次处理中";
      return "退库";
    }

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
          <th>批次</th><th>库别</th><th>领用日期</th><th>已领数量</th><th>已退数量</th><th>已消耗</th><th>状态</th><th>可退数量</th><th>操作</th></tr></thead>
          <tbody>${rows.map((row, index) => `<tr>
            <td>${escape(row.form_no)}</td><td>${escape(row.material_code)}</td>
            <td>${escape(row.material_name)}</td><td>${escape(row.spec || row.brand_model)}</td>
            <td>${escape(row.batch_no)}</td><td>${row.stock_source === "temporary" ? "临时库" : "正式库"}</td>
            <td>${escape(row.operation_date)}</td><td>${formatQty(row.issued_quantity)} ${escape(row.unit)}</td>
            <td>${formatQty(row.returned_quantity)}</td>
            <td>${formatQty(row.consumed_quantity || 0)}</td>
            <td>${escape(row.consume_status || "")}</td>
            <td>${formatQty(row.returnable_quantity)}</td>
            <td><button data-claim-return="${index}" ${row.can_return ? "" : "disabled"}>${returnLabel(row)}</button></td>
          </tr>`).join("") || '<tr><td colspan="13">暂无领用记录</td></tr>'}</tbody></table></div>
          <div class="pager"><span>共 ${data.total} 条，第 ${page} / ${pages} 页</span>
          <button id="myClaimsPrev" ${page <= 1 ? "disabled" : ""}>上一页</button>
          <button id="myClaimsNext" ${page >= pages ? "disabled" : ""}>下一页</button></div>`;
        host.querySelector("#myClaimsPrev").onclick = () => { page -= 1; load(); };
        host.querySelector("#myClaimsNext").onclick = () => { page += 1; load(); };
        host.querySelectorAll("[data-claim-return]").forEach(button => {
          button.onclick = () => {
            const row = rows[Number(button.dataset.claimReturn)];
            if (!row || !row.can_return) return;
            openReturn(row);
          };
        });
      } catch (error) {
        host.textContent = error.message;
      }
    }

    function openReturn(row) {
      const operationKey = Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, "0")).join("");
      const warehouseOptions = window.warehouseStepOptions
        ? window.warehouseStepOptions("claim_return", "return_inbound", "warehouse")
        : "";
      window.openModal({
        title: "退库",
        body: `<div class="form-grid">
          <p class="wide">${escape(row.material_code)} · ${escape(row.material_name)}<br>
          领用单：${escape(row.form_no)} · 批次：${escape(row.batch_no)}<br>
          可退数量：${formatQty(row.returnable_quantity)} ${escape(row.unit)}</p>
          <label>状态<select id="claimReturnCondition"><option value="正常">正常</option><option value="损坏">损坏</option></select></label>
          <label class="wide" id="claimReturnRemarkLabel" hidden>备注<textarea id="claimReturnRemark" rows="2"></textarea></label>
          <label class="wide">库房入库人<select id="claimReturnWarehouse">${warehouseOptions}</select></label>
          <label class="wide">退库数量<input id="claimReturnQuantity" type="number" min="0" max="${row.returnable_quantity}" step="any" value="${row.returnable_quantity}"></label>
        </div>`,
        okText: "提交退库",
        onReady: () => {
          const condition = document.getElementById("claimReturnCondition");
          const remarkLabel = document.getElementById("claimReturnRemarkLabel");
          condition.addEventListener("change", () => {
            remarkLabel.hidden = condition.value !== "损坏";
          });
        },
        onOk: async () => {
          const quantity = Number(document.getElementById("claimReturnQuantity").value);
          if (!Number.isFinite(quantity) || quantity <= 0 || quantity > row.returnable_quantity) {
            throw new Error("请输入大于0且不超过可退数量的数值");
          }
          const condition = document.getElementById("claimReturnCondition").value;
          const remark = document.getElementById("claimReturnRemark").value.trim();
          if (condition === "损坏" && !remark) throw new Error("损坏必须填写备注");
          const warehouseUserId = document.getElementById("claimReturnWarehouse").value;
          await api("/api/my-claims/" + (row.source_id || row.outbound_record_id) + "/return", {
            method: "POST",
            body: JSON.stringify({
              source_kind: row.source_kind || "claim_outbound",
              source_id: row.source_id || row.outbound_record_id,
              batch_id: row.batch_id,
              quantity,
              condition_status: condition,
              remark,
              warehouse_user_id: warehouseUserId,
              operation_key: operationKey,
            }),
          });
          window.toast("已提交，待库房入库");
          await load();
        },
      });
    }

    function render(main) {
      page = 1;
      query = "";
      main.innerHTML = moduleShell("我已领用", `<div class="work-panel">
        <div class="work-head"><h2>我的领用物料</h2></div><div class="work-body">
        <div class="my-claims-query">
          <label>查询<input id="myClaimsQuery" placeholder="物料编号 / 名称 / 品牌 / 规格 / 采购申请人 / 领用单号"></label>
          <button id="myClaimsSearch" class="primary">查询</button>
        </div>
        <div id="myClaimsResult"></div>
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
