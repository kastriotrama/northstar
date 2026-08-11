const PAGE_SIZE = 250;
const state = { filters: {}, offset: 0, selectedId: null, page: null, loading: false };
const ruleState = { page: null, selectedId: null, kind: "translation", loading: false };
const queueState = { page: null, selectedId: null, loading: false };
const tecdocState = { page: null, selectedId: null, offset: 0, loading: false };

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
  rulesView: document.querySelector("#rules-view"),
  vehiclesView: document.querySelector("#vehicles-view"),
  guideView: document.querySelector("#guide-view"),
  queueView: document.querySelector("#queue-view"),
  tecdocView: document.querySelector("#tecdoc-view"),
  ruleRows: document.querySelector("#rule-rows"),
  ruleSearch: document.querySelector("#rule-search"),
  ruleArea: document.querySelector("#rule-area"),
  ruleStateFilter: document.querySelector("#rule-state"),
  ruleEditor: document.querySelector(".rule-editor"),
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

function searchKey(value) {
  return String(value ?? "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-zA-Z0-9]+/g, " ")
    .trim()
    .toLowerCase();
}

function statusBadge(status) {
  return `<span class="status-badge status-${escapeHtml(status)}">${escapeHtml(labels[status] ?? humanize(status))}</span>`;
}

function percent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function statusExplanation(status) {
  return {
    resolved: "All required fields were resolved with accepted evidence.",
    provisional: "A likely value was found, but it still needs corroboration before becoming canonical.",
    review_required: "The evidence is missing or conflicting, so the system stopped instead of guessing.",
    failed: "The record could not be normalized safely and needs technical review.",
  }[status] ?? "Normalization outcome needs inspection.";
}

function formatDateTime(value, fallback) {
  if (!value) return fallback;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function ruleExplanation(ruleId) {
  const explanations = {
    "MFR-102": "If Tillverkare is a recognized vehicle manufacturer, use it as the canonical manufacturer.",
    "MFR-BRAND-PREFIX-FALLBACK": "If Tillverkare is missing, Brand may become a manufacturer candidate when it begins with an approved manufacturer alias, but supporting evidence is required.",
    "MFR-BRAND-REVIEWED-EXAMPLE": "If Tillverkare is missing and Brand exactly matches a reviewed example, the linked manufacturer may become a candidate, but supporting evidence is required.",
    "MFR-BRAND-REVIEWED-EXACT": "If Tillverkare is missing and Brand exactly matches a stakeholder-reviewed repair, use the approved canonical manufacturer.",
    "MFR-BRAND-LEGACY-EXACT": "If Tillverkare is missing and Brand exactly matches an approved legacy Brand value, use its canonical manufacturer.",
    "MFR-BRAND-CONFIRMED": "If Tillverkare is missing, use the manufacturer identified from Brand only when another source confirms the same manufacturer.",
    "MFR-BRAND-EVIDENCE-CONFIRMED": "If Tillverkare is missing and Brand agrees with Model, VIN, fabrication code, or TecDoc KType, use that confirmed manufacturer.",
    "MFR-BRAND-MODEL": "Model supports the same manufacturer identified from Brand.",
    "MFR-BRAND-VIN-WMI": "The VIN manufacturer code supports the same manufacturer identified from Brand.",
    "MFR-BRAND-FAB-CODE": "The fabrication code supports the same manufacturer identified from Brand.",
    "MFR-BRAND-KTYPE": "TecDoc KType supports the same manufacturer identified from Brand.",
    "MFR-CORPORATE-BRAND-OVERRIDE": "If Tillverkare names a corporate group but Brand identifies an approved marketed vehicle brand, use the marketed brand as manufacturer.",
    "MFR-PARENT-MARKETED": "If Tillverkare names a parent company and Brand identifies an approved child brand, use the child brand when independent evidence confirms it.",
    "MFR-PARENT-MODEL-CHILD": "If Tillverkare names a parent company and Model clearly identifies an approved child brand, use that child brand.",
    "MFR-BRAND-BASE-CONFIRMED": "If Tillverkare is a bodybuilder or converter and Brand agrees with Tillverkare grundfordonet, use the base manufacturer and keep the bodybuilder separately.",
    "MFR-MODEL-VARIANT-FALLBACK": "If Tillverkare is missing, a manufacturer found at the beginning of Model or Variant may become a candidate, but supporting evidence is required.",
    "DRV-001": "For Mercedes-Benz vehicles, 4MATIC is accepted as all-wheel drive.",
    "DRV-002": "For BMW vehicles, xDrive is accepted as all-wheel drive.",
    "DRV-003": "For Audi vehicles, quattro is accepted as all-wheel drive.",
    "DRV-004": "For Volkswagen vehicles, 4Motion is accepted as all-wheel drive.",
    "DRV-008": "When the official TS is_4wd flag is 1, all-wheel drive is accepted. A value of 0 does not identify front- or rear-wheel drive.",
  };
  if (explanations[ruleId]) return explanations[ruleId];
  if (ruleId.startsWith("MFE-")) return "If the source company matches this approved manufacturer entity, its reviewed classification and canonical manufacturer name are used.";
  if (ruleId.startsWith("MOD-")) return "When the confirmed manufacturer matches this rule and TS Model begins with the approved complete model-family term, that family is accepted while the remaining text stays available as source evidence.";
  return "If this rule's conditions match the source vehicle, its normalized value is applied according to the active rule version.";
}

function displayValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.map(humanize).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return humanize(value);
}

function sourceLabel(key) {
  return { manufacturer: "Tillverkare", brand: "Brand", model: "Model", variant: "Variant", version: "Version", body_code: "Body code", fuel1: "Fuel 1", fuel2: "Fuel 2", fuel3: "Fuel 3", gearbox: "Gearbox", eu_category: "EU category", vin: "VIN" }[key] || humanize(key);
}

function missingFields(vehicle) {
  const missing = [];
  if (!vehicle.normalized.manufacturer) missing.push("Manufacturer could not be confirmed");
  if (!vehicle.normalized.model_family && !vehicle.candidates.model_family) missing.push("Model family is unresolved");
  Object.entries(vehicle.candidates || {}).filter(([field, value]) => !field.endsWith("_confidence") && field !== "manufacturer_confirmation" && value !== null && value !== undefined && value !== "").forEach(([field, value]) => {
    missing.push(`${humanize(field)} “${displayValue(value)}” is a candidate; confirm it with an approved rule, VIN, or TecDoc KType match`);
  });
  const manufacturerConfirmation = vehicle.candidates?.manufacturer_confirmation;
  if (manufacturerConfirmation?.canonical_name) {
    const evidence = (manufacturerConfirmation.source_fields || []).map(sourceLabel).join(" and ") || "source evidence";
    missing.push(`Manufacturer “${manufacturerConfirmation.canonical_name}” was inferred from ${evidence}; confirm it with an approved manufacturer entity, VIN, or TecDoc KType match`);
  }
  if (vehicle.status === "provisional" && vehicle.normalized.model_family_candidate && !vehicle.candidates.model_family) {
    missing.push(`Model family “${displayValue(vehicle.normalized.model_family)}” was inferred from TS model text; confirm it with TecDoc KType or reviewed model evidence`);
  }
  vehicle.review_reasons.forEach((reason) => missing.push(humanize(reason)));
  return [...new Set(missing)];
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
  document.querySelector("#summary-provisional-rate").textContent = `${percent(page.summary.provisional / Math.max(page.summary.total, 1))} of batch`;
  document.querySelector("#summary-review-rate").textContent = `${percent(page.summary.review_required / Math.max(page.summary.total, 1))} of batch`;
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
      <td><div class="vehicle-cell"><strong>${escapeHtml(vehicle.manufacturer_group || vehicle.manufacturer || "Unresolved manufacturer")} ${escapeHtml(vehicle.model_family || "")}</strong><span>${vehicle.registration_plate ? `Plate: ${escapeHtml(vehicle.registration_plate)} · ` : ""}${vehicle.source_brand ? `Brand: ${escapeHtml(vehicle.source_brand)} · ` : ""}Record ${vehicle.source_record_id}${vehicle.engine_code ? ` · ${escapeHtml(vehicle.engine_code)}` : ""}</span></div></td>
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
  elements.inspector.classList.add("open");
}

function renderInspector(vehicle) {
  elements.inspectorEmpty.hidden = Boolean(vehicle);
  elements.inspectorContent.hidden = !vehicle;
  if (!vehicle) return;

  document.querySelector("#inspector-record").textContent = `Source record ${vehicle.source_record_id}`;
  document.querySelector("#inspector-name").textContent = `${vehicle.manufacturer_group || vehicle.manufacturer || "Unresolved"} ${vehicle.model_family || "vehicle"}`;
  document.querySelector("#inspector-identity").innerHTML = `<strong>${escapeHtml(vehicle.registration_plate || "No registration plate supplied")}</strong><span class="data-kind data-kind-${escapeHtml(vehicle.source_data_kind)}">${escapeHtml(vehicle.source_data_kind)} source</span><span>${escapeHtml(vehicle.source_batch_id)}</span>`;
  const status = document.querySelector("#inspector-status");
  status.className = `status-badge status-${vehicle.status}`;
  status.textContent = labels[vehicle.status] ?? humanize(vehicle.status);
  document.querySelector("#inspector-status-explanation").textContent = statusExplanation(vehicle.status);
  document.querySelector("#inspector-confidence").textContent = percent(vehicle.confidence);
  document.querySelector("#confidence-fill").style.width = percent(vehicle.confidence);

  const manufacturerRule = vehicle.candidate_rule_ids.find((rule) => rule.startsWith("MFR-") || rule.startsWith("MFE-"))
    || vehicle.applied_rule_ids.find((rule) => rule === "MFR-BRAND-REVIEWED-EXAMPLE")
    || vehicle.applied_rule_ids.find((rule) => rule.startsWith("MFR-") || rule.startsWith("MFE-"));
  const rawFields = ["plate", "manufacturer", "brand", "model", "variant", "version", "body_code", "body_code2", "body_code_extra", "text_code", "text_codes", "text_code_descriptions", "eu_category", "fuel1", "fuel2", "fuel3", "gearbox", "is_4wd", "vin"];
  const rawEntries = rawFields.filter((key) => vehicle.source_evidence?.[key] !== undefined && vehicle.source_evidence[key] !== null && vehicle.source_evidence[key] !== "").map((key) => [key, vehicle.source_evidence[key]]);
  document.querySelector("#source-evidence").innerHTML = [
    ["Brand", vehicle.source_brand || "Not supplied"],
    ["Source record", vehicle.source_record_id],
    ["Manufacturer decision", manufacturerRule ? ruleExplanation(manufacturerRule) : "No Manufacturer entity fallback used"],
    ...rawEntries,
  ].map(([key, value]) => `<div><dt>${escapeHtml(humanize(key))}</dt><dd>${escapeHtml(displayValue(value))}</dd></div>`).join("");

  const fieldOrder = ["manufacturer", "model_family", "bodywork_form", "drive_type", "energy_sources", "transmission_type", "engine_code", "production_year", "power_kw", "displacement_cc", "registration_date"];
  const entries = fieldOrder.filter((key) => vehicle.normalized[key] !== undefined).map((key) => [key, vehicle.normalized[key]]);
  document.querySelector("#normalized-fields").innerHTML = entries.length
    ? entries.map(([key, value]) => `<div><dt>${escapeHtml(humanize(key))}</dt><dd>${escapeHtml(displayValue(value))}</dd></div>`).join("")
    : "<div><dt>Result</dt><dd>No accepted normalized fields</dd></div>";

  const specialSection = document.querySelector("#special-vehicle-section");
  const hasSpecialEvidence = vehicle.text_codes.length || vehicle.special_vehicle_flags.length || vehicle.parts_matching_policy;
  specialSection.hidden = !hasSpecialEvidence;
  document.querySelector("#special-vehicle-summary").innerHTML = hasSpecialEvidence ? [
    ["Manufacturer group", vehicle.manufacturer_group || "Not changed"],
    ["Vehicle flags", vehicle.special_vehicle_flags.length ? vehicle.special_vehicle_flags.map(humanize).join(", ") : "None"],
    ["Parts matching", vehicle.parts_matching_policy ? humanize(vehicle.parts_matching_policy) : "No restriction"],
  ].map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join("") : "";
  document.querySelector("#text-code-list").innerHTML = vehicle.text_codes.map((item) => {
    const code = item.code || "Description-only evidence";
    const meaning = item.description_en || item.description || "Meaning not yet mapped";
    const candidates = item.candidate_codes?.length ? `<small>Possible official codes: ${escapeHtml(item.candidate_codes.join(", "))}. Confirm against the full TS text-code field.</small>` : "";
    return `<article><strong>${escapeHtml(code)}</strong><span>${escapeHtml(meaning)}</span>${item.description_sv ? `<em>${escapeHtml(item.description_sv)}</em>` : ""}${candidates}</article>`;
  }).join("");

  const reasons = document.querySelector("#review-reasons");
  reasons.innerHTML = vehicle.review_reasons.length
    ? vehicle.review_reasons.map((reason) => `<span>${escapeHtml(reason)}</span>`).join("")
    : "<span>No review reasons</span>";
  document.querySelector("#review-section").hidden = !vehicle.review_reasons.length;
  const missing = missingFields(vehicle);
  document.querySelector("#missing-section").hidden = missing.length === 0;
  document.querySelector("#missing-fields").innerHTML = missing.map((item) => `<div><span class="missing-icon">!</span><span>${escapeHtml(item)}</span></div>`).join("");

  const source = vehicle.source_evidence || {};
  const normalized = vehicle.normalized || {};
  const candidates = vehicle.candidates || {};
  const mappings = [
    ["manufacturer", "manufacturer"], ["brand", "manufacturer"], ["model", "model_family"], ["variant", "model_family"],
    ["body_code", "bodywork_form"], ["body_code2", "special_vehicle_flags"], ["body_code_extra", "special_vehicle_flags"], ["text_code", "vehicle_classification"], ["fuel1", "energy_sources"], ["fuel2", "energy_sources"], ["fuel3", "energy_sources"], ["gearbox", "transmission_type"],
  ].filter(([raw]) => source[raw] !== undefined && source[raw] !== null && source[raw] !== "" && !(["fuel2", "fuel3"].includes(raw) && String(source[raw]) === "0"));
  document.querySelector("#evidence-map").innerHTML = mappings.length ? mappings.map(([rawKey, target]) => {
    const accepted = normalized[target];
    const candidate = candidates[target];
    const output = accepted ?? candidate;
    const outputState = accepted !== undefined ? "accepted" : candidate !== undefined ? "candidate" : "unresolved";
    return `<div class="evidence-row"><div class="evidence-field">${escapeHtml(sourceLabel(rawKey))}<small>→ ${escapeHtml(humanize(target))}</small></div><code>${escapeHtml(displayValue(source[rawKey]))}</code><span class="evidence-arrow">→</span><code class="evidence-${outputState}">${escapeHtml(displayValue(output))}</code><span class="evidence-state">${escapeHtml(outputState)}</span></div>`;
  }).join("") : "<p class='section-hint'>No comparable source fields were supplied.</p>";

  document.querySelector("#decision-trace").innerHTML = vehicle.decision_trace.length
    ? vehicle.decision_trace.map((entry) => `<li><span class="trace-index">•</span><div class="trace-copy"><strong>${escapeHtml(humanize(entry.field || entry.signal || "decision"))}</strong><div class="trace-values"><span title="${escapeHtml(displayValue(entry.before ?? entry.value))}">${escapeHtml(displayValue(entry.before ?? entry.value))}</span><b>→</b><span title="${escapeHtml(displayValue(entry.after ?? entry.contribution))}">${escapeHtml(displayValue(entry.after ?? entry.contribution))}</span></div></div></li>`).join("")
    : '<li><span class="trace-index">•</span><div class="trace-copy"><strong>No trace available</strong></div></li>';

  const appliedRules = [...new Set([...vehicle.applied_rule_ids, ...vehicle.rule_matches.map((match) => match.rule_id).filter(Boolean)])];
  const candidateRules = [...new Set(vehicle.candidate_rule_ids || [])];
  const rules = [
    ...appliedRules.map((rule) => ({ rule, state: "Applied" })),
    ...candidateRules.filter((rule) => !appliedRules.includes(rule)).map((rule) => ({ rule, state: "Candidate" })),
  ];
  document.querySelector("#rule-list").innerHTML = rules.length
    ? rules.map(({ rule, state }) => `<button type="button" class="rule-evidence ${state === "Candidate" ? "candidate" : ""}" data-rule-id="${escapeHtml(rule)}" title="Show ${escapeHtml(rule)} details"><b>${escapeHtml(state)}</b>${escapeHtml(rule)}</button>`).join("")
    : "<span>No applied rules</span>";
  document.querySelector("#vehicle-rule-detail").hidden = true;
  document.querySelectorAll("#rule-list [data-rule-id]").forEach((button) => {
    button.addEventListener("click", () => showRuleInVehicle(button.dataset.ruleId));
  });
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
  if (event.key === "Escape") { elements.inspector.classList.remove("open"); elements.ruleEditor.classList.remove("open"); }
});
elements.inspector.addEventListener("click", (event) => { if (window.innerWidth <= 820 && event.target === elements.inspector) elements.inspector.classList.remove("open"); });
document.querySelector("#close-inspector").addEventListener("click", () => elements.inspector.classList.remove("open"));

