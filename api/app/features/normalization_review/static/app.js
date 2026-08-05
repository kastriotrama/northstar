const PAGE_SIZE = 250;
const state = { filters: {}, offset: 0, selectedId: null, page: null, loading: false };
const ruleState = { page: null, selectedId: null, kind: "translation", loading: false };

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

function statusBadge(status) {
  return `<span class="status-badge status-${escapeHtml(status)}">${escapeHtml(labels[status] ?? humanize(status))}</span>`;
}

function percent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function formatDateTime(value, fallback) {
  if (!value) return fallback;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function ruleExplanation(ruleId) {
  if (ruleId === "MFR-BRAND-PREFIX-FALLBACK") return "Brand begins with an approved Manufacturer entity alias";
  if (ruleId === "MFR-BRAND-REVIEWED-EXAMPLE") return "Brand is an exact reviewed example beneath its Manufacturer entity";
  if (ruleId === "MFR-MODEL-VARIANT-FALLBACK") return "Model or Variant begins with an approved Manufacturer entity alias";
  if (ruleId.startsWith("MFE-")) return "Reviewed Manufacturer entity classification";
  return "Normalization rule evidence";
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
      <td><div class="vehicle-cell"><strong>${escapeHtml(vehicle.manufacturer || "Unresolved manufacturer")} ${escapeHtml(vehicle.model_family || "")}</strong><span>${vehicle.source_brand ? `Brand: ${escapeHtml(vehicle.source_brand)} · ` : ""}Record ${vehicle.source_record_id}${vehicle.engine_code ? ` · ${escapeHtml(vehicle.engine_code)}` : ""}</span></div></td>
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

  const manufacturerRule = vehicle.candidate_rule_ids.find((rule) => rule.startsWith("MFR-") || rule.startsWith("MFE-"))
    || vehicle.applied_rule_ids.find((rule) => rule === "MFR-BRAND-REVIEWED-EXAMPLE")
    || vehicle.applied_rule_ids.find((rule) => rule.startsWith("MFR-") || rule.startsWith("MFE-"));
  document.querySelector("#source-evidence").innerHTML = [
    ["Brand", vehicle.source_brand || "Not supplied"],
    ["Source record", vehicle.source_record_id],
    ["Manufacturer decision", manufacturerRule ? ruleExplanation(manufacturerRule) : "No Manufacturer entity fallback used"],
  ].map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(displayValue(value))}</dd></div>`).join("");

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

  const appliedRules = [...new Set([...vehicle.applied_rule_ids, ...vehicle.rule_matches.map((match) => match.rule_id).filter(Boolean)])];
  const candidateRules = [...new Set(vehicle.candidate_rule_ids || [])];
  const rules = [
    ...appliedRules.map((rule) => ({ rule, state: "Applied" })),
    ...candidateRules.filter((rule) => !appliedRules.includes(rule)).map((rule) => ({ rule, state: "Candidate" })),
  ];
  document.querySelector("#rule-list").innerHTML = rules.length
    ? rules.map(({ rule, state }) => `<span class="rule-evidence ${state === "Candidate" ? "candidate" : ""}" title="${escapeHtml(ruleExplanation(rule))}"><b>${escapeHtml(state)}</b>${escapeHtml(rule)}</span>`).join("")
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
  elements.vehiclesView.hidden = showRules;
  elements.rulesView.hidden = !showRules;
  document.querySelectorAll(".view-tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === view));
  if (showRules && !ruleState.page) loadRules();
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
  const query = elements.ruleSearch.value.trim().toLowerCase();
  const area = elements.ruleArea.value;
  const status = elements.ruleStateFilter.value;
  if (ruleState.kind === "manufacturer") {
    return ruleState.page.manufacturer_entities.filter((entity) => {
      const searchable = [entity.entity_id, entity.source_field, entity.source_term, entity.effective_canonical_name, entity.effective_entity_role, ...(entity.reviewed_examples || [])].join(" ").toLowerCase();
      const statusMatches = !status || (status === "draft" ? entity.has_draft : status === entity.effective_entity_role);
      return (!query || searchable.includes(query)) && statusMatches;
    });
  }
  return ruleState.page.rules.filter((rule) => {
    const searchable = [rule.rule_id, rule.area, rule.canonical_field, rule.effective_canonical_value, ...rule.source_terms].join(" ").toLowerCase();
    const statusMatches = !status || (status === "draft" ? rule.has_draft : rule.effective_decision === status);
    return (!query || searchable.includes(query)) && (!area || rule.area === area) && statusMatches;
  });
}

function renderRules() {
  const page = ruleState.page;
  document.querySelector("#active-rule-version").textContent = page.active_version;
  document.querySelector("#rule-draft-count").textContent = page.draft_count.toLocaleString();
  const reasons = page.review_reason_summary || {};
  document.querySelector("#review-backlog-total").textContent = state.page?.summary.review_required ?? "—";
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
  document.querySelector("#manufacturer-match-type").textContent = entity.match_type === "whole_token_prefix" ? "Complete prefix + reviewed exact examples" : humanize(entity.match_type);
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

document.querySelectorAll(".view-tab").forEach((tab) => tab.addEventListener("click", () => switchView(tab.dataset.view)));
document.querySelectorAll(".rule-kind").forEach((tab) => tab.addEventListener("click", () => switchRuleKind(tab.dataset.ruleKind)));
[elements.ruleSearch, elements.ruleArea, elements.ruleStateFilter].forEach((control) => control.addEventListener(control.tagName === "INPUT" ? "input" : "change", () => { if (ruleState.page) renderRules(); }));
document.querySelector("#rule-form").addEventListener("submit", saveRuleDraft);
document.querySelector("#manufacturer-form").addEventListener("submit", saveManufacturerEntityDraft);
document.querySelector("#discard-draft").addEventListener("click", discardRuleDraft);
document.querySelector("#discard-manufacturer-draft").addEventListener("click", discardManufacturerEntityDraft);
document.querySelector("#manufacturer-role").addEventListener("change", syncManufacturerBehavior);
document.querySelector("#activate-rules").addEventListener("click", activateRules);
document.querySelector("#reprocess-batch").addEventListener("click", reprocessBatch);

loadVehicles();
