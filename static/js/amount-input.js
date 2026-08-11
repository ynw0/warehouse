(function () {
  "use strict";

  function numberValue(value) {
    const result = Number(value);
    return Number.isFinite(result) && result >= 0 ? result : 0;
  }

  function unitPriceFromTotal(totalAmount, quantity) {
    const qty = numberValue(quantity);
    return qty > 0 ? numberValue(totalAmount) / qty : 0;
  }

  function bind({ quantityInput, totalAmountInput, unitPriceInput }) {
    if (!quantityInput || !totalAmountInput || !unitPriceInput) return () => {};
    if (unitPriceInput._warehouseAmountSync) {
      unitPriceInput._warehouseAmountSync();
      return unitPriceInput._warehouseAmountSync;
    }
    const sync = () => {
      const price = unitPriceFromTotal(totalAmountInput.value, quantityInput.value);
      unitPriceInput.value = String(Number(price.toFixed(4)));
    };
    quantityInput.addEventListener("input", sync);
    totalAmountInput.addEventListener("input", sync);
    unitPriceInput._warehouseAmountSync = sync;
    sync();
    return sync;
  }

  window.WarehouseAmountInput = { unitPriceFromTotal, bind };
}());