function showToast(message) {
  const toast = document.querySelector("#toast");
  toast.textContent = message;
  toast.classList.add("visible");
  setTimeout(() => toast.classList.remove("visible"), 4000);
}

async function apiRequest(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Request failed (${response.status})`);
  }
  return response.json();
}

function switchView(view) {
  const showRules = view === "rules";
  const showGuide = view === "guide";
  const showQueue = view === "queue";
  const showTecDoc = view === "tecdoc";
  elements.vehiclesView.hidden = showRules || showGuide || showQueue || showTecDoc;
  elements.rulesView.hidden = !showRules;
  elements.guideView.hidden = !showGuide;
  elements.queueView.hidden = !showQueue;
  elements.tecdocView.hidden = !showTecDoc;
  document.querySelectorAll(".view-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === view));
  if (showRules && !ruleState.page) loadRules();
  if (showQueue) loadQueue();
  if (showTecDoc && !tecdocState.page) loadTecDoc();
}

async function loadTecDoc() {
  if (tecdocState.loading) return;
  tecdocState.loading = true;
  const query = document.querySelector("#tecdoc-search").value.trim();
  try {
    tecdocState.page = await apiRequest(`/v1/normalization-review/tecdoc/vehicles?query=${encodeURIComponent(query)}&limit=100&offset=${tecdocState.offset}`);
    if (!tecdocState.page.items.some((item) => item.ktype === tecdocState.selectedId)) tecdocState.selectedId = tecdocState.page.items[0]?.ktype || null;
    renderTecDoc();
  } catch (error) { showToast(`Could not load TecDoc vehicles. ${error.message}`); }
  finally { tecdocState.loading = false; }
}

function renderTecDoc() {
  const page = tecdocState.page;
  const summary = page.summary;
  document.querySelector("#tecdoc-promoted").textContent = Number(summary.promoted_ktypes).toLocaleString();
  document.querySelector("#tecdoc-manufacturers").textContent = Number(summary.manufacturers).toLocaleString();
  document.querySelector("#tecdoc-models").textContent = Number(summary.model_families).toLocaleString();
  document.querySelector("#tecdoc-engines").textContent = Number(summary.engines).toLocaleString();
  document.querySelector("#tecdoc-result-count").textContent = `${page.filtered_total.toLocaleString()} promoted KTypes · ${escapeHtml(summary.batch_id || "No batch")}`;
  const rows = document.querySelector("#tecdoc-rows");
  rows.innerHTML = page.items.map((item) => `<tr data-ktype="${escapeHtml(item.ktype)}" class="${item.ktype === tecdocState.selectedId ? "selected" : ""}"><td><div class="vehicle-cell"><strong>${escapeHtml(item.source_name || item.ktype)}</strong><span>KType ${escapeHtml(item.ktype)} · ${escapeHtml(item.model_family || "Model pending")}</span></div></td><td>${escapeHtml(item.manufacturer || "—")}</td><td><div class="vehicle-cell"><strong>${escapeHtml(item.engine_code || "—")}</strong><span>${item.displacement_cc ? `${escapeHtml(item.displacement_cc)} cc` : "Displacement pending"}</span></div></td><td>${escapeHtml(humanize(item.fuel_type))}</td><td>${statusBadge("provisional")}</td></tr>`).join("");
  rows.querySelectorAll("tr").forEach((row) => row.addEventListener("click", () => { tecdocState.selectedId = row.dataset.ktype; renderTecDoc(); }));
  document.querySelector("#tecdoc-empty").hidden = page.items.length !== 0;
  document.querySelector("#tecdoc-page").textContent = `Page ${Math.floor(tecdocState.offset / 100) + 1}`;
  document.querySelector("#tecdoc-previous").disabled = tecdocState.offset === 0;
  document.querySelector("#tecdoc-next").disabled = tecdocState.offset + 100 >= page.filtered_total;
  renderTecDocInspector(page.items.find((item) => item.ktype === tecdocState.selectedId));
}

function renderTecDocInspector(item) {
  document.querySelector("#tecdoc-inspector-empty").hidden = Boolean(item);
  const content = document.querySelector("#tecdoc-inspector-content");
  content.hidden = !item;
  if (!item) return;
  document.querySelector("#tecdoc-detail-name").textContent = item.source_name || `KType ${item.ktype}`;
  document.querySelector("#tecdoc-detail-subtitle").textContent = `${item.manufacturer || "Manufacturer pending"} · ${item.model_family || "Model pending"}`;
  const fields = { "KType": item.ktype, "Manufacturer": item.manufacturer, "Model family": item.model_family, "Engine code": item.engine_code, "Displacement": item.displacement_cc ? `${item.displacement_cc} cc` : null, "Fuel": humanize(item.fuel_type), "Powertrain years": [item.year_from, item.year_to || "present"].filter(Boolean).join("–") };
  document.querySelector("#tecdoc-canonical").innerHTML = Object.entries(fields).map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value || "—")}</dd></div>`).join("");
  document.querySelector("#tecdoc-gates").innerHTML = tecdocState.page.promotion_rules.map((rule) => `<article><span>Passed</span><div><strong>${escapeHtml(rule.label)}</strong><p>${escapeHtml(rule.outcome)}</p></div></article>`).join("");
  document.querySelector("#tecdoc-source-keys").innerHTML = Object.entries(item.source_keys).map(([key, value]) => `<div><dt>${escapeHtml(humanize(key))}</dt><dd>${escapeHtml(value)}</dd></div>`).join("");
  document.querySelector("#tecdoc-source-rows").innerHTML = item.source_row_refs.map((ref) => `<code>${escapeHtml(ref)}</code>`).join("");
}

