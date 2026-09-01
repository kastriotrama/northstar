"use strict";

const state = {
  buildId: null,
  status: "",
  query: "",
  offset: 0,
  limit: 100,
  total: 0,
  maxMembers: 1,
  selectedChunkId: null,
  selectedMemberId: null,
  members: [],
};

const elements = {
  buildSelect: document.getElementById("build-select"),
  stats: document.getElementById("stats"),
  statChunks: document.getElementById("stat-chunks"),
  statRows: document.getElementById("stat-rows"),
  statDecided: document.getElementById("stat-decided"),
  statCoverage: document.getElementById("stat-coverage"),
  statProgress: document.getElementById("stat-progress"),
  search: document.getElementById("search"),
  statusFilter: document.getElementById("status-filter"),
  listMeta: document.getElementById("list-meta"),
  chunkList: document.getElementById("chunk-list"),
  prevPage: document.getElementById("prev-page"),
  nextPage: document.getElementById("next-page"),
  pageLabel: document.getElementById("page-label"),
  detailEmpty: document.getElementById("detail-empty"),
  detail: document.getElementById("detail"),
  detailTitle: document.getElementById("detail-title"),
  detailSub: document.getElementById("detail-sub"),
  detailStatus: document.getElementById("detail-status"),
  signatureGrid: document.getElementById("signature-grid"),
  reasonChips: document.getElementById("reason-chips"),
  spreadVerdict: document.getElementById("spread-verdict"),
  spreadNote: document.getElementById("spread-note"),
  spreadList: document.getElementById("spread-list"),
  vehiclePlate: document.getElementById("vehicle-plate"),
  vehicleSub: document.getElementById("vehicle-sub"),
  compareRows: document.getElementById("compare-rows"),
  oemForm: document.getElementById("oem-form"),
  oemMember: document.getElementById("oem-member"),
  oemFetch: document.getElementById("oem-fetch"),
  sampleList: document.getElementById("sample-list"),
  propose: document.getElementById("propose"),
  proposalList: document.getElementById("proposal-list"),
  memberRows: document.getElementById("member-rows"),
  memberNote: document.getElementById("member-note"),
  toast: document.getElementById("toast"),
};

const numberFormat = new Intl.NumberFormat("en-US");

function formatCount(value) {
  return numberFormat.format(value);
}

let toastTimer = null;
function showToast(message, isError) {
  elements.toast.textContent = message;
  elements.toast.classList.toggle("error", Boolean(isError));
  elements.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    elements.toast.hidden = true;
  }, 4200);
}

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (body && body.detail) detail = String(body.detail);
    } catch (error) {
      /* keep default detail */
    }
    throw new Error(detail);
  }
  return response.json();
}

function signatureTitle(signature) {
  const manufacturer = signature.manufacturer || "Unknown manufacturer";
  const model = signature.model_family || "unknown model";
  return `${manufacturer} · ${model}`;
}

function signatureMetaLine(signature) {
  const parts = [];
  if (signature.production_year) parts.push(String(signature.production_year));
  if (Array.isArray(signature.energy_sources) && signature.energy_sources.length) {
    parts.push(signature.energy_sources.join("/"));
  }
  if (signature.power_kw) parts.push(`${signature.power_kw} kW`);
  if (signature.displacement_cc) parts.push(`${signature.displacement_cc} cc`);
  if (signature.drive_type) parts.push(signature.drive_type);
  if (signature.bodywork_form) parts.push(signature.bodywork_form);
  return parts.length ? parts.join(" · ") : "No further technical evidence";
}

function statusPill(status) {
  const pill = document.createElement("span");
  pill.className = `pill pill-${status}`;
  pill.textContent = status.replace("_", " ");
  return pill;
}

async function loadBuilds() {
  const builds = await api("/v1/match-review/builds");
  elements.buildSelect.innerHTML = "";
  if (!builds.length) {
    const option = document.createElement("option");
    option.textContent = "No builds yet";
    option.value = "";
    elements.buildSelect.appendChild(option);
    elements.listMeta.textContent =
      "Run `northstar-ingest build-match-chunks` to create the first chunk build.";
    return false;
  }
  for (const build of builds) {
    const option = document.createElement("option");
    option.value = build.build_id;
    const started = new Date(build.started_at).toISOString().slice(0, 10);
    option.textContent = `${build.source_batch_id} — ${started} (${formatCount(build.chunk_count)} chunks)`;
    elements.buildSelect.appendChild(option);
  }
  state.buildId = builds[0].build_id;
  elements.buildSelect.value = state.buildId;
  return true;
}

async function loadChunks() {
  const params = new URLSearchParams({
    limit: String(state.limit),
    offset: String(state.offset),
  });
  if (state.buildId) params.set("build_id", state.buildId);
  if (state.status) params.set("status", state.status);
  if (state.query) params.set("query", state.query);
  const page = await api(`/v1/match-review/chunks?${params.toString()}`);
  state.total = page.total;

  elements.stats.hidden = false;
  elements.statChunks.textContent = formatCount(page.build.chunk_count);
  elements.statRows.textContent = formatCount(page.build.row_count);
  elements.statDecided.textContent = formatCount(page.decided_members);
  const coverage = page.build.row_count
    ? Math.round((page.decided_members / page.build.row_count) * 1000) / 10
    : 0;
  elements.statCoverage.textContent = `${coverage}%`;
  elements.statProgress.style.width = `${Math.min(coverage, 100)}%`;

  state.maxMembers = page.items.length
    ? Math.max(...page.items.map((item) => item.member_count))
    : 1;

  elements.chunkList.innerHTML = "";
  for (const item of page.items) {
    elements.chunkList.appendChild(renderChunkCard(item));
  }
  elements.listMeta.textContent = `${formatCount(page.total)} chunks, largest first`;
  const pageIndex = Math.floor(state.offset / state.limit) + 1;
  const pageCount = Math.max(1, Math.ceil(page.total / state.limit));
  elements.pageLabel.textContent = `Page ${pageIndex} of ${pageCount}`;
  elements.prevPage.disabled = state.offset === 0;
  elements.nextPage.disabled = state.offset + state.limit >= page.total;
}

