function initializeCrm(root = document) {
  root.querySelectorAll("[data-secret]:not([data-crm-ready])").forEach((button) => {
    button.dataset.crmReady = "1";
    button.addEventListener("click", () => {
      const visible = button.dataset.visible === "1";
      button.textContent = visible ? "Показать" : button.dataset.secret;
      button.dataset.visible = visible ? "0" : "1";
    });
  });
  const form = document.querySelector("[data-client-search-url]");
  const input = document.getElementById("clientLookup");
  const results = document.getElementById("clientResults");
  if (!form || !input || !results || form.dataset.crmReady) return;
  form.dataset.crmReady = "1";
  let timer;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const query = input.value.trim();
    if (query.length < 2) { results.innerHTML = ""; return; }
    timer = setTimeout(async () => {
      const response = await fetch(`${form.dataset.clientSearchUrl}?q=${encodeURIComponent(query)}`, {headers:{"X-Requested-With":"XMLHttpRequest"}});
      const data = await response.json();
      results.innerHTML = data.results.map((client) => `<div class="crm-search-card"><b>${escapeHtml(client.name)}</b> · ${escapeHtml(client.phone)}<br><button type="button" class="crm-btn" data-client="${encodeURIComponent(JSON.stringify(client))}">Выбрать клиента</button>${client.devices.map(d => `<button type="button" class="crm-btn" data-client-device="${encodeURIComponent(JSON.stringify({client,device:d}))}">${escapeHtml(d.name)} ${escapeHtml(d.serial || "")}</button>`).join("")}</div>`).join("") || `<div class="crm-search-card">Совпадений нет — заполните нового клиента ниже.</div>`;
    }, 250);
  });
  results.addEventListener("click", (event) => {
    const clientButton = event.target.closest("[data-client]");
    const deviceButton = event.target.closest("[data-client-device]");
    const payload = deviceButton ? JSON.parse(decodeURIComponent(deviceButton.dataset.clientDevice)) : clientButton ? {client: JSON.parse(decodeURIComponent(clientButton.dataset.client))} : null;
    if (!payload) return;
    const client = payload.client;
    form.querySelector("[name=existing_client]").value = client.id;
    form.querySelector("[name=client_name]").value = client.name;
    form.querySelector("[name=phone]").value = client.phone;
    if (payload.device) {
      form.querySelector("[name=existing_device]").value = payload.device.id;
      document.getElementById("selectedDevice").innerHTML = `<div class="crm-alert">Выбрано: <b>${escapeHtml(payload.device.name)}</b> ${escapeHtml(payload.device.serial || "")}</div>`;
    }
    results.innerHTML = `<div class="crm-alert">Найден клиент: <b>${escapeHtml(client.name)}</b> · ${escapeHtml(client.phone)}</div>`;
  });
  const issueTemplate = form.querySelector("[name=issue_template]");
  const issueDataNode = document.getElementById("crm-issue-templates-data");
  const issueData = issueDataNode ? JSON.parse(issueDataNode.textContent) : [];
  if (issueTemplate) issueTemplate.addEventListener("change", () => {
    const issue = form.querySelector("[name=issue_description]");
    const selected = issueData.find(item => String(item.id) === issueTemplate.value);
    if (!issue.value && selected) issue.value = selected.text;
  });
  const brandInput = form.querySelector("[name=brand]");
  const modelList = document.getElementById("crm-device-models");
  if (brandInput && modelList) brandInput.addEventListener("change", async () => {
    const url = brandInput.dataset.modelsUrl;
    const response = await fetch(`${url}?brand=${encodeURIComponent(brandInput.value)}`);
    const data = await response.json();
    modelList.innerHTML = data.results.map(item => `<option value="${escapeHtml(item.name)}"></option>`).join("");
  });
  form.querySelectorAll("[data-chip-group] button").forEach((button) => {
    button.addEventListener("click", () => {
      const group = button.closest("[data-chip-group]").dataset.chipGroup;
      const target = form.querySelector(`[data-chip-target="${group}"]`);
      if (!target) return;
      const values = target.value.split(",").map(value => value.trim()).filter(Boolean);
      if (!values.includes(button.textContent.trim())) values.push(button.textContent.trim());
      target.value = values.join(", ");
      target.focus();
    });
  });
}
document.addEventListener("DOMContentLoaded", () => initializeCrm());
document.addEventListener("htmx:afterSwap", event => initializeCrm(event.detail.target));