async function loadRules() {
  if (ruleState.loading) return;
  ruleState.loading = true;
  document.querySelector("#rule-result-count").textContent = "Loading rules…";
  try {
    ruleState.page = await apiRequest("/v1/normalization-review/rules");
    ensureRuleSelection();
    renderRules();
  } catch (error) {
    showToast(`Could not load rules. ${error.message}`);
  } finally {
    ruleState.loading = false;
  }
}

function ruleDetailField(label, value) {
  return `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(displayValue(value))}</strong></div>`;
}

async function showRuleInVehicle(ruleId) {
  const detail = document.querySelector("#vehicle-rule-detail");
  const selectedButton = document.querySelector(`#rule-list [data-rule-id="${CSS.escape(ruleId)}"]`);
  if (!detail.hidden && detail.dataset.ruleId === ruleId) {
    detail.hidden = true;
    selectedButton?.classList.remove("selected");
    return;
  }

  detail.hidden = false;
  detail.dataset.ruleId = ruleId;
  detail.innerHTML = `<p class="section-hint">Loading ${escapeHtml(ruleId)}…</p>`;
  document.querySelectorAll("#rule-list [data-rule-id]").forEach((button) => button.classList.toggle("selected", button.dataset.ruleId === ruleId));
  if (!ruleState.page) await loadRules();
  if (!ruleState.page) return;

  const translationRule = ruleState.page.rules.find((rule) => rule.rule_id === ruleId);
  const manufacturerEntity = ruleState.page.manufacturer_entities.find((entity) => entity.entity_id === ruleId);
  if (translationRule) {
    detail.innerHTML = `<div class="vehicle-rule-detail-head"><span>Translation rule</span><strong>${escapeHtml(ruleId)}</strong><em class="decision-label decision-${escapeHtml(translationRule.effective_decision)}">${escapeHtml(translationRule.effective_decision)}</em></div><div class="vehicle-rule-detail-grid">${ruleDetailField("Area", humanize(translationRule.area))}${ruleDetailField("Source fields", translationRule.source_fields.join(", ") || "Any")}${ruleDetailField("Source terms", translationRule.source_terms.join(", ") || "Any")}${ruleDetailField("Canonical field", humanize(translationRule.canonical_field))}${ruleDetailField("Canonical value", humanize(translationRule.effective_canonical_value))}${ruleDetailField("Vehicle scope", translationRule.vehicle_scopes.join(", ") || "All vehicles")}${ruleDetailField("Manufacturer scope", translationRule.manufacturers.join(", ") || "Any manufacturer")}${ruleDetailField("Version state", translationRule.has_draft ? "Draft change" : "Active")}</div>`;
  } else if (manufacturerEntity) {
    detail.innerHTML = `<div class="vehicle-rule-detail-head"><span>Manufacturer entity</span><strong>${escapeHtml(ruleId)}</strong><em class="decision-label ${manufacturerEntity.effective_entity_role === "unknown" ? "decision-proposed" : "decision-accepted"}">${escapeHtml(humanize(manufacturerEntity.effective_entity_role))}</em></div><div class="vehicle-rule-detail-grid">${ruleDetailField("Source field", humanize(manufacturerEntity.source_field))}${ruleDetailField("Source term", manufacturerEntity.source_term)}${ruleDetailField("Canonical manufacturer", manufacturerEntity.effective_canonical_name)}${ruleDetailField("Base manufacturers", (manufacturerEntity.base_manufacturers || []).join(", ") || "None")}${ruleDetailField("Occurrences", manufacturerEntity.occurrences ?? 0)}${ruleDetailField("Version state", manufacturerEntity.has_draft ? "Draft change" : "Active")}</div>`;
  } else {
    detail.innerHTML = `<div class="vehicle-rule-detail-head"><span>Pipeline policy</span><strong>${escapeHtml(ruleId)}</strong></div><div class="policy-explanation"><span>What this rule means</span><p>${escapeHtml(ruleExplanation(ruleId))}</p></div><small>This policy is built into the normalizer and is read-only here.</small>`;
  }
}

