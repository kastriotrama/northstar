const PAGE_SIZE = 250;
const state = { filters: {}, offset: 0, selectedId: null, page: null, loading: false };

const elements = {
  rows: document.querySelector("#vehicle-rows"),
  empty: document.querySelector("#empty-state"),
  inspector: document.querySelector(".inspector"),
  inspectorEmpty: document.querySelector("#inspector-empty"),
  inspectorContent: document.querySelector("#inspector-content"),
  search: document.querySelector("#search"),
  status: document.querySelector("#filter-status"),
  manufacturer: document.querySelector("#filter-manufacturer"),
  bodywork: document.querySelector("#filter-bodywork"),
  fuel: document.querySelector("#filter-fuel"),
  transmission: document.querySelector("#filter-transmission"),
};

const labels = {
  resolved: "Resolved",
  provisional: "Provisional",
  review_required: "Needs review",
  failed: "Failed",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function humanize(value) {
  return String(value ?? "—").replaceAll("_", " ");
}

function statusBadge(status) {
  return `<span class="status-badge status-${escapeHtml(status)}">${escapeHtml(labels[status] ?? humanize(status))}</span>`;
}

function percent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function displayValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.map(humanize).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return humanize(value);
}

function params() {
  const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(state.offset) });
  Object.entries(state.filters).forEach(([key, value]) => { if (value) query.set(key, value); });
  return query;
}

async function loadVehicles({ preserveSelection = false } = {}) {
  if (state.loading) return;
  state.loading = true;
  document.querySelector("#result-count").textContent = "Updating view…";
  try {
    const response = await fetch(`/v1/normalization-review/vehicles?${params()}`);
    if (!response.ok) throw new Error(`Request failed (${response.status})`);
    state.page = await response.json();
    if (!preserveSelection || !state.page.items.some((item) => item.source_record_id === state.selectedId)) {
      state.selectedId = state.page.items[0]?.source_record_id ?? null;
    }
    renderPage();
  } catch (error) {
    showToast(`Could not load normalization data. ${error.message}`);
    elements.rows.replaceChildren();
    elements.empty.hidden = false;
  } finally {
    state.loading = false;
  }
}

function renderPage() {
  const page = state.page;
  document.querySelector("#batch-label").textContent = page.batch_id ? `Batch · ${page.batch_id}` : "No normalized batch yet";
  document.querySelector("#summary-total").textContent = page.summary.total.toLocaleString();
  document.querySelector("#summary-resolved").textContent = page.summary.resolved.toLocaleString();
  document.querySelector("#summary-provisional").textContent = page.summary.provisional.toLocaleString();
  document.querySelector("#summary-review").textContent = page.summary.review_required.toLocaleString();
  document.querySelector("#summary-failed").textContent = page.summary.failed.toLocaleString();
  document.querySelector("#visible-count").textContent = `${page.items.length} / ${PAGE_SIZE}`;
  document.querySelector("#result-count").textContent = `${page.filtered_total.toLocaleString()} matching vehicle${page.filtered_total === 1 ? "" : "s"}`;
  populateFacets(page.facets);
  renderRows(page.items);
  renderInspector(page.items.find((item) => item.source_record_id === state.selectedId));
  elements.empty.hidden = page.items.length > 0;

  const pageNumber = Math.floor(page.offset / page.limit) + 1;
  const pageCount = Math.max(1, Math.ceil(page.filtered_total / page.limit));
  document.querySelector("#page-label").textContent = `Page ${pageNumber} of ${pageCount}`;
  document.querySelector("#previous-page").disabled = page.offset === 0;
  document.querySelector("#next-page").disabled = page.offset + page.limit >= page.filtered_total;
}

function populateSelect(select, values, placeholder) {
  const current = select.value;
  select.replaceChildren(new Option(placeholder, ""), ...values.map((value) => new Option(humanize(value), value)));
  select.value = values.includes(current) ? current : "";
}

function populateFacets(facets) {
  populateSelect(elements.manufacturer, facets.manufacturers, "All manufacturers");
  populateSelect(elements.bodywork, facets.bodywork_forms, "All bodywork");
  populateSelect(elements.fuel, facets.fuels, "All fuels");
  populateSelect(elements.transmission, facets.transmissions, "All transmissions");
}

function renderRows(items) {
  elements.rows.innerHTML = items.map((vehicle, index) => `
    <tr data-id="${vehicle.source_record_id}" class="${vehicle.source_record_id === state.selectedId ? "selected" : ""}" style="animation-delay:${Math.min(index, 12) * 18}ms" tabindex="0">
      <td><div class="vehicle-cell"><strong>${escapeHtml(vehicle.manufacturer || "Unresolved manufacturer")} ${escapeHtml(vehicle.model_family || "")}</strong><span>Record ${vehicle.source_record_id}${vehicle.engine_code ? ` · ${escapeHtml(vehicle.engine_code)}` : ""}</span></div></td>
      <td>${statusBadge(vehicle.status)}</td>
      <td>${escapeHtml(humanize(vehicle.bodywork))}</td>
      <td>${escapeHtml(displayValue(vehicle.energy_sources))}</td>
      <td>${escapeHtml(humanize(vehicle.transmission))}</td>
      <td><div class="confidence-cell"><span>${percent(vehicle.confidence)}</span><span class="mini-track"><span style="width:${percent(vehicle.confidence)}"></span></span></div></td>
    </tr>`).join("");

  elements.rows.querySelectorAll("tr").forEach((row) => {
    const select = () => selectVehicle(Number(row.dataset.id));
    row.addEventListener("click", select);
    row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") select(); });
  });
}