function valueChip(entry, { clickable = false } = {}) {
  const chip = document.createElement(clickable ? "button" : "span");
  if (clickable) chip.type = "button";
  chip.className = `value-chip${clickable ? " clickable" : ""}`;

  const raw = document.createElement("span");
  raw.className = "chip-raw";
  raw.textContent = entry.value;
  chip.appendChild(raw);

  // The register's own meaning, when it defines one — `AC` alone is opaque.
  if (entry.meaning) {
    const meaning = document.createElement("span");
    meaning.className = "chip-meaning";
    meaning.textContent = entry.meaning;
    chip.appendChild(meaning);
  }

  const count = document.createElement("span");
  count.className = "chip-count";
  count.textContent = formatCount(entry.count);
  chip.appendChild(count);
  return chip;
}

function renderChunkCard(item) {
  const card = document.createElement("li");
  card.className = "chunk-card";
  card.dataset.chunkId = item.chunk_id;
  if (item.chunk_id === state.selectedChunkId) card.classList.add("selected");

  const top = document.createElement("div");
  top.className = "chunk-card-top";
  const title = document.createElement("span");
  title.className = "chunk-title";
  title.textContent = signatureTitle(item.signature);
  top.appendChild(title);
  top.appendChild(statusPill(item.status));
  card.appendChild(top);

  const meta = document.createElement("p");
  meta.className = "chunk-meta";
  meta.textContent = signatureMetaLine(item.signature);
  card.appendChild(meta);

  const leverage = document.createElement("div");
  leverage.className = "chunk-leverage";
  const track = document.createElement("div");
  track.className = "leverage-track";
  const fill = document.createElement("span");
  fill.style.width = `${Math.max(3, (item.member_count / state.maxMembers) * 100)}%`;
  track.appendChild(fill);
  leverage.appendChild(track);
  const count = document.createElement("span");
  count.className = "chunk-count";
  count.textContent = `${formatCount(item.member_count)} rows`;
  leverage.appendChild(count);
  card.appendChild(leverage);

  card.addEventListener("click", () => selectChunk(item.chunk_id));
  return card;
}

async function selectChunk(chunkId) {
  state.selectedChunkId = chunkId;
  history.replaceState(null, "", `#${chunkId}`);
  for (const card of elements.chunkList.children) {
    card.classList.toggle("selected", card.dataset.chunkId === chunkId);
  }
  await loadChunkDetail(chunkId);
}

async function loadChunkDetail(chunkId) {
  const detail = await api(`/v1/match-review/chunks/${chunkId}`);
  elements.detailEmpty.hidden = true;
  elements.detail.hidden = false;

  elements.detailTitle.textContent = signatureTitle(detail.signature);
  elements.detailSub.textContent =
    `${formatCount(detail.member_count)} rows share this signature — ` +
    "one decision covers them all.";
  elements.detailStatus.className = `pill pill-${detail.status}`;
  elements.detailStatus.textContent = detail.status.replace("_", " ");

  renderSignature(detail.signature);
  renderReasons(detail.reason_profile);
  renderMembers(detail);
  renderSamples(detail.oem_samples);
  renderProposals(detail.proposals);

  const closed = ["approved", "rejected", "split"].includes(detail.status);
  elements.propose.disabled = closed;
  elements.oemFetch.disabled = closed || !detail.members.length;

  state.members = detail.members;
  const current = detail.members.find(
    (member) => member.source_record_id === state.selectedMemberId
  );
  const first = current || detail.members[0] || null;
  await Promise.all([
    selectMember(first ? first.source_record_id : null),
    loadFieldProfile(chunkId),
  ]);
}

const IDENTITY_FIELDS = ["brand", "model", "model_no", "variant", "type_text"];

async function loadFieldProfile(chunkId) {
  let profile;
  try {
    profile = await api(`/v1/match-review/chunks/${chunkId}/field-profile`);
  } catch (error) {
    showToast(error.message, true);
    return;
  }
  const identitySpread = profile.varying_fields.filter((field) =>
    IDENTITY_FIELDS.includes(field)
  );
  const verdict = elements.spreadVerdict;
  if (identitySpread.length) {
    verdict.className = "pill pill-rejected";
    verdict.textContent = "mixed identity";
    elements.spreadNote.textContent =
      `Members disagree on ${identitySpread.join(", ")}. One decision cannot ` +
      "safely cover this chunk — split it rather than extrapolating.";
    elements.detailSub.textContent =
      `${formatCount(profile.member_count)} rows share this signature, but ` +
      "their source evidence does not — this chunk needs splitting.";
  } else if (profile.varying_fields.length) {
    verdict.className = "pill pill-split";
    verdict.textContent = "minor spread";
    elements.spreadNote.textContent =
      `Identity evidence agrees; ${profile.varying_fields.join(", ")} vary. ` +
      "Sample across the differing values before deciding.";
  } else {
    verdict.className = "pill pill-approved";
    verdict.textContent = "uniform";
    elements.spreadNote.textContent =
      "Every scanned member carries identical source evidence, so one " +
      "verified decision extrapolates safely.";
  }
  if (profile.truncated) {
    elements.spreadNote.textContent +=
      ` (scanned ${formatCount(profile.scanned_members)} of ` +
      `${formatCount(profile.member_count)} rows)`;
  }

  elements.spreadList.innerHTML = "";
  for (const field of profile.fields) {
    const item = document.createElement("li");
    item.className = "spread-item";
    if (!field.uniform) item.classList.add("varies");
    if (!field.uniform && IDENTITY_FIELDS.includes(field.field)) {
      item.classList.add("identity");
    }

    const head = document.createElement("div");
    head.className = "spread-head";
    const name = document.createElement("strong");
    name.textContent = field.field;
    head.appendChild(name);
    const count = document.createElement("span");
    count.className = "spread-count";
    count.textContent = field.uniform
      ? "1 value"
      : `${formatCount(field.distinct_count)} values`;
    head.appendChild(count);
    item.appendChild(head);

    const values = document.createElement("div");
    values.className = "spread-values";
    for (const entry of field.top_values) {
      values.appendChild(valueChip(entry));
    }
    if (field.distinct_count > field.top_values.length) {
      const more = document.createElement("span");
      more.className = "value-more";
      more.textContent = `+${formatCount(
        field.distinct_count - field.top_values.length
      )} more`;
      values.appendChild(more);
    }
    item.appendChild(values);
    elements.spreadList.appendChild(item);
  }
}