function currentRuleItems() {
  if (!ruleState.page) return [];
  return ruleState.kind === "manufacturer" ? ruleState.page.manufacturer_entities : ruleState.page.rules;
}

function ensureRuleSelection() {
  const items = currentRuleItems();
  const idField = ruleState.kind === "manufacturer" ? "entity_id" : "rule_id";
  if (!items.some((item) => item[idField] === ruleState.selectedId)) {
    ruleState.selectedId = items[0]?.[idField] ?? null;
  }
}

function filteredRules() {
  if (!ruleState.page) return [];
  const query = searchKey(elements.ruleSearch.value);
  const area = elements.ruleArea.value;
  const status = elements.ruleStateFilter.value;
  if (ruleState.kind === "manufacturer") {
    return ruleState.page.manufacturer_entities.filter((entity) => {
      const searchable = searchKey([entity.entity_id, entity.source_field, entity.source_term, entity.effective_canonical_name, entity.effective_entity_role, ...(entity.reviewed_examples || [])].join(" "));
      const statusMatches = !status || (status === "draft" ? entity.has_draft : status === entity.effective_entity_role);
      return (!query || searchable.includes(query)) && statusMatches;
    });
  }
  return ruleState.page.rules.filter((rule) => {
    const searchable = searchKey([rule.rule_id, rule.area, rule.canonical_field, rule.effective_canonical_value, ...rule.source_terms].join(" "));
    const statusMatches = !status || (status === "draft" ? rule.has_draft : rule.effective_decision === status);
    return (!query || searchable.includes(query)) && (!area || rule.area === area) && statusMatches;
  });
}

