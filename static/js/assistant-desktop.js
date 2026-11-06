/* 独立窗口复用网页小助手与服务器会话。 */
(() => {
  "use strict";
  const login = document.getElementById("desktopLogin");
  const error = document.getElementById("desktopError");
  let bridge = null;
  let stream = null;
  let seenNotifications = new Set();
  let seenTodos = new Set();
  let initialized = false;
  const channelReady = new Promise(resolve => {
    if (window.qt && window.QWebChannel) {
      new QWebChannel(qt.webChannelTransport, channel => {
        bridge = channel.objects.desktop;
        resolve();
      });
    } else {
      resolve();
    }
  });

  async function start() {
    const response = await fetch("/api/session");
    const data = await response.json();
    if (!data.authenticated) {
      login.hidden = false;
      return;
    }
    if (data.user.must_change_password) {
      error.textContent = "请先在库房系统修改初始密码，再登录小助手。";
      login.hidden = false;
      return;
    }
    login.hidden = true;
    const host = document.getElementById("desktopAssistant");
    host.innerHTML = window.WarehouseAssistant.html("你好，我是物料小助手。我可以帮你查询库存和待办、填写表单、处理流程、辅助物料编码，也可以帮你记下事项并在物料可申领时提醒。");
    window.toast = message => {
      const toast = document.getElementById("desktopToast");
      toast.textContent = String(message);
      toast.hidden = false;
      window.setTimeout(() => { toast.hidden = true; }, 5000);
    };
    window.WarehouseAssistant.mount({
      desktop:true, system:{ boot:{ user:data.user } }, openWorkflow:showWorkflow,
    });
    document.getElementById("aiFloatBox").classList.add("open");
    document.getElementById("aiFloatClose").addEventListener("click", () => {
      if (window.chrome?.webview) window.chrome.webview.postMessage({ type: "assistant-close" });
      else bridge?.hideChat();
    });
    await channelReady;
    // Windows 客户端已负责实时通知，避免在助手窗口重复订阅并弹窗。
    if (!window.chrome?.webview) watch();
  }

  async function showWorkflow(id) {
    const response = await fetch("/api/ai/assistant/workflows/" + id);
    const data = await response.json();
    if (!response.ok || !data.form) throw new Error(data.error || "读取流程失败");
    const form = data.form;
    const panel = document.createElement("section");
    panel.className = "desktop-detail";
    const close = document.createElement("button");
    close.className = "desktop-detail-close";
    close.type = "button";
    close.textContent = "关闭";
    close.addEventListener("click", () => panel.remove());
    const title = document.createElement("h2");
    title.textContent = form.title || form.form_no;
    const header = document.createElement("p");
    header.textContent = [form.form_no, form.status, form.applicant_name, form.created_at].filter(Boolean).join(" · ");
    panel.append(close, title, header);
    const itemsTitle = document.createElement("h3");
    itemsTitle.textContent = "物料明细";
    panel.appendChild(itemsTitle);
    (form.items || []).forEach(item => {
      const row = document.createElement("div");
      row.className = "desktop-detail-row";
      row.textContent = [
        [item.material_code, item.material_name, item.brand_model, item.spec].filter(Boolean).join(" · "),
        "申请数量：" + (item.request_quantity ?? "") + " " + (item.unit || ""),
      ].join("\n");
      panel.appendChild(row);
    });
    const tasksTitle = document.createElement("h3");
    tasksTitle.textContent = "办理记录";
    panel.appendChild(tasksTitle);
    (form.tasks || []).forEach(task => {
      const row = document.createElement("div");
      row.className = "desktop-detail-row";
      row.textContent = [task.step_code, task.assignee_name, task.decision, task.signature, task.signed_at].filter(Boolean).join(" · ");
      panel.appendChild(row);
    });
    document.body.appendChild(panel);
  }

  async function watch() {
    if (stream) stream.close();
    stream = new EventSource("/api/todos/stream");
    stream.addEventListener("todos", async event => {
      const data = JSON.parse(event.data);
      const response = await fetch("/api/ai/assistant/prompts");
      if (!response.ok) return;
      const prompts = await response.json();
      const todos = prompts.todos || [];
      const notifications = data.notifications || [];
      const todoKeys = new Set(todos.map(item => item.form_type + ":" + item.task_id));
      const newTodos = todos.filter(item => !seenTodos.has(item.form_type + ":" + item.task_id));
      const newNotifications = notifications.filter(item => !seenNotifications.has(item.id));
      seenTodos = todoKeys;
      seenNotifications = new Set(notifications.map(item => item.id));
      if (initialized && newTodos.length) {
        bridge?.notify("待办提醒", "你有 " + newTodos.length + " 个新的待办，需要查看吗？");
        window.WarehouseAssistant.checkPrompts();
      }
      if (initialized || newNotifications.length) {
        newNotifications.slice(0, 3).forEach(item => {
          bridge?.notify(item.title || "物料提醒", item.body || "");
          if (item.category === "assistant") {
            window.WarehouseAssistant.prompt(item.body || item.title, "", "note:" + item.id);
          }
        });
      }
      const incomplete = prompts.incomplete_notes || [];
      if (!initialized && incomplete.length) bridge?.notify("事项待补充", "你有需要补充信息的事项，需要查看吗？");
      initialized = true;
    });
  }

  login.addEventListener("submit", async event => {
    event.preventDefault();
    error.textContent = "";
    try {
      const response = await fetch("/api/assistant/desktop-login", {
        method:"POST", headers:{"Content-Type":"application/json"},
        body:JSON.stringify({username:document.getElementById("desktopUsername").value,
                             password:document.getElementById("desktopPassword").value}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "登录失败");
      document.getElementById("desktopPassword").value = "";
      await start();
    } catch (cause) { error.textContent = cause.message; }
  });
  start().catch(cause => { login.hidden = false; error.textContent = cause.message; });
})();