function selectVehicle(id) {
  state.selectedId = id;
  elements.rows.querySelectorAll("tr").forEach((row) => row.classList.toggle("selected", Number(row.dataset.id) === id));
  renderInspector(state.page.items.find((item) => item.source_record_id === id));
  if (window.innerWidth <= 820) elements.inspector.classList.add("open");
}

function renderInspector(vehicle) {
  elements.inspectorEmpty.hidden = Boolean(vehicle);
  elements.inspectorContent.hidden = !vehicle;
  if (!vehicle) return;

  document.querySelector("#inspector-record").textContent = `Source record ${vehicle.source_record_id}`;
  document.querySelector("#inspector-name").textContent = `${vehicle.manufacturer || "Unresolved"} ${vehicle.model_family || "vehicle"}`;
  const status = document.querySelector("#inspector-status");
  status.className = `status-badge status-${vehicle.status}`;
  status.textContent = labels[vehicle.status] ?? humanize(vehicle.status);
  document.querySelector("#inspector-confidence").textContent = percent(vehicle.confidence);
  document.querySelector("#confidence-fill").style.width = percent(vehicle.confidence);

  const fieldOrder = ["manufacturer", "model_family", "bodywork_form", "energy_sources", "transmission_type", "engine_code", "production_year", "power_kw", "displacement_cc", "registration_date"];
  const entries = fieldOrder.filter((key) => vehicle.normalized[key] !== undefined).map((key) => [key, vehicle.normalized[key]]);
  document.querySelector("#normalized-fields").innerHTML = entries.length
    ? entries.map(([key, value]) => `<div><dt>${escapeHtml(humanize(key))}</dt><dd>${escapeHtml(displayValue(value))}</dd></div>`).join("")
    : "<div><dt>Result</dt><dd>No accepted normalized fields</dd></div>";

  const reasons = document.querySelector("#review-reasons");
  reasons.innerHTML = vehicle.review_reasons.length
    ? vehicle.review_reasons.map((reason) => `<span>${escapeHtml(reason)}</span>`).join("")
    : "<span>No review reasons</span>";
  document.querySelector("#review-section").hidden = vehicle.status === "resolved" && !vehicle.review_reasons.length;

  document.querySelector("#decision-trace").innerHTML = vehicle.decision_trace.length
    ? vehicle.decision_trace.map((entry) => `<li><span class="trace-index">•</span><div class="trace-copy"><strong>${escapeHtml(humanize(entry.field || entry.signal || "decision"))}</strong><div class="trace-values"><span title="${escapeHtml(displayValue(entry.before ?? entry.value))}">${escapeHtml(displayValue(entry.before ?? entry.value))}</span><b>→</b><span title="${escapeHtml(displayValue(entry.after ?? entry.contribution))}">${escapeHtml(displayValue(entry.after ?? entry.contribution))}</span></div></div></li>`).join("")
    : '<li><span class="trace-index">•</span><div class="trace-copy"><strong>No trace available</strong></div></li>';

  const rules = [...new Set([...vehicle.applied_rule_ids, ...vehicle.rule_matches.map((match) => match.rule_id).filter(Boolean)])];
  document.querySelector("#rule-list").innerHTML = rules.length
    ? rules.map((rule) => `<span>${escapeHtml(rule)}</span>`).join("")
    : "<span>No applied rules</span>";
}

function updateFilters() {
  state.filters = {
    query: elements.search.value.trim(),
    status: elements.status.value,
    manufacturer: elements.manufacturer.value,
    bodywork: elements.bodywork.value,
    fuel: elements.fuel.value,
    transmission: elements.transmission.value,
  };
  state.offset = 0;
  loadVehicles();
}

let searchTimer;
elements.search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(updateFilters, 220);
});
[elements.status, elements.manufacturer, elements.bodywork, elements.fuel, elements.transmission].forEach((select) => select.addEventListener("change", updateFilters));
document.querySelector("#reset-filters").addEventListener("click", () => {
  elements.search.value = "";
  [elements.status, elements.manufacturer, elements.bodywork, elements.fuel, elements.transmission].forEach((select) => { select.value = ""; });
  updateFilters();
});
document.querySelector("#previous-page").addEventListener("click", () => { state.offset = Math.max(0, state.offset - PAGE_SIZE); loadVehicles(); });
document.querySelector("#next-page").addEventListener("click", () => { state.offset += PAGE_SIZE; loadVehicles(); });
document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); elements.search.focus(); }
  if (event.key === "Escape") elements.inspector.classList.remove("open");
});
elements.inspector.addEventListener("click", (event) => { if (window.innerWidth <= 820 && event.target === elements.inspector) elements.inspector.classList.remove("open"); });
document.querySelector("#close-inspector").addEventListener("click", () => elements.inspector.classList.remove("open"));

function showToast(message) {
  const toast = document.querySelector("#toast");
  toast.textContent = message;
  toast.classList.add("visible");
  setTimeout(() => toast.classList.remove("visible"), 4000);
}

loadVehicles();