function renderRules() {
  const page = ruleState.page;
  document.querySelector("#active-rule-version").textContent = page.active_version;
  document.querySelector("#rule-draft-count").textContent = page.draft_count.toLocaleString();
  const reasons = page.review_reason_summary || {};
  const reviewTotal = state.page?.summary.review_required;
  document.querySelector("#review-backlog-total").textContent = reviewTotal ?? "—";
  document.querySelector("#review-backlog-label").textContent = reviewTotal === 1 ? "vehicle needs evidence" : "vehicles need evidence";
  document.querySelector("#reason-manufacturer-missing").textContent = reasons.manufacturer_missing || 0;
  document.querySelector("#reason-brand-evidence").textContent = reasons.manufacturer_missing_compare_brand || 0;
  document.querySelector("#reason-bodywork-category").textContent = reasons.bodywork_code_unresolved_for_category || 0;
  document.querySelector("#activate-rules").disabled = page.draft_count === 0;
  document.querySelector("#reprocess-batch").disabled = page.draft_count > 0 || !state.page?.batch_id;
  document.querySelectorAll(".rule-kind").forEach((button) => button.classList.toggle("active", button.dataset.ruleKind === ruleState.kind));
  document.querySelector("#rule-area-filter").hidden = ruleState.kind === "manufacturer";
  if (ruleState.kind === "manufacturer") renderManufacturerEntities();
  else renderTranslationRules();
}

function renderTranslationRules() {
  const page = ruleState.page;
  const areas = [...new Set(page.rules.map((rule) => rule.area))].sort();
  populateSelect(elements.ruleArea, areas, "All areas");
  const rules = filteredRules();
  if (!rules.some((rule) => rule.rule_id === ruleState.selectedId)) {
    ruleState.selectedId = rules[0]?.rule_id ?? null;
  }
  document.querySelector("#rule-column-name").textContent = "Rule";
  document.querySelector("#rule-column-decision").textContent = "Decision";
  document.querySelector("#rule-result-count").textContent = `${rules.length.toLocaleString()} translation rule${rules.length === 1 ? "" : "s"}`;
  document.querySelector("#rules-empty").hidden = rules.length > 0;
  elements.ruleRows.innerHTML = rules.map((rule, index) => `
    <tr data-rule-id="${escapeHtml(rule.rule_id)}" class="${rule.rule_id === ruleState.selectedId ? "selected" : ""}" style="animation-delay:${Math.min(index, 12) * 18}ms" tabindex="0">
      <td><div class="rule-title"><strong>${escapeHtml(rule.rule_id)}</strong><span>${escapeHtml(humanize(rule.area))}</span></div></td>
      <td class="term-list" title="${escapeHtml(rule.source_terms.join(", "))}">${escapeHtml(rule.source_terms.join(", ") || "—")}</td>
      <td>${escapeHtml(humanize(rule.effective_canonical_value))}</td>
      <td><span class="decision-label decision-${escapeHtml(rule.effective_decision)}">${escapeHtml(rule.effective_decision)}</span></td>
      <td><span class="draft-marker ${rule.has_draft ? "changed" : ""}">${rule.has_draft ? "Draft" : "Active"}</span></td>
    </tr>`).join("");
  elements.ruleRows.querySelectorAll("tr").forEach((row) => {
    const select = () => selectRule(row.dataset.ruleId);
    row.addEventListener("click", select);
    row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") select(); });
  });
  renderRuleEditor(page.rules.find((rule) => rule.rule_id === ruleState.selectedId));
}

function renderManufacturerEntities() {
  const entities = filteredRules();
  if (!entities.some((entity) => entity.entity_id === ruleState.selectedId)) {
    ruleState.selectedId = entities[0]?.entity_id ?? null;
  }
  document.querySelector("#rule-column-name").textContent = "Entity";
  document.querySelector("#rule-column-decision").textContent = "Role";
  document.querySelector("#rule-result-count").textContent = `${entities.length.toLocaleString()} manufacturer entit${entities.length === 1 ? "y" : "ies"}`;
  document.querySelector("#rules-empty").hidden = entities.length > 0;
  elements.ruleRows.innerHTML = entities.map((entity, index) => `
    <tr data-rule-id="${escapeHtml(entity.entity_id)}" class="${entity.entity_id === ruleState.selectedId ? "selected" : ""}" style="animation-delay:${Math.min(index, 12) * 18}ms" tabindex="0">
      <td><div class="rule-title"><strong>${escapeHtml(entity.source_term)}</strong><span>${escapeHtml(humanize(entity.source_field))}${entity.reviewed_examples?.length ? ` · ${entity.reviewed_examples.length} reviewed` : entity.occurrences ? ` · ${entity.occurrences} current` : ""}</span></div></td>
      <td class="term-list">${escapeHtml(entity.source_field)}</td>
      <td>${escapeHtml(entity.effective_canonical_name || "—")}</td>
      <td><span class="decision-label ${entity.effective_entity_role === "unknown" ? "decision-proposed" : "decision-accepted"}">${escapeHtml(humanize(entity.effective_entity_role))}</span></td>
      <td><span class="draft-marker ${entity.has_draft ? "changed" : ""}">${entity.has_draft ? "Draft" : entity.is_discovered && entity.effective_entity_role === "unknown" ? "Review" : "Active"}</span></td>
    </tr>`).join("");
  elements.ruleRows.querySelectorAll("tr").forEach((row) => {
    const select = () => selectRule(row.dataset.ruleId);
    row.addEventListener("click", select);
    row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") select(); });
  });
  renderManufacturerEditor(ruleState.page.manufacturer_entities.find((entity) => entity.entity_id === ruleState.selectedId));
}

function selectRule(ruleId) {
  ruleState.selectedId = ruleId;
  elements.ruleEditor.scrollTop = 0;
  elements.ruleRows.querySelectorAll("tr").forEach((row) => row.classList.toggle("selected", row.dataset.ruleId === ruleId));
  if (ruleState.kind === "manufacturer") {
    renderManufacturerEditor(ruleState.page.manufacturer_entities.find((entity) => entity.entity_id === ruleId));
  } else {
    renderRuleEditor(ruleState.page.rules.find((rule) => rule.rule_id === ruleId));
  }
  if (window.innerWidth <= 820) elements.ruleEditor.classList.add("open");
}

