document.addEventListener("DOMContentLoaded", () => {
  const app = document.querySelector("[data-wb-app]");
  document.querySelector("[data-wb-toggle]")?.addEventListener("click", () => app?.classList.toggle("is-menu-open"));
  document.querySelectorAll("[data-wb-close]").forEach(item => item.addEventListener("click", () => app?.classList.remove("is-menu-open")));
  document.querySelectorAll("[data-wb-dismiss]").forEach(button => button.addEventListener("click", () => {
    const toast = button.closest(".wb-toast");
    toast?.classList.add("is-leaving");
    window.setTimeout(() => toast?.remove(), 180);
  }));
  document.querySelectorAll(".fin-table:not(.desktop-table):not(.reconciliation-table):not(.distribution-progress)").forEach(table => {
    const headings = [...table.querySelectorAll("thead th")].map(cell => cell.textContent.trim());
    if (!headings.length) return;
    table.classList.add("fin-table--responsive");
    table.querySelectorAll("tbody tr").forEach(row => {
      [...row.children].forEach((cell, index) => {
        if (headings[index]) cell.dataset.label = headings[index];
      });
    });
  });
  document.querySelectorAll(".fin-card, .crm-panel").forEach((card, index) => {
    card.style.setProperty("--reveal-order", Math.min(index, 8));
    card.classList.add("wb-reveal");
  });
  document.addEventListener("keydown", event => { if (event.key === "Escape") app?.classList.remove("is-menu-open"); });
});

document.addEventListener("DOMContentLoaded", () => {
  const command = document.querySelector("[data-command]");
  const commandInput = document.getElementById("wb-global-search");
  let previousFocus = null;
  const openCommand = () => {
    if (!command) return;
    previousFocus = document.activeElement;
    command.hidden = false;
    document.body.classList.add("wb-overlay-open");
    window.setTimeout(() => commandInput?.focus(), 0);
  };
  const closeCommand = () => {
    if (!command || command.hidden) return;
    command.hidden = true;
    document.body.classList.remove("wb-overlay-open");
    previousFocus?.focus?.();
  };
  document.querySelectorAll("[data-command-open]").forEach(button => button.addEventListener("click", openCommand));
  document.querySelectorAll("[data-command-close]").forEach(button => button.addEventListener("click", closeCommand));
  document.addEventListener("keydown", event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); openCommand(); }
    if (event.key === "Escape") closeCommand();
  });

  const modal = document.querySelector("[data-wb-modal]");
  let modalPreviousFocus = null;
  const closeModal = () => {
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    document.body.classList.remove("wb-overlay-open");
    modalPreviousFocus?.focus?.();
  };
  document.addEventListener("click", event => { if (event.target.closest("[data-modal-close]")) closeModal(); });
  document.addEventListener("keydown", event => { if (event.key === "Escape") closeModal(); });
  document.addEventListener("htmx:afterSwap", event => {
    if (event.detail.target?.id !== "app-modal-content" || !modal) return;
    modalPreviousFocus = document.activeElement;
    modal.hidden = false; document.body.classList.add("wb-overlay-open");
    window.setTimeout(() => modal.querySelector("input:not([type=hidden]), select, textarea, button")?.focus(), 0);
  });
  if (modal && !modal.hidden) {
    document.body.classList.add("wb-overlay-open");
    window.setTimeout(() => modal.querySelector("input:not([type=hidden]), select, textarea, button")?.focus(), 0);
  }
  modal?.addEventListener("keydown", event => {
    if (event.key !== "Tab") return;
    const focusable = [...modal.querySelectorAll('button:not([disabled]), a[href], input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')].filter(item => item.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  document.addEventListener("workbench:modal-close", closeModal);
  document.addEventListener("finance:changed", () => {
    if (document.querySelector(".fin-shell")) window.setTimeout(() => window.location.reload(), 500);
  });
});

function workbenchToast(message, level = "success") {
  if (!message) return;
  let stack = document.querySelector(".wb-toasts");
  if (!stack) { stack = document.createElement("div"); stack.className = "wb-toasts"; document.body.append(stack); }
  const toast = document.createElement("div");
  toast.className = `wb-toast${level === "error" ? " wb-toast--error" : ""}`;
  const icon = document.createElement("i"); icon.className = level === "error" ? "fa-solid fa-circle-exclamation" : "fa-solid fa-circle-check";
  const text = document.createElement("span"); text.textContent = message;
  const close = document.createElement("button"); close.type = "button"; close.setAttribute("aria-label", "Закрыть"); close.textContent = "×";
  close.addEventListener("click", () => toast.remove());
  toast.append(icon, text, close); stack.append(toast);
  window.setTimeout(() => { toast.classList.add("is-leaving"); window.setTimeout(() => toast.remove(), 180); }, 3500);
}

document.addEventListener("workbench:toast", event => workbenchToast(event.detail?.message, event.detail?.level));
document.addEventListener("htmx:beforeSwap", event => {
  if (event.detail.xhr.status === 422) { event.detail.shouldSwap = true; event.detail.isError = false; }
});
document.addEventListener("htmx:responseError", event => {
  if (event.detail.xhr.status !== 422) workbenchToast("Не удалось выполнить действие. Обновите страницу и попробуйте снова.", "error");
});
