(() => {
  async function systemApi(url, options = {}) {
    const response = await fetch(url, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    if (response.status === 401) {
      window.location.href = "/login";
      throw new Error("请先登录");
    }
    if (!response.ok) {
      let message = "请求失败";
      try {
        const data = await response.json();
        if (data.must_change_password) {
          window.location.href = "/change-password";
        }
        message = data.error || message;
      } catch (error) {}
      throw new Error(message);
    }
    const type = response.headers.get("content-type") || "";
    return type.includes("application/json") ? response.json() : response.text();
  }

  function money(value) {
    return "¥" + Number(value || 0).toFixed(2);
  }

  function formatDateCn(value) {
    const text = String(value || "").slice(0, 10);
    const match = text.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    return match ? match[1] + "年" + match[2] + "月" + match[3] + "日" : window.escapeHtml(text);
  }

  function formatInputQty(value) {
    const number = Number(value || 0);
    return Number.isInteger(number) ? String(number) : String(Number(number.toFixed(4)));
  }

  window.WarehouseSystemCore = {
    systemApi,
    money,
    formatDateCn,
    formatInputQty,
  };
})();