function renderRuleEditor(rule) {
  document.querySelector("#rule-editor-empty").hidden = Boolean(rule);
  document.querySelector("#rule-form").hidden = !rule;
  document.querySelector("#manufacturer-form").hidden = true;
  if (!rule) return;
  document.querySelector("#editor-area").textContent = humanize(rule.area);
  document.querySelector("#editor-rule-id").textContent = rule.rule_id;
  const marker = document.querySelector("#editor-state");
  marker.textContent = rule.has_draft ? "Draft change" : "Active";
  marker.classList.toggle("changed", rule.has_draft);
  document.querySelector("#editor-source-fields").textContent = rule.source_fields.join(", ") || "—";
  document.querySelector("#editor-source-terms").textContent = rule.source_terms.join(", ") || "—";
  document.querySelector("#editor-scopes").textContent = rule.vehicle_scopes.join(", ") || "All vehicles";
  document.querySelector("#editor-manufacturers").textContent = rule.manufacturers.join(", ") || "Any manufacturer";
  document.querySelector("#editor-canonical-field").value = rule.canonical_field;
  const canonical = document.querySelector("#editor-canonical-value");
  canonical.replaceChildren(new Option("Unresolved / no value", ""), ...rule.canonical_options.map((value) => new Option(humanize(value), value)));
  canonical.value = rule.effective_canonical_value ?? "";
  document.querySelector("#editor-decision").value = rule.effective_decision;
  document.querySelector("#editor-display").value = rule.effective_display_value ?? "";
  document.querySelector("#editor-note").value = rule.change_note ?? "";
  document.querySelector("#discard-draft").hidden = !rule.has_draft;
}

function renderManufacturerEditor(entity) {
  document.querySelector("#rule-editor-empty").hidden = Boolean(entity);
  document.querySelector("#manufacturer-form").hidden = !entity;
  document.querySelector("#rule-form").hidden = true;
  if (!entity) return;
  document.querySelector("#manufacturer-source-field").textContent = humanize(entity.source_field);
  document.querySelector("#manufacturer-entity-term").textContent = entity.source_term;
  const marker = document.querySelector("#manufacturer-state");
  marker.textContent = entity.has_draft ? "Draft change" : entity.is_discovered && entity.effective_entity_role === "unknown" ? "Needs classification" : "Active";
  marker.classList.toggle("changed", entity.has_draft || entity.effective_entity_role === "unknown");
  document.querySelector("#manufacturer-source-term").textContent = `${entity.source_field} = ${entity.source_term}`;
  const matchScope = {
    whole_token_prefix: "Complete prefix + reviewed exact examples",
    diacritic_insensitive_prefix: "Complete prefix · punctuation and accent tolerant",
  };
  document.querySelector("#manufacturer-match-type").textContent = matchScope[entity.match_type] || humanize(entity.match_type);
  document.querySelector("#manufacturer-occurrences").textContent = entity.occurrences || "No unresolved occurrences";
  document.querySelector("#manufacturer-base-values").textContent = entity.base_manufacturers.join(", ") || "None supplied";
  document.querySelector("#manufacturer-created-at").textContent = formatDateTime(entity.created_at, "Built-in catalog / not versioned");
  document.querySelector("#manufacturer-updated-at").textContent = formatDateTime(entity.updated_at, "No database update recorded");
  const reviewedExamples = entity.reviewed_examples || [];
  const examplesSection = document.querySelector("#manufacturer-examples-section");
  examplesSection.hidden = reviewedExamples.length === 0;
  examplesSection.open = false;
  document.querySelector("#manufacturer-example-count").textContent = reviewedExamples.length;
  document.querySelector("#manufacturer-examples").innerHTML = reviewedExamples.map((example) => `<li>${escapeHtml(example)}</li>`).join("");
  document.querySelector("#manufacturer-canonical").value = entity.effective_canonical_name ?? "";
  document.querySelector("#manufacturer-role").value = entity.effective_entity_role;
  document.querySelector("#manufacturer-behavior").value = entity.effective_base_behavior;
  document.querySelector("#manufacturer-note").value = entity.change_note ?? "";
  document.querySelector("#discard-manufacturer-draft").hidden = !entity.has_draft;
}

async function saveRuleDraft(event) {
  event.preventDefault();
  if (!ruleState.selectedId) return;
  const payload = {
    canonical_value: document.querySelector("#editor-canonical-value").value || null,
    decision: document.querySelector("#editor-decision").value,
    display_value: document.querySelector("#editor-display").value.trim() || null,
    change_note: document.querySelector("#editor-note").value.trim(),
  };
  try {
    ruleState.page = await apiRequest(`/v1/normalization-review/rules/${encodeURIComponent(ruleState.selectedId)}/draft`, { method: "PUT", body: JSON.stringify(payload) });
    renderRules();
    showToast("Draft saved. Activate it before re-importing data.");
  } catch (error) { showToast(`Draft was not saved. ${error.message}`); }
}

async function discardRuleDraft() {
  if (!ruleState.selectedId) return;
  try {
    ruleState.page = await apiRequest(`/v1/normalization-review/rules/${encodeURIComponent(ruleState.selectedId)}/draft`, { method: "DELETE" });
    renderRules();
    showToast("Draft discarded; the active rule is unchanged.");
  } catch (error) { showToast(`Draft was not discarded. ${error.message}`); }
}

function switchRuleKind(kind) {
  ruleState.kind = kind;
  ruleState.selectedId = null;
  elements.ruleSearch.value = "";
  elements.ruleStateFilter.replaceChildren(
    new Option("All rules", ""),
    new Option("Draft changes", "draft"),
    ...(kind === "manufacturer"
      ? [new Option("Vehicle manufacturers", "vehicle_manufacturer"), new Option("Bodybuilders / converters", "bodybuilder_converter"), new Option("Corporate groups", "corporate_group"), new Option("Needs classification", "unknown")]
      : [new Option("Accepted", "accepted"), new Option("Proposed", "proposed")])
  );
  elements.ruleSearch.placeholder = kind === "manufacturer" ? "Search company, source value, or role…" : "Search rule, source term, or value…";
  ensureRuleSelection();
  renderRules();
}

function syncManufacturerBehavior() {
  const role = document.querySelector("#manufacturer-role").value;
  document.querySelector("#manufacturer-behavior").value = {
    vehicle_manufacturer: "use_entity",
    bodybuilder_converter: "use_base_manufacturer",
    corporate_group: "require_evidence_review",
    unknown: "require_evidence_review",
  }[role];
}

async function saveManufacturerEntityDraft(event) {
  event.preventDefault();
  if (!ruleState.selectedId) return;
  const payload = {
    canonical_name: document.querySelector("#manufacturer-canonical").value.trim() || null,
    entity_role: document.querySelector("#manufacturer-role").value,
    base_behavior: document.querySelector("#manufacturer-behavior").value,
    change_note: document.querySelector("#manufacturer-note").value.trim(),
  };
  try {
    ruleState.page = await apiRequest(`/v1/normalization-review/rules/entities/${encodeURIComponent(ruleState.selectedId)}/draft`, { method: "PUT", body: JSON.stringify(payload) });
    renderRules();
    showToast("Manufacturer classification saved as a draft.");
  } catch (error) { showToast(`Entity draft was not saved. ${error.message}`); }
}

async function discardManufacturerEntityDraft() {
  if (!ruleState.selectedId) return;
  try {
    ruleState.page = await apiRequest(`/v1/normalization-review/rules/entities/${encodeURIComponent(ruleState.selectedId)}/draft`, { method: "DELETE" });
    renderRules();
    showToast("Manufacturer entity draft discarded.");
  } catch (error) { showToast(`Entity draft was not discarded. ${error.message}`); }
}