async function selectMember(sourceRecordId) {
  state.selectedMemberId = sourceRecordId;
  elements.oemMember.value =
    sourceRecordId === null ? "" : String(sourceRecordId);
  for (const row of elements.memberRows.children) {
    row.classList.toggle(
      "selected",
      row.dataset.recordId === String(sourceRecordId)
    );
  }
  if (sourceRecordId === null) {
    elements.vehiclePlate.textContent = "—";
    elements.vehicleSub.textContent = "No members in this chunk.";
    elements.compareRows.innerHTML = "";
    return;
  }
  try {
    const comparison = await api(
      `/v1/match-review/chunks/${state.selectedChunkId}/members/${sourceRecordId}`
    );
    renderComparison(comparison);
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderComparison(comparison) {
  elements.vehiclePlate.textContent = comparison.plate || `#${comparison.source_record_id}`;
  elements.vehicleSub.textContent = comparison.label;
  elements.compareRows.innerHTML = "";
  for (const row of comparison.rows) {
    if (!row.source_value && !row.normalized_value && !row.oem_value) continue;
    const tr = document.createElement("tr");
    if (row.conflict === true) tr.classList.add("conflict");
    if (row.conflict === false) tr.classList.add("agrees");
    if (row.status === "unresolved") tr.classList.add("row-unresolved");
    const field = document.createElement("td");
    field.className = "compare-field";

    const line = document.createElement("div");
    line.className = "compare-field-line";
    const label = document.createElement("span");
    label.textContent = row.field;
    line.appendChild(label);
    if (row.status !== "resolved") {
      // An unresolved field is actionable: jump to authoring a rule for it,
      // where the true scope (every matching car, not just this chunk) is
      // what the screen shows.
      const actionable = row.status === "unresolved" && row.resolvable;
      const tag = document.createElement(actionable ? "button" : "span");
      tag.className = `status-tag status-tag-${row.status}`;
      tag.textContent =
        row.status === "unresolved" ? "unknown meaning" : "missing";
      if (actionable) {
        tag.type = "button";
        tag.classList.add("status-tag-action");
        tag.textContent += " · resolve →";
        tag.title =
          `Author a rule for ${row.source_field} = ${row.source_value}. ` +
          "The rule applies to every matching car, not only this chunk.";
        tag.addEventListener("click", async (event) => {
          event.stopPropagation();
          switchView("unresolved");
          await loadPopulations();
          await selectPopulation({
            source_field: row.source_field,
            source_value: row.source_value,
          });
        });
      } else {
        tag.title =
          row.status === "unresolved"
            ? "The registry sent a value we cannot interpret yet."
            : "The registry sent nothing for this field.";
      }
      line.appendChild(tag);
    }
    field.appendChild(line);

    // The registry key behind the row, so a value can be traced back to the
    // exact TS field it came from.
    if (row.source_field) {
      const key = document.createElement("code");
      key.className = "compare-source-key";
      key.textContent = row.source_field;
      key.title = `Transportstyrelsen field: ${row.source_field}`;
      field.appendChild(key);
    }
    tr.appendChild(field);
    for (const value of [row.source_value, row.normalized_value, row.oem_value]) {
      const cell = document.createElement("td");
      cell.textContent = value || "—";
      if (!value) cell.className = "absent";
      tr.appendChild(cell);
    }
    if (row.conflict === true) {
      const badge = document.createElement("span");
      badge.className = "conflict-badge";
      badge.textContent = "conflict";
      tr.lastChild.appendChild(badge);
    }
    elements.compareRows.appendChild(tr);
  }
  if (!comparison.has_oem_evidence) {
    const tr = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 4;
    cell.className = "compare-hint";
    cell.textContent =
      "No OEM evidence for this vehicle yet — fetch it to complete the comparison.";
    tr.appendChild(cell);
    elements.compareRows.appendChild(tr);
  }
}

function renderSignature(signature) {
  const labels = {
    manufacturer: "Manufacturer",
    model_family: "Model family",
    production_year: "Year",
    energy_sources: "Energy",
    engine_code: "Engine code",
    displacement_cc: "Displacement",
    power_kw: "Power",
    drive_type: "Drive",
    bodywork_form: "Body",
  };
  elements.signatureGrid.innerHTML = "";
  for (const [key, label] of Object.entries(labels)) {
    const value = signature[key];
    const wrapper = document.createElement("div");
    const term = document.createElement("dt");
    term.textContent = label;
    const definition = document.createElement("dd");
    if (value === null || value === undefined || (Array.isArray(value) && !value.length)) {
      definition.textContent = "—";
      definition.className = "absent";
    } else {
      definition.textContent = Array.isArray(value) ? value.join(", ") : String(value);
    }
    wrapper.appendChild(term);
    wrapper.appendChild(definition);
    elements.signatureGrid.appendChild(wrapper);
  }
}

function renderReasons(reasonProfile) {
  elements.reasonChips.innerHTML = "";
  const entries = Object.entries(reasonProfile).sort((a, b) => b[1] - a[1]);
  for (const [reason, count] of entries) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = `${reason} `;
    const strong = document.createElement("b");
    strong.textContent = `×${formatCount(count)}`;
    chip.appendChild(strong);
    elements.reasonChips.appendChild(chip);
  }
}

function renderMembers(detail) {
  elements.memberRows.innerHTML = "";
  elements.oemMember.innerHTML = "";
  elements.memberNote.textContent =
    detail.member_count > detail.members.length
      ? `showing ${detail.members.length} of ${formatCount(detail.member_count)}`
      : "";
  for (const member of detail.members) {
    const row = document.createElement("tr");
    row.dataset.recordId = String(member.source_record_id);
    row.className = "member-row";
    const idCell = document.createElement("td");
    idCell.textContent = member.label;
    const statusCell = document.createElement("td");
    statusCell.textContent = member.normalization_status;
    const reasonCell = document.createElement("td");
    reasonCell.className = "member-reasons";
    reasonCell.textContent = member.review_reasons.join(", ") || "—";
    row.appendChild(idCell);
    row.appendChild(statusCell);
    row.appendChild(reasonCell);
    row.addEventListener("click", () =>
      selectMember(member.source_record_id)
    );
    elements.memberRows.appendChild(row);

    const option = document.createElement("option");
    option.value = String(member.source_record_id);
    option.textContent = member.label;
    elements.oemMember.appendChild(option);
  }
}

function renderSamples(samples) {
  elements.sampleList.innerHTML = "";
  if (!samples.length) {
    const empty = document.createElement("li");
    empty.className = "empty-row";
    empty.textContent = "No OEM evidence yet. Fetch a sample to start.";
    elements.sampleList.appendChild(empty);
    return;
  }
  for (const sample of samples) {
    const item = document.createElement("li");
    item.className = "sample-item";
    const head = document.createElement("div");
    head.className = "sample-head";
    const vin = document.createElement("strong");
    vin.textContent = sample.masked_vin;
    head.appendChild(vin);
    const provider = document.createElement("span");
    provider.className = "soft";
    provider.textContent = `${sample.provider} · record ${sample.source_record_id}`;
    head.appendChild(provider);
    if (sample.reused_cached_evidence) {
      const badge = document.createElement("span");
      badge.className = "badge badge-cached";
      badge.textContent = "cached — no new cost";
      head.appendChild(badge);
    }
    item.appendChild(head);
    const payload = document.createElement("pre");
    payload.className = "sample-payload";
    payload.textContent = JSON.stringify(sample.response_payload, null, 2);
    item.appendChild(payload);
    elements.sampleList.appendChild(item);
  }
}

function renderProposals(proposals) {
  elements.proposalList.innerHTML = "";
  if (!proposals.length) {
    const empty = document.createElement("li");
    empty.className = "empty-row";
    empty.textContent =
      "No proposals yet. Generate one once evidence is in place.";
    elements.proposalList.appendChild(empty);
    return;
  }
  for (const proposal of proposals) {
    elements.proposalList.appendChild(renderProposal(proposal));
  }
}

function renderProposal(proposal) {
  const item = document.createElement("li");
  item.className = "proposal-item";

  const head = document.createElement("div");
  head.className = "proposal-head";
  const label = document.createElement("strong");
  label.textContent = proposal.recommendation.replace(/_/g, " ");
  head.appendChild(label);
  const badge = document.createElement("span");
  badge.className = "badge";
  badge.textContent = `${proposal.status} · ${proposal.adjudicator_version}`;
  head.appendChild(badge);
  item.appendChild(head);

  const reasoning = document.createElement("p");
  reasoning.className = "proposal-reasoning";
  reasoning.textContent = proposal.reasoning;
  item.appendChild(reasoning);

  const meta = document.createElement("p");
  meta.className = "proposal-meta";
  const created = new Date(proposal.created_at).toLocaleString();
  let metaText = `Confidence ${Math.round(proposal.confidence * 100)}% · ${created}`;
  if (proposal.target_ktype_reference) {
    metaText += ` · target ${proposal.target_ktype_reference}`;
  }
  if (proposal.reviewed_by) {
    metaText += ` · reviewed by ${proposal.reviewed_by}`;
  }
  meta.textContent = metaText;
  item.appendChild(meta);

  if (proposal.status === "proposed") {
    const actions = document.createElement("div");
    actions.className = "proposal-actions";
    const approve = document.createElement("button");
    approve.type = "button";
    approve.className = "approve";
    approve.textContent = "Approve for whole chunk";
    approve.addEventListener("click", () =>
      reviewProposal(proposal.proposal_id, "approve")
    );
    const reject = document.createElement("button");
    reject.type = "button";
    reject.className = "reject";
    reject.textContent = "Reject";
    reject.addEventListener("click", () =>
      reviewProposal(proposal.proposal_id, "reject")
    );
    actions.appendChild(approve);
    actions.appendChild(reject);
    item.appendChild(actions);
  }
  return item;
}

async function reviewProposal(proposalId, action) {
  const reviewer = window.prompt(
    action === "approve"
      ? "Approve for every row in this chunk. Reviewer name:"
      : "Reject this proposal. Reviewer name:"
  );
  if (!reviewer || !reviewer.trim()) return;
  const note = window.prompt("Optional note:") || null;
  try {
    await api(`/v1/match-review/proposals/${proposalId}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, reviewer: reviewer.trim(), note }),
    });
    showToast(action === "approve" ? "Proposal approved." : "Proposal rejected.");
    await Promise.all([loadChunks(), loadChunkDetail(state.selectedChunkId)]);
  } catch (error) {
    showToast(error.message, true);
  }
}

elements.oemMember.addEventListener("change", () => {
  const value = Number(elements.oemMember.value);
  if (value) selectMember(value);
});

elements.oemForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.selectedChunkId || state.selectedMemberId === null) return;
  elements.oemFetch.disabled = true;
  try {
    const sample = await api(
      `/v1/match-review/chunks/${state.selectedChunkId}/oem-samples`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_record_id: state.selectedMemberId,
          request_id: crypto.randomUUID(),
        }),
      }
    );
    showToast(
      sample.reused_cached_evidence
        ? "Reused cached OEM evidence — no new provider cost."
        : "OEM evidence fetched and stored permanently."
    );
    await loadChunkDetail(state.selectedChunkId);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    elements.oemFetch.disabled = false;
  }
});

elements.propose.addEventListener("click", async () => {
  if (!state.selectedChunkId) return;
  elements.propose.disabled = true;
  try {
    await api(`/v1/match-review/chunks/${state.selectedChunkId}/proposals`, {
      method: "POST",
    });
    showToast("Proposal generated.");
    await Promise.all([loadChunks(), loadChunkDetail(state.selectedChunkId)]);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    elements.propose.disabled = false;
  }
});

elements.buildSelect.addEventListener("change", async () => {
  state.buildId = elements.buildSelect.value || null;
  state.offset = 0;
  await loadChunks().catch((error) => showToast(error.message, true));
});

elements.statusFilter.addEventListener("change", async () => {
  state.status = elements.statusFilter.value;
  state.offset = 0;
  await loadChunks().catch((error) => showToast(error.message, true));
});

let searchTimer = null;
elements.search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    state.query = elements.search.value.trim();
    state.offset = 0;
    await loadChunks().catch((error) => showToast(error.message, true));
  }, 250);
});

elements.prevPage.addEventListener("click", async () => {
  state.offset = Math.max(0, state.offset - state.limit);
  await loadChunks().catch((error) => showToast(error.message, true));
});

elements.nextPage.addEventListener("click", async () => {
  state.offset += state.limit;
  await loadChunks().catch((error) => showToast(error.message, true));
});

/* ---------- Unresolved field explorer ---------- */

const unresolved = {
  list: document.getElementById("population-list"),
  meta: document.getElementById("unresolved-meta"),
  empty: document.getElementById("population-empty"),
  detail: document.getElementById("population-detail"),
  title: document.getElementById("population-title"),
  sub: document.getElementById("population-sub"),
  conditionRow: document.getElementById("condition-row"),
  targetField: document.getElementById("target-field"),
  targetValue: document.getElementById("target-value"),
  targetList: document.getElementById("target-listbox"),
  targetToggle: document.getElementById("target-toggle"),
  targetCombo: document.getElementById("target-combo"),
  runPreview: document.getElementById("run-preview"),
  previewResult: document.getElementById("preview-result"),
  discriminators: document.getElementById("discriminator-list"),
  openAttributes: document.getElementById("open-attributes"),
  adviseRule: document.getElementById("advise-rule"),
  adviceBox: document.getElementById("advice-box"),
  refineStatus: document.getElementById("refine-status"),
  dialog: document.getElementById("attr-dialog"),
  dialogNote: document.getElementById("attr-dialog-note"),
  dialogClose: document.getElementById("attr-close"),
  attrSearch: document.getElementById("attr-search"),
  attrList: document.getElementById("attr-list"),
};

const OPERATORS = [
  ["equals", "="],
  ["not_equals", "≠"],
  ["starts_with", "starts"],
  ["contains", "has"],
  ["gte", "≥"],
  ["lte", "≤"],
];

let currentPopulation = null;
let attributeCache = null;
let currentVocabulary = null;

const TARGET_VALUES = {
  drive_type: ["fwd", "rwd", "awd"],
  bodywork_form: [],
  model_family: [],
};

let conditions = [];
let loadedPopulations = false;

function switchView(view) {
  state.view = view;
  if (view === "unresolved") history.replaceState(null, "", "#unresolved");
  document.getElementById("view-chunks").hidden = view !== "chunks";
  document.getElementById("view-unresolved").hidden = view !== "unresolved";
  for (const tab of document.querySelectorAll(".nav-tab")) {
    tab.classList.toggle("active", tab.dataset.view === view);
  }
  if (view === "unresolved" && !loadedPopulations) loadPopulations();
}

for (const tab of document.querySelectorAll(".nav-tab")) {
  tab.addEventListener("click", () => switchView(tab.dataset.view));
}

async function loadPopulations() {
  if (!state.buildId) return;
  unresolved.meta.textContent = "Scanning source evidence…";
  let data;
  try {
    data = await api(
      `/v1/match-review/unresolved?build_id=${state.buildId}`
    );
  } catch (error) {
    unresolved.meta.textContent = error.message;
    return;
  }
  loadedPopulations = true;
  state.populations = data.populations;
  unresolved.list.innerHTML = "";
  const max = data.populations.length ? data.populations[0].row_count : 1;
  for (const population of data.populations) {
    unresolved.list.appendChild(renderPopulationCard(population, max));
  }
  unresolved.meta.textContent = `${formatCount(
    data.populations.length
  )} unresolved values, largest first`;
}

function renderPopulationCard(population, max) {
  const card = document.createElement("li");
  card.className = "chunk-card";
  card.dataset.key = `${population.source_field}=${population.source_value}`;

  const top = document.createElement("div");
  top.className = "chunk-card-top";
  const title = document.createElement("span");
  title.className = "chunk-title";
  title.textContent = `${population.source_field} = ${population.source_value}`;
  top.appendChild(title);
  const target = document.createElement("span");
  target.className = "pill pill-split";
  target.textContent = population.signature_field;
  top.appendChild(target);
  card.appendChild(top);

  const meta = document.createElement("p");
  meta.className = "chunk-meta";
  meta.textContent = `no ${population.signature_field} could be derived`;
  card.appendChild(meta);

  const leverage = document.createElement("div");
  leverage.className = "chunk-leverage";
  const track = document.createElement("div");
  track.className = "leverage-track";
  const fill = document.createElement("span");
  fill.style.width = `${Math.max(3, (population.row_count / max) * 100)}%`;
  track.appendChild(fill);
  leverage.appendChild(track);
  const count = document.createElement("span");
  count.className = "chunk-count";
  count.textContent = `${formatCount(population.row_count)} rows`;
  leverage.appendChild(count);
  card.appendChild(leverage);

  card.addEventListener("click", () => selectPopulation(population));
  return card;
}

async function selectPopulation(population) {
  // `population` may be partial — a deep link carries only field and value —
  // so the authoritative signature field and row count come from the
  // discriminator report rather than the list, which is capped per field and
  // will not contain rarer values.
  history.replaceState(
    null,
    "",
    `#unresolved:${encodeURIComponent(
      `${population.source_field}=${population.source_value}`
    )}`
  );
  currentPopulation = population;
  attributeCache = null;
  unresolved.adviceBox.hidden = true;

  for (const card of unresolved.list.children) {
    card.classList.toggle(
      "selected",
      card.dataset.key === `${population.source_field}=${population.source_value}`
    );
  }
  unresolved.empty.hidden = true;
  unresolved.detail.hidden = false;
  unresolved.title.textContent = `${population.source_field} = ${population.source_value}`;
  unresolved.sub.textContent = "Loading…";
  unresolved.discriminators.innerHTML = '<li class="empty-row">Analysing…</li>';

  conditions = [
    {
      field: population.source_field,
      operator: "equals",
      values: [population.source_value],
      locked: true,
    },
  ];
  renderConditions();

  // One call answers everything the pane needs: counts, facets and whether the
  // population is already coherent — the same call every later edit makes.
  const report = await runRefine();
  if (!report) {
    unresolved.sub.textContent = "";
    unresolved.detail.hidden = true;
    unresolved.empty.hidden = false;
    return;
  }

  currentPopulation = {
    ...population,
    signature_field: report.signature_field,
    row_count: report.would_resolve,
  };

  if (report.would_resolve === 0) {
    unresolved.sub.textContent =
      `No unresolved cars here — ${report.signature_field} is already derived ` +
      `for cars with ${population.source_field} = ${population.source_value}.`;
    unresolved.discriminators.innerHTML =
      '<li class="empty-row">Nothing to resolve for this value.</li>';
    unresolved.refineStatus.hidden = true;
    unresolved.previewResult.hidden = true;
    unresolved.adviceBox.hidden = true;
    unresolved.runPreview.disabled = true;
    return;
  }

  unresolved.sub.textContent =
    `${formatCount(report.would_resolve)} cars carry this value and NorthStar ` +
    `cannot derive ${report.signature_field} from it.`;
  await renderTargets(report.signature_field);
  unresolved.previewResult.hidden = true;
}

async function renderTargets(signatureField) {
  unresolved.targetField.innerHTML = "";
  const option = document.createElement("option");
  option.value = signatureField;
  option.textContent = signatureField;
  unresolved.targetField.appendChild(option);

  unresolved.targetValue.value = "";
  unresolved.targetValue.disabled = false;
  unresolved.runPreview.disabled = false;
  closeCombo();

  let vocabulary;
  try {
    vocabulary = await api(
      "/v1/match-review/target-vocabulary?build_id=" +
        `${state.buildId}&target_field=${encodeURIComponent(signatureField)}`
    );
  } catch (error) {
    showToast(error.message, true);
    return;
  }

  currentVocabulary = vocabulary;
  unresolved.targetValue.placeholder = vocabulary.closed
    ? `choose one of ${vocabulary.values.length}`
    : "type a value or choose";
  unresolved.targetValue.title = vocabulary.closed
    ? "Fixed vocabulary — the value must be one of the listed canonical values."
    : "Open vocabulary — any value is accepted; the list shows values already used in this build.";
}

/* ---------- target value combobox ---------- */

let comboIndex = -1;
let comboItems = [];

function closeCombo() {
  unresolved.targetList.hidden = true;
  unresolved.targetValue.setAttribute("aria-expanded", "false");
  comboIndex = -1;
}

function openCombo() {
  if (!currentVocabulary) return;
  renderCombo();
  unresolved.targetList.hidden = false;
  unresolved.targetValue.setAttribute("aria-expanded", "true");
}

function renderCombo() {
  const typed = unresolved.targetValue.value.trim();
  const needle = typed.toLowerCase();
  const matches = currentVocabulary.values.filter((entry) =>
    entry.value.toLowerCase().includes(needle)
  );
  unresolved.targetList.innerHTML = "";
  comboItems = [];

  // Free text is an explicit, visible choice rather than a hidden affordance.
  const exact = currentVocabulary.values.some((entry) => entry.value === typed);
  if (!currentVocabulary.closed && typed && !exact) {
    comboItems.push({ value: typed, isNew: true });
  }
  for (const entry of matches) {
    comboItems.push({ value: entry.value, count: entry.count });
  }

  if (!comboItems.length) {
    const empty = document.createElement("li");
    empty.className = "combo-empty";
    empty.textContent = currentVocabulary.closed
      ? "No canonical value matches."
      : "Type a value to use it.";
    unresolved.targetList.appendChild(empty);
    return;
  }

  comboItems.forEach((item, index) => {
    const option = document.createElement("li");
    option.className = "combo-option";
    option.setAttribute("role", "option");
    if (item.isNew) option.classList.add("combo-new");
    if (index === comboIndex) option.classList.add("active");

    const label = document.createElement("span");
    label.textContent = item.isNew ? `Use "${item.value}"` : item.value;
    option.appendChild(label);

    const meta = document.createElement("span");
    meta.className = "combo-count";
    meta.textContent = item.isNew
      ? "new value"
      : item.count
        ? `${formatCount(item.count)} cars`
        : "canonical";
    option.appendChild(meta);

    option.addEventListener("mousedown", (event) => {
      event.preventDefault();
      chooseCombo(index);
    });
    unresolved.targetList.appendChild(option);
  });
}

function chooseCombo(index) {
  const item = comboItems[index];
  if (!item) return;
  unresolved.targetValue.value = item.value;
  closeCombo();
  markTargetValidity();
}

function markTargetValidity() {
  const problem = validateTargetValue();
  unresolved.targetValue.classList.toggle(
    "invalid",
    Boolean(problem) && unresolved.targetValue.value.trim() !== ""
  );
}

unresolved.targetValue.addEventListener("focus", openCombo);
unresolved.targetToggle.addEventListener("click", () => {
  if (unresolved.targetList.hidden) {
    unresolved.targetValue.focus();
    openCombo();
  } else {
    closeCombo();
  }
});
unresolved.targetValue.addEventListener("input", () => {
  comboIndex = -1;
  openCombo();
  markTargetValidity();
});
unresolved.targetValue.addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    if (unresolved.targetList.hidden) openCombo();
    const step = event.key === "ArrowDown" ? 1 : -1;
    comboIndex = (comboIndex + step + comboItems.length) % comboItems.length;
    renderCombo();
  } else if (event.key === "Enter") {
    if (!unresolved.targetList.hidden && comboIndex >= 0) {
      event.preventDefault();
      chooseCombo(comboIndex);
    } else {
      closeCombo();
    }
  } else if (event.key === "Escape") {
    closeCombo();
  }
});
document.addEventListener("click", (event) => {
  if (!unresolved.targetCombo.contains(event.target)) closeCombo();
});

function validateTargetValue() {
  const value = unresolved.targetValue.value.trim();
  if (!value) return "Enter a value for the rule to assign.";
  if (
    currentVocabulary &&
    currentVocabulary.closed &&
    !currentVocabulary.values.some((entry) => entry.value === value)
  ) {
    return `"${value}" is not a canonical ${currentVocabulary.target_field}.`;
  }
  return null;
}

function renderConditions() {
  unresolved.conditionRow.innerHTML = "";
  conditions.forEach((condition, index) => {
    if (index > 0) {
      const and = document.createElement("span");
      and.className = "rule-and";
      and.textContent = "AND";
      unresolved.conditionRow.appendChild(and);
    }
    const chip = document.createElement("span");
    chip.className = "condition-chip";
    if (condition.locked) chip.classList.add("locked");

    const name = document.createElement("b");
    name.className = "chip-field";
    name.textContent = condition.field;
    chip.appendChild(name);

    if (condition.locked) {
      const op = document.createElement("span");
      op.className = "chip-op";
      op.textContent = "=";
      chip.appendChild(op);
    } else {
      const op = document.createElement("select");
      op.className = "chip-op-select";
      op.setAttribute("aria-label", `Operator for ${condition.field}`);
      for (const [value, label] of OPERATORS) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        if (condition.operator === value) option.selected = true;
        op.appendChild(option);
      }
      op.addEventListener("change", () => {
        condition.operator = op.value;
        if (["gte", "lte"].includes(op.value)) {
          condition.values = condition.values.slice(0, 1);
        }
        renderConditions();
        unresolved.previewResult.hidden = true;
        scheduleRefine();
      });
      chip.appendChild(op);
    }

    condition.values.forEach((value, valueIndex) => {
      if (valueIndex > 0) {
        const or = document.createElement("i");
        or.className = "chip-or";
        or.textContent = "or";
        chip.appendChild(or);
      }
      const term = document.createElement("span");
      term.className = "chip-value";
      term.textContent = value;
      chip.appendChild(term);
    });

    if (!condition.locked) {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "condition-remove";
      remove.textContent = "×";
      remove.setAttribute("aria-label", `Remove ${condition.field} condition`);
      remove.addEventListener("click", () => {
        conditions = conditions.filter((item) => item !== condition);
        renderConditions();
        unresolved.previewResult.hidden = true;
        scheduleRefine();
      });
      chip.appendChild(remove);
    }
    unresolved.conditionRow.appendChild(chip);
  });
}

function addTerm(field, value, { operator = "equals" } = {}) {
  const existing = conditions.find(
    (item) => item.field === field && !item.locked
  );
  if (existing) {
    // Same field again means OR, not a replacement.
    if (!existing.values.includes(value)) existing.values.push(value);
    if (["gte", "lte"].includes(existing.operator)) {
      existing.values = [value];
    }
  } else {
    conditions.push({ field, operator, values: [value] });
  }
  renderConditions();
  unresolved.previewResult.hidden = true;
  scheduleRefine();
}

function renderDiscriminators(report) {
  unresolved.discriminators.innerHTML = "";
  for (const field of report.fields) {
    const item = document.createElement("li");
    item.className = "spread-item";
    if (!field.usable) item.classList.add("unusable");

    const head = document.createElement("div");
    head.className = "spread-head";
    const name = document.createElement("strong");
    name.textContent = field.field;
    head.appendChild(name);
    const stats = document.createElement("span");
    stats.className = "spread-count";
    stats.textContent = field.usable
      ? `score ${field.score.toFixed(2)} · ${formatCount(
          field.distinct_count
        )} values`
      : `${formatCount(field.distinct_count)} values · near-constant, no split`;
    head.appendChild(stats);
    item.appendChild(head);

    const values = document.createElement("div");
    values.className = "spread-values";
    for (const entry of field.top_values) {
      const chip = valueChip(entry, { clickable: true });
      chip.addEventListener("click", () => addTerm(field.field, entry.value));
      values.appendChild(chip);
    }
    item.appendChild(values);
    unresolved.discriminators.appendChild(item);
  }
}

unresolved.runPreview.addEventListener("click", async () => {
  const problem = validateTargetValue();
  if (problem) {
    showToast(problem, true);
    return;
  }
  unresolved.runPreview.disabled = true;
  try {
    const preview = await api("/v1/match-review/rule-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        build_id: state.buildId,
        conditions: conditions.map(({ field, operator, values }) => ({
          field,
          operator: operator || "equals",
          values,
        })),
        target_field: unresolved.targetField.value,
        target_value: unresolved.targetValue.value.trim(),
      }),
    });
    renderPreview(preview);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    unresolved.runPreview.disabled = false;
  }
});

function renderPreview(preview) {
  unresolved.previewResult.hidden = false;
  unresolved.previewResult.innerHTML = "";

  const headline = document.createElement("p");
  headline.className = "preview-headline";
  headline.textContent =
    `This rule would resolve ${formatCount(preview.would_resolve)} cars` +
    ` as ${preview.target_field} = ${preview.target_value}.`;
  unresolved.previewResult.appendChild(headline);

  const detail = document.createElement("p");
  detail.className = "preview-detail";
  detail.textContent =
    `${formatCount(preview.matched_rows)} cars match the conditions; ` +
    `${formatCount(preview.already_resolved)} already have a value and ` +
    "would not be overwritten by this rule.";
  unresolved.previewResult.appendChild(detail);

  if (preview.sample_plates.length) {
    const samples = document.createElement("p");
    samples.className = "preview-detail";
    samples.textContent = `Spot-check: ${preview.sample_plates.join(", ")}`;
    unresolved.previewResult.appendChild(samples);
  }

  const note = document.createElement("p");
  note.className = "preview-note";
  note.textContent =
    "Preview only — authoring this as an immutable reviewed rule comes next.";
  unresolved.previewResult.appendChild(note);
}



/* ---------- live refinement ---------- */

let refineTimer = null;
let refineToken = 0;

function scheduleRefine() {
  clearTimeout(refineTimer);
  refineTimer = setTimeout(runRefine, 250);
}

async function runRefine() {
  if (!currentPopulation || !conditions.length) return;
  const token = ++refineToken;
  unresolved.refineStatus.hidden = false;
  unresolved.refineStatus.classList.add("loading");
  let result;
  try {
    result = await api("/v1/match-review/unresolved/refine", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        build_id: state.buildId,
        source_field: currentPopulation.source_field,
        source_value: currentPopulation.source_value,
        conditions: conditions.map(({ field, operator, values }) => ({
          field,
          operator: operator || "equals",
          values,
        })),
      }),
    });
  } catch (error) {
    unresolved.refineStatus.classList.remove("loading");
    showToast(error.message, true);
    return;
  }
  // A slower earlier request must not overwrite a newer result.
  if (token !== refineToken) return;
  unresolved.refineStatus.classList.remove("loading");
  renderRefineStatus(result);
  renderDiscriminators(result);
  return result;
}

function renderRefineStatus(result) {
  unresolved.refineStatus.innerHTML = "";
  unresolved.refineStatus.classList.toggle("coherent", result.homogeneous);

  const headline = document.createElement("p");
  headline.className = "refine-headline";
  headline.textContent = `${formatCount(result.would_resolve)} cars would be resolved`;
  if (result.already_resolved) {
    const extra = document.createElement("span");
    extra.className = "refine-extra";
    extra.textContent = ` · ${formatCount(result.already_resolved)} already have a value`;
    headline.appendChild(extra);
  }
  unresolved.refineStatus.appendChild(headline);

  const verdict = document.createElement("p");
  verdict.className = "refine-verdict";
  verdict.textContent = result.homogeneous
    ? "One coherent block — nothing identity-bearing still varies, so a single value is safe."
    : `Still mixed on ${result.varying_identity_fields.join(", ")} — narrow further before assigning a value.`;
  unresolved.refineStatus.appendChild(verdict);

  if (result.trail.length > 1) {
    const trail = document.createElement("p");
    trail.className = "refine-trail";
    trail.textContent = result.trail
      .map((step) => `${step.label} → ${formatCount(step.matched_rows)}`)
      .join("   ·   ");
    unresolved.refineStatus.appendChild(trail);
  }
}

/* ---------- All-attributes dialog ---------- */

function populationQuery() {
  return (
    `build_id=${state.buildId}` +
    `&source_field=${encodeURIComponent(currentPopulation.source_field)}` +
    `&source_value=${encodeURIComponent(currentPopulation.source_value)}`
  );
}

unresolved.openAttributes.addEventListener("click", async () => {
  if (!currentPopulation) return;
  unresolved.dialog.showModal();
  if (!attributeCache) {
    unresolved.attrList.innerHTML = '<p class="empty-row">Scanning…</p>';
    try {
      attributeCache = await api(
        `/v1/match-review/unresolved/attributes?${populationQuery()}`
      );
    } catch (error) {
      unresolved.attrList.innerHTML = "";
      showToast(error.message, true);
      return;
    }
  }
  unresolved.dialogNote.textContent = attributeCache.sampled
    ? `Every attribute present on these cars. Counts from a ${formatCount(
        attributeCache.scanned_members
      )}-row sample.`
    : `Every attribute present on all ${formatCount(
        attributeCache.scanned_members
      )} cars in this population.`;
  renderAttributes();
});

unresolved.dialogClose.addEventListener("click", () => unresolved.dialog.close());
unresolved.attrSearch.addEventListener("input", renderAttributes);

function renderAttributes() {
  if (!attributeCache) return;
  const filter = unresolved.attrSearch.value.trim().toLowerCase();
  unresolved.attrList.innerHTML = "";
  const matches = attributeCache.attributes.filter((attribute) => {
    if (!filter) return true;
    if (attribute.field.toLowerCase().includes(filter)) return true;
    return attribute.top_values.some((entry) =>
      String(entry.value).toLowerCase().includes(filter)
    );
  });
  if (!matches.length) {
    unresolved.attrList.innerHTML =
      '<p class="empty-row">Nothing matches that filter.</p>';
    return;
  }
  for (const attribute of matches) {
    const row = document.createElement("div");
    row.className = "attr-row";

    const head = document.createElement("div");
    head.className = "attr-head";
    const name = document.createElement("strong");
    name.textContent = attribute.field;
    head.appendChild(name);
    const meta = document.createElement("span");
    meta.textContent = `${formatCount(attribute.distinct_count)} values · present on ${formatCount(attribute.present_count)}`;
    head.appendChild(meta);
    row.appendChild(head);

    const values = document.createElement("div");
    values.className = "spread-values";
    for (const entry of attribute.top_values) {
      const chip = valueChip(entry, { clickable: true });
      chip.addEventListener("click", () => {
        addTerm(attribute.field, entry.value);
        unresolved.dialog.close();
      });
      values.appendChild(chip);
    }
    row.appendChild(values);
    unresolved.attrList.appendChild(row);
  }
}

/* ---------- Rule advisor ---------- */

unresolved.adviseRule.addEventListener("click", async () => {
  if (!currentPopulation) return;
  unresolved.adviseRule.disabled = true;
  try {
    const advice = await api("/v1/match-review/unresolved/advise", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        build_id: state.buildId,
        source_field: currentPopulation.source_field,
        source_value: currentPopulation.source_value,
      }),
    });
    applyAdvice(advice);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    unresolved.adviseRule.disabled = false;
  }
});

function applyAdvice(advice) {
  conditions = advice.conditions.map((condition, index) => ({
    field: condition.field,
    operator: condition.operator,
    values: condition.values && condition.values.length
      ? condition.values
      : [condition.value],
    locked: index === 0,
  }));
  renderConditions();
  if (advice.target_value) {
    unresolved.targetValue.value = advice.target_value;
  }

  unresolved.adviceBox.hidden = false;
  unresolved.adviceBox.innerHTML = "";
  unresolved.adviceBox.className = advice.confident
    ? "advice-box confident"
    : "advice-box";

  const head = document.createElement("p");
  head.className = "advice-head";
  // Say plainly which advisor ran: the button is labelled "AI", but without a
  // configured key the deterministic statistical advisor answers instead.
  const isLlm = advice.advisor.startsWith("llm:");
  const source = isLlm
    ? advice.advisor
    : advice.advisor.includes("llm unavailable")
      ? "statistical advisor (AI unavailable, fell back)"
      : "statistical advisor (no AI key configured)";
  head.textContent = advice.confident
    ? `Suggested by ${source} — evidence supports a value`
    : `Suggested by ${source} — needs evidence for the value`;
  unresolved.adviceBox.appendChild(head);

  const reasoning = document.createElement("p");
  reasoning.className = "advice-reasoning";
  reasoning.textContent = advice.reasoning;
  unresolved.adviceBox.appendChild(reasoning);
}

(async function start() {
  try {
    const hasBuilds = await loadBuilds();
    if (hasBuilds) await loadChunks();
    const linked = decodeURIComponent(window.location.hash.slice(1));
    if (linked.startsWith("unresolved")) {
      switchView("unresolved");
      const target = linked.slice("unresolved".length).replace(/^:/, "");
      const split = target.indexOf("=");
      if (split > 0) {
        await loadPopulations();
        // Select straight from the URL: the listing is capped per field, so a
        // rarer value would not be found by searching it.
        await selectPopulation({
          source_field: target.slice(0, split),
          source_value: target.slice(split + 1),
        });
      }
    } else if (linked) {
      await selectChunk(linked);
    }
  } catch (error) {
    showToast(error.message, true);
  }
})();
