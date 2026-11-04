(() => {
  const usageOptions = selected => `<option value="研发用料" ${selected === "研发用料" ? "selected" : ""}>研发用料</option><option value="生产用料" ${selected === "生产用料" ? "selected" : ""}>生产用料</option>`;
  const projectOptions = (projects, selected, escapeHtml) => (projects || [])
    .filter(row => row.active !== false && row.active !== 0)
    .map(row => {
      const code = escapeHtml(row.code || "");
      return `<option value="${code}" ${row.code === selected ? "selected" : ""}>${code}</option>`;
    }).join("");
  const purchaseStatusText = {
    pending_summary: "待汇总",
    returned: "已退回",
    summarized: "已汇总",
    registered: "已登记",
  };

  function page(title, body) {
    return `<div class="module-content" data-page-title="${title}">${body}</div>`;
  }

  function placeResults(input, list) {
    const rect = input.getBoundingClientRect();
    list.style.left = `${Math.max(8, rect.left)}px`;
    list.style.top = `${rect.bottom + 4}px`;
    list.style.width = `${Math.max(rect.width, 320)}px`;
    list.style.display = "block";
  }

  async function renderProjects(main, ctx) {
    const rows = ctx.system.boot?.projects || [];
    const users = ctx.system.boot?.users || [];
    const userOptions = users.map(user => `<option value="${user.id}">${ctx.escapeHtml(user.display_name || user.username || "")}</option>`).join("");
    const memberOptions = users.map(user => `<label><input type="checkbox" value="${user.id}">${ctx.escapeHtml(user.display_name || user.username || "")}</label>`).join("") || `<span>暂无用户</span>`;
    main.innerHTML = page("项目设置", `
      <div class="work-panel"><div class="work-head"><h2>项目设置</h2></div><div class="work-body">
        <div class="form-grid">
          <label>代号<input id="projectCode"></label>
          <label>负责人<select id="projectOwner"><option value="">未设置</option>${userOptions}</select></label>
          <div class="wide">项目成员<div class="project-member-grid" id="projectMembers">${memberOptions}</div></div>
          <div class="wide"><button class="primary" id="saveProject" ${ctx.system.boot?.user_permissions?.manage_projects ? "" : "disabled"}>保存项目</button></div>
        </div>
        <table class="flow-table"><thead><tr><th>代号</th><th>负责人</th><th>成员</th><th>状态</th><th></th></tr></thead><tbody>
          ${rows.map(row => {
            const disableButton = row.code === "办公" ? "" : `<button type="button" data-disable-project="${row.id}">${row.active ? "停用" : "启用"}</button>`;
            const deleteButton = row.code === "办公" || row.active ? "" : `<button type="button" data-delete-project="${row.id}">删除</button>`;
            return `<tr><td>${ctx.escapeHtml(row.code)}</td><td>${ctx.escapeHtml(row.owner_name || "")}</td><td>${ctx.escapeHtml((row.members || []).map(item => item.display_name).join("、"))}</td><td>${row.active ? "启用" : "停用"}</td><td class="mini-actions">${disableButton}${deleteButton}<button type="button" data-edit-project="${row.id}">编辑</button></td></tr>`;
          }).join("")}
        </tbody></table>
      </div></div>
    `);
    const saveButton = document.getElementById("saveProject");
    const syncMemberOwnerLock = () => {
      const ownerId = String(document.getElementById("projectOwner").value || "");
      document.querySelectorAll("#projectMembers input").forEach(input => {
        const locked = Boolean(ownerId) && input.value === ownerId;
        input.disabled = locked;
        if (locked) input.checked = false;
      });
    };
    const fillForm = row => {
      const codeInput = document.getElementById("projectCode");
      codeInput.value = row.code || "";
      codeInput.readOnly = row.code === "办公";
      document.getElementById("projectOwner").value = row.owner_user_id || "";
      const selected = new Set((row.member_ids || []).map(id => String(id)));
      document.querySelectorAll("#projectMembers input").forEach(input => {
        input.checked = selected.has(input.value);
      });
      syncMemberOwnerLock();
      if (saveButton) {
        saveButton.dataset.editingId = String(row.id);
        saveButton.textContent = "保存修改";
      }
      codeInput.focus();
    };
    document.getElementById("projectOwner")?.addEventListener("change", syncMemberOwnerLock);
    saveButton?.addEventListener("click", async () => {
      try {
        const member_ids = [...document.querySelectorAll("#projectMembers input:checked")].map(input => Number(input.value)).filter(Boolean);
        const code = document.getElementById("projectCode").value.trim();
        const editingId = Number(saveButton.dataset.editingId || 0);
        const sameCode = rows.find(row => String(row.code || "").toLowerCase() === code.toLowerCase());
        if (!editingId && sameCode) {
          ctx.toast("项目代号已存在，请点编辑修改");
          return;
        }
        const existing = editingId ? rows.find(row => Number(row.id) === editingId) : null;
        const payload = { code, owner_user_id: document.getElementById("projectOwner").value, member_ids, active: existing ? existing.active : true };
        await ctx.api(existing ? `/api/projects/${existing.id}` : "/api/projects", { method: "POST", body: JSON.stringify(payload) });
        ctx.system.boot = null;
        ctx.toast("项目已保存");
        location.reload();
      } catch (error) {
        ctx.toast(error.message);
      }
    });
    main.querySelectorAll("[data-edit-project]").forEach(button => button.addEventListener("click", () => {
      const row = rows.find(item => Number(item.id) === Number(button.dataset.editProject));
      if (row) fillForm(row);
    }));
    main.querySelectorAll("[data-delete-project]").forEach(button => button.addEventListener("click", async () => {
      const row = rows.find(item => Number(item.id) === Number(button.dataset.deleteProject));
      if (!row || !window.confirm(`删除项目 ${row.code}？`)) return;
      try {
        await ctx.api(`/api/projects/${row.id}`, { method: "DELETE" });
        location.reload();
      } catch (error) {
        ctx.toast(error.message);
      }
    }));
    main.querySelectorAll("[data-disable-project]").forEach(button => button.addEventListener("click", async () => {
      const row = rows.find(item => Number(item.id) === Number(button.dataset.disableProject));
      try {
        await ctx.api(`/api/projects/${button.dataset.disableProject}`, { method: "POST", body: JSON.stringify({ code: row.code, owner_user_id: row.owner_user_id, member_ids: row.member_ids || [], active: !row.active }) });
        location.reload();
      } catch (error) {
        ctx.toast(error.message);
      }
    }));
  }

  function bindPurchaseRow(row, ctx) {
    const sync = () => {
      row.querySelector("[data-total]").value = (Number(row.querySelector("[data-qty]").value || 0) * Number(row.querySelector("[data-price]").value || 0)).toFixed(2);
    };
    const syncPlatform = () => {
      const platform = row.querySelector("[data-platform]");
      const electronic = row.querySelector("[data-electronic]").value === "是";
      platform.disabled = !electronic;
      if (!electronic) platform.value = "";
    };
    row.querySelector("[data-qty]").addEventListener("input", sync);
    row.querySelector("[data-price]").addEventListener("input", sync);
    row.querySelector("[data-electronic]").addEventListener("change", syncPlatform);
    row.querySelector("[data-remove]").addEventListener("click", () => row.remove());
    const nameInput = row.querySelector("[data-name]");
    const resultList = document.createElement("div");
    resultList.className = "material-results";
    nameInput.parentElement.classList.add("material-search");
    nameInput.parentElement.appendChild(resultList);
    let searchTimer = 0;
    nameInput.addEventListener("input", () => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(async () => {
        const keyword = nameInput.value.trim();
        if (!keyword) {
          resultList.style.display = "none";
          return;
        }
        const found = await ctx.api(`/api/materials/search?keyword=${encodeURIComponent(keyword)}`);
        const items = (Array.isArray(found) ? found : (found.items || [])).slice(0, 12);
        if (!items.length) {
          resultList.style.display = "none";
          return;
        }
        resultList.innerHTML = items.map((material, index) => `<button class="material-option" type="button" data-pick="${index}">${ctx.escapeHtml(material.material_code || "")} · ${ctx.escapeHtml(material.name || "")}<small>${ctx.escapeHtml(material.brand_model || "")} ${ctx.escapeHtml(material.spec || "")}</small></button>`).join("");
        placeResults(nameInput, resultList);
        resultList.querySelectorAll("[data-pick]").forEach(button => button.addEventListener("click", () => {
          const material = items[Number(button.dataset.pick)];
          nameInput.value = material.name || "";
          row.querySelector("[data-brand]").value = material.brand_model || "";
          row.querySelector("[data-spec]").value = material.spec || "";
          row.querySelector("[data-unit]").value = material.unit || "";
          resultList.style.display = "none";
        }));
      }, 200);
    });
    syncPlatform();
  }

  function purchaseLineData(item) {
    try {
      return JSON.parse(item.data_json || "{}");
    } catch (error) {
      return {};
    }
  }

  function purchaseStatusLabel(status) {
    return purchaseStatusText[status] || status || "";
  }

  async function renderPurchase(main, ctx) {
    const projects = ctx.system.boot?.projects || [];
    main.innerHTML = page("采购申请", `
      <div class="work-panel"><div class="work-head"><h2>发起采购申请</h2><button type="button" id="addPurchaseLine">添加行</button></div>
        <div class="work-body">
          <div id="purchaseLines"></div>
          <div class="submit-flow-actions"><button class="primary" id="submitPurchase">提交申请</button></div>
        </div>
      </div>
    `);
    const host = document.getElementById("purchaseLines");
    const addLine = () => {
      const row = document.createElement("div");
      row.className = "inline-row purchase";
      row.innerHTML = `<label>名称<input data-name></label><label>品牌型号<input data-brand></label><label>技术规格<input data-spec></label><label>单位<input data-unit></label><label>项目<select data-project>${projectOptions(projects, "", ctx.escapeHtml)}</select></label><label>用料形式<select data-usage>${usageOptions("研发用料")}</select></label><label>申请数量<input data-qty type="number" min="0" step="1"></label><label>预算单价<input data-price type="number" min="0" step="0.01"></label><label>预算总价<input data-total readonly></label><label>每单位数量<input data-pack type="number" min="1" step="1" value="1"></label><label>电子采购<select data-electronic><option value="否">否</option><option value="是">是</option></select></label><label>平台<input data-platform></label><button type="button" data-remove>删除</button>`;
      bindPurchaseRow(row, ctx);
      host.appendChild(row);
    };
    addLine();
    document.getElementById("addPurchaseLine").addEventListener("click", addLine);
    document.getElementById("submitPurchase").addEventListener("click", async () => {
      const items = [...host.querySelectorAll(".inline-row")].map(row => {
        const unit = row.querySelector("[data-unit]").value;
        return {
          name: row.querySelector("[data-name]").value,
          brand_model: row.querySelector("[data-brand]").value,
          spec: row.querySelector("[data-spec]").value,
          unit,
          purchase_project: row.querySelector("[data-project]").value,
          usage_kind: row.querySelector("[data-usage]").value,
          request_quantity: row.querySelector("[data-qty]").value,
          budget_unit_price: row.querySelector("[data-price]").value,
          pack_quantity: row.querySelector("[data-pack]").value,
          arrival_unit: unit,
          electronic_purchase: row.querySelector("[data-electronic]").value,
          platform: row.querySelector("[data-platform]").value,
        };
      }).filter(item => String(item.name || "").trim());
      if (!items.length) {
        ctx.toast("请填写物料名称");
        return;
      }
      try {
        await ctx.api("/api/purchase-requests", { method: "POST", body: JSON.stringify({ items }) });
        ctx.toast("采购申请已提交");
      } catch (error) {
        ctx.toast(error.message);
      }
    });
  }

  function summarySheetHtml(form, escapeHtml, { removable = false, acceptance = false } = {}) {
    const rows = (form.items || []).map(item => {
      const data = purchaseLineData(item);
      const unit = item.unit || data.unit || data.arrival_unit || "";
      const remove = removable ? `<td><button type="button" data-remove-line="${item.id}">撤行</button></td>` : "";
      const ready = Boolean(item.acceptance_ready);
      const select = acceptance ? `<td><input type="checkbox" data-accept-line="${item.id}" ${ready ? "" : "disabled"}></td>` : "";
      const status = acceptance ? `<td>${escapeHtml(item.coding_status || "")}</td>` : "";
      const action = acceptance ? `<td>${ready ? `<button type="button" data-start-acceptance="${item.id}">发起验收</button>` : ""}</td>` : "";
      return `<tr>${select}<td>${escapeHtml(item.material_name || "")}</td><td>${escapeHtml(item.brand_model || "")}</td><td>${escapeHtml(item.spec || "")}</td><td>${escapeHtml(unit)}</td><td>${escapeHtml(item.request_quantity ?? "")}</td><td>${escapeHtml(data.pack_quantity || 1)}</td><td>${escapeHtml(item.unit_price ?? "")}</td><td>${escapeHtml(data.budget_total ?? "")}</td><td>${escapeHtml(data.electronic_purchase || "")}</td><td>${escapeHtml(data.platform || "")}</td><td>${escapeHtml(item.purchase_project || "")}</td><td>${escapeHtml(data.usage_kind || "")}</td><td>${escapeHtml(item.purchase_applicant || "")}</td>${status}${action}${remove}</tr>`;
    }).join("");
    const removeHead = removable ? "<th></th>" : "";
    const selectHead = acceptance ? `<th><input type="checkbox" id="purchaseAcceptAll" aria-label="全选"></th>` : "";
    const statusHead = acceptance ? "<th>赋码状态</th><th></th>" : "";
    const toolbar = acceptance ? `<div class="purchase-toolbar"><button type="button" id="batchStartAcceptance">批量发起验收</button></div>` : "";
    return `${toolbar}<h3>${escapeHtml(form.form_no || "")} ${escapeHtml(purchaseStatusLabel(form.status))}</h3><table class="flow-table"><thead><tr>${selectHead}<th>名称</th><th>品牌型号</th><th>技术规格</th><th>单位</th><th>申请数量</th><th>每单位数量</th><th>预算单价</th><th>预算总价</th><th>电子采购</th><th>平台</th><th>项目</th><th>用料形式</th><th>申请人</th>${statusHead}${removeHead}</tr></thead><tbody>${rows || `<tr><td colspan="15">没有物料行</td></tr>`}</tbody></table><p>制表　　　　校对　　　　审核　　　　审批</p>`;
  }

  function printSummary(form) {
    const sheet = window.open("", "_blank");
    if (!sheet) return;
    const body = summarySheetHtml(form, value => String(value ?? "").replace(/[&<>"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;" }[char])));
    sheet.document.write(`<!DOCTYPE html><html><head><meta charset="utf-8"><title>${form.form_no || "采购汇总"}</title><style>body{font-family:"Microsoft YaHei",sans-serif;padding:24px;}table{border-collapse:collapse;width:100%;font-size:12px;}th,td{border:1px solid #333;padding:4px 6px;text-align:left;}p{margin-top:28px;letter-spacing:2px;}</style></head><body>${body}</body></html>`);
    sheet.document.close();
    sheet.focus();
    sheet.print();
  }

  async function renderPurchaseSummary(main, ctx) {
    main.innerHTML = page("汇总采购申请", `
      <div class="work-panel"><div class="work-head"><h2>待汇总</h2><span class="purchase-toolbar"><button type="button" id="refreshPendingPurchase">刷新</button><button type="button" class="primary" id="makePurchaseSummary">汇总采购申请</button></span></div>
        <div class="work-body" id="pendingPurchaseList"></div>
      </div>
    `);
    const loadPending = async () => {
      const data = await ctx.api("/api/purchase-requests?scope=pending");
      document.getElementById("pendingPurchaseList").innerHTML = `<table class="flow-table"><thead><tr><th><input type="checkbox" id="purchaseSelectAll" checked aria-label="全选"></th><th>单号</th><th>申请人</th><th>状态</th><th>明细</th><th></th></tr></thead><tbody>${(data.items || []).map(row => `<tr><td><input type="checkbox" data-request-id="${row.id}" checked></td><td>${ctx.escapeHtml(row.form_no || "")}</td><td>${ctx.escapeHtml(row.applicant_name || "")}</td><td>${ctx.escapeHtml(purchaseStatusLabel(row.status))}</td><td>${(row.items || []).map(item => ctx.escapeHtml(item.material_name || "")).join("、")}</td><td><button type="button" data-return-request="${row.id}">退回</button></td></tr>`).join("") || `<tr><td colspan="6">暂无待汇总申请</td></tr>`}</tbody></table>`;
      document.getElementById("purchaseSelectAll")?.addEventListener("change", event => {
        document.querySelectorAll("[data-request-id]").forEach(input => { input.checked = event.target.checked; });
      });
      document.querySelectorAll("[data-return-request]").forEach(button => button.addEventListener("click", async () => {
        const reason = window.prompt("退回原因");
        if (!reason) return;
        try {
          await ctx.api(`/api/purchase-requests/${button.dataset.returnRequest}/return`, { method: "POST", body: JSON.stringify({ reason }) });
          ctx.toast("已退回");
          await loadPending();
        } catch (error) {
          ctx.toast(error.message);
        }
      }));
    };
    document.getElementById("refreshPendingPurchase").addEventListener("click", () => loadPending().catch(error => ctx.toast(error.message)));
    document.getElementById("makePurchaseSummary").addEventListener("click", async () => {
      const request_ids = [...document.querySelectorAll("[data-request-id]:checked")].map(input => Number(input.dataset.requestId));
      try {
        const data = await ctx.api("/api/purchase-summaries", { method: "POST", body: JSON.stringify({ request_ids }) });
        ctx.toast(`汇总已生成 ${data.form?.form_no || ""}`);
        await loadPending();
      } catch (error) {
        ctx.toast(error.message);
      }
    });
    loadPending().catch(error => ctx.toast(error.message));
  }

  async function renderPurchaseHistory(main, ctx) {
    main.innerHTML = page("历史采购申请", `
      <div class="work-panel"><div class="work-head"><h2>历史采购申请</h2><button type="button" id="refreshPurchaseHistory">刷新</button></div>
        <div class="work-body" id="purchaseHistoryList"></div>
        <div class="work-body" id="purchaseHistoryDetail"></div>
      </div>
    `);
    const load = async () => {
      const data = await ctx.api("/api/purchase-summaries?scope=history");
      const forms = data.items || [];
      document.getElementById("purchaseHistoryList").innerHTML = `<table class="flow-table"><thead><tr><th>单号</th><th>制表人</th><th>状态</th><th>时间</th><th></th></tr></thead><tbody>${forms.map(row => `<tr><td>${ctx.escapeHtml(row.form_no || "")}</td><td>${ctx.escapeHtml(row.applicant_name || "")}</td><td>${ctx.escapeHtml(purchaseStatusLabel(row.status))}</td><td>${ctx.escapeHtml(row.created_at || "")}</td><td><button type="button" data-history-open="${row.id}">查看</button><button type="button" data-history-print="${row.id}">打印</button></td></tr>`).join("") || `<tr><td colspan="5">暂无汇总</td></tr>`}</tbody></table>`;
      const acceptanceItem = item => {
        const data = purchaseLineData(item);
        return {
          purchase_item_id: item.id,
          material_id: item.material_id || "",
          material_code: item.material_code || "",
          material_name: item.material_name || "",
          brand_model: item.brand_model || "",
          spec: item.spec || "",
          unit: item.unit || data.unit || data.arrival_unit || "",
          arrival_unit: data.arrival_unit || item.unit || "",
          purchase_applicant: item.purchase_applicant || "",
          purchase_project: item.purchase_project || "",
          usage_kind: data.usage_kind || "研发用料",
          pack_quantity: data.pack_quantity || 1,
          request_quantity: item.request_quantity || "",
        };
      };
      const startAcceptance = items => {
        const ready = items.filter(item => item.acceptance_ready);
        if (!ready.length) {
          ctx.toast("请选择已赋码且未验收入库的物料");
          return;
        }
        ctx.system.acceptancePrefill = {
          title: `${formNoTitle(ready)}`,
          items: ready.map(acceptanceItem),
        };
        if (ctx.openView) ctx.openView("acceptance");
      };
      const formNoTitle = () => {
        const openForm = document.getElementById("purchaseHistoryDetail")?.dataset.formNo || "";
        return openForm ? `${openForm} 到货验收` : "到货验收";
      };
      document.querySelectorAll("[data-history-open]").forEach(button => button.addEventListener("click", () => {
        const form = forms.find(row => Number(row.id) === Number(button.dataset.historyOpen));
        if (!form) return;
        const host = document.getElementById("purchaseHistoryDetail");
        host.dataset.formNo = form.form_no || "";
        host.innerHTML = summarySheetHtml(form, ctx.escapeHtml, { acceptance: true });
        document.getElementById("purchaseAcceptAll")?.addEventListener("change", event => {
          host.querySelectorAll("[data-accept-line]:not(:disabled)").forEach(input => { input.checked = event.target.checked; });
        });
        host.querySelectorAll("[data-start-acceptance]").forEach(action => action.addEventListener("click", () => {
          const item = (form.items || []).find(row => Number(row.id) === Number(action.dataset.startAcceptance));
          if (item) startAcceptance([item]);
        }));
        document.getElementById("batchStartAcceptance")?.addEventListener("click", () => {
          const ids = [...host.querySelectorAll("[data-accept-line]:checked")].map(input => Number(input.dataset.acceptLine));
          startAcceptance((form.items || []).filter(item => ids.includes(Number(item.id))));
        });
      }));
      document.querySelectorAll("[data-history-print]").forEach(button => button.addEventListener("click", () => {
        const form = forms.find(row => Number(row.id) === Number(button.dataset.historyPrint));
        if (form) printSummary(form);
      }));
    };
    document.getElementById("refreshPurchaseHistory").addEventListener("click", () => load().catch(error => ctx.toast(error.message)));
    load().catch(error => ctx.toast(error.message));
  }

  async function renderPurchaseCoding(main, ctx) {
    main.innerHTML = page("申请物料编码", `
      <div class="work-panel"><div class="work-head"><h2>待赋码汇总</h2><button type="button" id="refreshPurchaseCoding">刷新</button></div>
        <div class="work-body" id="purchaseCodingList"></div>
      </div>
      <div class="work-panel"><div class="work-head"><h2>赋码</h2><button type="button" class="primary" id="savePurchaseCodes" disabled>保存至库房</button></div>
        <div class="work-body" id="purchaseCodingDetail">请选择一张汇总。</div>
      </div>
    `);
    let currentForm = null;
    const load = async () => {
      const data = await ctx.api("/api/purchase-summaries?scope=coding");
      const forms = data.items || [];
      document.getElementById("purchaseCodingList").innerHTML = `<table class="flow-table"><thead><tr><th>单号</th><th>制表人</th><th>行数</th><th></th></tr></thead><tbody>${forms.map(row => `<tr><td>${ctx.escapeHtml(row.form_no || "")}</td><td>${ctx.escapeHtml(row.applicant_name || "")}</td><td>${(row.items || []).length}</td><td><button type="button" data-code-form="${row.id}">赋码</button></td></tr>`).join("") || `<tr><td colspan="4">暂无待赋码汇总</td></tr>`}</tbody></table>`;
      document.querySelectorAll("[data-code-form]").forEach(button => button.addEventListener("click", () => {
        const form = forms.find(row => Number(row.id) === Number(button.dataset.codeForm));
        if (form) openCoding(form).catch(error => ctx.toast(error.message));
      }));
      const pendingId = Number(ctx.system.codingFormId || 0);
      if (pendingId) {
        const form = forms.find(row => Number(row.id) === pendingId);
        if (form) await openCoding(form);
      }
    };
    const openCoding = async form => {
      currentForm = form;
      document.getElementById("savePurchaseCodes").disabled = false;
      const lines = await Promise.all((form.items || []).map(async item => {
        const keyword = [item.material_name, item.brand_model, item.spec].filter(Boolean).join(" ");
        const found = keyword
          ? await ctx.api(`/api/materials/search?keyword=${encodeURIComponent(item.material_name || item.brand_model || item.spec || "")}`)
          : { items: [] };
        const exact = await ctx.api(`/api/purchase-summaries/match?name=${encodeURIComponent(item.material_name || "")}&brand_model=${encodeURIComponent(item.brand_model || "")}&spec=${encodeURIComponent(item.spec || "")}`);
        return { item, matches: (Array.isArray(found) ? found : (found.items || [])), exact: exact.items || [] };
      }));
      document.getElementById("purchaseCodingDetail").innerHTML = `<table class="flow-table"><thead><tr><th>名称</th><th>品牌型号</th><th>技术规格</th><th>单位</th><th>物料赋码</th><th>所属分类编码</th><th>物品和服务分类代码</th><th></th></tr></thead><tbody>${lines.map(({ item, matches }) => {
        const data = purchaseLineData(item);
        const similar = (matches || []).slice(0, 5).map(material => `${material.material_code || ""} ${material.name || ""} ${material.brand_model || ""} ${material.spec || ""}`.trim()).join("；");
        return `<tr data-line-id="${item.id}"><td>${ctx.escapeHtml(item.material_name || "")}</td><td>${ctx.escapeHtml(item.brand_model || "")}</td><td>${ctx.escapeHtml(item.spec || "")}</td><td><input data-line-unit value="${ctx.escapeHtml(item.unit || data.unit || "")}"></td><td class="material-search"><input data-code maxlength="14" value="" placeholder="14位物料编码"><div data-result-list class="material-results"></div></td><td><input data-group-category readonly placeholder="所属分类编码"></td><td><input data-group-service readonly placeholder="物品和服务分类代码"></td><td><button type="button" data-ai-code="${item.id}">AI 赋码</button><button type="button" data-remove-line="${item.id}">撤行</button></td></tr><tr><td colspan="8" data-ai-note="${item.id}">${similar ? `相近物料：${ctx.escapeHtml(similar)}` : ""}</td></tr>`;
      }).join("") || `<tr><td colspan="8">这张汇总没有物料行</td></tr>`}</tbody></table>`;
      document.querySelectorAll("#purchaseCodingDetail [data-remove-line]").forEach(button => button.addEventListener("click", async () => {
        try {
          await ctx.api(`/api/purchase-summaries/${form.id}/remove-line`, { method: "POST", body: JSON.stringify({ item_id: Number(button.dataset.removeLine) }) });
          ctx.toast("已撤行，原申请回到待汇总");
          await load();
        } catch (error) {
          ctx.toast(error.message);
        }
      }));
      const paintJob = (itemId, job) => {
        const row = document.querySelector(`#purchaseCodingDetail [data-line-id="${itemId}"]`);
        const note = document.querySelector(`[data-ai-note="${itemId}"]`);
        if (!row || !note) return;
        const result = job.result || {};
        if (job.status === "succeeded") {
          const code = row.querySelector("[data-code]");
          if (code) code.value = result.material_code || "";
          const category = row.querySelector("[data-group-category]");
          const service = row.querySelector("[data-group-service]");
          if (category) category.value = result.group_category_code || "";
          if (service) service.value = result.group_service_code || "";
          const similar = (result.similar || []).map(item => `${item.material_code || ""} ${item.name || ""}`.trim()).filter(Boolean).join("；");
          const ruleButton = result.new_category && !result.rule_saved ? `<button type="button" data-confirm-rule="${job.id}">确认保存分类参考</button>` : "";
          note.innerHTML = `${ctx.escapeHtml(result.rationale || result.message || "赋码完成")}${result.standard_description ? `<br>标准描述：${ctx.escapeHtml(result.standard_description)}` : ""}${similar ? `<br>相近物料：${ctx.escapeHtml(similar)}` : ""}${ruleButton ? `<br>${ruleButton}` : ""}`;
          note.querySelector("[data-confirm-rule]")?.addEventListener("click", async () => {
            try {
              await ctx.api(`/api/coding-jobs/${job.id}/confirm-rule`, { method: "POST", body: JSON.stringify({}) });
              ctx.toast("分类参考已保存");
            } catch (error) {
              ctx.toast(error.message);
            }
          });
          return;
        }
        if (job.status === "needs_input") {
          const missing = (result.missing || []).join("、");
          note.innerHTML = `${ctx.escapeHtml(result.message || "请补充属性")}${missing ? `：${ctx.escapeHtml(missing)}` : ""}<br><input data-supplement placeholder="补充属性"><button type="button" data-send-supplement="${job.id}">提交补充</button>`;
          note.querySelector("[data-send-supplement]")?.addEventListener("click", async () => {
            try {
              const text = note.querySelector("[data-supplement]")?.value || "";
              await ctx.api(`/api/coding-jobs/${job.id}/supplement`, { method: "POST", body: JSON.stringify({ text }) });
              note.textContent = "正在根据补充信息继续赋码";
              await pollJob(itemId, job.id);
            } catch (error) {
              ctx.toast(error.message);
            }
          });
          return;
        }
        note.textContent = job.error || result.message || "赋码失败";
      };
      const pollJob = async (itemId, jobId) => {
        const note = document.querySelector(`[data-ai-note="${itemId}"]`);
        for (let attempt = 0; attempt < 150; attempt += 1) {
          await new Promise(resolve => window.setTimeout(resolve, 2000));
          const data = await ctx.api(`/api/coding-jobs/${jobId}`);
          const job = data.job || {};
          if (job.status === "queued" || job.status === "running") {
            if (note) note.textContent = "正在赋码";
            continue;
          }
          paintJob(itemId, job);
          return;
        }
        if (note) note.textContent = "赋码仍在进行，请稍后再次点击 AI 赋码";
      };
      document.querySelectorAll("#purchaseCodingDetail [data-ai-code]").forEach(button => button.addEventListener("click", async () => {
        const itemId = Number(button.dataset.aiCode);
        const note = document.querySelector(`[data-ai-note="${itemId}"]`);
        try {
          if (note) note.textContent = "正在赋码";
          const started = await ctx.api(`/api/purchase-summaries/${form.id}/items/${itemId}/ai-code`, { method: "POST", body: JSON.stringify({}) });
          await pollJob(itemId, started.job_id);
        } catch (error) {
          if (note) note.textContent = error.message;
          ctx.toast(error.message);
        }
      }));
      document.querySelectorAll("#purchaseCodingDetail [data-code]").forEach(input => {
        if (input.readOnly) return;
        const row = input.closest("[data-line-id]");
        const list = row.querySelector("[data-result-list]");
        const line = (form.items || []).find(item => Number(item.id) === Number(row.dataset.lineId));
        let timer = 0;
        input.addEventListener("input", () => {
          window.clearTimeout(timer);
          const typed = input.value.trim();
          if (!typed || /^\d{14}$/.test(typed)) {
            list.style.display = "none";
            return;
          }
          timer = window.setTimeout(async () => {
            const keyword = typed || line?.material_name || line?.brand_model || line?.spec || "";
            if (!keyword) {
              list.style.display = "none";
              return;
            }
            const found = await ctx.api(`/api/materials/search?keyword=${encodeURIComponent(keyword)}`);
            const items = (Array.isArray(found) ? found : (found.items || [])).slice(0, 12);
            if (!items.length) {
              list.style.display = "none";
              return;
            }
            list.innerHTML = items.map((material, index) => `<button class="material-option" type="button" data-pick="${index}">${ctx.escapeHtml(material.material_code || "")} · ${ctx.escapeHtml(material.name || "")}<small>${ctx.escapeHtml(material.brand_model || "")} ${ctx.escapeHtml(material.spec || "")}</small></button>`).join("");
            placeResults(input, list);
            list.querySelectorAll("[data-pick]").forEach(button => button.addEventListener("click", () => {
              input.value = items[Number(button.dataset.pick)].material_code || "";
              list.style.display = "none";
            }));
          }, 200);
        });
      });
    };
    document.getElementById("refreshPurchaseCoding").addEventListener("click", () => load().catch(error => ctx.toast(error.message)));
    document.getElementById("savePurchaseCodes").addEventListener("click", async () => {
      if (!currentForm) return;
      const items = [...document.querySelectorAll("#purchaseCodingDetail [data-line-id]")].map(row => ({
        id: Number(row.dataset.lineId),
        material_code: row.querySelector("[data-code]")?.value || "",
        suggested_code: "",
        unit: row.querySelector("[data-line-unit]")?.value || "",
        group_category_code: row.querySelector("[data-group-category]")?.value || "",
        group_service_code: row.querySelector("[data-group-service]")?.value || "",
      }));
      try {
        await ctx.api(`/api/purchase-summaries/${currentForm.id}/save`, { method: "POST", body: JSON.stringify({ items }) });
        ctx.toast("已保存至库房");
        currentForm = null;
        document.getElementById("purchaseCodingDetail").textContent = "请选择一张汇总。";
        document.getElementById("savePurchaseCodes").disabled = true;
        await load();
      } catch (error) {
        ctx.toast(error.message);
      }
    });
    load().catch(error => ctx.toast(error.message));
  }

  async function renderSpecial(main, ctx) {
    const projects = ctx.system.boot?.projects || [];
    const leaders = (ctx.system.boot?.users || []).filter(user => String(user.role_codes || "").split(",").includes("leader"));
    main.innerHTML = page("特殊用料", `
      <div class="work-panel"><div class="work-head"><h2>发起特殊用料</h2><button type="button" id="addSpecialLine">添加行</button></div>
        <div class="work-body">
          <div class="form-grid">
            <div class="wide special-head-row">
              <label class="special-reason">情况说明<textarea id="specialReason" rows="2"></textarea></label>
              <label class="compact-field">部门领导<select id="specialLeader">${leaders.map(user => `<option value="${user.id}">${ctx.escapeHtml(user.display_name || user.username || "")}</option>`).join("")}</select></label>
            </div>
            <div class="wide" id="specialLines"></div>
            <div class="wide submit-flow-actions"><button class="primary" id="submitSpecial">提交</button></div>
          </div>
        </div>
      </div>
    `);
    const host = document.getElementById("specialLines");
    const addLine = () => {
      const row = document.createElement("div");
      row.className = "inline-row special";
      row.innerHTML = `<label class="material-search">物料<input data-search placeholder="输入编号/名称/规格"><div data-result-list class="material-results"></div></label><label>编号<input data-code readonly></label><label>品牌型号<input data-brand readonly></label><label>技术规格<input data-spec readonly></label><label>数量<input data-qty type="number" min="0" step="1"></label><label>项目<select data-project>${projectOptions(projects, "", ctx.escapeHtml)}</select></label><label>用料形式<select data-usage>${usageOptions("研发用料")}</select></label><button type="button" data-remove>删除</button><input type="hidden" data-material-id>`;
      const input = row.querySelector("[data-search]");
      const list = row.querySelector("[data-result-list]");
      const projectSelect = row.querySelector("[data-project]");
      const usageSelect = row.querySelector("[data-usage]");
      let timer = 0;
      const clearPick = () => {
        row.querySelector("[data-material-id]").value = "";
        row.dataset.formal = "";
        row.dataset.materialName = "";
      };
      const search = async () => {
        const keyword = input.value.trim();
        if (!keyword) {
          list.style.display = "none";
          return;
        }
        const params = new URLSearchParams({
          keyword,
          usage_kind: usageSelect.value,
          purchase_project: projectSelect.value,
        });
        const found = await ctx.api(`/api/special-claims/materials?${params.toString()}`);
        const items = (Array.isArray(found) ? found : []).slice(0, 12);
        if (!items.length) {
          list.innerHTML = `<div class="material-option">没有正式库可用物料</div>`;
          placeResults(input, list);
          return;
        }
        list.innerHTML = items.map((material, index) => `<button class="material-option" type="button" data-material-index="${index}">${ctx.escapeHtml(material.material_code || "")} · ${ctx.escapeHtml(material.name || "")}<small>${ctx.escapeHtml(material.brand_model || "")} ${ctx.escapeHtml(material.spec || "")} · 正式库 ${material.formal_available_quantity}</small></button>`).join("");
        placeResults(input, list);
        list.querySelectorAll("[data-material-index]").forEach(button => button.addEventListener("click", () => {
          const material = items[Number(button.dataset.materialIndex)];
          row.querySelector("[data-material-id]").value = material.id || "";
          row.dataset.formal = String(material.formal_available_quantity || 0);
          row.dataset.materialName = material.name || material.material_code || "";
          input.value = material.name || "";
          row.querySelector("[data-code]").value = material.material_code || "";
          row.querySelector("[data-brand]").value = material.brand_model || "";
          row.querySelector("[data-spec]").value = material.spec || "";
          list.style.display = "none";
        }));
      };
      input.addEventListener("input", () => {
        window.clearTimeout(timer);
        clearPick();
        timer = window.setTimeout(() => search().catch(error => ctx.toast(error.message)), 220);
      });
      const resetPick = () => {
        if (!row.querySelector("[data-material-id]").value) return;
        clearPick();
        input.value = "";
        row.querySelector("[data-code]").value = "";
        row.querySelector("[data-brand]").value = "";
        row.querySelector("[data-spec]").value = "";
      };
      projectSelect.addEventListener("change", resetPick);
      usageSelect.addEventListener("change", resetPick);
      row.querySelector("[data-remove]").addEventListener("click", () => row.remove());
      document.addEventListener("click", event => {
        if (!row.contains(event.target)) list.style.display = "none";
      });
      host.appendChild(row);
    };
    addLine();
    document.getElementById("addSpecialLine").addEventListener("click", addLine);
    document.getElementById("submitSpecial").addEventListener("click", async () => {
      try {
        const items = [...host.querySelectorAll(".inline-row")].map(row => ({
          material_id: Number(row.querySelector("[data-material-id]").value || 0),
          request_quantity: row.querySelector("[data-qty]").value,
          purchase_project: row.querySelector("[data-project]").value,
          usage_kind: row.querySelector("[data-usage]").value,
        }));
        if (!items.length || items.some(item => !item.material_id)) throw new Error("请先从搜索结果选择物料");
        const grouped = new Map();
        [...host.querySelectorAll(".inline-row")].forEach(row => {
          const materialId = Number(row.querySelector("[data-material-id]").value || 0);
          const quantity = Number(row.querySelector("[data-qty]").value || 0);
          const project = row.querySelector("[data-project]").value;
          const usage = row.querySelector("[data-usage]").value;
          if (!materialId) return;
          const key = `${materialId}|${project}|${usage}`;
          const current = grouped.get(key) || { name: row.dataset.materialName || "物料", formal: Number(row.dataset.formal || 0), quantity: 0 };
          current.quantity += quantity;
          grouped.set(key, current);
        });
        for (const item of grouped.values()) {
          if (item.quantity > item.formal + 1e-9) throw new Error(`${item.name} 正式库可用数量不足`);
        }
        await ctx.api("/api/special-claims", { method: "POST", body: JSON.stringify({ reason: document.getElementById("specialReason").value, leader_id: document.getElementById("specialLeader").value, items }) });
        ctx.toast("特殊用料已提交");
      } catch (error) {
        ctx.toast(error.message);
      }
    });
  }

  window.WarehouseInventoryFlow = { renderProjects, renderPurchase, renderPurchaseSummary, renderPurchaseHistory, renderPurchaseCoding, renderSpecial };
})();