async function activateRules() {
  const note = document.querySelector("#activation-note").value.trim();
  if (note.length < 5) { showToast("Add an activation note of at least 5 characters."); return; }
  const button = document.querySelector("#activate-rules");
  button.disabled = true;
  try {
    const result = await apiRequest("/v1/normalization-review/rules/activate", { method: "POST", body: JSON.stringify({ note }) });
    document.querySelector("#activation-note").value = "";
    await loadRules();
    showToast(`${result.activated_rules} draft rule${result.activated_rules === 1 ? "" : "s"} activated as ${result.version}.`);
  } catch (error) { showToast(`Rules were not activated. ${error.message}`); button.disabled = false; }
}

async function reprocessBatch() {
  if (!state.page?.batch_id) { showToast("Load a normalized batch before re-importing."); return; }
  const button = document.querySelector("#reprocess-batch");
  button.disabled = true;
  button.textContent = "Re-importing…";
  try {
    const result = await apiRequest("/v1/normalization-review/rules/reprocess", { method: "POST", body: JSON.stringify({ source_batch_id: state.page.batch_id }) });
    document.querySelector("#comparison").hidden = false;
    document.querySelector("#comparison-batch").textContent = result.new_batch_id;
    document.querySelector("#before-resolved").textContent = result.before.resolved;
    document.querySelector("#after-resolved").textContent = result.after.resolved;
    document.querySelector("#before-provisional").textContent = result.before.provisional;
    document.querySelector("#after-provisional").textContent = result.after.provisional;
    document.querySelector("#before-review").textContent = result.before.review_required;
    document.querySelector("#after-review").textContent = result.after.review_required;
    await loadVehicles();
    showToast("Re-import finished. The original batch remains unchanged.");
  } catch (error) { showToast(`Re-import failed safely. ${error.message}`); }
  finally { button.textContent = "Re-import current batch"; renderRules(); }
}

async function loadQueue() {
  if (queueState.loading) return;
  queueState.loading = true;
  document.querySelector("#queue-result-count").textContent = "Loading queue…";
  const status = document.querySelector("#queue-status").value;
  try {
    const query = new URLSearchParams();
    if (status) query.set("status", status);
    if (state.page?.batch_id) query.set("batch_id", state.page.batch_id);
    queueState.page = await apiRequest(`/v1/normalization-review/queue?${query}`);
    if (!queueState.page.items.some((item) => item.id === queueState.selectedId)) queueState.selectedId = queueState.page.items[0]?.id ?? null;
    renderQueue();
  } catch (error) { showToast(`Could not load review queue. ${error.message}`); }
  finally { queueState.loading = false; }
}