function initializeIntakeForm(root = document) {
  const wizard = root.matches?.("[data-intake-wizard]") ? root : root.querySelector?.("[data-intake-wizard]");
  if (!wizard || wizard.dataset.wizardReady) return;
  wizard.dataset.wizardReady = "1";
  const form = wizard.querySelector("[data-intake-form]");
  form.addEventListener("input", event => { if (event.target.type !== "hidden") form.dataset.dirty = "1"; });
  form.addEventListener("change", event => { if (event.target.type !== "hidden") form.dataset.dirty = "1"; });
  form.addEventListener("submit", () => { form.dataset.dirty = "0"; });

}
document.addEventListener("DOMContentLoaded", () => initializeIntakeForm());
document.addEventListener("htmx:afterSwap", event => initializeIntakeForm(event.detail.target));

let intakePreviousFocus = null;
function openIntakeModal(root = document) {
  const overlay = root.matches?.(".intake-overlay") ? root : root.querySelector?.(".intake-overlay");
  if (!overlay) return;
  document.body.classList.add("intake-open");
  window.setTimeout(() => {
    const field = overlay.querySelector(".crm-error")?.closest(".crm-field");
    const target = field?.querySelector("input, select, textarea") || overlay.querySelector(".intake-error-summary");
    if (target) {
      target.focus({preventScroll: true});
      (field || target).scrollIntoView({block: "center", behavior: "instant"});
    } else overlay.querySelector("input:not([type=hidden]), select, textarea, button")?.focus();
  }, 0);
}
function closeIntakeModal() {
  const host = document.getElementById("intake-modal-host");
  const wizard = host?.querySelector("[data-intake-wizard]");
  if (!host || !wizard) return;
  const form = wizard.querySelector("[data-intake-form]");
  if (form?.dataset.dirty === "1" && !window.confirm("Закрыть форму? Введённые данные будут потеряны.")) return;
  const direct = wizard.dataset.direct === "true";
  const closeUrl = wizard.dataset.closeUrl;
  host.replaceChildren();
  document.body.classList.remove("intake-open");
  if (direct && closeUrl) { window.location.assign(closeUrl); return; }
  intakePreviousFocus?.focus?.();
}
document.addEventListener("click", event => {
  const opener = event.target.closest('a[href*="/crm/orders/new/"]');
  if (opener) intakePreviousFocus = opener;
  if (event.target.closest("[data-intake-close]")) closeIntakeModal();
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && document.querySelector("#intake-modal-host .intake-overlay")) closeIntakeModal();
});
document.addEventListener("htmx:afterSwap", event => {
  if (event.detail.target?.id === "intake-modal-host") openIntakeModal(event.detail.target);
});
document.addEventListener("DOMContentLoaded", () => openIntakeModal(document));
document.addEventListener("click", event => {
  const link = event.target.closest('a[href*="/crm/orders/new/"]');
  if (!link || link.hasAttribute("hx-get") || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  if (!window.htmx) return;
  event.preventDefault();
  intakePreviousFocus = link;
  window.htmx.ajax("GET", link.href, {target: "#intake-modal-host", swap: "innerHTML"});
});
document.addEventListener("DOMContentLoaded", () => {
  const node = document.getElementById("crm-work-templates-data");
  if (!node) return;
  const items = JSON.parse(node.textContent);
  const select = document.querySelector("[name=template]");
  if (!select) return;
  select.addEventListener("change", () => {
    const item = items.find(row => String(row.id) === select.value);
    if (!item) return;
    const form = select.closest("form");
    form.querySelector("[name=name]").value = item.name;
    form.querySelector("[name=unit_price]").value = item.default_price;
  });
});
document.addEventListener("DOMContentLoaded", () => {
  const tabs = [...document.querySelectorAll("[data-settings-tab]")];
  const panels = [...document.querySelectorAll("[data-settings-panel]")];
  if (!tabs.length || !panels.length) return;

  const openSection = (name, updateUrl = true) => {
    const normalized = name === "models" ? "brands" : name;
    tabs.forEach(tab => {
      const active = tab.dataset.settingsTab === normalized;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-current", active ? "page" : "false");
    });
    panels.forEach(panel => panel.classList.toggle("is-active", panel.dataset.settingsPanel === normalized));
    if (updateUrl) {
      const url = new URL(window.location.href);
      url.searchParams.set("section", normalized);
      url.searchParams.delete("edit");
      window.history.replaceState({}, "", url);
    }
  };

  tabs.forEach(tab => tab.addEventListener("click", event => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    openSection(tab.dataset.settingsTab);
  }));
  document.querySelectorAll("[data-settings-open]").forEach(link => link.addEventListener("click", event => {
    event.preventDefault();
    openSection(link.dataset.settingsOpen);
    document.querySelector("[data-settings-tabs]")?.scrollIntoView({behavior: "smooth", block: "nearest"});
  }));

});
function escapeHtml(value){const div=document.createElement("div");div.textContent=value||"";return div.innerHTML;}