function renderQueue() {
  const page = queueState.page;
  if (!page) return;
  ["pending", "in_review", "resolved", "rejected"].forEach((status) => {
    document.querySelector(`#queue-${status.replace("_", "-")}`).textContent = (page.counts[status] || 0).toLocaleString();
  });
  document.querySelector("#queue-result-count").textContent = `${page.items.length.toLocaleString()} review item${page.items.length === 1 ? "" : "s"}`;
  document.querySelector("#queue-empty").hidden = page.items.length > 0;
  document.querySelector("#queue-rows").innerHTML = page.items.map((item) => {
    const source = item.source_evidence || {};
    const vehicle = [source.manufacturer, source.brand, source.model].filter(Boolean).join(" · ") || `Source record ${item.source_record_id}`;
    return `<tr data-queue-id="${item.id}" class="${item.id === queueState.selectedId ? "selected" : ""}" tabindex="0"><td><div class="vehicle-cell"><strong>${escapeHtml(vehicle)}</strong><span>Record ${item.source_record_id} · ${escapeHtml(item.source_batch_id || "Unknown batch")}</span></div></td><td>${escapeHtml(humanize(item.reason_detail || item.reason_code))}</td><td>${percent(item.confidence)}</td><td><span class="queue-state queue-${escapeHtml(item.status)}">${escapeHtml(humanize(item.status))}</span></td></tr>`;
  }).join("");
  document.querySelectorAll("#queue-rows tr").forEach((row) => {
    const select = () => { queueState.selectedId = Number(row.dataset.queueId); renderQueue(); };
    row.addEventListener("click", select);
    row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") select(); });
  });
  const changes = page.rule_activity || [];
  document.querySelector("#queue-rule-changes").innerHTML = changes.length ? changes.map((activity) => `<article><div><strong>${escapeHtml(activity.rule_id)}</strong><small>${escapeHtml(humanize(activity.rule_kind))}</small></div><div class="rule-change-values"><span>${escapeHtml(displayValue(activity.previous_value))}</span><b>→</b><strong>${escapeHtml(displayValue(activity.new_value))}</strong><small>${escapeHtml(activity.change_note)}</small></div><div><span class="draft-marker changed">Draft</span><small>${escapeHtml(activity.changed_by || "Reviewer not recorded")} · ${escapeHtml(formatDateTime(activity.changed_at, "Recorded"))}${activity.related_review_item_id ? ` · Queue #${activity.related_review_item_id}` : ""}</small></div></article>`).join("") : "<p>No unactivated rule drafts currently exist.</p>";
  renderQueueEditor(page.items.find((item) => item.id === queueState.selectedId));
}

function renderQueueEditor(item) {
  document.querySelector("#queue-editor-empty").hidden = Boolean(item);
  document.querySelector("#queue-review-form").hidden = !item;
  if (!item) return;
  const source = item.source_evidence || {};
  document.querySelector("#queue-item-state").textContent = humanize(item.status);
  document.querySelector("#queue-item-title").textContent = `${source.manufacturer || source.brand || "Unresolved vehicle"} ${source.model || ""}`.trim();
  document.querySelector("#queue-item-confidence").textContent = percent(item.confidence);
  document.querySelector("#queue-source-evidence").innerHTML = Object.entries(source).filter(([, value]) => value !== null && value !== "").slice(0, 20).map(([key, value]) => `<div><dt>${escapeHtml(sourceLabel(key))}</dt><dd>${escapeHtml(displayValue(value))}</dd></div>`).join("");
  document.querySelector("#queue-current-result").innerHTML = Object.entries({ ...item.normalized, ...item.candidates }).map(([key, value]) => `<div><dt>${escapeHtml(humanize(key))}</dt><dd>${escapeHtml(displayValue(value))}</dd></div>`).join("") || "<div><dt>Result</dt><dd>No candidates or accepted values</dd></div>";
  document.querySelector("#queue-reason-detail").textContent = item.reason_detail || item.reason_code;
  const terminal = item.status === "resolved" || item.status === "rejected";
  document.querySelector("#queue-decision-fields").hidden = terminal;
  document.querySelector("#queue-actions").hidden = terminal;
  document.querySelector("#queue-resolution-summary").hidden = !terminal;
  document.querySelector("#start-review").hidden = item.status !== "pending";
  document.querySelector("#save-review-draft").hidden = item.status !== "in_review";
  const draft = item.review_draft || {};
  document.querySelector("#queue-reviewer").value = draft.reviewer || "";
  document.querySelector("#queue-field").value = draft.field || "manufacturer";
  document.querySelector("#queue-canonical-value").value = draft.canonical_value || "";
  document.querySelector("#queue-decision-scope").value = draft.decision_scope || "vehicle_only";
  document.querySelector("#queue-rule-reference").value = draft.rule_reference || "";
  document.querySelector("#queue-rule-reference-label").hidden = (draft.decision_scope || "vehicle_only") === "vehicle_only";
  document.querySelector("#queue-decision-reason").value = draft.reason || "";
  if (terminal) document.querySelector("#queue-resolution-values").innerHTML = Object.entries(item.resolution || {}).filter(([, value]) => value).map(([key, value]) => `<div><dt>${escapeHtml(humanize(key))}</dt><dd>${escapeHtml(displayValue(value))}</dd></div>`).join("");
}

async function transitionQueue(status, overrides = {}) {
  if (!queueState.selectedId) return;
  const payload = { status, ...overrides };
  await apiRequest(`/v1/normalization-review/queue/${queueState.selectedId}/transition`, { method: "POST", body: JSON.stringify(payload) });
  await loadQueue();
}

function queueDraftPayload() {
  return {
    reviewer: document.querySelector("#queue-reviewer").value.trim() || null,
    field: document.querySelector("#queue-field").value,
    canonical_value: document.querySelector("#queue-canonical-value").value.trim() || null,
    decision_scope: document.querySelector("#queue-decision-scope").value,
    rule_reference: document.querySelector("#queue-rule-reference").value.trim() || null,
    reason: document.querySelector("#queue-decision-reason").value.trim() || null,
  };
}

async function createReviewRuleDraft(scope, reference, canonicalValue, reason) {
  if (!ruleState.page) await loadRules();
  if (scope === "manufacturer_entity") {
    const entity = ruleState.page?.manufacturer_entities.find((item) => item.entity_id === reference);
    if (!entity) throw new Error(`Manufacturer entity ${reference} was not found.`);
    await apiRequest(`/v1/normalization-review/rules/entities/${encodeURIComponent(reference)}/draft`, { method: "PUT", body: JSON.stringify({ canonical_name: canonicalValue, entity_role: "vehicle_manufacturer", base_behavior: "use_entity", change_note: reason }) });
  } else if (scope === "translation_rule") {
    const rule = ruleState.page?.rules.find((item) => item.rule_id === reference);
    if (!rule) throw new Error(`Translation rule ${reference} was not found.`);
    await apiRequest(`/v1/normalization-review/rules/${encodeURIComponent(reference)}/draft`, { method: "PUT", body: JSON.stringify({ canonical_value: canonicalValue, decision: "accepted", change_note: reason }) });
  }
  ruleState.page = null;
}

async function approveQueueDecision(event) {
  event.preventDefault();
  const reviewer = document.querySelector("#queue-reviewer").value.trim();
  const field = document.querySelector("#queue-field").value;
  const canonicalValue = document.querySelector("#queue-canonical-value").value.trim();
  const decisionScope = document.querySelector("#queue-decision-scope").value;
  const ruleReference = document.querySelector("#queue-rule-reference").value.trim() || null;
  const reason = document.querySelector("#queue-decision-reason").value.trim();
  if (!reviewer || !canonicalValue || reason.length < 5) { showToast("Add the reviewer, canonical value, and a clear review reason."); return; }
  if (decisionScope !== "vehicle_only" && !ruleReference) { showToast("Choose the exact existing rule or manufacturer entity ID."); return; }
  try {
    if (decisionScope !== "vehicle_only") await createReviewRuleDraft(decisionScope, ruleReference, canonicalValue, reason);
    await transitionQueue("resolved", { reviewer, field, canonical_value: canonicalValue, decision_scope: decisionScope, rule_reference: ruleReference, reason });
    showToast(decisionScope === "vehicle_only" ? "Vehicle review decision recorded." : "Review resolved and reusable rule draft created.");
  } catch (error) { showToast(`Decision was not saved. ${error.message}`); }
}

document.querySelectorAll(".view-tab").forEach((tab) => tab.addEventListener("click", () => switchView(tab.dataset.view)));
let tecdocSearchTimer;
document.querySelector("#tecdoc-search").addEventListener("input", () => {
  clearTimeout(tecdocSearchTimer);
  tecdocSearchTimer = setTimeout(() => { tecdocState.offset = 0; loadTecDoc(); }, 220);
});
document.querySelector("#tecdoc-previous").addEventListener("click", () => { tecdocState.offset = Math.max(0, tecdocState.offset - 100); loadTecDoc(); });
document.querySelector("#tecdoc-next").addEventListener("click", () => { tecdocState.offset += 100; loadTecDoc(); });
document.querySelectorAll(".rule-kind").forEach((tab) => tab.addEventListener("click", () => switchRuleKind(tab.dataset.ruleKind)));
[elements.ruleSearch, elements.ruleArea, elements.ruleStateFilter].forEach((control) => control.addEventListener(control.tagName === "INPUT" ? "input" : "change", () => { if (ruleState.page) renderRules(); }));
document.querySelector("#rule-form").addEventListener("submit", saveRuleDraft);
document.querySelector("#manufacturer-form").addEventListener("submit", saveManufacturerEntityDraft);
document.querySelector("#discard-draft").addEventListener("click", discardRuleDraft);
document.querySelector("#discard-manufacturer-draft").addEventListener("click", discardManufacturerEntityDraft);
document.querySelector("#manufacturer-role").addEventListener("change", syncManufacturerBehavior);
document.querySelector("#activate-rules").addEventListener("click", activateRules);
document.querySelector("#reprocess-batch").addEventListener("click", reprocessBatch);
document.querySelector("#queue-status").addEventListener("change", loadQueue);
document.querySelector("#refresh-queue").addEventListener("click", loadQueue);
document.querySelector("#queue-decision-scope").addEventListener("change", (event) => {
  document.querySelector("#queue-rule-reference-label").hidden = event.target.value === "vehicle_only";
});
document.querySelector("#queue-review-form").addEventListener("submit", approveQueueDecision);
document.querySelector("#start-review").addEventListener("click", async () => {
  try { await transitionQueue("in_review", queueDraftPayload()); showToast("Review claimed and correction draft saved."); }
  catch (error) { showToast(`Review was not started. ${error.message}`); }
});
document.querySelector("#save-review-draft").addEventListener("click", async () => {
  try { await transitionQueue("in_review", queueDraftPayload()); showToast("Review correction draft saved."); }
  catch (error) { showToast(`Review draft was not saved. ${error.message}`); }
});
document.querySelector("#reject-review").addEventListener("click", async () => {
  const reviewer = document.querySelector("#queue-reviewer").value.trim();
  const reason = document.querySelector("#queue-decision-reason").value.trim();
  if (!reviewer || reason.length < 5) { showToast("Add the reviewer and a clear rejection reason."); return; }
  try { await transitionQueue("rejected", { reviewer, reason }); showToast("Review rejected with its reason recorded."); }
  catch (error) { showToast(`Review was not rejected. ${error.message}`); }
});

loadVehicles();
