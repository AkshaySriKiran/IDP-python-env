/* =============================================================
 * OmniParse IDP — UI (API-first extract)
 * PDF/TXT/images go to the Python FastAPI backend.
 * ============================================================= */

// Configure PDF.js Worker safely
if (typeof pdfjsLib !== 'undefined') {
  pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.worker.min.js';
}

// Preloaded Sichuan Honghua EXAMPLE_EQUIPMENT_DO_NOT_COPY High-Fidelity Dataset
let maintenanceRegistry = [];
let sparePartsRegistry = [];
let troubleshootingRegistry = [];
let activeRegistryTab = "maintenance"; // "maintenance", "spare_parts", "troubleshooting"

// Document storage for contextual searches
let loadedPages = []; 

// Global active filters
let currentTabFilter = "all"; // maintenance intervals
let currentSpareFilter = "all"; // spare part types
let currentStatusFilter = "all"; // review status: all, Pending Review, Approved, Rejected
let currentSearchQuery = "";
let currentConfidenceFilter = "all";
let highlightRecordIds = [];
let selectedRegistryRowId = null;

// Review & Approval Lifecycle + Document Metadata State
let activeDocumentMetadata = null;
let activeDocumentStatus = "Pending Review";
let activeApprovedBy = null;
let activeApprovedAt = null;
let pendingRejectInfo = null;

// Globals to store actively filtered data for Excel export
let filteredMaintenance = [];
let filteredSpareParts = [];
let lastExtractMeta = null;

// Dual-Storage Audit Trail & Diff Comparison State
let baselineExtraction = null;
let isDiffViewActive = false;
let currentDiffModalTab = "spare_parts";

function formatColumnLabel(col) {
  const map = {
    equipment_title: "Equipment Title",
    subsystem_location: "Sub-system / Location",
    subsystem_component: "Sub-system / Component",
    item_no: "Item No.",
    part_name: "Part Name / Description",
    part_number_code: "Mfr Part Number / Code",
    drawing_model_no: "Drawing / Model No",
    oem_standard_body: "OEM / Standard Body",
    part_categorization: "Part Categorization",
    quantity: "Quantity",
    recommended_stock_qty: "Stock Qty",
    warranty_period: "Warranty Period",
    frequency_of_use: "Frequency of Use",
    maintenance_routine: "Maintenance Routine",
    checks_instructions: "Required Maintenance Checks / Instructions",
    maintenance_work_description: "Maintenance Work Description",
    parts_renewed: "Parts Renewed",
    attended_by: "Attended By",
    remarks: "Remarks",
    problem: "Problem",
    root_cause_solution: "Root Cause / Solution",
    page: "Page",
    title: "Document Title",
    oem_manufacturer: "OEM Manufacturer",
    equipment_model: "Equipment Model",
    equipment_type: "Equipment Type",
    document_version: "Document Version",
    publication_date: "Publication Date",
  };
  return map[col] || col.replace(/_/g, " ").replace(/\b\w/g, l => l.toUpperCase());
}

const CANONICAL_DIFF_COLUMNS = {
  spare_parts: [
    "equipment_title", "subsystem_location", "item_no", "part_name",
    "part_number_code", "drawing_model_no", "oem_standard_body",
    "part_categorization", "quantity", "recommended_stock_qty",
    "warranty_period", "frequency_of_use"
  ],
  maintenance: [
    "equipment_title", "subsystem_component", "maintenance_routine",
    "checks_instructions", "date", "maintenance_work_description",
    "parts_renewed", "attended_by", "remarks"
  ],
  troubleshooting: [
    "equipment_title", "subsystem_component", "problem", "root_cause_solution"
  ]
};

function isEquivalentEmpty(v) {
  if (v === undefined || v === null) return true;
  const s = String(v).trim().toUpperCase();
  return s === "" || s === "NA" || s === "N/A" || s === "-" || s === "NONE" || s === "NULL" || s === "UNKNOWN";
}

function normalizeDiffVal(v) {
  if (isEquivalentEmpty(v)) return "";
  let s = String(v).trim();
  if (/^-?\d+(\.0+)?$/.test(s)) {
    s = String(parseInt(s, 10));
  }
  return s;
}

function getBaselineRow(regType, row, rowIndex) {
  if (!baselineExtraction) return null;
  const list = baselineExtraction[regType] || [];
  if (!Array.isArray(list) || list.length === 0) return null;

  // 1. Match by numeric row.id
  if (row && row.id !== undefined && row.id !== null) {
    const rowIdNum = parseInt(String(row.id).replace(/\D/g, ""), 10);
    if (!isNaN(rowIdNum) && rowIdNum > 0) {
      const byId = list.find(b => {
        const bId = parseInt(String(b.id).replace(/\D/g, ""), 10);
        return bId === rowIdNum;
      });
      if (byId) return byId;
    }
  }
  // 2. Match by pdf_order
  if (row && row.pdf_order !== undefined && row.pdf_order !== null) {
    const orderNum = parseInt(String(row.pdf_order).replace(/\D/g, ""), 10);
    if (!isNaN(orderNum) && orderNum > 0) {
      const byOrder = list.find(b => {
        const bOrder = parseInt(String(b.pdf_order).replace(/\D/g, ""), 10);
        return bOrder === orderNum;
      });
      if (byOrder) return byOrder;
    }
  }
  // 3. Fallback to index if within bounds
  const idx = rowIndex !== undefined ? rowIndex : (row && Number(row.id) > 0 ? Number(row.id) - 1 : -1);
  if (idx >= 0 && idx < list.length) {
    return list[idx];
  }
  return null;
}

function renderCellWithDiff(regType, row, col, innerHtml, extraStyle = "", extraClass = "") {
  let isModified = false;
  let originalVal = null;
  if (isDiffViewActive && baselineExtraction) {
    const baseRow = getBaselineRow(regType, row);
    if (baseRow) {
      const cVal = normalizeDiffVal(row[col]);
      const bVal = normalizeDiffVal(baseRow[col]);
      if (cVal !== bVal) {
        isModified = true;
        originalVal = isEquivalentEmpty(baseRow[col]) ? "NA" : String(baseRow[col]).trim();
      }
    }
  }

  let cellClass = `editable ${extraClass}`;
  if (isModified) cellClass += " cell-diff-modified";
  const diffBadge = isModified ? `<span class="diff-badge-original" title="Original AI baseline value">AI: ${escapeHTML(originalVal)}</span>` : "";
  const styleAttr = extraStyle ? ` style="${extraStyle}"` : "";

  return `<td class="${cellClass.trim()}" data-col="${col}"${styleAttr}>${innerHtml}${diffBadge}</td>`;
}

function renderIdCellWithDiff(regType, row) {
  let isNew = false;
  if (isDiffViewActive && baselineExtraction) {
    const baseRow = getBaselineRow(regType, row);
    if (!baseRow) {
      isNew = true;
    }
  }
  const badge = isNew ? `<span class="diff-badge-custom-row">+ Added</span>` : "";
  return `<td class="page-cell" style="font-weight: 600;">#${row.id}${badge}</td>`;
}

function getUserRole() {
  try {
    if (window.authState && window.authState.user && window.authState.user.role) {
      return String(window.authState.user.role).toLowerCase();
    }
    const raw = sessionStorage.getItem("omniparse_auth_user") || localStorage.getItem("omniparse_auth_user") || localStorage.getItem("idp_user_profile");
    if (raw) {
      const u = JSON.parse(raw);
      if (u && u.role) return String(u.role).toLowerCase();
    }
  } catch (e) {}
  return "editor";
}

function getCurrentUserEmail() {
  try {
    if (window.authState && window.authState.user && window.authState.user.email) {
      return String(window.authState.user.email).toLowerCase();
    }
    const raw = sessionStorage.getItem("omniparse_auth_user") || localStorage.getItem("omniparse_auth_user") || localStorage.getItem("idp_user_profile");
    if (raw) {
      const u = JSON.parse(raw);
      if (u && u.email) return String(u.email).toLowerCase();
    }
  } catch (e) {}
  return "";
}

function canApproveOrSignOff() {
  const role = getUserRole();
  if (role === "admin") return true;
  if (role !== "approver") return false;
  const userEmail = (getCurrentUserEmail() || "").trim().toLowerCase();
  const docApprover = String(
    (lastExtractMeta && lastExtractMeta.assigned_approver) ||
    (activeDocumentMetadata && activeDocumentMetadata.assigned_approver) ||
    ""
  ).trim().toLowerCase();
  if (!userEmail || !docApprover) return false;
  return docApprover === userEmail;
}

function canEditRecords() {
  const role = getUserRole();
  return role !== "viewer";
}

function formatStatusCell(row) {
  let status = row.status;
  if (!status || status === "Pending Review") {
    if (activeDocumentStatus === "Approved") {
      status = "Approved";
    } else {
      status = "Pending Review";
    }
  }
  let pillClass = "status-pending";
  if (status === "Approved") pillClass = "status-approved";
  else if (status === "Rejected") pillClass = "status-rejected";
  else if (status === "Draft") pillClass = "status-draft";

  let tooltip = `Status: ${escapeHTML(status)}`;
  const reviewer = row.reviewed_by || (status === "Approved" ? (activeApprovedBy || "Authorized Reviewer") : null);
  if (reviewer) tooltip += ` by ${escapeHTML(reviewer)}`;
  if (row.rejection_reason) tooltip += ` - Reason: ${escapeHTML(row.rejection_reason)}`;
  return `<span class="status-pill ${pillClass}" title="${tooltip}">${escapeHTML(status)}</span>`;
}

function formatRowActionsCell(row, registryType) {
  let status = row.status;
  if (!status || status === "Pending Review") {
    if (activeDocumentStatus === "Approved") {
      status = "Approved";
    } else {
      status = "Pending Review";
    }
  }
  const isApproved = status === "Approved";
  const isRejected = status === "Rejected";
  const allowReview = canApproveOrSignOff();
  const allowDelete = canEditRecords();

  let html = `<div style="display: inline-flex; align-items: center; justify-content: center; gap: 4px;">`;
  if (allowReview) {
    const approverName = row.reviewed_by || (isApproved ? (activeApprovedBy || "Authorized Reviewer") : "");
    html += `
      <button type="button" class="row-btn btn-approve" data-action="approve" data-reg="${registryType}" data-id="${row.id}" title="${isApproved ? 'Approved by ' + escapeHTML(approverName) : 'Approve record'}" ${isApproved ? 'style="color: var(--accent-green);"' : ''}>
        <i data-lucide="check"></i>
      </button>
      <button type="button" class="row-btn btn-reject" data-action="reject" data-reg="${registryType}" data-id="${row.id}" title="${isRejected ? 'Rejected: ' + escapeHTML(row.rejection_reason || '') : 'Reject record'}" ${isRejected ? 'style="color: var(--accent-red);"' : ''}>
        <i data-lucide="x"></i>
      </button>
    `;
  }
  if (allowDelete) {
    html += `<button type="button" class="row-btn btn-delete" data-action="delete" data-reg="${registryType}" data-id="${row.id}" title="Delete record"><i data-lucide="trash-2"></i></button>`;
  }
  html += `</div>`;
  return html;
}


function formatConfidencePercent(row) {
  if (!row || row.confidence == null || row.confidence === "") return "—";
  const n = Number(row.confidence);
  if (Number.isNaN(n)) return "—";
  return `${Math.round(n * 100)}%`;
}

function formatConfidenceCell(row) {
  const pct = formatConfidencePercent(row);
  if (pct === "—") return "—";
  return (
    `<button type="button" class="confidence-btn" data-row-id="${row.id}" ` +
    `title="View why this score is ${pct}">${pct}</button>`
  );
}

function isLowConfidenceRow(row) {
  if (!row || row.confidence == null || row.confidence === "") return false;
  const n = Number(row.confidence);
  return !Number.isNaN(n) && n < 0.7;
}

function _fieldFilledLocal(val) {
  const s = String(val == null ? "" : val).trim();
  if (!s) return false;
  return !["NA", "N/A", "NONE", "-", "NULL", "UNDEFINED"].includes(s.toUpperCase());
}

const FIELD_LABELS = {
  equipment_title: "Equipment title",
  subsystem_component: "Sub-system / component",
  subsystem_location: "Sub-system / location",
  maintenance_routine: "Maintenance routine",
  checks_instructions: "Checks & instructions",
  maintenance_work_description: "Work description",
  attended_by: "Attended by",
  date: "Date",
  parts_renewed: "Parts renewed",
  remarks: "Remarks",
  part_name: "Part name",
  part_number_code: "Part number",
  item_no: "Item no",
  drawing_model_no: "Drawing / model no",
  problem: "Problem",
  root_cause_solution: "Root cause / solution",
  part_number_or_drawing: "Part number or drawing",
};

function scoredFieldsForTab(registryTab) {
  const isLogbook = activeEquipmentCategory === "Logbook";
  if (registryTab === "spare_parts") {
    return ["equipment_title", "part_name", "part_number_code", "item_no", "drawing_model_no"];
  }
  if (registryTab === "troubleshooting") {
    return ["equipment_title", "subsystem_component", "problem", "root_cause_solution"];
  }
  if (isLogbook) {
    return ["maintenance_work_description", "attended_by", "date", "parts_renewed", "remarks"];
  }
  return ["equipment_title", "subsystem_component", "maintenance_routine", "checks_instructions"];
}

function listNaFields(row, registryTab) {
  return scoredFieldsForTab(registryTab).filter((f) => !_fieldFilledLocal(row[f]));
}

function fieldLabel(key) {
  return FIELD_LABELS[key] || String(key || "").replace(/_/g, " ");
}

/** Plain-language score reasons for end users (column + popup tags). */
function buildClientQualityReasons(row, registryTab) {
  const q = row && row.quality ? row.quality : {};
  const reasons = [];
  const grounding = q.grounding_score != null ? Number(q.grounding_score) : null;
  const naFields = listNaFields(row, registryTab);
  const pageNum = row && row.page != null && String(row.page).toUpperCase() !== "NA"
    ? Number(row.page)
    : null;
  const page = (loadedPages || []).find((p) => Number(p.pageNum) === pageNum);
  const comparable = getComparablePdfPageText(page && page.text ? page.text : "");
  const apiGrounded = q.grounding_available === true;
  const groundingSkipped =
    !apiGrounded &&
    ((q.grounding_available === false && comparable.visionOnly) ||
      (q.grounding_available == null && comparable.visionOnly));

  if (naFields.length === 0) {
    reasons.push("All key fields filled");
  } else if (naFields.length === 1) {
    reasons.push(`Blank: ${fieldLabel(naFields[0])}`);
  } else if (naFields.length === 2) {
    reasons.push(`Blank: ${fieldLabel(naFields[0])}, ${fieldLabel(naFields[1])}`);
  } else {
    reasons.push(`Many blank fields (${naFields.length}) — lowers score`);
    naFields.slice(0, 3).forEach((f) => reasons.push(`Blank: ${fieldLabel(f)}`));
  }

  if (groundingSkipped) {
    reasons.push("OCR page — confirm in PDF");
  } else if (grounding != null && !Number.isNaN(grounding)) {
    if (grounding >= 0.7) reasons.push("Matches the page");
    else if (grounding >= 0.4) reasons.push("May not match the page");
    else reasons.push("Does not match the page well");
  } else {
    reasons.push("Could not check against the page");
  }
  return reasons;
}

/** Short cell text for the Reasons column. */
function formatScoreReasonsCell(row) {
  const reasons = buildClientQualityReasons(row, activeRegistryTab);
  const weak = reasons.filter((r) => {
    const t = String(r).toLowerCase();
    return !(
      t.includes("all key fields filled") ||
      t === "matches the page" ||
      t.includes("ocr page")
    );
  });
  const conf = row && row.confidence != null ? Number(row.confidence) : null;
  if (conf != null && !Number.isNaN(conf) && conf >= 0.99 && weak.length === 0) {
    return `<span class="score-reasons-ok">Looks good</span>`;
  }
  if (!weak.length) {
    return `<span class="score-reasons-ok">Looks good</span>`;
  }
  const summary = weak.slice(0, 2).join(" · ");
  const more = weak.length > 2 ? ` (+${weak.length - 2} more)` : "";
  return (
    `<button type="button" class="score-reasons-btn confidence-btn" data-row-id="${row.id}" ` +
    `title="View full reasons">${escapeHTML(summary)}${escapeHTML(more)}</button>`
  );
}

function reasonTagClass(label) {
  const t = String(label || "").toLowerCase();
  if (t.includes("all key fields") || t === "matches the page" || t.includes("looks good")) {
    return "reason-tag reason-ok";
  }
  if (
    t.includes("blank:") ||
    t.includes("many blank") ||
    t.includes("does not match") ||
    t.includes("lowers score")
  ) {
    return "reason-tag reason-bad";
  }
  return "reason-tag reason-warn";
}

function findRegistryRowById(id) {
  const n = Number(id);
  if (activeRegistryTab === "spare_parts") return sparePartsRegistry.find((r) => r.id === n);
  if (activeRegistryTab === "troubleshooting") return troubleshootingRegistry.find((r) => r.id === n);
  return maintenanceRegistry.find((r) => r.id === n);
}

/** Missing AI tokens saved at extract time (quality.reasons) — used when PDF page text is not in the browser. */
function missingWordsFromStoredQuality(row) {
  const reasons = (row && row.quality && Array.isArray(row.quality.reasons)) ? row.quality.reasons : [];
  for (let i = 0; i < reasons.length; i++) {
    const m = String(reasons[i] || "").match(/AI words missing from page:\s*(.+)/i);
    if (!m) continue;
    return m[1].split(/[,;]\s*/).map((w) => w.trim()).filter(Boolean);
  }
  return [];
}

/** Text from the AI row that we compare to the PDF page (same idea as the API). */
function getAiExtractTextForGrounding(row, registryTab) {
  if (!row) return "";
  if (registryTab === "spare_parts") {
    return [row.part_name, row.part_number_code, row.drawing_model_no].filter(Boolean).join(" ");
  }
  if (registryTab === "troubleshooting") {
    return [row.problem, row.root_cause_solution].filter(Boolean).join(" ");
  }
  if (activeEquipmentCategory === "Logbook") {
    return String(row.maintenance_work_description || "");
  }
  return String(row.checks_instructions || "");
}

function extractContentTokensLocal(text) {
  const stop = new Set([
    "the", "and", "for", "with", "from", "into", "that", "this", "then", "than",
    "are", "was", "were", "have", "has", "had", "will", "shall", "should", "can",
    "must", "not", "all", "any", "page", "unit", "system", "check", "inspect", "na",
  ]);
  const tokens = String(text || "")
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter(Boolean);
  const out = [];
  const seen = new Set();
  tokens.forEach((t) => {
    if (stop.has(t)) return;
    if (t.length < 4 && !/^\d+$/.test(t)) return;
    if (seen.has(t)) return;
    seen.add(t);
    out.push(t);
  });
  return out;
}

/** Stored page text is often just a vision marker — not real PDF/OCR transcription. */
function getComparablePdfPageText(pageText) {
  const raw = String(pageText || "");
  const hasVisionMarker = /OCR\s*VISION\s*EXTRACTION/i.test(raw);
  const cleaned = raw
    .replace(/OCR\s*VISION\s*EXTRACTION/gi, " ")
    .replace(/\s+/g, " ")
    .trim();
  // Only treat as unusable when we still have the placeholder and almost no real text.
  // After a successful OCR extract, pages[] holds the vision transcription.
  if (!cleaned) {
    return { text: "", visionOnly: hasVisionMarker };
  }
  if (hasVisionMarker && cleaned.length < 40) {
    return { text: "", visionOnly: true };
  }
  return { text: cleaned, visionOnly: false };
}

/**
 * Compare AI extract vs PDF page text and pinpoint where the match is weak.
 * (Not a .md file — we compare the AI table fields to the text taken from that PDF page.)
 */
function analyzePageMatch(row, registryTab) {
  const pageNum = row && row.page != null && String(row.page).toUpperCase() !== "NA"
    ? Number(row.page)
    : null;
  const page = (loadedPages || []).find((p) => Number(p.pageNum) === pageNum);
  const aiText = getAiExtractTextForGrounding(row, registryTab).trim();
  const q = row && row.quality ? row.quality : {};
  const apiGrounded = q.grounding_available === true;
  const comparable = getComparablePdfPageText(page && page.text ? page.text : "");
  const pdfText = comparable.text;
  const pdfLower = pdfText.toLowerCase();
  const pdfCompact = pdfLower.replace(/[^a-z0-9]/g, "");
  const tokens = extractContentTokensLocal(aiText);
  const matched = [];
  const missing = [];
  const storedMissing = missingWordsFromStoredQuality(row);

  // Server already letter-matched during extract — trust that even if browser page cache is stale.
  if (!pdfText && apiGrounded) {
    return {
      pageNum: Number.isNaN(pageNum) ? null : pageNum,
      hasPageText: false,
      visionOnly: false,
      groundingUnavailable: false,
      apiGroundedOnly: true,
      aiText: aiText.slice(0, 400),
      matched: [],
      missing: storedMissing,
      snippet: "",
      snippetAnchor: "",
      matchRatio: q.grounding_score != null ? Number(q.grounding_score) : null,
    };
  }

  // No searchable page text (placeholder only / failed OCR transcription).
  if (comparable.visionOnly || (!pdfText && !apiGrounded && !page)) {
    return {
      pageNum: Number.isNaN(pageNum) ? null : pageNum,
      hasPageText: false,
      visionOnly: true,
      groundingUnavailable: true,
      apiGroundedOnly: false,
      aiText: aiText.slice(0, 400),
      matched: [],
      missing: [],
      snippet: "",
      snippetAnchor: "",
      matchRatio: null,
    };
  }

  tokens.forEach((t) => {
    if (pdfLower.includes(t) || (t.length >= 4 && pdfCompact.includes(t))) matched.push(t);
    else missing.push(t);
  });
  if (!missing.length && storedMissing.length) {
    storedMissing.forEach((w) => {
      if (!missing.includes(w)) missing.push(w);
    });
  }

  // Best short snippet from the PDF near the first matched word (or page start)
  let snippet = "";
  let snippetAnchor = "";
  if (pdfText) {
    const anchor = matched[0] || "";
    const idx = anchor ? pdfLower.indexOf(anchor) : 0;
    const start = Math.max(0, (idx >= 0 ? idx : 0) - 80);
    const end = Math.min(pdfText.length, start + 280);
    snippet = pdfText.slice(start, end).replace(/\s+/g, " ").trim();
    if (start > 0) snippet = "…" + snippet;
    if (end < pdfText.length) snippet = snippet + "…";
    snippetAnchor = anchor;
  }

  return {
    pageNum: Number.isNaN(pageNum) ? null : pageNum,
    hasPageText: !!pdfText,
    visionOnly: false,
    groundingUnavailable: false,
    apiGroundedOnly: false,
    aiText: aiText.slice(0, 400),
    matched,
    missing,
    snippet,
    snippetAnchor,
    matchRatio: tokens.length ? matched.length / tokens.length : null,
  };
}

function openRowConfidenceModal(row) {
  const modal = document.getElementById("row-confidence-modal");
  if (!modal || !row) return;
  const scoreEl = document.getElementById("row-confidence-score");
  const metricsEl = document.getElementById("row-confidence-metrics");
  const tagsEl = document.getElementById("row-confidence-tags");
  const detailEl = document.getElementById("row-confidence-detail");
  const noteEl = document.getElementById("row-confidence-note");
  const actionsEl = document.getElementById("row-confidence-actions");
  const gotoBtn = document.getElementById("row-confidence-goto-page");
  const q = row.quality || {};
  const conf = row.confidence != null ? Number(row.confidence) : null;
  const g = q.grounding_score != null ? Number(q.grounding_score) : null;
  const c = q.completeness_score != null ? Number(q.completeness_score) : null;
  const pageNum = row.page != null && String(row.page).trim() !== "" && String(row.page).toUpperCase() !== "NA"
    ? Number(row.page)
    : null;
  const naFields = listNaFields(row, activeRegistryTab);
  const scoredCount = scoredFieldsForTab(activeRegistryTab).length;
  const pageMatch = analyzePageMatch(row, activeRegistryTab);

  if (scoreEl) {
    scoreEl.textContent = conf == null || Number.isNaN(conf) ? "—" : `${Math.round(conf * 100)}%`;
  }
  const pageMatchLabel = pageMatch.groundingUnavailable
    ? "Not checked (OCR)"
    : (g == null || Number.isNaN(g) ? "—" : Math.round(g * 100) + "%");
  if (metricsEl) {
    metricsEl.innerHTML =
      `<div class="row-confidence-metric"><span>Fields filled</span><strong>${c == null || Number.isNaN(c) ? "—" : Math.round(c * 100) + "%"}</strong></div>` +
      `<div class="row-confidence-metric"><span>Page match</span><strong>${pageMatchLabel}</strong></div>` +
      `<div class="row-confidence-metric"><span>Blank fields</span><strong>${naFields.length} of ${scoredCount}</strong></div>`;
  }

  const reasons = buildClientQualityReasons(row, activeRegistryTab);
  if (tagsEl) {
    tagsEl.innerHTML = reasons.length
      ? reasons.map((r) => `<span class="${reasonTagClass(r)}">${escapeHTML(r)}</span>`).join("")
      : "";
  }

  // Human explanation blocks
  if (detailEl) {
    const blocks = [];
    const checkedFields = scoredFieldsForTab(activeRegistryTab);
    const filledFields = checkedFields.filter((f) => !naFields.includes(f));

    blocks.push(
      `<div class="rc-block">` +
      `<p class="rc-block-title">Blank fields: ${naFields.length} of ${scoredCount}</p>` +
      (naFields.length
        ? `<p class="rc-block-text">These required boxes are empty or “NA”, so Fields filled went down:</p>` +
          `<ul class="rc-list">${naFields.map((f) => `<li>${escapeHTML(fieldLabel(f))}</li>`).join("")}</ul>`
        : `<p class="rc-block-text">0 of ${scoredCount} means none of the required boxes are empty. All of these have a value (not NA):</p>` +
          `<ul class="rc-list">${filledFields.map((f) => `<li>${escapeHTML(fieldLabel(f))}</li>`).join("")}</ul>` +
          `<p class="rc-block-text">That is why Fields filled is 100%.</p>`) +
      `</div>`
    );

    // Page match explanation
    let matchTitle = "Page match (AI extract vs PDF page)";
    let matchBody = "";
    if (pageMatch.apiGroundedOnly) {
      const pct = g == null || Number.isNaN(g) ? "—" : Math.round(g * 100) + "%";
      if (pageMatch.missing.length) {
        matchBody =
          `Page match is ${pct} because some words the AI wrote were not found as exact text on the source page during extraction. ` +
          `Those missing words are listed below.`;
      } else if (g != null && !Number.isNaN(g) && g < 0.999) {
        matchBody =
          `Page match is ${pct} (not 100%) because a few distinctive words in this row did not letter-match the source page during extraction ` +
          `(OCR spelling, hyphens, or extra wording). This saved extract did not keep the missing-word list — ` +
          `re-run extract once to store it, or open the PDF page text to confirm.`;
      } else {
        matchBody =
          `Page match was checked during extraction (score ${pct}). ` +
          `Open PDF page text below to see the source wording.`;
      }
    } else if (pageMatch.groundingUnavailable || pageMatch.visionOnly) {
      matchBody =
        `This page was read as an image, but no searchable OCR text was returned for letter-match. ` +
        `Open PDF page ${pageMatch.pageNum || "?"} and confirm visually, then re-run extraction if needed.`;
    } else if (!pageMatch.hasPageText) {
      matchBody =
        `We could not load text for page ${pageMatch.pageNum || "?"} to compare. ` +
        `Re-run extraction so page text is available, then open this again.`;
    } else if (pageMatch.missing.length === 0 && pageMatch.matched.length) {
      matchBody =
        `Good news: the important words from the AI extract also appear on PDF page ${pageMatch.pageNum}` +
        ` (native text or OCR transcription).`;
    } else {
      matchBody =
        `Page match is below 100% because some words the AI wrote are not found as exact text on PDF page ${pageMatch.pageNum}. ` +
        `On scanned/handwritten pages this can mean a real mismatch, or OCR cleaned/split words differently than the table.`;
    }

    let compareHtml = "";
    if (pageMatch.aiText) {
      compareHtml +=
        `<p class="rc-compare-label">What the AI wrote (from this row)</p>` +
        `<div class="rc-compare-box">${escapeHTML(pageMatch.aiText)}</div>`;
    }
    if (pageMatch.missing.length) {
      compareHtml +=
        `<p class="rc-compare-label">Words from the AI that were not found on the PDF page</p>` +
        `<div class="rc-missing-words">${pageMatch.missing
          .slice(0, 12)
          .map((w) => `<span class="rc-missing-word">${escapeHTML(w)}</span>`)
          .join("")}</div>`;
    }
    if (pageMatch.snippet) {
      const pin = pageMatch.pageNum != null ? `Page ${pageMatch.pageNum}` : "PDF";
      let snip = escapeHTML(pageMatch.snippet);
      if (pageMatch.snippetAnchor) {
        const re = new RegExp(`(${pageMatch.snippetAnchor.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "ig");
        snip = snip.replace(re, "<mark>$1</mark>");
      }
      compareHtml +=
        `<p class="rc-compare-label">Where to look in the PDF (${escapeHTML(pin)})</p>` +
        `<div class="rc-compare-box rc-pdf-snip">${snip}</div>`;
    }

    blocks.push(
      `<div class="rc-block">` +
      `<p class="rc-block-title">${matchTitle}</p>` +
      `<p class="rc-block-text">${matchBody}</p>` +
      compareHtml +
      `</div>`
    );

    detailEl.innerHTML = blocks.join("");
  }

  if (noteEl) {
    if (pageMatch.groundingUnavailable || pageMatch.visionOnly) {
      noteEl.hidden = false;
      noteEl.textContent =
        `No OCR page text available for letter-match. Open PDF page ${pageMatch.pageNum || "?"} and confirm visually.`;
    } else if (conf != null && !Number.isNaN(conf) && conf < 1) {
      const parts = [];
      if (naFields.length) {
        parts.push(`Fill blank fields: ${naFields.map(fieldLabel).join(", ")}.`);
      }
      if (pageMatch.missing.length) {
        parts.push(
          `On page ${pageMatch.pageNum || "?"}, check why these AI words are missing from the PDF: ${pageMatch.missing.slice(0, 6).join(", ")}.`
        );
      }
      if (!parts.length) {
        parts.push("Open the PDF page and confirm the row against the manual.");
      }
      noteEl.hidden = false;
      noteEl.textContent = parts.join(" ");
    } else {
      noteEl.hidden = true;
      noteEl.textContent = "";
    }
  }

  if (actionsEl && gotoBtn) {
    if (!Number.isNaN(pageNum) && pageNum != null) {
      actionsEl.hidden = false;
      gotoBtn.textContent = `Open PDF page ${pageNum} text`;
      gotoBtn.onclick = () => {
        closeRowConfidenceModal();
        if (typeof window.jumpToPageContext === "function") {
          window.jumpToPageContext(pageNum);
        }
        const tr = document.querySelector(`.data-table tbody tr[data-id="${row.id}"]`);
        if (tr) {
          tr.scrollIntoView({ behavior: "smooth", block: "center" });
          tr.classList.add("row-confidence-flash");
          setTimeout(() => tr.classList.remove("row-confidence-flash"), 1800);
        }
      };
    } else {
      actionsEl.hidden = true;
      gotoBtn.onclick = null;
    }
  }

  const tr = document.querySelector(`.data-table tbody tr[data-id="${row.id}"]`);
  if (tr) {
    tr.scrollIntoView({ behavior: "smooth", block: "nearest" });
    tr.classList.add("row-confidence-flash");
    setTimeout(() => tr.classList.remove("row-confidence-flash"), 1800);
  }

  modal.hidden = false;
}

function closeRowConfidenceModal() {
  const modal = document.getElementById("row-confidence-modal");
  if (modal) modal.hidden = true;
}

function handleConfidenceFilterChange(filterValue) {
  currentConfidenceFilter = filterValue || "all";
  highlightRecordIds = [];
  renderGrid();
}

function filterByConfidence(rows) {
  if (!Array.isArray(rows) || currentConfidenceFilter === "all") return rows;
  return rows.filter(row => {
    const score = row.confidence != null && row.confidence !== "" ? Number(row.confidence) : 1.0;
    if (Number.isNaN(score)) return currentConfidenceFilter === "all";
    if (currentConfidenceFilter === "high") return score >= 0.8;
    if (currentConfidenceFilter === "review") return score < 0.8;
    if (currentConfidenceFilter === "low") return score < 0.5;
    return true;
  });
}

// Safe Lucide icon rendering wrapper
function safeCreateIcons() {
  if (typeof lucide !== 'undefined' && lucide.createIcons) {
    lucide.createIcons();
  }
}

// Theme toggle lives in auth-admin.js (shared with admin.html). Do not redeclare
// THEME_STORAGE_KEY / applyTheme here — duplicate const breaks the whole script.

/* -------------------------------------------------------------
 * Python FastAPI extraction backend
 * UI stays in JS; heavy PDF/OCR/LLM work runs on the API when available.
 * Browser extractor remains for heuristics, Word/DOCX, and API-down fallback.
 * ------------------------------------------------------------- */
const API_BASE_KEY = "omniparse_api_base";
/** Local UI → local FastAPI. CloudFront UI → same-origin /api/* (ALB via CF). */
function defaultApiBaseUrl() {
  try {
    const host = typeof location !== "undefined" ? location.hostname : "";
    if (host && host !== "localhost" && host !== "127.0.0.1") return "";
  } catch (e) {}
  return "http://127.0.0.1:8001";
}
let apiBaseUrl = defaultApiBaseUrl();
try {
  const savedApiBase = localStorage.getItem(API_BASE_KEY);
  if (savedApiBase !== null && savedApiBase !== undefined) {
    const trimmed = String(savedApiBase).replace(/\/$/, "");
    const host = typeof location !== "undefined" ? location.hostname : "";
    // If hosted on CloudFront / domain, discard any leftover localhost overrides from dev
    if (host && host !== "localhost" && host !== "127.0.0.1" && (trimmed.includes("localhost") || trimmed.includes("127.0.0.1"))) {
      apiBaseUrl = "";
      localStorage.removeItem(API_BASE_KEY);
    } else {
      apiBaseUrl = trimmed;
    }
  }
} catch (e) {}

async function checkPythonApiHealth(retryCount = 1) {
  for (let attempt = 0; attempt <= retryCount; attempt++) {
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 20000);
      const resp = await fetch(`${apiBaseUrl}/api/health`, {
        method: "GET",
        signal: controller.signal
      });
      clearTimeout(timeoutId);
      if (resp.ok) {
        const data = await resp.json();
        return {
          ok: !!(data && data.status === "ok"),
          busy: !!(data && data.busy)
        };
      }
    } catch (e) {
      if (attempt < retryCount) {
        await new Promise(r => setTimeout(r, 1000));
      }
    }
  }
  return { ok: false, busy: false };
}

function canUsePythonApiForFile(file, extension) {
  // Slim build: PDF / TXT / images go to FastAPI only (no browser LLM fallback).
  if (!["pdf", "txt", "jpg", "jpeg", "png"].includes(extension)) return false;
  return !!file;
}

function getConfiguredPageRange() {
  const startVal = pageRangeStartInput && pageRangeStartInput.value ? parseInt(pageRangeStartInput.value, 10) : NaN;
  const endVal = pageRangeEndInput && pageRangeEndInput.value ? parseInt(pageRangeEndInput.value, 10) : NaN;
  return {
    start: (!isNaN(startVal) && startVal > 0) ? startVal : null,
    end: (!isNaN(endVal) && endVal > 0) ? endVal : null
  };
}

async function countPdfPages(file) {
  if (typeof pdfjsLib === "undefined") return null;
  const buf = await file.arrayBuffer();
  const pdf = await pdfjsLib.getDocument({ data: new Uint8Array(buf) }).promise;
  return pdf.numPages || 0;
}

function formatElapsed(ms) {
  const totalSec = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

async function confirmLargePdfIfNeeded(pages, fileSizeBytes = 0) {
  const range = getConfiguredPageRange();
  if (range.start || range.end) return true;

  if (pages && pages > 5000) {
    return confirm(
      `This PDF has ${pages} pages.\n\n` +
      `One-shot limit is 5000 pages. The API will process pages 1–5000 only unless you set a From/To range.\n\n` +
      `Continue?`
    );
  }

  if (pages && pages > 80) {
    const hoursLow = Math.max(1, Math.round((pages / 8) * 8 / 3600));
    const hoursHigh = Math.max(hoursLow + 1, Math.round((pages / 8) * 25 / 3600));
    return confirm(
      `This PDF has ${pages} pages.\n\n` +
      `Full one-shot extraction processes EVERY page (up to 5000) and can take roughly ${hoursLow}–${hoursHigh}+ hours.\n\n` +
      `Keep this browser tab open. Prefer Native text + Flash-Lite for speed/cost.\n\n` +
      `Continue with ALL ${pages} pages in one go?`
    );
  }

  if (!pages && fileSizeBytes > 40 * 1024 * 1024) {
    const mb = (fileSizeBytes / (1024 * 1024)).toFixed(0);
    return confirm(
      `This PDF is ~${mb}MB (likely a very large manual).\n\n` +
      `Full one-shot extraction processes every page (max 5000) and can take many hours.\n\n` +
      `Keep this browser tab open.\n\n` +
      `Continue with the FULL document in one go?`
    );
  }

  return true;
}

async function extractViaPythonApi(sharepointItem, pageCountHint = null) {
  // Prefer admin/local browser key when present; otherwise API uses GEMINI_API_KEY from backend/.env
  refreshAdminTestGeminiKey();
  function buildExtractForm() {
    const form = new FormData();
    if (sharepointItem && sharepointItem.file) {
      form.append("file", sharepointItem.file);
    } else if (sharepointItem && sharepointItem.id) {
      form.append("sharepoint_item_id", sharepointItem.id);
    }
    form.append("engine", engineMode === "ollama" ? "ollama" : "gemini");
    // History cards are image-only scans — always request OCR for Logbook.
    const strategy = activeEquipmentCategory === "Logbook"
      ? "ocr"
      : (parseStrategy === "native" ? "native" : "ocr");
    form.append("parse_strategy", strategy);
    form.append("gemini_api_key", geminiApiKey || "");
    form.append("gemini_model", (() => {
      const allowed = getAssignedGeminiModels();
      const chosen = geminiModel || "gemini-3.5-flash";
      if (allowed && allowed.length && !allowed.includes(chosen)) return allowed[0];
      return chosen;
    })());
    form.append("ollama_url", ollamaUrl || "http://localhost:11434");
    form.append("ollama_model", ollamaModel || "");
    form.append("equipment_category", activeEquipmentCategory || "Default");

    const range = getConfiguredPageRange();
    if (range.start) form.append("page_start", String(range.start));
    if (range.end) form.append("page_end", String(range.end));

    if (learnedPatterns && learnedPatterns.length > 0) {
      form.append("learned_patterns", JSON.stringify(learnedPatterns));
    }
    return form;
  }

  const range = getConfiguredPageRange();
  const fileSizeBytes = Number(sharepointItem.size) || 0;
  const estimatedPages = (() => {
    if (range.start && range.end) return Math.max(1, range.end - range.start + 1);
    if (range.start && pageCountHint) return Math.max(1, pageCountHint - range.start + 1);
    if (range.end) return range.end;
    if (pageCountHint) return pageCountHint;
    // Rough fallback when page count is unknown (large manuals).
    return Math.max(200, Math.round(fileSizeBytes / (80 * 1024)));
  })();

  // Full-book runs need many hours. Scale timeout; cap at 24h.
  // Async job+poll path avoids CloudFront's ~120s origin timeout.
  const timeoutMs = Math.min(
    24 * 60 * 60 * 1000,
    Math.max(2 * 60 * 60 * 1000, estimatedPages * 15 * 1000)
  );

  progressStatus.innerText = "Initiating document extraction…";
  if (progressFill) progressFill.style.width = "8%";

  if (typeof window.requireAuthForApi === "function") window.requireAuthForApi();
  const authHeaders = (typeof window.getAuthHeaders === "function") ? window.getAuthHeaders() : {};

  const startedAt = Date.now();
  let createResp;
  try {
    createResp = await fetch(`${apiBaseUrl}/api/extract/jobs`, {
      method: "POST",
      body: buildExtractForm(),
      headers: authHeaders
    });
  } catch (err) {
    if (err.message === "Sign in required") throw err;
    // Older API without /jobs — fall back to sync extract.
    return extractViaPythonApiSync(buildExtractForm(), authHeaders, timeoutMs, estimatedPages, startedAt);
  }

  if (createResp.status === 404) {
    return extractViaPythonApiSync(buildExtractForm(), authHeaders, timeoutMs, estimatedPages, startedAt);
  }

  if (!createResp.ok) {
    let detail = "";
    try {
      const errJson = await createResp.json();
      detail = errJson.detail || JSON.stringify(errJson);
    } catch (e) {
      detail = await createResp.text();
    }
    throw new Error(detail || `API HTTP ${createResp.status}`);
  }

  const created = await createResp.json();
  const jobId = created && created.job_id;
  if (!jobId) throw new Error("Server did not return an extraction job id");

  const queuePos = (created && created.position) || 0;
  progressStatus.innerText = queuePos > 0
    ? `Queued at position ${queuePos} — waiting for ${queuePos} job(s) ahead to finish...`
    : "Job accepted — starting document processing...";
  if (progressFill) progressFill.style.width = "12%";

  const pollIntervalMs = 2000;
  let consecutivePollFailures = 0;
  while (Date.now() - startedAt < timeoutMs) {
    await new Promise(r => setTimeout(r, pollIntervalMs));
    let statusResp;
    try {
      statusResp = await fetch(`${apiBaseUrl}/api/extract/jobs/${encodeURIComponent(jobId)}`, {
        method: "GET",
        headers: authHeaders
      });
      consecutivePollFailures = 0;
    } catch (err) {
      consecutivePollFailures += 1;
      progressStatus.innerText =
        `Waiting for status update… ${formatElapsed(Date.now() - startedAt)} elapsed` +
        ` (Connection retry ×${consecutivePollFailures})`;
      // Don't spin for hours if the API process died mid-job.
      if (consecutivePollFailures >= 5) {
        throw new Error(
          `Server stopped responding while job ${jobId} was running. ` +
          `Please check server status and try again. ` +
          `For large manuals, consider selecting a specific page range.`
        );
      }
      continue;
    }

    if (!statusResp.ok) {
      let detail = "";
      try {
        const errJson = await statusResp.json();
        detail = errJson.detail || JSON.stringify(errJson);
      } catch (e) {
        detail = await statusResp.text();
      }
      // Unknown/expired job after API restart
      if (statusResp.status === 400 || statusResp.status === 404) {
        throw new Error(
          detail ||
          `Job ${jobId} expired or server restarted. Please start a new extraction.`
        );
      }
      throw new Error(detail || `Job status HTTP ${statusResp.status}`);
    }

    const contentType = (statusResp.headers && statusResp.headers.get("content-type")) || "";
    if (!contentType.includes("application/json")) {
      consecutivePollFailures += 1;
      if (consecutivePollFailures >= 5) {
        throw new Error(`Job ${jobId} expired or server restarted. Please start a new extraction.`);
      }
      continue;
    }

    let job;
    try {
      job = await statusResp.json();
    } catch (e) {
      consecutivePollFailures += 1;
      if (consecutivePollFailures >= 5) {
        throw new Error(`Job ${jobId} expired or server restarted. Please start a new extraction.`);
      }
      continue;
    }
    if (!job || typeof job !== "object" || !job.status) {
      consecutivePollFailures += 1;
      if (consecutivePollFailures >= 5) {
        throw new Error(`Job ${jobId} expired or server restarted. Please start a new extraction.`);
      }
      continue;
    }
    const pct = Math.min(92, 12 + Math.floor((Number(job.progress) || 0) * 80));
    if (progressFill) progressFill.style.width = `${pct}%`;
    const msg = job.message || "Processing…";
    progressStatus.innerText =
      `${formatElapsed(Date.now() - startedAt)} elapsed — ${msg}`;

    if (job.status === "error") {
      throw new Error(job.error || job.message || "Extraction job failed");
    }
    // Treat result as finished even if a late progress update left status=running
    // (Fabric cache hits used to race and stick the overlay on "Loaded from Fabric").
    if (job.status === "done" || job.result) {
      if (!job.result) throw new Error("Job finished but returned no result");
      if (progressFill) progressFill.style.width = "85%";
      progressStatus.innerText = "Merging registries into grid...";
      return job.result;
    }
  }

  throw new Error(
    `Extraction timed out after ${formatElapsed(timeoutMs)}. ` +
    `Use a smaller From/To page range, switch to Native text (if searchable), or try Flash-Lite.`
  );
}

async function extractViaPythonApiSync(form, authHeaders, timeoutMs, estimatedPages, startedAt) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  const tickId = setInterval(() => {
    const elapsed = formatElapsed(Date.now() - startedAt);
    const pct = Math.min(70, 12 + Math.floor(((Date.now() - startedAt) / timeoutMs) * 55));
    if (progressFill) progressFill.style.width = `${pct}%`;
    progressStatus.innerText =
      `Processing document… ${elapsed} elapsed` +
      ` (~${estimatedPages} pages queued).`;
  }, 2000);

  let resp;
  try {
    resp = await fetch(`${apiBaseUrl}/api/extract`, {
      method: "POST",
      body: form,
      headers: authHeaders,
      signal: controller.signal
    });
  } catch (err) {
    if (err.message === "Sign in required") throw err;
    if (err.name === "AbortError") {
      throw new Error(
        `Extraction timed out after ${formatElapsed(timeoutMs)}. ` +
        `Use a smaller From/To page range, switch to Native text (if the PDF is searchable), or try Flash-Lite.`
      );
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
    clearInterval(tickId);
  }

  if (!resp.ok) {
    let detail = "";
    try {
      const errJson = await resp.json();
      detail = errJson.detail || JSON.stringify(errJson);
    } catch (e) {
      detail = await resp.text();
    }
    throw new Error(detail || `API HTTP ${resp.status}`);
  }

  if (progressFill) progressFill.style.width = "85%";
  progressStatus.innerText = "Merging registries into grid...";
  return resp.json();
}

function selectRegistryTab(mode) {
  if (!mode || !registryModeTabs) return;
  const btn = registryModeTabs.querySelector(`.mode-tab-btn[data-mode="${mode}"]`);
  if (!btn) return;
  document.querySelectorAll(".mode-tab-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  activeRegistryTab = mode;
  if (mode === "maintenance") {
    currentTabFilter = "all";
    resetFilterTabActive(filterTabs, "data-filter", "all");
  } else if (mode === "spare_parts") {
    currentSpareFilter = "all";
    resetFilterTabActive(spareFilterTabs, "data-spare-filter", "all");
  }
  if (maintenanceTable && sparePartsTable && troubleshootingTable) {
    maintenanceTable.style.display = mode === "maintenance" ? "table" : "none";
    sparePartsTable.style.display = mode === "spare_parts" ? "table" : "none";
    troubleshootingTable.style.display = mode === "troubleshooting" ? "table" : "none";
  }
  if (typeof syncRegistryFilterTabs === "function") syncRegistryFilterTabs();
}

/** Prefer a tab that actually has rows so extract output is visible immediately. */
function preferTabWithResults() {
  const counts = [
    ["maintenance", maintenanceRegistry.length],
    ["spare_parts", sparePartsRegistry.length],
    ["troubleshooting", troubleshootingRegistry.length]
  ];
  const currentCount = (
    activeRegistryTab === "spare_parts" ? sparePartsRegistry.length :
    activeRegistryTab === "troubleshooting" ? troubleshootingRegistry.length :
    maintenanceRegistry.length
  );
  if (currentCount > 0) return;
  const best = counts.sort((a, b) => b[1] - a[1])[0];
  if (best && best[1] > 0) selectRegistryTab(best[0]);
}

function resetProgressCardPosition() {
  if (!progressOverlay) return;
  progressOverlay.style.left = "";
  progressOverlay.style.top = "";
  progressOverlay.style.right = "";
  progressOverlay.style.bottom = "";
  progressOverlay.style.transform = "";
  progressOverlay.classList.remove("is-dragging");
}

function setExtractingUi(active, title, status) {
  isExtracting = !!active;
  if (dropZone) dropZone.classList.toggle("is-processing", !!active);
  if (progressOverlay) {
    progressOverlay.classList.toggle("active", !!active);
    if (active) {
      // Always reopen from the middle of the page for a new run.
      resetProgressCardPosition();
    } else {
      resetProgressCardPosition();
    }
  }
  if (active) {
    if (progressFill) progressFill.style.width = "0%";
    if (title && progressTitle) progressTitle.innerText = title;
    if (status && progressStatus) progressStatus.innerText = status;
  }
}

function clearExtractingUi() {
  setExtractingUi(false);
}

/** Drag the floating processing card anywhere on the page. */
function initProgressCardDrag() {
  const handle = document.getElementById("progress-drag-handle");
  if (!progressOverlay || !handle || handle.dataset.dragBound === "1") return;
  handle.dataset.dragBound = "1";

  let dragging = false;
  let startX = 0;
  let startY = 0;
  let originLeft = 0;
  let originTop = 0;

  const onPointerDown = (e) => {
    if (!progressOverlay.classList.contains("active")) return;
    if (e.button != null && e.button !== 0) return;
    const rect = progressOverlay.getBoundingClientRect();
    // Switch from centered transform to absolute left/top for free dragging.
    progressOverlay.style.left = `${rect.left}px`;
    progressOverlay.style.top = `${rect.top}px`;
    progressOverlay.style.transform = "none";
    originLeft = rect.left;
    originTop = rect.top;
    startX = e.clientX;
    startY = e.clientY;
    dragging = true;
    progressOverlay.classList.add("is-dragging");
    try { handle.setPointerCapture(e.pointerId); } catch (_) {}
    e.preventDefault();
  };

  const onPointerMove = (e) => {
    if (!dragging) return;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    const cardW = progressOverlay.offsetWidth;
    const cardH = progressOverlay.offsetHeight;
    const margin = 8;
    const maxLeft = Math.max(margin, window.innerWidth - cardW - margin);
    const maxTop = Math.max(margin, window.innerHeight - cardH - margin);
    const nextLeft = Math.min(maxLeft, Math.max(margin, originLeft + dx));
    const nextTop = Math.min(maxTop, Math.max(margin, originTop + dy));
    progressOverlay.style.left = `${nextLeft}px`;
    progressOverlay.style.top = `${nextTop}px`;
  };

  const onPointerUp = (e) => {
    if (!dragging) return;
    dragging = false;
    progressOverlay.classList.remove("is-dragging");
    try { handle.releasePointerCapture(e.pointerId); } catch (_) {}
  };

  handle.addEventListener("pointerdown", onPointerDown);
  handle.addEventListener("pointermove", onPointerMove);
  handle.addEventListener("pointerup", onPointerUp);
  handle.addEventListener("pointercancel", onPointerUp);
}

function applyApiExtractResult(result, file) {
  try {
    const meta = (result && result.meta) || {};
    lastExtractMeta = meta;
    activeFabricRunId = meta.run_id || (result && result.run_id) || (result && result.fabric_run_id) || activeFabricRunId || null;
    activeDocumentMetadata = meta.doc_metadata || (result && result.doc_metadata) || null;
    if (meta.assigned_approver) {
      activeDocumentMetadata = Object.assign({}, activeDocumentMetadata || {}, { assigned_approver: meta.assigned_approver });
    }
    activeDocumentStatus = meta.document_status || (result && result.document_status) || "Pending Review";
    activeApprovedBy = meta.approved_by || (result && result.approved_by) || null;
    activeApprovedAt = meta.approved_at || (result && result.approved_at) || null;
    
    // Rehydrate and normalize baseline extraction snapshot for dual-storage diff auditing
    const rawBaseline = (result && (result.baseline || result.raw_payload)) || null;
    if (rawBaseline) {
      baselineExtraction = {
        spare_parts: (rawBaseline.spare_parts || []).map((r, i) => ({ ...r, id: r.id !== undefined && r.id !== null ? Number(r.id) : (i + 1), pdf_order: r.pdf_order !== undefined && r.pdf_order !== null ? Number(r.pdf_order) : (i + 1) })),
        maintenance: (rawBaseline.maintenance || []).map((r, i) => ({ ...r, id: r.id !== undefined && r.id !== null ? Number(r.id) : (i + 1), pdf_order: r.pdf_order !== undefined && r.pdf_order !== null ? Number(r.pdf_order) : (i + 1) })),
        troubleshooting: (rawBaseline.troubleshooting || []).map((r, i) => ({ ...r, id: r.id !== undefined && r.id !== null ? Number(r.id) : (i + 1), pdf_order: r.pdf_order !== undefined && r.pdf_order !== null ? Number(r.pdf_order) : (i + 1) })),
        doc_metadata: rawBaseline.doc_metadata || null,
        extracted_at: rawBaseline.extracted_at || null,
      };
    } else {
      const spares_init = (result && result.spare_parts) || [];
      const maint_init = (result && result.maintenance) || [];
      const trouble_init = (result && result.troubleshooting) || [];
      if (spares_init.length > 0 || maint_init.length > 0 || trouble_init.length > 0) {
        baselineExtraction = {
          spare_parts: spares_init.map((r, i) => ({ ...r, id: r.id !== undefined && r.id !== null ? Number(r.id) : (i + 1), pdf_order: r.pdf_order !== undefined && r.pdf_order !== null ? Number(r.pdf_order) : (i + 1) })),
          maintenance: maint_init.map((r, i) => ({ ...r, id: r.id !== undefined && r.id !== null ? Number(r.id) : (i + 1), pdf_order: r.pdf_order !== undefined && r.pdf_order !== null ? Number(r.pdf_order) : (i + 1) })),
          troubleshooting: trouble_init.map((r, i) => ({ ...r, id: r.id !== undefined && r.id !== null ? Number(r.id) : (i + 1), pdf_order: r.pdf_order !== undefined && r.pdf_order !== null ? Number(r.pdf_order) : (i + 1) })),
          doc_metadata: activeDocumentMetadata ? { ...activeDocumentMetadata } : null,
          extracted_at: new Date().toISOString(),
        };
      } else {
        baselineExtraction = null;
      }
    }

    if (meta && meta.has_diff && canApproveOrSignOff()) {
      isDiffViewActive = true;
    }

    const maint = (result && result.maintenance) || [];
    const spares = (result && result.spare_parts) || [];
    const trouble = (result && result.troubleshooting) || [];
    const defaultRowStatus = activeDocumentStatus === "Approved" ? "Approved" : "Pending Review";

    maintenanceRegistry = maint.map((row, idx) => ({
      ...row,
      id: row.id || idx + 1,
      status: row.status || defaultRowStatus,
      reviewed_by: row.reviewed_by || (activeDocumentStatus === "Approved" ? activeApprovedBy : null),
      reviewed_at: row.reviewed_at || (activeDocumentStatus === "Approved" ? activeApprovedAt : null),
    }));
    sparePartsRegistry = spares.map((row, idx) => ({
      ...row,
      id: row.id || idx + 1,
      status: row.status || defaultRowStatus,
      reviewed_by: row.reviewed_by || (activeDocumentStatus === "Approved" ? activeApprovedBy : null),
      reviewed_at: row.reviewed_at || (activeDocumentStatus === "Approved" ? activeApprovedAt : null),
    }));
    troubleshootingRegistry = trouble.map((row, idx) => ({
      ...row,
      id: row.id || idx + 1,
      status: row.status || defaultRowStatus,
      reviewed_by: row.reviewed_by || (activeDocumentStatus === "Approved" ? activeApprovedBy : null),
      reviewed_at: row.reviewed_at || (activeDocumentStatus === "Approved" ? activeApprovedAt : null),
    }));

    loadedPages = ((result && result.pages) || []).map(p => ({
      pageNum: p.pageNum,
      text: p.text || ""
    }));

    if (typeof assembleRegistriesInPageOrder === "function") {
      assembleRegistriesInPageOrder();
    }

    (meta.warnings || []).forEach(w => appendChatSystemMessage(`⚠️ ${w}`));

    if (meta.already_approved) {
      const who = meta.prior_approved_by || activeApprovedBy || "an approver";
      const when = meta.prior_approved_at || activeApprovedAt ? ` on ${meta.prior_approved_at || activeApprovedAt}` : "";
      const prompt = activeDocumentStatus === "Approved"
        ? `This document was already signed off by ${who}${when}. Review is complete — no further action required.`
        : `This document was already signed off by ${who}${when}. A new pending copy was created for your workspace. The original AI extraction is preserved as the audit baseline.`;
      try { window.alert(prompt); } catch (e) {}
    }

    if (progressFill) progressFill.style.width = "100%";
    if (progressStatus) progressStatus.innerText = "Extraction finished!";
    
    const resolvedDocName = (meta && meta.filename) || (file && file.name) || (activeDocumentMetadata && activeDocumentMetadata.title) || lastSourceDocName || "document.pdf";
    lastSourceDocName = resolvedDocName;
    setActiveDocBadge(resolvedDocName);
    updateDocMetadataBadge();
    safeCreateIcons();

    preferTabWithResults();
    renderGrid();
    notifyExtractionFinished(resolvedDocName, maint.length, spares.length, trouble.length);
    offerSaveExcelAfterExtraction(file || { name: resolvedDocName });
  } finally {
    clearExtractingUi();
  }
}

// DOM Elements
const maintenanceTable = document.getElementById("maintenance-table");
const sparePartsTable = document.getElementById("spare-parts-table");
const troubleshootingTable = document.getElementById("troubleshooting-table");
const maintenanceTableBody = document.getElementById("maintenance-table-body");
const sparePartsTableBody = document.getElementById("spare-parts-table-body");
const troubleshootingTableBody = document.getElementById("troubleshooting-table-body");
const registryModeTabs = document.getElementById("registry-mode-tabs");
const tableEmpty = document.getElementById("table-empty");
const countRules = document.getElementById("count-rules");
const countParts = document.getElementById("count-parts");
const countConsumables = document.getElementById("count-consumables");
const countTime = document.getElementById("count-time");
const countTroubleshooting = document.getElementById("count-troubleshooting");
const countOverallScore = document.getElementById("count-overall-score");
const filterTabs = document.getElementById("filter-tabs");
const spareFilterTabs = document.getElementById("spare-filter-tabs");
const gridSearch = document.getElementById("grid-search");
const confidenceFilter = document.getElementById("confidence-filter");
const addRowBtn = document.getElementById("add-row-btn");
const exportBtn = document.getElementById("export-btn");
const dropZone = document.getElementById("drop-zone");
const sharepointFilesList = document.getElementById("sharepoint-files-list");
const sharepointRefreshBtn = document.getElementById("sharepoint-refresh-btn");
const pageRangeStartInput = document.getElementById("page-range-start");
const pageRangeEndInput = document.getElementById("page-range-end");
const progressOverlay = document.getElementById("progress-overlay");
const progressFill = document.getElementById("progress-fill");
const progressTitle = document.getElementById("progress-title");
const progressStatus = document.getElementById("progress-status");
const activeDocName = document.getElementById("active-doc-name");
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const chatMessages = document.getElementById("chat-messages");

/** Header badge: show filename as soon as upload starts; idle = No Document Loaded. */
function setActiveDocBadge(fileName) {
  if (!activeDocName) return;
  const name = String(fileName || "").trim();
  if (!name) {
    activeDocName.innerHTML = `<i data-lucide="file-warning"></i><span>No Document Loaded</span>`;
    activeDocName.style.borderColor = "hsla(0, 85%, 60%, 0.3)";
    activeDocName.style.color = "hsl(0, 85%, 65%)";
    activeDocName.style.background = "hsla(0, 85%, 60%, 0.05)";
  } else {
    activeDocName.innerHTML = `<i data-lucide="file-text"></i><span title="${escapeHTML(name)}">${escapeHTML(name)}</span>`;
    activeDocName.style.borderColor = "var(--accent-cyan-glow)";
    activeDocName.style.color = "var(--accent-cyan)";
    activeDocName.style.background = "hsla(190, 90%, 50%, 0.05)";
  }
  if (typeof safeCreateIcons === "function") safeCreateIcons();
}

// Registry Mode Switching Listener
if (registryModeTabs) {
  registryModeTabs.addEventListener("click", (e) => {
    const tabBtn = e.target.closest(".mode-tab-btn");
    if (!tabBtn) return;
    
    document.querySelectorAll(".mode-tab-btn").forEach(btn => btn.classList.remove("active"));
    tabBtn.classList.add("active");
    activeRegistryTab = tabBtn.getAttribute("data-mode");

    // Reset each registry's filter set to "All …" when switching modes
    if (activeRegistryTab === "maintenance") {
      currentTabFilter = "all";
      resetFilterTabActive(filterTabs, "data-filter", "all");
    } else if (activeRegistryTab === "spare_parts") {
      currentSpareFilter = "all";
      resetFilterTabActive(spareFilterTabs, "data-spare-filter", "all");
    }
    
    if (activeRegistryTab === "maintenance") {
      maintenanceTable.style.display = "table";
      sparePartsTable.style.display = "none";
      troubleshootingTable.style.display = "none";
    } else if (activeRegistryTab === "spare_parts") {
      maintenanceTable.style.display = "none";
      sparePartsTable.style.display = "table";
      troubleshootingTable.style.display = "none";
    } else if (activeRegistryTab === "troubleshooting") {
      maintenanceTable.style.display = "none";
      sparePartsTable.style.display = "none";
      troubleshootingTable.style.display = "table";
    }
    syncRegistryFilterTabs();
    
    highlightRecordIds = []; // clear RAG filters on switch
    clearSelectedRegistryRow();
    renderGrid();
  });
}

// AI Engine configuration state — Gemini only (server-managed API key).
let engineMode = "gemini";
let parseStrategy = "native"; // auto: "ocr" for Field History / Logbook
let ollamaUrl = "http://localhost:11434";
let ollamaModel = "";
let isExtracting = false;
let abortExtraction = false;

function isOllamaMode() {
  return false;
}

function isGeminiMode() {
  return true;
}

// Google Gemini — model selection only. API key comes from the Python API env (never from the browser UI).
let geminiApiKey = "";
let geminiModel = "gemini-3.5-flash"; // Best active Flash for dense/scanned manuals

const GEMINI_SETTINGS_KEY = "omniparse_gemini_settings";
// Same localStorage key as auth-admin.js, but a distinct identifier —
// both scripts share the global scope, so a duplicate `const` breaks app.js entirely.
const LOCAL_TEST_GEMINI_STORAGE_KEY = "omniparse_admin_test_gemini_key";
const GEMINI_FALLBACK_MODEL = "gemini-3.5-flash";
// Active lineup only (live Gemini API). Pro = gemini-3.1-pro-preview (+ 2.5-pro).
// There is no gemini-3.6-pro; 3.6 ships as Flash.
// Dropdown shows the bare model id only.
const GEMINI_RECOMMENDED_MODELS = [
  { id: "gemini-3.6-flash" },
  { id: "gemini-3.5-flash-lite" },
  { id: "gemini-3.5-flash" },
  { id: "gemini-3.1-pro-preview" },
  { id: "gemini-2.5-flash" },
  { id: "gemini-2.5-pro" }
];
const RETIRED_GEMINI_MODEL_PATTERNS = [
  /^gemini-1\./i,
  /^gemini-2\.0-/i,
  /^gemini-2\.5-flash-lite/i,
  /^gemini-pro$/i,
  /^gemini-flash$/i
];

// Map curated IDs → live API aliases (preview suffixes, short names, etc.).
const GEMINI_MODEL_ALIASES = {
  "gemini-3.6-flash": ["gemini-3.6-flash"],
  "gemini-3.5-flash-lite": ["gemini-3.5-flash-lite"],
  "gemini-3.5-flash": ["gemini-3.5-flash", "gemini-3.5-flash-preview"],
  "gemini-3.1-pro-preview": ["gemini-3.1-pro-preview", "gemini-3.1-pro"],
  "gemini-2.5-flash": ["gemini-2.5-flash"],
  "gemini-2.5-pro": ["gemini-2.5-pro"]
};

function isRetiredGeminiModel(modelName) {
  const name = String(modelName || "").trim();
  if (!name) return true;
  return RETIRED_GEMINI_MODEL_PATTERNS.some(rx => rx.test(name));
}

function normalizeGeminiModel(modelName) {
  const name = String(modelName || "").trim().replace(/^models\//, "");
  if (!name || isRetiredGeminiModel(name)) return GEMINI_FALLBACK_MODEL;
  // Treat bare 3.1 Pro as the preview API id used in the dropdown.
  if (name === "gemini-3.1-pro") return "gemini-3.1-pro-preview";
  return name;
}

/** Collapses API aliases (e.g. gemini-3.5-flash-preview) onto the curated id. */
function canonicalGeminiModelId(modelId) {
  const id = String(modelId || "").trim().replace(/^models\//, "");
  for (const [canonical, aliases] of Object.entries(GEMINI_MODEL_ALIASES)) {
    if (canonical === id || aliases.includes(id)) return canonical;
  }
  return id;
}

function resolveGeminiModelId(preferredId, availableModelIds) {
  const preferred = normalizeGeminiModel(preferredId);
  const available = (availableModelIds || [])
    .map(id => String(id || "").trim().replace(/^models\//, ""))
    .filter(Boolean);
  if (available.length === 0) return preferred;
  if (available.includes(preferred)) return preferred;

  const aliases = GEMINI_MODEL_ALIASES[preferred] || [preferred];
  for (const alias of aliases) {
    if (available.includes(alias)) return alias;
  }

  const base = preferred.replace(/-preview$/i, "");
  const fuzzy = available.find(id => id === base || id.startsWith(base + "-") || id.startsWith(base));
  return fuzzy || preferred;
}

function populateGeminiModelSelect(availableModelIds, preferredModelId) {
  if (!geminiModelInput) return;

  const available = (availableModelIds || [])
      .map(id => String(id || "").trim().replace(/^models\//, ""))
    .filter(Boolean);

  const policyAllowed = getAssignedGeminiModels();
  const curated = GEMINI_RECOMMENDED_MODELS.map(m => m.id);
  // When signed in with assigned models, only those appear — never the full curated list.
  const sourceIds = policyAllowed && policyAllowed.length
    ? policyAllowed
    : curated;

  geminiModelInput.innerHTML = "";

  const seen = new Set();
  sourceIds.forEach(id => {
    const resolved = resolveGeminiModelId(id, available.length ? available : [id]);
    // Dedupe on the canonical id so aliases of the same model appear once.
    const canonical = canonicalGeminiModelId(resolved);
    if (seen.has(canonical)) return;
    seen.add(canonical);
    const option = document.createElement("option");
    option.value = resolved;
    option.innerText = canonical;
    geminiModelInput.appendChild(option);
  });

  const preferred = resolveGeminiModelId(preferredModelId || geminiModel, available.length ? available : sourceIds);
  const optionIds = Array.from(geminiModelInput.options || []).map(o => o.value);
  let selected = optionIds.includes(preferred) ? preferred : null;
  if (!selected && policyAllowed && policyAllowed.length) {
    selected = optionIds.find(id => policyAllowed.includes(id)) || optionIds[0];
  }
  if (!selected) {
    selected = optionIds.find(id => id.includes("gemini-3.5-flash"))
      || optionIds.find(id => id.includes("gemini-3.1-flash-lite"))
      || optionIds[0];
  }
  geminiModelInput.value = selected;
  geminiModel = normalizeGeminiModel(selected);
  geminiModelInput.disabled = !!(policyAllowed && policyAllowed.length <= 1 && !isAuthAdminUser());
}

function getAssignedGeminiModels() {
  try {
    const u = window.authState && window.authState.user;
    if (!u) return null;
    if (Array.isArray(u.allowed_models) && u.allowed_models.length) {
      return u.allowed_models.map(m => String(m || "").trim()).filter(Boolean);
    }
    if (u.preferred_model) return [String(u.preferred_model).trim()];
  } catch (e) {}
  return null;
}

function isAuthAdminUser() {
  try {
    return !!(window.authState && window.authState.user && window.authState.user.role === "admin");
  } catch (e) {
    return false;
  }
}

/** Local-only test key from Admin console (browser localStorage). Never used on AWS hosts. */
function refreshAdminTestGeminiKey() {
  geminiApiKey = "";
  try {
    const host = typeof location !== "undefined" ? location.hostname : "";
    const isLocal = !host || host === "localhost" || host === "127.0.0.1";
    // Deployed/AWS UI must use server GEMINI_API_KEY — ignore any leftover browser key.
    if (!isLocal) return;
    geminiApiKey = String(localStorage.getItem(LOCAL_TEST_GEMINI_STORAGE_KEY) || "").trim();
  } catch (e) {}
}

window.refreshAdminTestGeminiKey = refreshAdminTestGeminiKey;

function applyAssignedGeminiModels(allowedModels, preferredModelId, isAdmin) {
  const allowed = (allowedModels || []).map(m => String(m || "").trim()).filter(Boolean);
  if (!allowed.length || !geminiModelInput) return;
  if (window.authState && window.authState.user) {
    window.authState.user.allowed_models = allowed.slice();
    if (preferredModelId) window.authState.user.preferred_model = preferredModelId;
  }
  populateGeminiModelSelect(allowed, preferredModelId || allowed[0]);
  geminiModelInput.disabled = !isAdmin && allowed.length <= 1;
  saveGeminiSettings();
}

window.applyAssignedGeminiModels = applyAssignedGeminiModels;
window.getAssignedGeminiModels = getAssignedGeminiModels;

let savedGeminiSettings = null;
try {
  const rawGeminiSettings = localStorage.getItem(GEMINI_SETTINGS_KEY);
  if (rawGeminiSettings) {
    savedGeminiSettings = JSON.parse(rawGeminiSettings);
    if (savedGeminiSettings && savedGeminiSettings.model) {
      geminiModel = normalizeGeminiModel(savedGeminiSettings.model);
      if (geminiModel !== savedGeminiSettings.model) {
        savedGeminiSettings.model = geminiModel;
        localStorage.setItem(GEMINI_SETTINGS_KEY, JSON.stringify({
          model: geminiModel
        }));
      }
    }
    // Drop any legacy browser-stored API key — keys are server-managed only.
    if (savedGeminiSettings && savedGeminiSettings.apiKey) {
      delete savedGeminiSettings.apiKey;
      localStorage.setItem(GEMINI_SETTINGS_KEY, JSON.stringify({ model: geminiModel }));
    }
  }
} catch (e) {
  console.error("Failed to load saved Gemini settings", e);
}

function saveGeminiSettings() {
  try {
    localStorage.setItem(GEMINI_SETTINGS_KEY, JSON.stringify({
      model: geminiModel
    }));
  } catch (e) {}
}

function buildGeminiUrl(path) {
  return `https://generativelanguage.googleapis.com/v1beta/${path.replace(/^\//, "")}`;
}

function geminiFetchHeaders() {
  return {
    "Content-Type": "application/json",
    "x-goog-api-key": String(geminiApiKey || "").trim()
  };
}

function sleepMs(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// Parse Retry-After as delta-seconds or HTTP-date. Returns ms to wait (0 if absent/invalid).
function parseRetryAfterMs(retryAfterHeader) {
  if (!retryAfterHeader) return 0;
  const raw = String(retryAfterHeader).trim();
  if (!raw) return 0;

  // Delta-seconds (integer), including decimals some proxies send.
  if (/^\d+(\.\d+)?$/.test(raw)) {
    return Math.max(0, Math.round(parseFloat(raw) * 1000));
  }

  const asDate = Date.parse(raw);
  if (!isNaN(asDate)) {
    return Math.max(0, asDate - Date.now());
  }
  return 0;
}

// Gemini generateContent with 429-aware exponential backoff retries.
// Sleeps only inside THIS request's coroutine — other concurrent page workers keep running.
async function fetchGeminiGenerateContent(modelName, fetchBody, options = {}) {
  const timeoutMs = options.timeoutMs || 180000;
  const maxAttempts = options.maxAttempts || 5;
  const requestLabel = options.requestLabel || modelName;
  let lastError = null;

  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(buildGeminiUrl(`models/${modelName}:generateContent`), {
        method: "POST",
        headers: geminiFetchHeaders(),
        body: JSON.stringify(fetchBody),
        signal: controller.signal
      });

      // Tier 1 overload shows up as 503 more often than 429 — retry both the same way.
      if (response.status !== 429 && response.status !== 503) {
        return response;
      }

      // Drain body so the connection can be reused; do not block sibling workers.
      try { await response.text(); } catch (e) { /* ignore */ }

      const retryAfterHeader = response.headers.get("retry-after");
      const retryAfterMs = parseRetryAfterMs(retryAfterHeader);
      // Slightly longer base backoff for 503 overload vs pure 429.
      const base = response.status === 503 ? 1200 : 800;
      const backoffMs = Math.min(25000, base * Math.pow(2, attempt - 1)) + Math.floor(Math.random() * 300);
      const waitMs = Math.max(retryAfterMs, backoffMs);
      lastError = new Error(
        `Gemini API ${response.status === 503 ? "overloaded (503)" : "rate limited (429)"} ` +
        `on attempt ${attempt}/${maxAttempts}`
      );
      if (attempt < maxAttempts) {
        console.warn(
          `[${requestLabel}] ${lastError.message}. ` +
          `Retry-After=${retryAfterHeader || "n/a"} → pausing THIS request ${waitMs}ms ` +
          `(other concurrent pages continue).`
        );
        await sleepMs(waitMs);
        continue;
      }
      throw lastError;
    } catch (fetchErr) {
      if (fetchErr.name === "AbortError") {
        throw new Error("Gemini API took too long to respond (timeout). The page/image might be too complex.");
      }
      // Re-throw intentional rate-limit exhaustion from above.
      if (fetchErr === lastError && attempt >= maxAttempts) throw fetchErr;

      lastError = fetchErr;
      if (attempt < maxAttempts) {
        const waitMs = Math.min(12000, 500 * Math.pow(2, attempt - 1));
        console.warn(`[${requestLabel}] Gemini network error on attempt ${attempt}/${maxAttempts}: ${fetchErr.message}. Retrying this request in ${waitMs}ms...`);
        await sleepMs(waitMs);
        continue;
      }
      throw fetchErr;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  throw lastError || new Error("Gemini API request failed after retries.");
}

// Equipment Manifest state
let equipmentManifest = null;
let activeEquipmentCategory = "Default";

// Few-Shot Learned Patterns
let learnedPatterns = [];
try {
  const savedPatterns = localStorage.getItem("omniparse_learned_patterns");
  if (savedPatterns) {
    learnedPatterns = JSON.parse(savedPatterns);
  }
} catch (e) {
  console.error("Failed to load learned patterns", e);
}

async function fetchManifest() {
  try {
    const res = await fetch("equipment_manifest.json");
    if (res.ok) {
      equipmentManifest = await res.json();
      console.log("Equipment manifest loaded successfully:", equipmentManifest.version);
    } else {
      console.error("Failed to load equipment_manifest.json", res.status);
    }
  } catch (err) {
    console.warn("Error fetching equipment manifest (likely file:// CORS block), using fallback.", err);
    equipmentManifest = {
      categories: {
        "Default": { keywords: ["maintenance", "spare part"], partClasses: [] },
        "Logbook": { keywords: ["logbook", "shift", "repair", "history", "history card", "attended by"], partClasses: [] }
      }
    };
  }
}
fetchManifest();

// Settings DOM Elements
const cancelExtractBtn = document.getElementById("cancel-extract-btn");
const equipmentCategorySelect = document.getElementById("equipment-category");
const geminiModelInput = document.getElementById("gemini-model-select");

if (geminiModelInput) {
  populateGeminiModelSelect([], geminiModel || GEMINI_FALLBACK_MODEL);
}

// Settings event listeners
const EQUIPMENT_CATEGORY_STORAGE_KEY = "idp_equipment_category";

/** Show/hide a stat card by the id of its value element. */
function setStatCardVisible(valueElId, visible) {
  const el = document.getElementById(valueElId);
  const card = el && el.closest(".stat-card");
  if (card) card.style.display = visible ? "" : "none";
}

/**
 * Applies everything category-dependent in one place: parse strategy, table
 * headers, which registry tabs/stat cards are shown, and labels. Called on
 * category change and on page load (restored category).
 */
function applyEquipmentCategoryUi() {
  const isLogbook = activeEquipmentCategory === "Logbook";

  // Field history cards are scanned photos — force OCR Vision internally.
  parseStrategy = isLogbook ? "ocr" : "native";

  // Logbook extraction only produces history records, so the Spare Parts and
  // Troubleshooting registries would always be empty — hide them entirely.
  if (registryModeTabs) {
    registryModeTabs.querySelectorAll('[data-mode="spare_parts"], [data-mode="troubleshooting"]').forEach((btn) => {
      btn.style.display = isLogbook ? "none" : "";
    });
  }
  if (isLogbook && activeRegistryTab !== "maintenance") {
    selectRegistryTab("maintenance");
  }
  setStatCardVisible("count-parts", !isLogbook);
  setStatCardVisible("count-consumables", !isLogbook);
  setStatCardVisible("count-time", !isLogbook);
  setStatCardVisible("count-troubleshooting", !isLogbook);
  document.querySelectorAll(".logbook-stat").forEach((card) => {
    card.style.display = isLogbook ? "" : "none";
  });

  const rulesLabel = countRules && countRules.closest(".stat-info")
    ? countRules.closest(".stat-info").querySelector(".stat-label")
    : null;
  if (rulesLabel) rulesLabel.innerText = isLogbook ? "History Records" : "Maintenance Rules";

  const maintTabLabel = registryModeTabs
    ? registryModeTabs.querySelector('[data-mode="maintenance"] span')
    : null;
  if (maintTabLabel) maintTabLabel.innerText = isLogbook ? "Field History Records" : "Maintenance Tasks";

  const exportBtnLabel = document.querySelector("#export-btn span");
  if (exportBtnLabel) exportBtnLabel.innerText = isLogbook ? "Export Excel (Field History)" : "Export Excel (3 sheets)";

    const maintenanceHeaders = document.getElementById("maintenance-table-headers");
    if (maintenanceHeaders) {
    if (isLogbook) {
        maintenanceHeaders.innerHTML = `
          <th style="width: 60px;">ID</th>
          <th style="width: 150px;">Date</th>
          <th style="width: 300px;">Maintenance Work Description</th>
          <th style="width: 200px;">Parts Renewed</th>
          <th style="width: 150px;">Attended By</th>
          <th>Remarks</th>
        <th class="confidence-cell" style="width: 80px; text-align: center;">Confidence</th>
        <th class="score-reasons-cell" style="width: 220px;">Why low score</th>
          <th style="width: 70px;">Page</th>
          <th class="actions-col" style="width: 70px; text-align: center;">Actions</th>
        `;
      } else {
        maintenanceHeaders.innerHTML = `
          <th style="width: 60px;">ID</th>
          <th style="width: 150px;">Equipment Title</th>
          <th style="width: 200px;">Sub-system / Component</th>
          <th style="width: 150px;">Maintenance Routine</th>
          <th>Checks & Instructions</th>
        <th class="confidence-cell" style="width: 80px; text-align: center;">Confidence</th>
        <th class="score-reasons-cell" style="width: 220px;">Why low score</th>
          <th style="width: 70px;">Page</th>
          <th class="actions-col" style="width: 70px; text-align: center;">Actions</th>
        `;
      }
  }

  // Maintenance-interval filters don't apply to logbook rows.
  syncRegistryFilterTabs();
  updateDashboardMetrics();
  renderGrid();
}

if (equipmentCategorySelect) {
  equipmentCategorySelect.addEventListener("change", (e) => {
    activeEquipmentCategory = e.target.value;
    console.log("Switched equipment category to:", activeEquipmentCategory);
    try { localStorage.setItem(EQUIPMENT_CATEGORY_STORAGE_KEY, activeEquipmentCategory); } catch (_) {}

    if (activeEquipmentCategory === "Logbook") {
      appendChatSystemMessage(
        "ℹ️ Field History / Logbook: OCR Vision enabled automatically. Use a small From/To page range on CloudFront (origin timeout ~60s)."
      );
    }
    applyEquipmentCategoryUi();
  });

  // Restore the last selected category so a reload doesn't silently fall back
  // to the O&M Manual table while the user thinks Field History is active.
  try {
    const savedCategory = localStorage.getItem(EQUIPMENT_CATEGORY_STORAGE_KEY);
    if (savedCategory && equipmentCategorySelect.querySelector(`option[value="${savedCategory}"]`)) {
      activeEquipmentCategory = savedCategory;
      equipmentCategorySelect.value = savedCategory;
    }
  } catch (_) {}
  applyEquipmentCategoryUi();
}

if (geminiModelInput) {
  geminiModelInput.addEventListener("change", (e) => {
    const next = normalizeGeminiModel(e.target.value.trim());
    const allowed = getAssignedGeminiModels();
    if (allowed && allowed.length && !allowed.includes(next)) {
      const fallback = allowed[0];
      geminiModelInput.value = fallback;
      geminiModel = normalizeGeminiModel(fallback);
      appendChatSystemMessage(`Model **${next}** is not assigned to your account. Using **${fallback}**.`);
    saveGeminiSettings();
      return;
    }
    geminiModel = next;
    saveGeminiSettings();
  });
}

if (cancelExtractBtn) {
  cancelExtractBtn.addEventListener("click", () => {
    abortExtraction = true;
    appendChatSystemMessage("Extraction cancel requested. Halting parser...");
  });
}

// Helper to sanitize extracted field values to fallback to "NA" if empty or unavailable
function renderMarkdown(text) {
  if (!text) return "";
  let html = escapeHTML(text);
  
  // Bold: **text**
  html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  
  // Process lines for bullet points
  const lines = html.split(/\r?\n/);
  const processedLines = lines.map(line => {
    // Bullet points starting with * or -
    if (/^\s*[-*]\s+/.test(line)) {
      const p1 = line.replace(/^\s*[-*]\s+/, '');
      return `<span style="display:inline-block; padding-left:0.75rem; color:var(--accent-cyan); font-weight:500;">• ${p1}</span>`;
    }
    return line;
  });
  
  // Join lines with <br>
  return processedLines.join("<br>");
}

/* -------------------------------------------------------------
 * 1. UI Rendering Engine
 * ------------------------------------------------------------- */

/** True when a logbook cell holds real data, not a dash/nil placeholder. */
function hasMeaningfulValue(val) {
  const s = String(val || "").trim().toLowerCase();
  return !!s && !["-", "--", "—", "nil", "n/a", "na", "none"].includes(s);
}

/**
 * Parses the free-form dates found on scanned history cards.
 * Day-first (dd/mm/yyyy) is assumed for numeric dates, which is the format
 * used on field logbooks. Returns a timestamp or null.
 */
function parseLogbookDate(raw) {
  const s = String(raw || "").trim();
  if (!s) return null;
  const inRange = (t) => {
    const y = new Date(t).getFullYear();
    return y >= 1980 && y <= 2100 ? t : null;
  };
  // dd/mm/yyyy, dd-mm-yy, dd.mm.yyyy
  let m = s.match(/^(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2,4})$/);
  if (m) {
    const d = Number(m[1]);
    const mo = Number(m[2]);
    let y = Number(m[3]);
    if (y < 100) y += 2000;
    if (mo < 1 || mo > 12 || d < 1 || d > 31) return null;
    return inRange(new Date(y, mo - 1, d).getTime());
  }
  // yyyy-mm-dd
  m = s.match(/^(\d{4})[\/\-.](\d{1,2})[\/\-.](\d{1,2})/);
  if (m) return inRange(new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).getTime());
  // "12 May 2023", "May 2023", etc.
  const t = Date.parse(s);
  return Number.isNaN(t) ? null : inRange(t);
}

/** Fills the Field History stat cards from the logbook rows. */
function updateLogbookMetrics() {
  const logPartsEl = document.getElementById("count-log-parts");
  if (!logPartsEl) return;
  const rows = maintenanceRegistry;

  logPartsEl.innerText = rows.filter((r) => hasMeaningfulValue(r.parts_renewed)).length;

  const techs = new Set();
  rows.forEach((r) => {
    if (hasMeaningfulValue(r.attended_by)) techs.add(String(r.attended_by).trim().toLowerCase());
  });
  const techsEl = document.getElementById("count-log-techs");
  if (techsEl) techsEl.innerText = techs.size;

  const remarksEl = document.getElementById("count-log-remarks");
  if (remarksEl) remarksEl.innerText = rows.filter((r) => hasMeaningfulValue(r.remarks)).length;

  let latest = null;
  rows.forEach((r) => {
    const t = parseLogbookDate(r.date);
    if (t != null && (latest == null || t > latest)) latest = t;
  });
  const latestEl = document.getElementById("count-log-latest");
  if (latestEl) {
    latestEl.innerText = latest != null
      ? new Date(latest).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })
      : "—";
  }
}

function updateDashboardMetrics() {
  const rules = maintenanceRegistry.length;
  const parts = sparePartsRegistry.length;
  
  // Estimate consumables from spare parts
  const consumables = sparePartsRegistry.filter(r => 
    String(r.part_name || "").toLowerCase().includes("oil") || 
    String(r.part_name || "").toLowerCase().includes("grease") || 
    String(r.part_name || "").toLowerCase().includes("filter") || 
    String(r.part_name || "").toLowerCase().includes("seal") || 
    String(r.part_name || "").toLowerCase().includes("gasket") || 
    String(r.part_categorization || "").toLowerCase().includes("consumable")
  ).length;

  // Filter time-based rules
  const timeBased = maintenanceRegistry.filter(r => 
    String(r.maintenance_routine || "").toLowerCase().includes("hour") || 
    String(r.maintenance_routine || "").toLowerCase().includes("month") || 
    String(r.maintenance_routine || "").toLowerCase().includes("week") || 
    String(r.maintenance_routine || "").toLowerCase().includes("year") || 
    String(r.maintenance_routine || "").toLowerCase().includes("day") || 
    String(r.maintenance_routine || "").toLowerCase().includes("shift")
  ).length;

  countRules.innerText = rules;
  countParts.innerText = parts;
  countConsumables.innerText = consumables;
  countTime.innerText = timeBased;
  countTroubleshooting.innerText = troubleshootingRegistry.length;
  if (countOverallScore) {
    const score = lastExtractMeta && lastExtractMeta.overall_score;
    if (score != null && score !== "") {
      countOverallScore.innerText = `${Number(score).toFixed(1)}%`;
    } else {
      countOverallScore.innerText = "—";
    }
  }
  updateQualityScoreCard();
  updateLogbookMetrics();
}

/** Band for extraction quality: high (>80), mid (50–80), low (<50). */
function getQualityScoreBand(score) {
  if (score == null || score === "" || Number.isNaN(Number(score))) return null;
  const n = Number(score);
  if (n > 80) return "high";
  if (n >= 50) return "mid";
  return "low";
}

function updateQualityScoreCard() {
  const card = document.getElementById("card-quality-score");
  const icon = document.getElementById("quality-score-icon");
  if (!card) return;

  card.classList.remove("score-high", "score-mid", "score-low");
  if (icon) {
    icon.classList.remove("blue-glow", "green-glow", "amber-glow", "red-glow");
  }

  const score = lastExtractMeta && lastExtractMeta.overall_score;
  const band = getQualityScoreBand(score);
  if (!band) {
    if (icon) icon.classList.add("blue-glow");
    return;
  }

  card.classList.add(`score-${band}`);
  if (icon) {
    icon.classList.add(
      band === "high" ? "green-glow" : band === "mid" ? "amber-glow" : "red-glow"
    );
  }
}

function openQualityScoreModal() {
  const modal = document.getElementById("quality-score-modal");
  const valueEl = document.getElementById("quality-score-value");
  const levelEl = document.getElementById("quality-score-level");
  const msgEl = document.getElementById("quality-score-message");
  if (!modal || !valueEl || !levelEl || !msgEl) return;

  const score = lastExtractMeta && lastExtractMeta.overall_score;
  const band = getQualityScoreBand(score);
  levelEl.classList.remove("score-high", "score-mid", "score-low");

  if (band == null) {
    valueEl.textContent = "—";
    levelEl.textContent = "No score yet";
    msgEl.textContent =
      "Extraction quality appears after a document is processed. It reflects how complete and consistent the recovered records were for that run.";
  } else {
    const n = Number(score);
    valueEl.textContent = `${n.toFixed(1)}%`;
    if (band === "high") {
      levelEl.textContent = "Good";
      levelEl.classList.add("score-high");
      msgEl.textContent =
        "This run scored above 80%. The source document supported a solid extract. Review key rows before use — AI output can still miss or misread details.";
    } else if (band === "mid") {
      levelEl.textContent = "Fair";
      levelEl.classList.add("score-mid");
      msgEl.textContent =
        "This run scored between 50% and 80%. Some records were recovered, but the source layout or page quality may have limited completeness. Review the table carefully, or try a clearer page range.";
    } else {
      levelEl.textContent = "Needs attention";
      levelEl.classList.add("score-low");
      msgEl.textContent =
        "This run scored below 50%. The source file may be hard to parse (scanned pages, weak text, or dense layout), so few reliable records were recovered. Try a cleaner PDF, a smaller From/To range, or OCR for scanned pages, then extract again.";
    }
  }

  modal.hidden = false;
}

function closeQualityScoreModal() {
  const modal = document.getElementById("quality-score-modal");
  if (modal) modal.hidden = true;
}

let renderGridDebounceTimer = null;
const RENDER_GRID_DEBOUNCE_MS = 180;

function getGridScrollEl() {
  return document.querySelector(".grid-container");
}

function renderGridPreservingScroll() {
  const scrollEl = getGridScrollEl();
  const prevTop = scrollEl ? scrollEl.scrollTop : 0;
  const prevLeft = scrollEl ? scrollEl.scrollLeft : 0;
  renderGrid();
  if (scrollEl) {
    scrollEl.scrollTop = prevTop;
    scrollEl.scrollLeft = prevLeft;
  }
}

// Debounce rapid concurrent page completions so the UI doesn't thrash/freeze scroll.
function scheduleRenderGrid(immediate = false) {
  if (immediate) {
    if (renderGridDebounceTimer) {
      clearTimeout(renderGridDebounceTimer);
      renderGridDebounceTimer = null;
    }
    renderGridPreservingScroll();
    return;
  }
  if (renderGridDebounceTimer) clearTimeout(renderGridDebounceTimer);
  renderGridDebounceTimer = setTimeout(() => {
    renderGridDebounceTimer = null;
    renderGridPreservingScroll();
  }, RENDER_GRID_DEBOUNCE_MS);
}

function isMissingFieldValue(val) {
  const s = String(val || "").trim().toLowerCase();
  return !s || s === "na" || s === "n/a" || s === "null" || s === "undefined" || s === "-";
}

function matchesSparePartTypeFilter(row, filterKey) {
  if (!filterKey || filterKey === "all") return true;
  const cat = String(row.part_categorization || "").trim().toLowerCase();
  const missing = isMissingFieldValue(cat);
  if (filterKey === "unspecified") return missing;
  if (missing) return false;
  if (filterKey === "critical") return cat.includes("critical");
  if (filterKey === "consumable") return cat.includes("consumable");
  if (filterKey === "standard") return cat.includes("standard");
  return true;
}

function resetFilterTabActive(container, attrName, value) {
  if (!container) return;
  container.querySelectorAll(".tab-btn").forEach((btn) => {
    const isActive = (btn.getAttribute(attrName) || "all") === value;
    btn.classList.toggle("active", isActive);
  });
}

function syncRegistryFilterTabs() {
  const showMaint =
    activeRegistryTab === "maintenance" &&
    !(typeof activeEquipmentCategory !== "undefined" && activeEquipmentCategory === "Logbook");
  const showSpare = activeRegistryTab === "spare_parts";
  const showTrouble = activeRegistryTab === "troubleshooting";

  if (filterTabs) {
    const chatFilterChip = document.getElementById("chat-filter-chip");
    if (showMaint) {
      filterTabs.style.display = "flex";
      filterTabs.querySelectorAll("[data-filter]").forEach((btn) => {
        btn.style.display = "block";
      });
    } else if (!showSpare && !showTrouble && (highlightRecordIds.length > 0 || chatFilterChip)) {
      filterTabs.style.display = "flex";
      filterTabs.querySelectorAll("[data-filter]").forEach((btn) => {
        btn.style.display = "none";
      });
    } else {
      filterTabs.style.display = "none";
    }
  }
  if (spareFilterTabs) {
    spareFilterTabs.style.display = showSpare ? "flex" : "none";
  }
}

function renderGrid() {
  let filtered = [];

  if (activeRegistryTab === "maintenance") {
    maintenanceTableBody.innerHTML = "";
    
    filteredMaintenance = maintenanceRegistry.filter(row => {
      // 1. Status Filter
      if (currentStatusFilter !== "all") {
        const s = row.status || "Pending Review";
        if (s !== currentStatusFilter) return false;
      }

      // 2. Tab Filter
      if (currentTabFilter !== "all") {
        const routine = String(row.maintenance_routine || "").toLowerCase();
        if (currentTabFilter === "hours" && !routine.includes("hour")) return false;
        if (currentTabFilter === "days" && !routine.includes("day") && !routine.includes("shift") && !routine.includes("week")) return false;
        if (currentTabFilter === "months" && !routine.includes("month")) return false;
        if (currentTabFilter === "years" && !routine.includes("year")) return false;
      }
      
      // 3. Search Text Query
      if (currentSearchQuery) {
        const q = currentSearchQuery.toLowerCase();
        const matchText = activeEquipmentCategory === "Logbook"
          ? `${row.date} ${row.maintenance_work_description} ${row.parts_renewed} ${row.attended_by} ${row.remarks}`.toLowerCase()
          : `${row.equipment_title} ${row.subsystem_component} ${row.maintenance_routine} ${row.checks_instructions}`.toLowerCase();
        if (!matchText.includes(q)) return false;
      }

      // 4. Cognitive Chat Highlight Filter
      if (highlightRecordIds.length > 0) {
        if (!highlightRecordIds.includes(row.id)) return false;
      }
      
      return true;
    });
    filteredMaintenance = filterByConfidence(filteredMaintenance);
    filtered = filteredMaintenance;

    if (filtered.length === 0) {
      tableEmpty.style.display = "flex";
    } else {
      tableEmpty.style.display = "none";
      
      if (activeEquipmentCategory === "Logbook") {
        filtered.forEach(row => {
          const tr = document.createElement("tr");
          tr.setAttribute("data-id", row.id);
          if (isLowConfidenceRow(row)) tr.classList.add("row-low-confidence");
          
          tr.innerHTML = `
            ${renderIdCellWithDiff("maintenance", row)}
            ${renderCellWithDiff("maintenance", row, "date", escapeHTML(row.date || "NA"), "font-weight: 500;")}
            ${renderCellWithDiff("maintenance", row, "maintenance_work_description", escapeHTML(row.maintenance_work_description || "NA"), "white-space: normal; max-width: 300px;")}
            ${renderCellWithDiff("maintenance", row, "parts_renewed", escapeHTML(row.parts_renewed || "NA"), "font-weight: 500; font-family: monospace;")}
            ${renderCellWithDiff("maintenance", row, "attended_by", escapeHTML(row.attended_by || "NA"))}
            ${renderCellWithDiff("maintenance", row, "remarks", escapeHTML(row.remarks || "NA"), "white-space: normal;")}
            <td class="confidence-cell">${formatConfidenceCell(row)}</td>
            <td class="score-reasons-cell">${formatScoreReasonsCell(row)}</td>
            <td style="text-align: center;">${formatStatusCell(row)}</td>
            ${renderCellWithDiff("maintenance", row, "page", `Page ${row.page || "NA"}`, "text-align: center;", "page-cell")}
            <td class="row-actions">${formatRowActionsCell(row, "maintenance")}</td>
          `;
          maintenanceTableBody.appendChild(tr);
        });
      } else {
        filtered.forEach(row => {
          const tr = document.createElement("tr");
          tr.setAttribute("data-id", row.id);
          if (isLowConfidenceRow(row)) tr.classList.add("row-low-confidence");
          
          let tagClass = "tag-days";
          const routine = String(row.maintenance_routine || "").toLowerCase();
          if (routine.includes("hour")) tagClass = "tag-hours";
          if (routine.includes("month")) tagClass = "tag-months";
          if (routine.includes("year")) tagClass = "tag-years";

          tr.innerHTML = `
            ${renderIdCellWithDiff("maintenance", row)}
            ${renderCellWithDiff("maintenance", row, "equipment_title", escapeHTML(row.equipment_title || "NA"))}
            ${renderCellWithDiff("maintenance", row, "subsystem_component", escapeHTML(row.subsystem_component || "NA"), "font-weight: 500;")}
            ${renderCellWithDiff("maintenance", row, "maintenance_routine", `<span class="freq-tag ${tagClass}">${escapeHTML(row.maintenance_routine || "NA")}</span>`)}
            ${renderCellWithDiff("maintenance", row, "checks_instructions", escapeHTML(row.checks_instructions || "NA"), "white-space: normal; max-width: 350px;")}
            <td class="confidence-cell">${formatConfidenceCell(row)}</td>
            <td class="score-reasons-cell">${formatScoreReasonsCell(row)}</td>
            <td style="text-align: center;">${formatStatusCell(row)}</td>
            ${renderCellWithDiff("maintenance", row, "page", `Page ${row.page || "NA"}`, "text-align: center;", "page-cell")}
            <td class="row-actions">${formatRowActionsCell(row, "maintenance")}</td>
          `;
          maintenanceTableBody.appendChild(tr);
        });
      }
    }
  } else if (activeRegistryTab === "spare_parts") {
    // Spare Parts Tab
    sparePartsTableBody.innerHTML = "";
    
    filteredSpareParts = sparePartsRegistry.filter(row => {
      // 1. Status Filter
      if (currentStatusFilter !== "all") {
        const s = row.status || "Pending Review";
        if (s !== currentStatusFilter) return false;
      }

      // 2. Part-type filter tabs
      if (!matchesSparePartTypeFilter(row, currentSpareFilter)) return false;

      // 3. Search Text Query
      if (currentSearchQuery) {
        const q = currentSearchQuery.toLowerCase();
        const matchText = `${row.equipment_title} ${row.subsystem_location} ${row.item_no} ${row.part_name} ${row.part_number_code} ${row.drawing_model_no} ${row.oem_standard_body} ${row.part_categorization} ${row.quantity} ${row.frequency_of_use}`.toLowerCase();
        if (!matchText.includes(q)) return false;
      }

      // 4. Cognitive Chat Highlight Filter
      if (highlightRecordIds.length > 0) {
        if (!highlightRecordIds.includes(row.id)) return false;
      }
      
      return true;
    });
    filteredSpareParts = filterByConfidence(filteredSpareParts);
    filtered = filteredSpareParts;

    if (filtered.length === 0) {
      tableEmpty.style.display = "flex";
    } else {
      tableEmpty.style.display = "none";
      
      filtered.forEach(row => {
        const tr = document.createElement("tr");
        tr.setAttribute("data-id", row.id);
        if (isLowConfidenceRow(row)) tr.classList.add("row-low-confidence");

        tr.innerHTML = `
          ${renderIdCellWithDiff("spare_parts", row)}
          ${renderCellWithDiff("spare_parts", row, "equipment_title", escapeHTML(row.equipment_title || "NA"))}
          ${renderCellWithDiff("spare_parts", row, "subsystem_location", escapeHTML(row.subsystem_location || "NA"))}
          ${renderCellWithDiff("spare_parts", row, "item_no", escapeHTML(row.item_no || "NA"), "font-family: monospace;")}
          ${renderCellWithDiff("spare_parts", row, "part_name", escapeHTML(row.part_name || "NA"), "font-weight: 500;")}
          ${renderCellWithDiff("spare_parts", row, "part_number_code", escapeHTML(row.part_number_code || "NA"), "font-family: monospace; color: var(--accent-cyan);")}
          ${renderCellWithDiff("spare_parts", row, "drawing_model_no", escapeHTML(row.drawing_model_no || "NA"), "font-family: monospace;")}
          ${renderCellWithDiff("spare_parts", row, "oem_standard_body", escapeHTML(row.oem_standard_body || "NA"))}
          ${renderCellWithDiff("spare_parts", row, "part_categorization", `<span class="freq-tag tag-parts">${escapeHTML(row.part_categorization || "NA")}</span>`, "color: var(--accent-amber); font-weight: 500;")}
          ${renderCellWithDiff("spare_parts", row, "quantity", escapeHTML(row.quantity || "NA"), "font-weight: 600; text-align: center; color: var(--text-main);")}
          ${renderCellWithDiff("spare_parts", row, "recommended_stock_qty", escapeHTML(row.recommended_stock_qty || "NA"), "font-weight: 600; text-align: center; color: var(--accent-green);")}
          ${renderCellWithDiff("spare_parts", row, "warranty_period", escapeHTML(row.warranty_period || "NA"))}
          ${renderCellWithDiff("spare_parts", row, "frequency_of_use", escapeHTML(row.frequency_of_use || "NA"), "text-align: center;")}
          <td class="confidence-cell">${formatConfidenceCell(row)}</td>
          <td class="score-reasons-cell">${formatScoreReasonsCell(row)}</td>
          <td style="text-align: center;">${formatStatusCell(row)}</td>
          ${renderCellWithDiff("spare_parts", row, "page", `Page ${row.page || "NA"}`, "text-align: center;", "page-cell")}
          <td class="row-actions">${formatRowActionsCell(row, "spare_parts")}</td>
        `;
        sparePartsTableBody.appendChild(tr);
      });
    }
  } else if (activeRegistryTab === "troubleshooting") {
    // Troubleshooting Tab
    troubleshootingTableBody.innerHTML = "";
    
    filteredTroubleshooting = troubleshootingRegistry.filter(row => {
      // 1. Status Filter
      if (currentStatusFilter !== "all") {
        const s = row.status || "Pending Review";
        if (s !== currentStatusFilter) return false;
      }

      // 2. Search Text Query
      if (currentSearchQuery) {
        const q = currentSearchQuery.toLowerCase();
        const matchText = `${row.equipment_title} ${row.subsystem_component} ${row.problem} ${row.root_cause_solution}`.toLowerCase();
        if (!matchText.includes(q)) return false;
      }

      // 3. Cognitive Chat Highlight Filter
      if (highlightRecordIds.length > 0) {
        if (!highlightRecordIds.includes(row.id)) return false;
      }
      
      return true;
    });
    filteredTroubleshooting = filterByConfidence(filteredTroubleshooting);
    filtered = filteredTroubleshooting;

    if (filtered.length === 0) {
      tableEmpty.style.display = "flex";
    } else {
      tableEmpty.style.display = "none";
      
      filtered.forEach(row => {
        const tr = document.createElement("tr");
        tr.setAttribute("data-id", row.id);
        if (isLowConfidenceRow(row)) tr.classList.add("row-low-confidence");

        tr.innerHTML = `
          ${renderIdCellWithDiff("troubleshooting", row)}
          ${renderCellWithDiff("troubleshooting", row, "equipment_title", escapeHTML(row.equipment_title || "NA"))}
          ${renderCellWithDiff("troubleshooting", row, "subsystem_component", escapeHTML(row.subsystem_component || "NA"), "font-weight: 500;")}
          ${renderCellWithDiff("troubleshooting", row, "problem", escapeHTML(row.problem || "NA"), "color: var(--accent-amber); font-weight: 500; white-space: normal;")}
          ${renderCellWithDiff("troubleshooting", row, "root_cause_solution", escapeHTML(row.root_cause_solution || "NA"), "white-space: normal;")}
          <td class="confidence-cell">${formatConfidenceCell(row)}</td>
          <td class="score-reasons-cell">${formatScoreReasonsCell(row)}</td>
          <td style="text-align: center;">${formatStatusCell(row)}</td>
          ${renderCellWithDiff("troubleshooting", row, "page", `Page ${row.page || "NA"}`, "text-align: center;", "page-cell")}
          <td class="row-actions">${formatRowActionsCell(row, "troubleshooting")}</td>
        `;
        troubleshootingTableBody.appendChild(tr);
      });
    }
  }

  // Handle visibility of filter tab containers
  const chatFilterChip = document.getElementById("chat-filter-chip");
  if (highlightRecordIds.length === 0 && chatFilterChip) {
    chatFilterChip.remove();
  }
  syncRegistryFilterTabs();
  
  updateDiffToolbarButtons();
  if (typeof updateRoleActionButtons === "function") {
    updateRoleActionButtons();
  }
  safeCreateIcons();
  attachTableListeners();
  updateDashboardMetrics();
}

function escapeHTML(str) {
  if (!str) return '';
  return str.toString()
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

/* -------------------------------------------------------------
 * 2. In-Line Grid Editing
 * ------------------------------------------------------------- */

function attachTableListeners() {
  // Cell double click editing
  const editables = document.querySelectorAll(".data-table td.editable");
  editables.forEach(cell => {
    cell.addEventListener("dblclick", function() {
      if (!canEditRecords()) return; // Viewers are read-only
      if (this.classList.contains("editing")) return;
      
      const col = this.getAttribute("data-col");
      const tr = this.closest("tr");
      const id = parseInt(tr.getAttribute("data-id"));
      const originalValue = this.innerText.replace("Page ", "");
      
      this.classList.add("editing");
      const input = document.createElement("input");
      input.type = "text";
      input.value = originalValue;
      this.innerHTML = "";
      this.appendChild(input);
      input.focus();
      
      const saveEdit = () => {
        let newValue = input.value.trim();
        this.classList.remove("editing");
        
        let record;
        if (activeRegistryTab === "maintenance") {
          record = maintenanceRegistry.find(r => r.id === id);
        } else if (activeRegistryTab === "spare_parts") {
          record = sparePartsRegistry.find(r => r.id === id);
        } else if (activeRegistryTab === "troubleshooting") {
          record = troubleshootingRegistry.find(r => r.id === id);
        }
        
        if (record) {
          if (col === "page") {
            newValue = parseInt(newValue) || "NA";
          }
          record[col] = newValue;
          
          // Self-Learning Loop: Save corrected record to learnedPatterns
          const patternToLearn = { ...record };
          delete patternToLearn.id;
          
          learnedPatterns.unshift({ type: activeRegistryTab, record: patternToLearn });
          if (learnedPatterns.length > 10) learnedPatterns.pop();
          
          try {
            localStorage.setItem("omniparse_learned_patterns", JSON.stringify(learnedPatterns));
          } catch(e) {}

          if (activeDocumentStatus !== "Approved" || !canApproveOrSignOff()) {
            activeDocumentStatus = "In Review";
            updateDocMetadataBadge();
            if (typeof updateRoleActionButtons === "function") updateRoleActionButtons();
          }
          if (typeof triggerAutoSaveDebounce === "function") {
            triggerAutoSaveDebounce();
          }
        }
        renderGrid();
      };
      
      input.addEventListener("keydown", function(e) {
        if (e.key === "Enter") saveEdit();
        if (e.key === "Escape") {
          input.value = originalValue;
          saveEdit();
        }
      });
      
      input.addEventListener("blur", saveEdit);
    });
  });

  // Approve row button click
  const approveBtns = document.querySelectorAll(".data-table .btn-approve");
  approveBtns.forEach(btn => {
    btn.addEventListener("click", function(e) {
      e.stopPropagation();
      const tr = this.closest("tr");
      const id = parseInt(tr.getAttribute("data-id"));
      const reg = this.getAttribute("data-reg") || activeRegistryTab;
      approveRow(reg, id);
    });
  });

  // Reject row button click
  const rejectBtns = document.querySelectorAll(".data-table .btn-reject");
  rejectBtns.forEach(btn => {
    btn.addEventListener("click", function(e) {
      e.stopPropagation();
      const tr = this.closest("tr");
      const id = parseInt(tr.getAttribute("data-id"));
      const reg = this.getAttribute("data-reg") || activeRegistryTab;
      openRejectionModal(reg, id);
    });
  });

  // Delete row button click
  const deleteBtns = document.querySelectorAll(".data-table .btn-delete");
  deleteBtns.forEach(btn => {
    btn.addEventListener("click", function(e) {
      e.stopPropagation();
      if (!canEditRecords()) return;
      const tr = this.closest("tr");
      const id = parseInt(tr.getAttribute("data-id"));
      if (activeRegistryTab === "maintenance") {
        maintenanceRegistry = maintenanceRegistry.filter(r => r.id !== id);
      } else if (activeRegistryTab === "spare_parts") {
        sparePartsRegistry = sparePartsRegistry.filter(r => r.id !== id);
      } else if (activeRegistryTab === "troubleshooting") {
        troubleshootingRegistry = troubleshootingRegistry.filter(r => r.id !== id);
      }
      if (selectedRegistryRowId === id) clearSelectedRegistryRow();
      renderGrid();
    });
  });

  // Single-click selects a row for "Ask Copilot about this row"
  document.querySelectorAll(".data-table tbody tr[data-id]").forEach(tr => {
    tr.addEventListener("click", function(e) {
      if (e.target.closest(".row-btn") || e.target.closest("td.editing") || e.target.closest("input")) return;
      const id = parseInt(this.getAttribute("data-id"), 10);
      if (!Number.isFinite(id)) return;
      selectRegistryRow(id, this);
    });
  });

  if (selectedRegistryRowId != null) {
    const selectedTr = document.querySelector(`.data-table tr[data-id="${selectedRegistryRowId}"]`);
    if (selectedTr) selectedTr.classList.add("row-selected");
    else clearSelectedRegistryRow();
  }
  updateAskSelectedBar();
}

// Add Custom Record
if (addRowBtn) {
addRowBtn.addEventListener("click", () => {
  if (!canEditRecords()) {
    alert("Permission Denied: Viewers cannot add records.");
    return;
  }
  let newId;
  if (activeRegistryTab === "maintenance") {
    newId = maintenanceRegistry.length > 0 ? Math.max(...maintenanceRegistry.map(r => r.id)) + 1 : 1;
    const newRow = activeEquipmentCategory === "Logbook" ? {
      id: newId,
      date: "NA",
      maintenance_work_description: "Maintenance Work Description",
      parts_renewed: "NA",
      attended_by: "NA",
      remarks: "NA",
      page: "NA",
      status: "Pending Review"
    } : {
      id: newId,
      equipment_title: "Equipment Title",
      subsystem_component: "Sub-system / Component",
      maintenance_routine: "Monthly",
      checks_instructions: "Required Maintenance Checks / Instructions",
      page: "NA",
      status: "Pending Review"
    };
    maintenanceRegistry.unshift(newRow);
  } else if (activeRegistryTab === "spare_parts") {
    newId = sparePartsRegistry.length > 0 ? Math.max(...sparePartsRegistry.map(r => r.id)) + 1 : 1;
    const newRow = {
      id: newId,
      equipment_title: "Equipment Title",
      subsystem_location: "Component Location",
      item_no: "NA",
      part_name: "Part Name / Description",
      part_number_code: "Part Number",
      drawing_model_no: "Drawing Number",
      oem_standard_body: "OEM Standard",
      part_categorization: "Critical Spare",
      quantity: "1",
      recommended_stock_qty: "1",
      warranty_period: "NA",
      frequency_of_use: "NA",
      page: "NA",
      status: "Pending Review"
    };
    sparePartsRegistry.unshift(newRow);
  } else if (activeRegistryTab === "troubleshooting") {
    newId = troubleshootingRegistry.length > 0 ? Math.max(...troubleshootingRegistry.map(r => r.id)) + 1 : 1;
    const newRow = {
      id: newId,
      equipment_title: "Equipment Title",
      subsystem_component: "Sub-system / Component",
      problem: "Problem Description",
      root_cause_solution: "Root Cause / Solution",
      page: "NA",
      status: "Pending Review"
    };
    troubleshootingRegistry.unshift(newRow);
  }
  
  renderGrid();
  
  // Automatically open edit on the first column of the newly inserted row
  setTimeout(() => {
    let tableId = "maintenance-table";
    if (activeRegistryTab === "spare_parts") tableId = "spare-parts-table";
    else if (activeRegistryTab === "troubleshooting") tableId = "troubleshooting-table";
    const firstCell = document.querySelector(`#${tableId} tr[data-id="${newId}"] td.editable`);
    if (firstCell) {
      const event = new MouseEvent('dblclick', { bubbles: true, cancelable: true });
      firstCell.dispatchEvent(event);
    }
  }, 50);
});
}

// Search grid bar
if (gridSearch) {
gridSearch.addEventListener("input", (e) => {
  currentSearchQuery = e.target.value;
  highlightRecordIds = []; // clear AI search highlights when manual filtering
  renderGrid();
});
}
if (confidenceFilter) {
  confidenceFilter.addEventListener("change", (e) => {
    handleConfidenceFilterChange(e.target.value);
  });
}

// Filter Tabs — Maintenance intervals
if (filterTabs) {
filterTabs.addEventListener("click", (e) => {
  const tab = e.target.closest(".tab-btn");
  if (!tab) return;
  
    filterTabs.querySelectorAll(".tab-btn").forEach((btn) => btn.classList.remove("active"));
  tab.classList.add("active");
    currentTabFilter = tab.getAttribute("data-filter") || "all";
  highlightRecordIds = []; // clear AI highlights
  renderGrid();
});
}

// Filter Tabs — Spare part types
if (spareFilterTabs) {
  spareFilterTabs.addEventListener("click", (e) => {
    const tab = e.target.closest(".tab-btn");
    if (!tab) return;

    spareFilterTabs.querySelectorAll(".tab-btn").forEach((btn) => btn.classList.remove("active"));
    tab.classList.add("active");
    currentSpareFilter = tab.getAttribute("data-spare-filter") || "all";
    highlightRecordIds = [];
    renderGrid();
  });
}

/* -------------------------------------------------------------
 * 3. SheetJS High-Fidelity Excel Export (3 sheets in one workbook)
 * ------------------------------------------------------------- */

let lastSourceDocName = "";

function sanitizeExportBaseName(filename) {
  const base = String(filename || "document").replace(/\.[^/.]+$/, "");
  const cleaned = base
    .replace(/[<>:"/\\|?*\u0000-\u001F]/g, "_")
    .replace(/\s+/g, " ")
    .trim();
  return cleaned || "document";
}

function orderRowsForExport(rows) {
  return [...rows]
    .map((row, idx) => ({ row, idx }))
    .sort((a, b) => comparePdfRowOrder(a.row, b.row, a.idx, b.idx))
    .map(d => d.row);
}

function buildCoverSheetData() {
  const meta = activeDocumentMetadata || {};
  return {
    data: [
      { "Property": "Document Title", "Value": meta.title || lastSourceDocName || "NA" },
      { "Property": "OEM / Manufacturer", "Value": meta.oem_manufacturer || "NA" },
      { "Property": "Equipment Model / Series", "Value": meta.equipment_model || "NA" },
      { "Property": "Equipment Classification", "Value": meta.equipment_type || activeEquipmentCategory || "NA" },
      { "Property": "Document Version", "Value": meta.document_version || "NA" },
      { "Property": "Publication Date", "Value": meta.publication_date || "NA" },
      { "Property": "Document Sign-Off Status", "Value": activeDocumentStatus || "Pending Review" },
      { "Property": "Approved By", "Value": activeApprovedBy || (activeDocumentStatus === "Approved" ? "Authorized Reviewer" : "Pending Sign-Off") },
      { "Property": "Approval Timestamp", "Value": activeApprovedAt || "NA" },
      { "Property": "Overall Extraction Quality", "Value": lastExtractMeta && lastExtractMeta.overall_score != null ? `${Math.round(lastExtractMeta.overall_score)}%` : "100%" },
      { "Property": "Export Timestamp", "Value": new Date().toISOString() },
      { "Property": "Total Maintenance Records", "Value": maintenanceRegistry.length },
      { "Property": "Total Spare Parts Records", "Value": sparePartsRegistry.length },
      { "Property": "Total Troubleshooting Records", "Value": troubleshootingRegistry.length },
    ],
    cols: [{ wch: 32 }, { wch: 60 }]
  };
}

function buildMaintenanceExportRows(rows) {
  const ordered = orderRowsForExport(rows);
  if (activeEquipmentCategory === "Logbook") {
    return {
      data: ordered.map((r, idx) => ({
        "Record ID": `#${idx + 1}`,
        "Date": r.date || "NA",
        "Maintenance Work Description": r.maintenance_work_description || "NA",
        "Parts Renewed": r.parts_renewed || "NA",
        "Attended By": r.attended_by || "NA",
        "Remarks": r.remarks || "NA",
        "Confidence": formatConfidencePercent(r),
        "Review Status": r.status || "Pending Review",
        "Reviewed By": r.reviewed_by || "NA",
        "Rejection Reason": r.rejection_reason || "NA",
        "Source Page Reference": r.page === "NA" || r.page == null ? "NA" : `Page ${r.page}`
      })),
      cols: [
        { wch: 10 }, { wch: 15 }, { wch: 45 }, { wch: 25 },
        { wch: 20 }, { wch: 45 }, { wch: 12 }, { wch: 16 }, { wch: 24 }, { wch: 30 }, { wch: 15 }
      ]
    };
  }
  return {
    data: ordered.map((r, idx) => ({
      "Record ID": `#${idx + 1}`,
      "Equipment Title": r.equipment_title || "NA",
      "Sub-system / Component": r.subsystem_component || "NA",
      "Maintenance Routine / Interval": r.maintenance_routine || "NA",
      "Required Maintenance Checks / Instructions": r.checks_instructions || "NA",
      "Confidence": formatConfidencePercent(r),
      "Review Status": r.status || "Pending Review",
      "Reviewed By": r.reviewed_by || "NA",
      "Rejection Reason": r.rejection_reason || "NA",
      "Source Page Reference": r.page === "NA" || r.page == null ? "NA" : `Page ${r.page}`
    })),
    cols: [
      { wch: 10 }, { wch: 22 }, { wch: 28 }, { wch: 25 }, { wch: 65 }, { wch: 12 }, { wch: 16 }, { wch: 24 }, { wch: 30 }, { wch: 15 }
    ]
  };
}

function buildSparePartsExportRows(rows) {
  // Final safety: export in PDF order even if registry was edited/unsorted.
  const ordered = orderRowsForExport(rows);
  return {
    data: ordered.map((r, idx) => ({
      "Record ID": `#${idx + 1}`,
      "PDF Sequence": idx + 1,
      "Equipment Title": r.equipment_title || "NA",
      "Sub-system / Component Location": r.subsystem_location || "NA",
      "Item No.": r.item_no || "NA",
      "Part Name / Description": r.part_name || "NA",
      "Manufacturer Part Number / Code": r.part_number_code || "NA",
      "Drawing / Model Number": r.drawing_model_no || "NA",
      "OEM / Standard Body": r.oem_standard_body || "NA",
      "Part Categorization": r.part_categorization || "NA",
      "Quantity": r.quantity || "NA",
      "Recommended Stock QTY": r.recommended_stock_qty || "NA",
      "Warranty Period": r.warranty_period || "NA",
      "Frequency of Use": r.frequency_of_use || "NA",
      "Confidence": formatConfidencePercent(r),
      "Review Status": r.status || "Pending Review",
      "Reviewed By": r.reviewed_by || "NA",
      "Rejection Reason": r.rejection_reason || "NA",
      "Source Page Reference": r.page === "NA" || r.page == null ? "NA" : `Page ${r.page}`
    })),
    cols: [
      { wch: 10 }, { wch: 12 }, { wch: 22 }, { wch: 28 }, { wch: 10 }, { wch: 28 },
      { wch: 25 }, { wch: 22 }, { wch: 20 }, { wch: 20 }, { wch: 12 },
      { wch: 15 }, { wch: 15 }, { wch: 22 }, { wch: 12 }, { wch: 16 }, { wch: 24 }, { wch: 30 }, { wch: 15 }
    ]
  };
}

function buildTroubleshootingExportRows(rows) {
  const ordered = orderRowsForExport(rows);
  return {
    data: ordered.map((r, idx) => ({
      "Record ID": `#${idx + 1}`,
      "Equipment Title": r.equipment_title || "NA",
      "Sub-system / Component": r.subsystem_component || "NA",
      "Problem / Symptom": r.problem || "NA",
      "Root Cause / Solution": r.root_cause_solution || "NA",
      "Confidence": formatConfidencePercent(r),
      "Review Status": r.status || "Pending Review",
      "Reviewed By": r.reviewed_by || "NA",
      "Rejection Reason": r.rejection_reason || "NA",
      "Source Page Reference": r.page === "NA" || r.page == null ? "NA" : `Page ${r.page}`
    })),
    cols: [
      { wch: 10 }, { wch: 22 }, { wch: 28 }, { wch: 35 }, { wch: 65 }, { wch: 12 }, { wch: 16 }, { wch: 24 }, { wch: 30 }, { wch: 15 }
    ]
  };
}

function appendSheetOrEmpty(wb, sheetName, built) {
  const rows = (built.data && built.data.length > 0)
    ? built.data
    : [{ Note: "No records extracted for this category." }];
  const ws = XLSX.utils.json_to_sheet(rows);
  if (built.data && built.data.length > 0 && built.cols) {
    ws["!cols"] = built.cols;
  } else {
    ws["!cols"] = [{ wch: 50 }];
  }
  // Excel sheet names max 31 chars
  XLSX.utils.book_append_sheet(wb, ws, sheetName.slice(0, 31));
}

function buildCombinedWorkbook({ useFiltered = false } = {}) {
  const maintRows = useFiltered ? filteredMaintenance : maintenanceRegistry;

  const wb = XLSX.utils.book_new();
  appendSheetOrEmpty(wb, "Overview & Sign-Off", buildCoverSheetData());

  if (activeEquipmentCategory === "Logbook") {
    // Field history only produces logbook records — one sheet, no empty extras.
    appendSheetOrEmpty(wb, "Field History", buildMaintenanceExportRows(maintRows));
    return wb;
  }

  const partsRows = useFiltered ? filteredSpareParts : sparePartsRegistry;
  const troubleRows = useFiltered ? filteredTroubleshooting : troubleshootingRegistry;
  appendSheetOrEmpty(wb, "Maintenance Tasks", buildMaintenanceExportRows(maintRows));
  appendSheetOrEmpty(wb, "Spare Parts", buildSparePartsExportRows(partsRows));
  appendSheetOrEmpty(wb, "Troubleshooting", buildTroubleshootingExportRows(troubleRows));
  return wb;
}

function getExportFileName(sourceFileName) {
  return `${sanitizeExportBaseName(sourceFileName || lastSourceDocName || "document")}.xlsx`;
}

function exportCombinedWorkbook(sourceFileName, { ask = false, useFiltered = false } = {}) {
  if (typeof XLSX === "undefined" || !XLSX.utils) {
    alert("Excel library (SheetJS) failed to load. Refresh the page and try again.");
    return false;
  }

  const total =
    (useFiltered ? filteredMaintenance.length : maintenanceRegistry.length) +
    (useFiltered ? filteredSpareParts.length : sparePartsRegistry.length) +
    (useFiltered ? filteredTroubleshooting.length : troubleshootingRegistry.length);

  if (total === 0) {
    alert("No records to export yet.");
    return false;
  }

  const isLogbook = activeEquipmentCategory === "Logbook";
  const filename = getExportFileName(sourceFileName);
  if (ask) {
    const sheetsInfo = isLogbook
      ? `One workbook with 1 sheet:\n• Field History`
      : `One workbook with 3 sheets:\n` +
        `• Maintenance Tasks\n` +
        `• Spare Parts\n` +
        `• Troubleshooting`;
    const ok = confirm(
      `Save extraction results to your computer?\n\n` +
      `Excel file: ${filename}\n\n` +
      `${sheetsInfo}\n\n` +
      `Using the uploaded document name avoids mixed/duplicate generic Excel names.`
    );
    if (!ok) return false;
  }

  const wb = buildCombinedWorkbook({ useFiltered });
    XLSX.writeFile(wb, filename);
  appendChatSystemMessage(
    isLogbook
      ? `Saved Excel workbook **${filename}** with the **Field History** sheet.`
      : `Saved Excel workbook **${filename}** with **Maintenance**, **Spare Parts**, and **Troubleshooting** sheets.`
  );
  return true;
}

function offerSaveExcelAfterExtraction(fileOrName) {
  const name = (fileOrName && fileOrName.name) ? fileOrName.name : (fileOrName || lastSourceDocName || "document");
  // Let the progress overlay close / grid paint first, then ask.
  setTimeout(() => {
    exportCombinedWorkbook(name, { ask: true, useFiltered: false });
  }, 500);
}

if (exportBtn) {
  exportBtn.addEventListener("click", () => {
    // Manual export: full workbook (1 sheet for Field History, 3 for manuals).
    exportCombinedWorkbook(lastSourceDocName || "OmniParse_Export", {
      ask: false,
      useFiltered: false
    });
  });
}
/* -------------------------------------------------------------
 * 4. SharePoint library picker (no local PC upload)
 * ------------------------------------------------------------- */

function formatSharePointSize(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function closeSharePointPopover() {
  const popover = document.getElementById("page-range-row");
  const menuBtn = document.getElementById("upload-menu-btn");
  if (popover) popover.hidden = true;
  if (menuBtn) menuBtn.setAttribute("aria-expanded", "false");
}

let sharePointBreadcrumbTrail = [
  { id: null, name: "Project Root" }
];
let currentSharePointFolderId = null;
let currentSharePointParentId = null;

function renderSharePointBreadcrumbs(trail) {
  const breadcrumbEl = document.getElementById("sharepoint-breadcrumb");
  if (!breadcrumbEl) return;
  
  if (!Array.isArray(trail) || !trail.length) {
    trail = [{ id: null, name: "Project Root" }];
  }

  const parts = trail.map((crumb, idx) => {
    const isLast = idx === trail.length - 1;
    const cleanName = escapeHTML(crumb.name || (idx === 0 ? "Project Root" : "Folder"));
    
    if (isLast && trail.length > 1) {
      return `
        <span class="sp-crumb-current" title="${cleanName}">
          ${cleanName}
        </span>
      `;
    } else if (isLast && trail.length === 1) {
      return `
        <button type="button" class="sp-crumb-btn" data-index="0" data-folder-id="" title="Project Root" style="color: #38bdf8; font-weight: 600;">
          <i data-lucide="folder"></i>
          <span>Project Root</span>
        </button>
      `;
    } else {
      return `
        <button type="button" class="sp-crumb-btn" data-index="${idx}" data-folder-id="${escapeHTML(crumb.id || '')}" title="Jump to ${cleanName}">
          ${idx === 0 ? '<i data-lucide="folder"></i>' : ''}
          <span>${cleanName}</span>
        </button>
      `;
    }
  });

  breadcrumbEl.innerHTML = parts.join('<span class="sp-crumb-sep">/</span>');
  if (typeof safeCreateIcons === "function") safeCreateIcons();
}

async function loadSharePointFiles(targetFolderId = null, targetFolderName = null, isNavigatingUp = false, skipTrailUpdate = false) {
  if (!sharepointFilesList) return;
  sharepointFilesList.innerHTML = `
    <div class="sp-loading-container">
      <div class="sp-spinner-ring"></div>
      <p class="sp-loading-text">Loading SharePoint directory…</p>
    </div>
  `;

  try {
    if (typeof window.requireAuthForApi === "function") window.requireAuthForApi();
  } catch (e) {
    sharepointFilesList.innerHTML = `<p class="sharepoint-files-error">Sign in required to list SharePoint files.</p>`;
    return;
  }

  currentSharePointFolderId = targetFolderId || null;
  const authHeaders = (typeof window.getAuthHeaders === "function") ? window.getAuthHeaders() : {};
  let resp;
  const url = targetFolderId 
    ? `${apiBaseUrl}/api/integrations/sharepoint/files?folder_id=${encodeURIComponent(targetFolderId)}&_t=${Date.now()}`
    : `${apiBaseUrl}/api/integrations/sharepoint/files?_t=${Date.now()}`;

  try {
    resp = await fetch(url, { headers: authHeaders });
  } catch (err) {
    sharepointFilesList.innerHTML = `<p class="sharepoint-files-error">Could not reach API: ${escapeHTML(String(err.message || err))}</p>`;
    return;
  }

  if (!resp.ok) {
    let detail = "";
    try {
      const errJson = await resp.json();
      detail = errJson.detail || JSON.stringify(errJson);
    } catch (e) {
      detail = await resp.text();
    }
    sharepointFilesList.innerHTML = `<p class="sharepoint-files-error">${escapeHTML(detail || `HTTP ${resp.status}`)}</p>`;
    return;
  }

  const payload = await resp.json();
  if (payload && payload.configured === false) {
    sharepointFilesList.innerHTML = `<p class="sharepoint-files-error">SharePoint is not configured on the API. Set AZURE_* and SHAREPOINT_DRIVE_ID in backend/.env.</p>`;
    return;
  }

  const files = (payload && payload.files) || [];
  const folders = (payload && payload.folders) || [];
  const currFolder = payload && payload.current_folder;
  currentSharePointParentId = (payload && payload.parent_folder_id) || null;

  // Synchronize Breadcrumb Trail
  const resolvedName = currFolder ? currFolder.name : (targetFolderName || "Project Root");
  if (!skipTrailUpdate) {
    if (!targetFolderId) {
      sharePointBreadcrumbTrail = [{ id: null, name: "Project Root" }];
    } else {
      const existingIdx = sharePointBreadcrumbTrail.findIndex(c => c.id === targetFolderId);
      if (existingIdx >= 0) {
        sharePointBreadcrumbTrail = sharePointBreadcrumbTrail.slice(0, existingIdx + 1);
        sharePointBreadcrumbTrail[existingIdx].name = resolvedName;
      } else {
        sharePointBreadcrumbTrail.push({ id: targetFolderId, name: resolvedName });
      }
    }
  } else if (sharePointBreadcrumbTrail.length && targetFolderId) {
    sharePointBreadcrumbTrail[sharePointBreadcrumbTrail.length - 1].name = resolvedName;
  }

  renderSharePointBreadcrumbs(sharePointBreadcrumbTrail);

  // Update Back Button state
  const backBtn = document.getElementById("sharepoint-back-btn") || document.getElementById("sharepoint-up-btn");
  if (backBtn) {
    if (sharePointBreadcrumbTrail.length > 1) {
      const parentCrumb = sharePointBreadcrumbTrail[sharePointBreadcrumbTrail.length - 2];
      backBtn.hidden = false;
      backBtn.title = `Go back to ${parentCrumb.name || "parent folder"}`;
    } else {
      backBtn.hidden = true;
    }
  }

  if (!files.length && !folders.length) {
    sharepointFilesList.innerHTML = `
      <div style="padding: 1.5rem 1rem; text-align: center; color: var(--text-muted);">
        <i data-lucide="folder-open" style="width: 28px; height: 28px; opacity: 0.5; margin-bottom: 0.5rem; display: inline-block;"></i>
        <p style="margin: 0; font-size: 0.82rem; font-weight: 500; color: #94a3b8;">This folder is currently empty</p>
        <p style="margin: 0.25rem 0 0; font-size: 0.74rem;">Upload documents via <strong>Local PC Upload</strong> or place PDF files into SharePoint.</p>
      </div>
    `;
    if (typeof safeCreateIcons === "function") safeCreateIcons();
    return;
  }

  let html = "";

  // 1. Render Subfolders (if any)
  if (folders.length) {
    html += `
      <div class="sp-section-heading">
        <span>Sub-Folders (${folders.length})</span>
        <span style="font-size: 0.68rem; color: var(--text-muted); text-transform: none;">Click card or Open to browse</span>
      </div>
      <div class="sp-folders-container" style="margin-bottom: 0.5rem;">
    `;
    html += folders.map((fol) => `
      <div class="sharepoint-folder-card" role="button" tabindex="0" data-folder-id="${escapeHTML(fol.id)}" data-folder-name="${escapeHTML(fol.name)}">
        <div class="sp-folder-icon-box">
          <i data-lucide="folder"></i>
        </div>
        <div class="sp-folder-info">
          <span class="sp-folder-title">${escapeHTML(fol.name)}</span>
          <div class="sp-folder-meta">
            <span class="sp-item-count-pill">${fol.item_count !== null && fol.item_count !== undefined ? `${fol.item_count} items` : 'Directory'}</span>
            <span>Click to navigate</span>
          </div>
        </div>
        <div class="sp-folder-open-action">
          <span>Open</span>
          <i data-lucide="arrow-right"></i>
        </div>
      </div>
    `).join("");
    html += `</div>`;
  }

  // 2. Render Files (if any)
  if (files.length) {
    html += `
      <div class="sp-section-heading">
        <span>Documents &amp; Manuals (${files.length})</span>
        <span style="font-size: 0.68rem; color: var(--text-muted); text-transform: none;">Select document to extract</span>
      </div>
      <div class="sp-files-container">
    `;
    html += files.map((f) => `
      <button type="button" class="sharepoint-file-item" role="option" data-id="${escapeHTML(f.id)}" data-name="${escapeHTML(f.name)}" data-size="${Number(f.size) || 0}">
        <i data-lucide="file-text"></i>
        <span class="sharepoint-file-meta">
          <span class="sharepoint-file-name">${escapeHTML(f.name)}</span>
          <span class="sharepoint-file-size">${formatSharePointSize(f.size)}</span>
        </span>
        <span class="sp-folder-open-action" style="font-size: 0.72rem; padding: 0.2rem 0.55rem; color: #2dd4bf; background: hsla(160, 80%, 40%, 0.15); border-color: hsla(160, 80%, 50%, 0.3);">
          <span>Select</span>
          <i data-lucide="chevron-right" style="width: 12px; height: 12px;"></i>
        </span>
      </button>
    `).join("");
    html += `</div>`;
  }

  sharepointFilesList.innerHTML = html;
  if (typeof safeCreateIcons === "function") safeCreateIcons();
}

window.loadSharePointFiles = loadSharePointFiles;

if (sharepointRefreshBtn) {
  sharepointRefreshBtn.addEventListener("click", async (e) => {
    e.preventDefault();
    e.stopPropagation();
    sharepointRefreshBtn.classList.add("spinning");
    const curName = sharePointBreadcrumbTrail.length 
      ? sharePointBreadcrumbTrail[sharePointBreadcrumbTrail.length - 1].name 
      : null;
    try {
      await loadSharePointFiles(currentSharePointFolderId, curName, false, true);
    } finally {
      sharepointRefreshBtn.classList.remove("spinning");
    }
  });
}

const spBackBtn = document.getElementById("sharepoint-back-btn") || document.getElementById("sharepoint-up-btn");
if (spBackBtn) {
  spBackBtn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (sharePointBreadcrumbTrail.length > 1) {
      sharePointBreadcrumbTrail.pop();
      const parentCrumb = sharePointBreadcrumbTrail[sharePointBreadcrumbTrail.length - 1];
      loadSharePointFiles(parentCrumb.id, parentCrumb.name, true, true);
    } else {
      loadSharePointFiles(currentSharePointParentId || null, null, true);
    }
  });
}

const spBreadcrumbNav = document.getElementById("sharepoint-breadcrumb");
if (spBreadcrumbNav) {
  spBreadcrumbNav.addEventListener("click", (e) => {
    const btn = e.target.closest(".sp-crumb-btn");
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    const idx = parseInt(btn.getAttribute("data-index"), 10);
    if (!isNaN(idx) && idx >= 0 && idx < sharePointBreadcrumbTrail.length) {
      const targetCrumb = sharePointBreadcrumbTrail[idx];
      sharePointBreadcrumbTrail = sharePointBreadcrumbTrail.slice(0, idx + 1);
      loadSharePointFiles(targetCrumb.id, targetCrumb.name, false, true);
    } else {
      const fid = btn.getAttribute("data-folder-id") || null;
      loadSharePointFiles(fid);
    }
  });
}

if (sharepointFilesList) {
  sharepointFilesList.addEventListener("click", (e) => {
    // 1. Check if user clicked a folder card or its open action
    const folderCard = e.target.closest(".sharepoint-folder-card");
    if (folderCard) {
      e.preventDefault();
      e.stopPropagation();
      const folderId = folderCard.getAttribute("data-folder-id");
      const folderName = folderCard.getAttribute("data-folder-name");
      if (folderId) {
        loadSharePointFiles(folderId, folderName);
      }
      return;
    }

    // 2. Check if user clicked a file item
    const fileBtn = e.target.closest(".sharepoint-file-item");
    if (!fileBtn) return;
    e.preventDefault();
    e.stopPropagation();
    handleSharePointExtract({
      id: fileBtn.getAttribute("data-id"),
      name: fileBtn.getAttribute("data-name") || "document.pdf",
      size: Number(fileBtn.getAttribute("data-size")) || 0
    });
  });

  // Support Enter key for accessibility on folder cards
  sharepointFilesList.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      const folderCard = e.target.closest(".sharepoint-folder-card");
      if (folderCard) {
        e.preventDefault();
        folderCard.click();
      }
    }
  });
}

// Intake Tabs (SharePoint vs Local PC Upload)
const tabSharepoint = document.getElementById("tab-sharepoint");
const tabLocalUpload = document.getElementById("tab-local-upload");
const panelSharepoint = document.getElementById("panel-sharepoint");
const panelLocalUpload = document.getElementById("panel-local-upload");

if (tabSharepoint && tabLocalUpload) {
  tabSharepoint.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    tabSharepoint.classList.add("active");
    tabLocalUpload.classList.remove("active");
    if (panelSharepoint) panelSharepoint.hidden = false;
    if (panelLocalUpload) panelLocalUpload.hidden = true;
  });

  tabLocalUpload.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    tabLocalUpload.classList.add("active");
    tabSharepoint.classList.remove("active");
    if (panelSharepoint) panelSharepoint.hidden = true;
    if (panelLocalUpload) panelLocalUpload.hidden = false;
  });
}

// Local PC Upload Dropzone & File Handler
const localDropzone = document.getElementById("local-dropzone");
const localFileInput = document.getElementById("local-file-input");
const selectedFileInfo = document.getElementById("selected-file-info");
const selectedFileName = document.getElementById("selected-file-name");
const localExtractBtn = document.getElementById("local-extract-btn");

let selectedLocalFile = null;

function isSupportedDocument(filename) {
  if (!filename) return false;
  const lower = filename.toLowerCase();
  return lower.endsWith(".pdf") || lower.endsWith(".docx") || lower.endsWith(".doc");
}

function updateSelectedLocalFile(file) {
  if (!file) return;
  if (!isSupportedDocument(file.name)) {
    alert("Please select a supported PDF or Word (.docx, .doc) file.");
    return;
  }
  selectedLocalFile = file;
  if (selectedFileName) selectedFileName.innerText = file.name;
  if (selectedFileInfo) selectedFileInfo.hidden = false;
  if (localExtractBtn) {
    const isDocx = file.name.toLowerCase().endsWith(".docx") || file.name.toLowerCase().endsWith(".doc");
    localExtractBtn.innerText = isDocx ? "Extract Word (DOCX)" : "Extract PDF";
  }
  if (typeof safeCreateIcons === "function") safeCreateIcons();
}

if (localDropzone && localFileInput) {
  localDropzone.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    try {
      localFileInput.value = "";
      localFileInput.click();
    } catch (err) {
      console.error("Could not open file picker", err);
      alert("Could not open the file picker. Try drag-and-drop.");
    }
  });

  localFileInput.addEventListener("change", (e) => {
    if (e.target.files && e.target.files[0]) {
      updateSelectedLocalFile(e.target.files[0]);
    }
  });

  localDropzone.addEventListener("dragover", (e) => {
    e.preventDefault();
    e.stopPropagation();
    localDropzone.classList.add("dragover");
  });

  localDropzone.addEventListener("dragleave", (e) => {
    e.preventDefault();
    e.stopPropagation();
    localDropzone.classList.remove("dragover");
  });

  localDropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    e.stopPropagation();
    localDropzone.classList.remove("dragover");
    if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
      updateSelectedLocalFile(e.dataTransfer.files[0]);
    }
  });
}

if (localExtractBtn) {
  localExtractBtn.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (!selectedLocalFile) {
      alert("Please select a PDF or Word (.docx) file first.");
      return;
    }
    handleSharePointExtract({
      file: selectedLocalFile,
      name: selectedLocalFile.name,
      size: selectedLocalFile.size
    });
  });
}

const MAX_UPLOAD_SIZE_BYTES = 1024 * 1024 * 1024; // 1GB, matches UI copy

async function handleSharePointExtract(item) {
  if (isExtracting) {
    alert("An extraction is already in progress. Please wait for it to finish or cancel it first.");
    return;
  }

  if (!item || (!item.id && !item.file)) {
    alert("Invalid file selection.");
    return;
  }

  try {
    if (typeof window.requireAuthForApi === "function") window.requireAuthForApi();
  } catch (e) {
    return;
  }

  const fileSize = Number(item.size) || 0;
  if (fileSize > MAX_UPLOAD_SIZE_BYTES) {
    alert(`File is too large (${(fileSize / (1024 * 1024)).toFixed(1)}MB). Maximum supported size is 1GB.`);
    return;
  }

  if (maintenanceRegistry.length > 0 || sparePartsRegistry.length > 0 || troubleshootingRegistry.length > 0) {
    const proceed = confirm(`Loading "${item.name}" will clear the current registry data (${maintenanceRegistry.length} maintenance, ${sparePartsRegistry.length} spare parts, ${troubleshootingRegistry.length} troubleshooting records). Continue?`);
    if (!proceed) return;
  }

  closeSharePointPopover();

  maintenanceRegistry = [];
  sparePartsRegistry = [];
  troubleshootingRegistry = [];
  highlightRecordIds = [];
  lastSourceDocName = item.name;
  renderGrid();

  setActiveDocBadge(item.name);
  setExtractingUi(true, `Processing "${item.name}"`, "Preparing extraction…");

  let extractFinishedCleanly = false;
  try {
    if (progressStatus) progressStatus.innerText = "Connecting to extraction service…";
    const apiHealth = await checkPythonApiHealth();
    if (!apiHealth.ok) {
      setActiveDocBadge("");
      alert(
        `Extraction service is not reachable.\n\n` +
        `Please verify the backend server is running, then try again.`
      );
      return;
    }

    const okLarge = await confirmLargePdfIfNeeded(null, fileSize);
    if (!okLarge) {
      setActiveDocBadge("");
      appendChatSystemMessage("Extraction cancelled.");
      return;
    }

    if (progressStatus) progressStatus.innerText = "Fetching document from SharePoint…";
    const result = await extractViaPythonApi(item, null);
    applyApiExtractResult(result, { name: item.name });
    extractFinishedCleanly = true;
  } catch (error) {
    console.error(error);
    const msg = String(error && error.message ? error.message : error);
    if (/gemini api key required/i.test(msg)) {
      alert(
        "Extraction failed: Gemini API key required.\n\n" +
        "Set GEMINI_API_KEY in backend/.env and restart ./start-api.sh."
      );
    } else {
      alert("Extraction failed: " + msg);
    }
    setActiveDocBadge("");
    appendChatSystemMessage(`Extraction failed: ${msg}`);
  } finally {
    if (!extractFinishedCleanly && isExtracting) {
      clearExtractingUi();
    }
  }
}

function pageOrderKey(row) {
  const raw = row && row.page;
  if (raw == null || raw === "" || raw === "NA") return Number.MAX_SAFE_INTEGER;
  if (typeof raw === "number" && Number.isFinite(raw)) return raw;
  const s = String(raw).trim();
  // Accept "12", "Page 12", "p.12", etc.
  const m = s.match(/(\d{1,5})/);
  if (m) {
    const n = parseInt(m[1], 10);
    if (Number.isFinite(n)) return n;
  }
  return Number.MAX_SAFE_INTEGER;
}

function itemNoOrderKey(row) {
  const raw = row && row.item_no;
  if (raw == null || raw === "" || raw === "NA") return Number.MAX_SAFE_INTEGER;
  const m = String(raw).trim().match(/^(\d{1,6})\b/);
  if (!m) return Number.MAX_SAFE_INTEGER;
  const n = parseInt(m[1], 10);
  return Number.isFinite(n) ? n : Number.MAX_SAFE_INTEGER;
}

function pdfOrderKey(row) {
  const n = parseInt(row && row.pdf_order, 10);
  return Number.isFinite(n) && n > 0 ? n : Number.MAX_SAFE_INTEGER;
}

/** Same-page order must match the PDF (top→bottom / Item No.), never A–Z by name. */
function comparePdfRowOrder(a, b, aIdx, bIdx) {
  const pa = pageOrderKey(a);
  const pb = pageOrderKey(b);
  if (pa !== pb) return pa - pb;

  const oa = pdfOrderKey(a);
  const ob = pdfOrderKey(b);
  if (oa !== ob) return oa - ob;

  const ia = itemNoOrderKey(a);
  const ib = itemNoOrderKey(b);
  if (ia !== ib) return ia - ib;

  return aIdx - bIdx;
}

/** Stamp stable reading order from the array the model returned (1-based). */
function stampPdfOrder(rows) {
  if (!Array.isArray(rows)) return rows;
  rows.forEach((row, idx) => {
    if (!row || typeof row !== "object") return;
    const existing = parseInt(row.pdf_order, 10);
    if (!Number.isFinite(existing) || existing <= 0) {
      row.pdf_order = idx + 1;
    }
  });
  return rows;
}

// Keep grid/export order matching the PDF page reading order.
function assembleRegistriesInPageOrder() {
  const sortAndReindex = (registry) => {
    const decorated = registry.map((row, idx) => ({ row, idx }));
    decorated.sort((a, b) => comparePdfRowOrder(a.row, b.row, a.idx, b.idx));
    const sorted = decorated.map(d => d.row);
    // Keep pdf_order aligned with final visible/export sequence.
    sorted.forEach((row, idx) => {
      row.id = idx + 1;
      row.pdf_order = idx + 1;
    });
    return sorted;
  };

  maintenanceRegistry = sortAndReindex(maintenanceRegistry);
  sparePartsRegistry = sortAndReindex(sparePartsRegistry);
  troubleshootingRegistry = sortAndReindex(troubleshootingRegistry);
}

/* -------------------------------------------------------------
 * 5. Cognitive AI Copilot Chatbot Engine
 * ------------------------------------------------------------- */

function appendChatSystemMessage(text) {
  const msg = document.createElement("div");
  msg.className = "chat-message assistant";
  msg.innerHTML = `
    <div class="msg-avatar"><i data-lucide="bot"></i></div>
    <div class="msg-content" style="border-color: var(--accent-green-glow); background: hsla(145, 80%, 48%, 0.03);">
      <p>${escapeHTML(text).replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')}</p>
    </div>
  `;
  chatMessages.appendChild(msg);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  safeCreateIcons();
}

function appendUserMessage(text) {
  const msg = document.createElement("div");
  msg.className = "chat-message user";
  msg.innerHTML = `
    <div class="msg-avatar"><i data-lucide="user"></i></div>
    <div class="msg-content">
      <p>${escapeHTML(text)}</p>
    </div>
  `;
  chatMessages.appendChild(msg);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  safeCreateIcons();
}

// Client-Side Cognitive Matching and Context Extraction (asynchronous for Ollama RAG support)
// Sends a plain-text (non-JSON) prompt to whichever LLM engine is active and returns the raw
// reply text. Used by the RAG chatbot, which needs a conversational answer rather than the
// structured JSON extraction produced by runOllamaExtractor/runGeminiExtractor.
async function callLLMRagAnswer(ragPrompt) {
  if (engineMode === "gemini") {
    let modelName = normalizeGeminiModel(geminiModel);
    async function postRag(activeModel) {
      return fetchGeminiGenerateContent(activeModel, {
        contents: [{ role: "user", parts: [{ text: ragPrompt }] }],
        generationConfig: { temperature: 0.2 }
      }, { timeoutMs: 120000, maxAttempts: 4 });
    }

    let response = await postRag(modelName);

    if (!response.ok) {
      let errDetail = "";
      try {
        const errJson = await response.json();
        errDetail = (errJson.error && errJson.error.message) || "";
      } catch (e) {}
      if (response.status === 404) {
        throw new Error(
          `Gemini model "${modelName}" returned 404 Not Found` +
          `${errDetail ? " - " + errDetail : ""}. ` +
          `Pick a live model in Settings — automatic fallback is disabled.`
        );
      }
      throw new Error(`Gemini API returned HTTP ${response.status}${errDetail ? " - " + errDetail : ""}`);
    }
    const data = await response.json();
    const candidate = data.candidates && data.candidates[0];
    const text = (candidate && candidate.content && candidate.content.parts && candidate.content.parts[0] && candidate.content.parts[0].text) || "";
    if (!text) {
      throw new Error("Gemini returned no content (check API key/model name).");
    }
    return text.trim();
  }

  if (engineMode !== "ollama") {
    throw new Error("LLM chat is disabled unless Ollama or Gemini mode is selected.");
  }

  const response = await fetch(`${ollamaUrl}/api/generate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      model: ollamaModel,
      prompt: ragPrompt,
      stream: false,
      options: {
        temperature: 0.2
      }
    })
  });
  if (!response.ok) {
    throw new Error(`Ollama Server returned HTTP ${response.status}`);
  }
  const data = await response.json();
  return data.response.trim();
}

const COPILOT_LLM_DAILY_LIMIT = 5;
const COPILOT_LLM_QUOTA_KEY = "omniparse_copilot_llm_quota_v1";

function todayKeyUTC() {
  return new Date().toISOString().slice(0, 10);
}

function readCopilotLlmQuota() {
  try {
    const raw = localStorage.getItem(COPILOT_LLM_QUOTA_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (!parsed || parsed.date !== todayKeyUTC()) {
      return { date: todayKeyUTC(), used: 0 };
    }
    return { date: parsed.date, used: Number(parsed.used) || 0 };
  } catch (e) {
    return { date: todayKeyUTC(), used: 0 };
  }
}

function remainingCopilotLlmQuota() {
  return Math.max(0, COPILOT_LLM_DAILY_LIMIT - readCopilotLlmQuota().used);
}

function consumeCopilotLlmQuota() {
  const q = readCopilotLlmQuota();
  if (q.used >= COPILOT_LLM_DAILY_LIMIT) return false;
  q.used += 1;
  q.date = todayKeyUTC();
  try {
    localStorage.setItem(COPILOT_LLM_QUOTA_KEY, JSON.stringify(q));
  } catch (e) {}
  updateCopilotQuotaBadge();
  return true;
}

function refundCopilotLlmQuota() {
  try {
    const q = readCopilotLlmQuota();
    q.used = Math.max(0, (Number(q.used) || 1) - 1);
    q.date = todayKeyUTC();
    localStorage.setItem(COPILOT_LLM_QUOTA_KEY, JSON.stringify(q));
  } catch (e) {}
  updateCopilotQuotaBadge();
}

function updateCopilotQuotaBadge() {
  const badge = document.getElementById("copilot-quota-badge");
  if (!badge) return;
  const left = remainingCopilotLlmQuota();
  badge.textContent = left > 0 ? `AI ${left}/${COPILOT_LLM_DAILY_LIMIT} left` : `AI limit reached`;
  badge.classList.toggle("quota-exhausted", left <= 0);
  badge.title = left > 0
    ? `Copilot answers use API tokens. ${left} of ${COPILOT_LLM_DAILY_LIMIT} remaining today (per browser user).`
    : `Daily Copilot AI limit (${COPILOT_LLM_DAILY_LIMIT}) reached. Try again tomorrow.`;
}

function isTimeBasedRoutine(routine) {
  const s = String(routine || "").toLowerCase();
  return s.includes("hour") || s.includes("month") || s.includes("week") ||
    s.includes("year") || s.includes("day") || s.includes("shift") ||
    s.includes("daily") || s.includes("weekly") || s.includes("monthly") ||
    s.includes("yearly") || s.includes("annual") || /\b\d+\s*h\b/.test(s);
}

function isConsumablePart(row) {
  const name = String(row.part_name || "").toLowerCase();
  const cat = String(row.part_categorization || "").toLowerCase();
  return name.includes("oil") || name.includes("grease") || name.includes("filter") ||
    name.includes("seal") || name.includes("gasket") || cat.includes("consumable");
}

function resolveCopilotIntent(q) {
  const intents = [];
  if (/\btime[-\s]?based\b/.test(q) || /\b(interval|schedule|periodic|preventive)\b/.test(q) ||
      (/\btasks?\b/.test(q) && /\b(time|interval|schedule|hour|day|month|year)\b/.test(q))) {
    intents.push({
      id: "time_based",
      label: "Time-based Tasks",
      type: "maintenance",
      filter: (row) => isTimeBasedRoutine(row.maintenance_routine)
    });
  }
  if (/\b(hour|hourly|hrs?)\b/.test(q)) {
    intents.push({
      id: "hours",
      label: "Hourly tasks",
      type: "maintenance",
      filter: (row) => String(row.maintenance_routine || "").toLowerCase().includes("hour")
    });
  }
  if (/\b(month|monthly)\b/.test(q)) {
    intents.push({
      id: "months",
      label: "Monthly tasks",
      type: "maintenance",
      filter: (row) => String(row.maintenance_routine || "").toLowerCase().includes("month")
    });
  }
  if (/\b(year|yearly|annual)\b/.test(q)) {
    intents.push({
      id: "years",
      label: "Yearly tasks",
      type: "maintenance",
      filter: (row) => String(row.maintenance_routine || "").toLowerCase().includes("year")
    });
  }
  if (/\b(day|daily|shift)\b/.test(q)) {
    intents.push({
      id: "days",
      label: "Daily / shift tasks",
      type: "maintenance",
      filter: (row) => {
        const s = String(row.maintenance_routine || "").toLowerCase();
        return s.includes("day") || s.includes("daily") || s.includes("shift");
      }
    });
  }
  if (/\bconsumables?\b/.test(q)) {
    intents.push({
      id: "consumables",
      label: "Consumables",
      type: "spare_parts",
      filter: (row) => isConsumablePart(row)
    });
  }
  if (/\bspare\s*parts?\b/.test(q) || (/\bparts?\b/.test(q) && !/\btime[-\s]?based\b/.test(q))) {
    intents.push({
      id: "spare_parts",
      label: "Spare Parts",
      type: "spare_parts",
      filter: () => true
    });
  }
  if (/\b(troubleshoot|troubleshooting|faults?|problems?|failures?)\b/.test(q)) {
    intents.push({
      id: "troubleshooting",
      label: "Troubleshooting",
      type: "troubleshooting",
      filter: () => true
    });
  }
  if (/\b(maintenance\s+(rules?|tasks?|registry)|all\s+maintenance)\b/.test(q)) {
    intents.push({
      id: "maintenance_all",
      label: "Maintenance Rules",
      type: "maintenance",
      filter: () => true
    });
  }
  const seen = new Set();
  return intents.filter(i => (seen.has(i.id) ? false : (seen.add(i.id), true)));
}

function formatRegistryContextRow(row, type) {
  if (type === "spare_parts") {
    return `- [Spare #${row.id}] ${row.equipment_title || "NA"} | ${row.part_name || "NA"} | ${row.part_number_code || "NA"} | loc: ${row.subsystem_location || "NA"} | page ${row.page || "NA"}`;
  }
  if (type === "troubleshooting") {
    return `- [Troubleshoot #${row.id}] ${row.equipment_title || "NA"} | problem: ${row.problem || "NA"} | solution: ${row.root_cause_solution || "NA"} | page ${row.page || "NA"}`;
  }
  if (row.maintenance_work_description && activeEquipmentCategory === "Logbook") {
    return `- [Logbook #${row.id}] ${row.date || "NA"} | ${row.maintenance_work_description || "NA"} | parts: ${row.parts_renewed || "NA"} | page ${row.page || "NA"}`;
  }
  return `- [Maint #${row.id}] ${row.equipment_title || "NA"} | ${row.subsystem_component || "NA"} | interval: ${row.maintenance_routine || "NA"} | ${row.checks_instructions || "NA"} | page ${row.page || "NA"}`;
}

function buildCopilotRetrieval(query) {
  const queryLower = String(query || "").toLowerCase().trim();
  const STOP_TOKENS = new Set([
    "the", "and", "for", "with", "from", "that", "this", "what", "which", "when",
    "where", "how", "about", "into", "please", "show", "list", "give", "tell", "you", "your"
  ]);
  const tokens = queryLower.split(/[^a-z0-9]+/).filter(t => t.length > 2 && !STOP_TOKENS.has(t));
  const intents = resolveCopilotIntent(queryLower);

  const pageMatches = [];
  (loadedPages || []).forEach(page => {
    let score = 0;
    const pageText = String(page.text || "").toLowerCase();
    tokens.forEach(token => {
      if (pageText.includes(token)) score += 1;
    });
    if (score > 0) pageMatches.push({ pageNum: page.pageNum, text: page.text, score });
  });
  pageMatches.sort((a, b) => b.score - a.score);

  function scoreRegistryRow(row, type) {
      let text = "";
    if (type === "maintenance") {
      text = `${row.equipment_title} ${row.subsystem_component} ${row.maintenance_routine} ${row.checks_instructions} ${row.date || ""} ${row.maintenance_work_description || ""}`;
    } else if (type === "spare_parts") {
        text = `${row.equipment_title} ${row.subsystem_location} ${row.part_name} ${row.part_number_code} ${row.drawing_model_no} ${row.part_categorization}`;
    } else {
        text = `${row.equipment_title} ${row.subsystem_component} ${row.problem} ${row.root_cause_solution}`;
      }
      text = text.toLowerCase();
    let score = 0;
    tokens.forEach(token => {
      if (text.includes(token)) score += 1;
    });
    if (queryLower.length > 4 && text.includes(queryLower)) score += 3;
    return score;
  }

  const registryPools = [
    { type: "maintenance", rows: maintenanceRegistry },
    { type: "spare_parts", rows: sparePartsRegistry },
    { type: "troubleshooting", rows: troubleshootingRegistry }
  ];

  const intentSummaryLines = [];
  const allRegistryMatches = [];
  const intentMatchedKeys = new Set();

  intents.forEach(intent => {
    const pool = registryPools.find(p => p.type === intent.type);
    const rows = (pool && pool.rows) || [];
    const matched = rows.filter(intent.filter);
    intentSummaryLines.push(`- Dashboard "${intent.label}": ${matched.length} of ${rows.length} rows`);
    matched.forEach(row => {
      const key = `${intent.type}:${row.id}`;
      if (intentMatchedKeys.has(key)) return;
      intentMatchedKeys.add(key);
      allRegistryMatches.push({
        rowId: row.id,
        score: 100 + matched.length,
        type: intent.type,
        row,
        snippet: formatRegistryContextRow(row, intent.type),
        viaIntent: intent.id
      });
    });
  });

  registryPools.forEach(pool => {
    (pool.rows || []).forEach(row => {
      const key = `${pool.type}:${row.id}`;
      if (intentMatchedKeys.has(key)) return;
      const score = scoreRegistryRow(row, pool.type);
    if (score > 0) {
        allRegistryMatches.push({
          rowId: row.id,
          score,
          type: pool.type,
          row,
          snippet: formatRegistryContextRow(row, pool.type)
        });
      }
    });
  });
  allRegistryMatches.sort((a, b) => b.score - a.score);

  let gridMatches = [];
  if (intents.length > 0) {
    const preferType = intents[0].type;
    gridMatches = allRegistryMatches.filter(m => m.type === preferType && m.viaIntent);
    if (!gridMatches.length) gridMatches = allRegistryMatches.filter(m => m.type === preferType);
      } else {
    gridMatches = allRegistryMatches.filter(m => m.type === activeRegistryTab);
    if (!gridMatches.length && allRegistryMatches.length) {
      const bestType = allRegistryMatches[0].type;
      gridMatches = allRegistryMatches.filter(m => m.type === bestType);
    }
  }

  return {
    tokens,
    intents,
    intentSummaryLines,
    pageMatches,
    allRegistryMatches,
    gridMatches,
    matchingRecordIds: gridMatches.map(m => m.rowId)
  };
}

async function processCognitiveChatSearch(query) {
  appendUserMessage(query);

  const loggedIn = typeof window.isLoggedIn === "function" && window.isLoggedIn();
  if (typeof window.requireAuthForApi === "function") {
    try { window.requireAuthForApi(); } catch (e) {
      appendAssistantReply("Sign in required to use Copilot.");
      return;
    }
  }

  // Logged-in users use server Copilot (Gemini key on API). Guests still need local engine mode.
  if (!loggedIn && engineMode !== "gemini" && engineMode !== "ollama") {
    appendAssistantReply("Copilot requires **Gemini** or **Ollama** in AI Parsing Engine Settings (or sign in for server Copilot).");
    return;
  }
  if (typeof isExtracting !== "undefined" && isExtracting) {
    appendAssistantReply("Extraction is running. Wait until it finishes so Copilot does not compete with active users' extract jobs.");
    return;
  }

  if (loggedIn && window.authState && window.authState.user) {
    if (window.authState.user.copilot_remaining_today <= 0) {
      appendAssistantReply(`Daily Copilot AI limit reached (**${window.authState.user.copilot_daily_limit}/day** for your account). Ask Global Admin to raise your limit.`);
      if (typeof window.applyUserPolicyToUi === "function") window.applyUserPolicyToUi();
      return;
    }
  } else if (remainingCopilotLlmQuota() <= 0) {
    appendAssistantReply(`Daily Copilot AI limit reached (**${COPILOT_LLM_DAILY_LIMIT}/user/day**). Try again tomorrow.`);
    updateCopilotQuotaBadge();
    return;
  }

  const loader = document.createElement("div");
  loader.className = "chat-message assistant";
  loader.id = "chat-loader";
  const leftHint = loggedIn && window.authState.user
    ? `${window.authState.user.copilot_remaining_today}/${window.authState.user.copilot_daily_limit}`
    : `${remainingCopilotLlmQuota()}/${COPILOT_LLM_DAILY_LIMIT}`;
  loader.innerHTML = `
        <div class="msg-avatar"><i data-lucide="bot"></i></div>
    <div class="msg-content">
      <p>${loggedIn ? "Asking server Copilot…" : (engineMode === "gemini" ? "Asking Gemini…" : "Asking Ollama…")} (${leftHint} AI left today)</p>
        </div>
      `;
  chatMessages.appendChild(loader);
      chatMessages.scrollTop = chatMessages.scrollHeight;
      safeCreateIcons();

  if (!String(query || "").trim()) {
    const loaderElem = document.getElementById("chat-loader");
    if (loaderElem) loaderElem.remove();
    appendAssistantReply("Please enter a question about the uploaded manual or extracted registry.");
      return;
  }

  const retrieval = buildCopilotRetrieval(query);
  const { intents, intentSummaryLines, pageMatches, allRegistryMatches, gridMatches, matchingRecordIds } = retrieval;

  const contextParts = [];
  if (intentSummaryLines.length) {
    contextParts.push(
      `[Dashboard metrics]\n${intentSummaryLines.join("\n")}\n` +
      `Totals — maintenance: ${maintenanceRegistry.length}; spare parts: ${sparePartsRegistry.length}; troubleshooting: ${troubleshootingRegistry.length}.`
    );
  }

  const topRegistryLimit = intents.length > 0 ? 25 : 12;
  const topRegistry = allRegistryMatches.slice(0, topRegistryLimit);
  if (topRegistry.length) {
    contextParts.push(
      `[Extracted registry matches (${topRegistry.length}${allRegistryMatches.length > topRegistryLimit ? ` of ${allRegistryMatches.length}` : ""})]:\n` +
      topRegistry.map(m => m.snippet).join("\n")
    );
  }

  let topPageNum = null;
  if (pageMatches.length) {
    const topPages = pageMatches.slice(0, intents.length ? 1 : 2);
    topPageNum = topPages[0].pageNum;
    contextParts.push(topPages.map(p => `[Page ${p.pageNum} text]:\n${String(p.text || "").slice(0, 3000)}`).join("\n\n"));
  } else if (topRegistry.length) {
    const pageFromRow = topRegistry.find(m => m.row && m.row.page != null && m.row.page !== "NA");
    if (pageFromRow) topPageNum = pageFromRow.row.page;
  }

  if (!contextParts.length) {
    contextParts.push(
      "No matching registry rows or page text were found for this query. " +
      "Say so clearly and suggest the user upload/extract a manual or try a different term."
    );
  }

  const contextText = contextParts.join("\n\n").slice(0, 12000);

  try {
    let aiReply = "";
    let engineLabel = "";
    let left = 0;
    let limit = COPILOT_LLM_DAILY_LIMIT;

    if (loggedIn) {
      const allowed = (typeof window.getAssignedGeminiModels === "function")
        ? window.getAssignedGeminiModels()
        : null;
      let model = (window.authState.user && window.authState.user.preferred_model) || geminiModel;
      if (allowed && allowed.length && !allowed.includes(model)) model = allowed[0];
      if (geminiModelInput && geminiModelInput.value && (!allowed || allowed.includes(geminiModelInput.value))) {
        model = geminiModelInput.value;
      }
      const resp = await fetch(`${apiBaseUrl}/api/copilot`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...((typeof window.getAuthHeaders === "function") ? window.getAuthHeaders() : {})
        },
        body: JSON.stringify({ question: query, context: contextText, model })
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = typeof data.detail === "string" ? data.detail : (data.detail ? JSON.stringify(data.detail) : `HTTP ${resp.status}`);
        throw new Error(detail);
      }
      aiReply = data.answer || "";
      left = data.copilot_remaining_today;
      limit = data.copilot_daily_limit;
      engineLabel = `Server Copilot (<strong>${escapeHTML(data.model || model)}</strong>)`;
      if (typeof window.refreshMe === "function") await window.refreshMe();
      if (typeof window.applyUserPolicyToUi === "function") window.applyUserPolicyToUi();
    } else {
      if (!consumeCopilotLlmQuota()) {
    const loaderElem = document.getElementById("chat-loader");
    if (loaderElem) loaderElem.remove();
        appendAssistantReply(`Daily Copilot AI limit reached (**${COPILOT_LLM_DAILY_LIMIT}/user/day**).`);
      return;
    }
      const ragPrompt = `You are a helpful AI technical assistant for OmniParse IDP.
Answer using the provided context (dashboard metrics + extracted registry rows + optional page text).
When dashboard metrics say rows exist (e.g. time-based tasks), you MUST use those registry matches — do not claim they are missing.
Keep answers concise and technical. Cite page numbers when available. Do not invent intervals or part numbers.
If user wording differs slightly from titles, map to the closest registry entries and say what you matched.

Document Context:
"""
${contextText}
"""

User Question: ${query}`;
      try {
        aiReply = await callLLMRagAnswer(ragPrompt);
      } catch (innerErr) {
        refundCopilotLlmQuota();
        throw innerErr;
      }
      left = remainingCopilotLlmQuota();
      engineLabel = engineMode === "gemini"
        ? `Gemini (<strong>${geminiModel}</strong>)`
        : `Ollama (<strong>${ollamaModel}</strong>)`;
      updateCopilotQuotaBadge();
    }

    const loaderElem = document.getElementById("chat-loader");
    if (loaderElem) loaderElem.remove();

    let responseHTML = `<div style="line-height:1.5;white-space:normal;">${renderMarkdown(aiReply)}</div>`;
    if (intents.length) {
      responseHTML += `<div class="msg-excerpt" style="font-style:normal;margin-top:0.5rem;">Intent: <strong>${escapeHTML(intents.map(i => i.label).join(", "))}</strong></div>`;
    }
    // No engine/quota meta line — only a page reference when there is one.
    const pageRef = pageRefHtml(topPageNum);
    if (pageRef) {
      responseHTML += `<div class="msg-meta">${pageRef}</div>`;
    }
    if (matchingRecordIds.length) {
      responseHTML += `<button class="msg-action-btn" onclick="applyChatFilter([${matchingRecordIds.join(',')}])">
        <i data-lucide="filter" style="width:14px;height:14px;"></i>
        <span>Filter Grid to ${matchingRecordIds.length} Matches</span>
      </button>`;
    }

    const msg = document.createElement("div");
    msg.className = "chat-message assistant";
    msg.innerHTML = `
      <div class="msg-avatar"><i data-lucide="bot"></i></div>
      <div class="msg-content" style="border-color: var(--accent-cyan-glow);">${responseHTML}</div>
    `;
    chatMessages.appendChild(msg);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    safeCreateIcons();
  } catch (err) {
    const loaderElem = document.getElementById("chat-loader");
    if (loaderElem) loaderElem.remove();
    appendChatSystemMessage(`⚠️ Copilot AI failed: ${err.message}`);
  }
}

function appendAssistantReply(text) {
  const msg = document.createElement("div");
  msg.className = "chat-message assistant";
  msg.innerHTML = `
    <div class="msg-avatar"><i data-lucide="bot"></i></div>
    <div class="msg-content">
      <p>${escapeHTML(text).replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')}</p>
    </div>
  `;
  chatMessages.appendChild(msg);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  safeCreateIcons();
}

function pageRefHtml(pageNum) {
  if (pageNum == null || pageNum === "" || pageNum === "NA") {
    return "";
  }
  const n = Number(pageNum);
  if (!Number.isFinite(n)) {
    return `<span class="page-ref">${escapeHTML(String(pageNum))}</span>`;
  }
  return `<button type="button" class="page-ref page-ref-btn" onclick="jumpToPageContext(${n})" title="View extracted text from page ${n}">Page ${n}</button>`;
}

function getActiveRegistry() {
  if (activeRegistryTab === "spare_parts") return sparePartsRegistry;
  if (activeRegistryTab === "troubleshooting") return troubleshootingRegistry;
  return maintenanceRegistry;
}

function getSelectedRegistryRow() {
  if (selectedRegistryRowId == null) return null;
  return getActiveRegistry().find(r => r.id === selectedRegistryRowId) || null;
}

function selectRegistryRow(id, trEl) {
  selectedRegistryRowId = id;
  document.querySelectorAll(".data-table tr.row-selected").forEach(r => r.classList.remove("row-selected"));
  if (trEl) trEl.classList.add("row-selected");
  updateAskSelectedBar();
}

function clearSelectedRegistryRow() {
  selectedRegistryRowId = null;
  document.querySelectorAll(".data-table tr.row-selected").forEach(r => r.classList.remove("row-selected"));
  updateAskSelectedBar();
}

function updateAskSelectedBar() {
  const bar = document.getElementById("ask-selected-bar");
  const label = document.getElementById("ask-selected-label");
  if (!bar || !label) return;
  const row = getSelectedRegistryRow();
  if (!row) {
    bar.hidden = true;
    label.textContent = "Row selected";
    return;
  }
  bar.hidden = false;
  let summary = `#${row.id}`;
  if (activeRegistryTab === "maintenance") {
    summary = `#${row.id} · ${row.subsystem_component || row.equipment_title || "Maintenance"}`;
  } else if (activeRegistryTab === "spare_parts") {
    summary = `#${row.id} · ${row.part_name || row.equipment_title || "Spare part"}`;
  } else if (activeRegistryTab === "troubleshooting") {
    summary = `#${row.id} · ${row.problem || row.equipment_title || "Issue"}`;
  } else if (row.maintenance_work_description) {
    summary = `#${row.id} · ${String(row.maintenance_work_description).slice(0, 48)}`;
  }
  label.textContent = summary;
  safeCreateIcons();
}

function buildAskAboutRowQuery(row) {
  if (activeRegistryTab === "spare_parts") {
    return `Explain this spare part from the manual in more detail: Equipment "${row.equipment_title || "NA"}", part "${row.part_name || "NA"}", part number "${row.part_number_code || "NA"}", location "${row.subsystem_location || "NA"}". What should the technician know?`;
  }
  if (activeRegistryTab === "troubleshooting") {
    return `Explain this troubleshooting item from the manual: Equipment "${row.equipment_title || "NA"}", problem "${row.problem || "NA"}", solution "${row.root_cause_solution || "NA"}". Expand with any related guidance in the document.`;
  }
  if (activeEquipmentCategory === "Logbook" || row.maintenance_work_description) {
    return `Explain this field history / logbook entry using the document context: Date "${row.date || "NA"}", work "${row.maintenance_work_description || "NA"}", parts renewed "${row.parts_renewed || "NA"}", attended by "${row.attended_by || "NA"}".`;
  }
  return `Explain this maintenance task from the manual in more detail: Equipment "${row.equipment_title || "NA"}", component "${row.subsystem_component || "NA"}", routine "${row.maintenance_routine || "NA"}", instructions: "${row.checks_instructions || "NA"}". Cite the relevant page if possible.`;
}

function askAboutSelectedRow() {
  const row = getSelectedRegistryRow();
  if (!row) {
    appendChatSystemMessage("Select a registry row first, then click **Ask about row**.");
    return;
  }
  if (!loadedPages || loadedPages.length === 0) {
    appendChatSystemMessage("Upload a document first so Copilot can answer from the manual.");
    return;
  }
  processCognitiveChatSearch(buildAskAboutRowQuery(row));
}

window.jumpToPageContext = function(pageNum) {
  const n = Number(pageNum);
  const modal = document.getElementById("page-context-modal");
  const title = document.getElementById("page-context-title");
  const body = document.getElementById("page-context-body");
  if (!modal || !title || !body) return;

  const page = (loadedPages || []).find(p => Number(p.pageNum) === n);
  title.textContent = `Page ${n}`;
  if (!page || !page.text) {
    body.textContent = "No extracted text is available for this page. Re-run extraction with Native/OCR so Copilot can show the source text.";
  } else {
    body.textContent = page.text.trim();
  }
  modal.hidden = false;

  // Highlight registry rows that cite this page
  const matchingIds = getActiveRegistry()
    .filter(r => Number(r.page) === n)
    .map(r => r.id);
  if (matchingIds.length > 0) {
    highlightRecordIds = matchingIds;
    const oldChip = document.getElementById("chat-filter-chip");
    if (oldChip) oldChip.remove();
    const chip = document.createElement("button");
    chip.className = "tab-btn active";
    chip.id = "chat-filter-chip";
    chip.innerHTML = `<i data-lucide="file-text" style="width:12px;height:12px;display:inline-block;margin-right:4px;"></i>Page ${n} rows`;
    document.querySelectorAll(".tab-btn").forEach(btn => {
      if (btn.id !== "chat-filter-chip") btn.classList.remove("active");
    });
    filterTabs.appendChild(chip);
    chip.addEventListener("click", () => {
      highlightRecordIds = [];
      chip.remove();
      const allBtn = document.querySelector(".tab-btn[data-filter='all']");
      if (allBtn) allBtn.click();
    });
    renderGrid();
    safeCreateIcons();
  }
};

function closePageContextModal() {
  const modal = document.getElementById("page-context-modal");
  if (modal) modal.hidden = true;
}

// Triggered by the chatbot filter action buttons
window.applyChatFilter = function(rowIds) {
  highlightRecordIds = rowIds;
  
  // Visual state indicator on filter tab
  document.querySelectorAll(".tab-btn").forEach(btn => btn.classList.remove("active"));
  
  const chip = document.createElement("button");
  chip.className = "tab-btn active";
  chip.id = "chat-filter-chip";
  chip.innerHTML = `<i data-lucide="sparkles" style="width:12px;height:12px;display:inline-block;margin-right:4px;"></i>AI Filtered Result`;
  
  // Remove existing AI filter chip if present
  const oldChip = document.getElementById("chat-filter-chip");
  if (oldChip) oldChip.remove();
  
  filterTabs.appendChild(chip);
  safeCreateIcons();
  
  chip.addEventListener("click", () => {
    highlightRecordIds = [];
    chip.remove();
    document.querySelector(".tab-btn[data-filter='all']").click();
  });

  renderGrid();
};

// Chat Form Listener
chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = chatInput.value.trim();
  if (q) {
    processCognitiveChatSearch(q);
    chatInput.value = "";
  }
});

const askSelectedBtn = document.getElementById("ask-selected-btn");
const askSelectedClear = document.getElementById("ask-selected-clear");
const pageContextModal = document.getElementById("page-context-modal");
const pageContextClose = document.getElementById("page-context-close");

if (askSelectedBtn) {
  askSelectedBtn.addEventListener("click", (e) => {
    e.preventDefault();
    askAboutSelectedRow();
  });
}
if (askSelectedClear) {
  askSelectedClear.addEventListener("click", (e) => {
    e.preventDefault();
    clearSelectedRegistryRow();
  });
}
if (pageContextClose) {
  pageContextClose.addEventListener("click", closePageContextModal);
}
if (pageContextModal) {
  pageContextModal.addEventListener("click", (e) => {
    if (e.target === pageContextModal) closePageContextModal();
  });
}

const qualityScoreCard = document.getElementById("card-quality-score");
const qualityScoreModal = document.getElementById("quality-score-modal");
const qualityScoreClose = document.getElementById("quality-score-close");
if (qualityScoreCard) {
  qualityScoreCard.addEventListener("click", openQualityScoreModal);
}
if (qualityScoreClose) {
  qualityScoreClose.addEventListener("click", closeQualityScoreModal);
}
if (qualityScoreModal) {
  qualityScoreModal.addEventListener("click", (e) => {
    if (e.target === qualityScoreModal) closeQualityScoreModal();
  });
}

const rowConfidenceModal = document.getElementById("row-confidence-modal");
const rowConfidenceClose = document.getElementById("row-confidence-close");
if (rowConfidenceClose) {
  rowConfidenceClose.addEventListener("click", closeRowConfidenceModal);
}
if (rowConfidenceModal) {
  rowConfidenceModal.addEventListener("click", (e) => {
    if (e.target === rowConfidenceModal) closeRowConfidenceModal();
  });
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeRowConfidenceModal();
});
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".confidence-btn");
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  const row = findRegistryRowById(btn.getAttribute("data-row-id"));
  if (row) openRowConfidenceModal(row);
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closePageContextModal();
    closeQualityScoreModal();
    closeRejectionModal();
    closeDocMetadataModal();
  }
});

/* -------------------------------------------------------------
 * 5. Review & Approval Lifecycle + Document Metadata Handlers
 * ------------------------------------------------------------- */

function getRegistryArray(regType) {
  if (regType === "maintenance") return maintenanceRegistry;
  if (regType === "spare_parts") return sparePartsRegistry;
  if (regType === "troubleshooting") return troubleshootingRegistry;
  return activeRegistryTab === "spare_parts" ? sparePartsRegistry : (activeRegistryTab === "troubleshooting" ? troubleshootingRegistry : maintenanceRegistry);
}

let activeFabricRunId = null;
let autoSaveDebounceTimer = null;
let reviewSyncDebounceTimer = null;

function scheduleReviewSync(delayMs = 800) {
  if (reviewSyncDebounceTimer) clearTimeout(reviewSyncDebounceTimer);
  reviewSyncDebounceTimer = setTimeout(() => {
    reviewSyncDebounceTimer = null;
    syncReviewStateToFabric();
  }, delayMs);
}

function triggerAutoSaveDebounce() {
  if (autoSaveDebounceTimer) clearTimeout(autoSaveDebounceTimer);
  autoSaveDebounceTimer = setTimeout(() => {
    saveWorkspaceEdits(true);
  }, 1200);
}

async function saveWorkspaceEdits(isAutoSave = false) {
  const allRows = [...maintenanceRegistry, ...sparePartsRegistry, ...troubleshootingRegistry];
  if (allRows.length === 0) return;

  if (activeDocumentStatus === "Approved" || activeDocumentStatus === "Pending Review" || !activeDocumentStatus) {
    activeDocumentStatus = "In Review";
    updateDocMetadataBadge();
  }

  const saveBtn = document.getElementById("save-changes-btn");
  const origHtml = saveBtn ? saveBtn.innerHTML : "";
  if (saveBtn && !isAutoSave) {
    saveBtn.disabled = true;
    saveBtn.innerHTML = `<i data-lucide="loader-2" class="spin"></i><span>Saving…</span>`;
    if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
  }

  try {
    const resp = await syncReviewStateToFabric();
    if (saveBtn && !isAutoSave) {
      saveBtn.innerHTML = `<i data-lucide="check"></i><span>Saved ✓</span>`;
      if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
      setTimeout(() => {
        if (saveBtn) {
          saveBtn.disabled = false;
          saveBtn.innerHTML = origHtml;
          if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
        }
      }, 1500);
    }
    if (!isAutoSave) {
      appendChatSystemMessage(`Workspace edits saved to Microsoft Fabric (Status: **${activeDocumentStatus}**).`);
    }
    return resp;
  } catch (err) {
    if (saveBtn && !isAutoSave) {
      saveBtn.disabled = false;
      saveBtn.innerHTML = origHtml;
      if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
    }
    console.error("Save workspace edits error:", err);
  }
}

async function syncReviewStateToFabric() {
  if (!activeFabricRunId && lastExtractMeta && lastExtractMeta.run_id) {
    activeFabricRunId = lastExtractMeta.run_id;
  }
  if (!activeFabricRunId) {
    console.debug("No active Fabric run ID to sync review state.");
    return;
  }

  const sanitizeRow = (r) => {
    if (!r || typeof r !== "object") return {};
    const cleaned = { ...r };
    if (cleaned.confidence !== undefined) {
      const num = parseFloat(cleaned.confidence);
      cleaned.confidence = isNaN(num) ? 1.0 : num;
    }
    return cleaned;
  };

  try {
    const headers = { "Content-Type": "application/json" };
    if (typeof window.getAuthHeaders === "function") {
      Object.assign(headers, window.getAuthHeaders());
    }

    let syncStatus = activeDocumentStatus || "Pending Review";
    if (!canApproveOrSignOff() && syncStatus === "Approved") {
      syncStatus = "In Review";
      activeDocumentStatus = "In Review";
    }

    const payload = {
      document_status: syncStatus,
      approved_by: activeApprovedBy || getCurrentUserEmail(),
      approved_at: activeApprovedAt || (syncStatus === "Approved" ? new Date().toISOString() : null),
      rejection_notes: syncStatus === "Needs Revision" ? "Flagged during technical review" : null,
      doc_metadata: activeDocumentMetadata || null,
      spare_parts: Array.isArray(sparePartsRegistry) ? sparePartsRegistry.map(sanitizeRow) : [],
      maintenance: Array.isArray(maintenanceRegistry) ? maintenanceRegistry.map(sanitizeRow) : [],
      troubleshooting: Array.isArray(troubleshootingRegistry) ? troubleshootingRegistry.map(sanitizeRow) : [],
    };
    const resp = await fetch(`${apiBaseUrl}/api/fabric/extracts/${encodeURIComponent(activeFabricRunId)}/review-sync`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
    if (resp.ok) {
      console.log("Fabric review state & workspace records synced successfully:", payload.document_status);
      if (typeof updateDiffToolbarButtons === "function") updateDiffToolbarButtons();
    } else {
      const errJson = await resp.json().catch(() => ({}));
      console.warn("Fabric review sync warning:", resp.status, errJson);
    }
    return resp;
  } catch (e) {
    console.warn("Fabric review sync error:", e);
  }
}

function approveRow(regType, rowId) {
  if (!canApproveOrSignOff()) {
    alert("Permission Denied: Only Approvers and Admins can approve records.");
    return;
  }
  const reg = getRegistryArray(regType);
  const row = reg.find(r => r.id === rowId);
  if (row) {
    row.status = "Approved";
    row.reviewed_by = getCurrentUserEmail();
    row.reviewed_at = new Date().toISOString();
    delete row.rejection_reason;
    renderGridPreservingScroll();
    checkAutoUpdateDocumentStatus();
    scheduleReviewSync();
  }
}

function openRejectionModal(regType, rowId) {
  if (!canApproveOrSignOff()) {
    alert("Permission Denied: Only Approvers and Admins can reject records.");
    return;
  }
  pendingRejectInfo = { regType, rowId };
  const quickSelect = document.getElementById("rejection-quick-reason");
  const notes = document.getElementById("rejection-notes");
  if (quickSelect) quickSelect.value = "";
  if (notes) notes.value = "";
  const modal = document.getElementById("rejection-modal");
  if (modal) modal.hidden = false;
}

function closeRejectionModal() {
  pendingRejectInfo = null;
  const modal = document.getElementById("rejection-modal");
  if (modal) modal.hidden = true;
}

function confirmRejection() {
  if (!pendingRejectInfo) return;
  const { regType, rowId } = pendingRejectInfo;
  const quickReason = document.getElementById("rejection-quick-reason")?.value || "";
  const notes = document.getElementById("rejection-notes")?.value.trim() || "";
  const finalReason = [quickReason, notes].filter(Boolean).join(": ") || "Rejected during technical review";

  const reg = getRegistryArray(regType);
  const row = reg.find(r => r.id === rowId);
  if (row) {
    row.status = "Rejected";
    row.rejection_reason = finalReason;
    row.reviewed_by = getCurrentUserEmail();
    row.reviewed_at = new Date().toISOString();
    renderGridPreservingScroll();
    checkAutoUpdateDocumentStatus();
    scheduleReviewSync();
  }
  closeRejectionModal();
}

function checkAutoUpdateDocumentStatus() {
  const allRows = [...maintenanceRegistry, ...sparePartsRegistry, ...troubleshootingRegistry];
  if (allRows.length === 0) return;
  const anyRejected = allRows.some(r => r.status === "Rejected");
  const allApproved = allRows.every(r => r.status === "Approved");

  if (allApproved) {
    activeDocumentStatus = "Approved";
    activeApprovedBy = getCurrentUserEmail();
    activeApprovedAt = new Date().toISOString();
  } else if (anyRejected) {
    activeDocumentStatus = "Needs Revision";
  } else {
    activeDocumentStatus = "Pending Review";
  }
  updateDocMetadataBadge();
}

function approveAllRecords() {
  if (!canApproveOrSignOff()) {
    alert("Permission Denied: Only Approvers and Admins can perform final document sign-off.");
    return;
  }
  const total = maintenanceRegistry.length + sparePartsRegistry.length + troubleshootingRegistry.length;
  if (total === 0) {
    alert("No records to sign off.");
    return;
  }
  const ok = confirm(`Sign off and approve all ${total} extracted records for this document?`);
  if (!ok) return;

  const email = getCurrentUserEmail();
  const now = new Date().toISOString();

  [...maintenanceRegistry, ...sparePartsRegistry, ...troubleshootingRegistry].forEach(r => {
    r.status = "Approved";
    r.reviewed_by = email;
    r.reviewed_at = now;
    delete r.rejection_reason;
  });

  activeDocumentStatus = "Approved";
  activeApprovedBy = email;
  activeApprovedAt = now;

  updateDocMetadataBadge();
  renderGridPreservingScroll();
  appendChatSystemMessage(`Document and all **${total} records** signed off and Approved by **${email}**.`);
  syncReviewStateToFabric();
}

function updateDocMetadataBadge() {
  const badge = document.getElementById("doc-meta-badge");
  const textEl = document.getElementById("doc-meta-badge-text");
  if (!badge) return;
  if (activeDocumentMetadata || lastExtractMeta) {
    badge.style.display = "inline-flex";
    const statusText = activeDocumentStatus === "Approved"
      ? (lastExtractMeta && lastExtractMeta.already_approved ? "✓ Already Approved" : "✓ Approved")
      : "Sign-Off";
    const title = (activeDocumentMetadata && activeDocumentMetadata.equipment_model) || (activeDocumentMetadata && activeDocumentMetadata.title) || "Metadata";
    if (textEl) textEl.textContent = `${title} (${statusText})`;
  } else {
    badge.style.display = "none";
  }
}

function openDocMetadataModal() {
  const meta = activeDocumentMetadata || {};
  const setVal = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val || "—";
  };
  setVal("meta-doc-title", meta.title || lastSourceDocName || "NA");
  setVal("meta-doc-oem", meta.oem_manufacturer || "NA");
  setVal("meta-doc-model", meta.equipment_model || "NA");
  setVal("meta-doc-type", meta.equipment_type || activeEquipmentCategory || "NA");
  setVal("meta-doc-version", meta.document_version || "NA");
  setVal("meta-doc-date", meta.publication_date || "NA");
  setVal("meta-doc-approver", activeApprovedBy || (activeDocumentStatus === "Approved" ? "Authorized Reviewer" : "Pending Sign-Off"));

  const statusEl = document.getElementById("meta-doc-status");
  if (statusEl) {
    statusEl.textContent = activeDocumentStatus;
    statusEl.className = `status-pill ${activeDocumentStatus === 'Approved' ? 'status-approved' : (activeDocumentStatus === 'Needs Revision' ? 'status-rejected' : 'status-pending')}`;
  }

  const modal = document.getElementById("doc-metadata-modal");
  if (modal) modal.hidden = false;
}

function closeDocMetadataModal() {
  const modal = document.getElementById("doc-metadata-modal");
  if (modal) modal.hidden = true;
}

function requestNotificationPermission() {
  if (typeof Notification !== "undefined" && Notification.permission === "default") {
    Notification.requestPermission().catch(() => {});
  }
}

function notifyExtractionFinished(filename, maintCount, sparesCount, troubleCount) {
  if (typeof Notification !== "undefined" && Notification.permission === "granted") {
    const total = maintCount + sparesCount + troubleCount;
    const body = `${filename}: Extracted ${total} total records (${maintCount} maintenance, ${sparesCount} parts, ${troubleCount} troubleshooting). Ready for review.`;
    try {
      const n = new Notification("IDP AI Agent — Extraction Complete", {
        body,
        tag: `extract-${Date.now()}`
      });
      n.onclick = () => {
        window.focus();
        n.close();
      };
    } catch (e) {}
  }
}

async function signOffPartialRecords() {
  if (!canApproveOrSignOff()) {
    alert("Permission Denied: Only Approvers and Admins can perform sign-off.");
    return;
  }
  const allRows = [...maintenanceRegistry, ...sparePartsRegistry, ...troubleshootingRegistry];
  if (allRows.length === 0) {
    alert("No records in the workspace to sign off.");
    return;
  }

  const email = getCurrentUserEmail();
  const now = new Date().toISOString();

  // If reviewer is signing off, stamp reviewed rows with email if not already set
  allRows.forEach(r => {
    if (r.status === "Approved" && !r.reviewed_by) {
      r.reviewed_by = email;
      r.reviewed_at = now;
    }
  });

  checkAutoUpdateDocumentStatus();
  if (activeDocumentStatus === "Approved" && !activeApprovedBy) {
    activeApprovedBy = email;
    activeApprovedAt = now;
  }

  updateDocMetadataBadge();
  renderGridPreservingScroll();

  const approved = allRows.filter(r => r.status === "Approved").length;
  const rejected = allRows.filter(r => r.status === "Rejected").length;
  const pending = allRows.filter(r => (r.status || "Pending Review") === "Pending Review").length;

  const signoffBtn = document.getElementById("signoff-btn");
  const origHtml = signoffBtn ? signoffBtn.innerHTML : "";
  if (signoffBtn) {
    signoffBtn.disabled = true;
    signoffBtn.innerHTML = `<i data-lucide="loader-2" class="spin"></i><span>Syncing…</span>`;
    if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
  }

  try {
    await syncReviewStateToFabric();
    appendChatSystemMessage(`Sign-off state synced to Microsoft Fabric: **${approved} Approved**, **${rejected} Rejected**, **${pending} Pending** (Document Status: **${activeDocumentStatus}**).`);
    if (signoffBtn) {
      signoffBtn.innerHTML = `<i data-lucide="check-check"></i><span>Saved</span>`;
      if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
      setTimeout(() => {
        if (signoffBtn) {
          signoffBtn.disabled = false;
          signoffBtn.innerHTML = origHtml;
          if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
        }
      }, 1500);
    }
  } catch (err) {
    if (signoffBtn) {
      signoffBtn.disabled = false;
      signoffBtn.innerHTML = origHtml;
      if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
    }
    console.error("Sign-off sync error:", err);
  }
}

async function submitDocumentForReview() {
  const allRows = [...maintenanceRegistry, ...sparePartsRegistry, ...troubleshootingRegistry];
  if (allRows.length === 0) {
    alert("No records in the workspace to submit for review.");
    return;
  }

  activeDocumentStatus = "Pending Sign-Off";
  updateDocMetadataBadge();
  renderGridPreservingScroll();

  const signoffBtn = document.getElementById("signoff-btn");
  if (signoffBtn) {
    signoffBtn.disabled = true;
    signoffBtn.innerHTML = `<i data-lucide="loader-2" class="spin"></i><span>Submitting…</span>`;
    if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
  }

  try {
    await syncReviewStateToFabric();
    appendChatSystemMessage(`Document submitted for approval. Status changed to **Pending Sign-Off**.`);
    if (signoffBtn) {
      signoffBtn.innerHTML = `<i data-lucide="check-check"></i><span>Submitted</span>`;
      if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
      setTimeout(() => {
        if (signoffBtn) {
          signoffBtn.disabled = false;
          updateRoleActionButtons();
        }
      }, 1500);
    }
  } catch (err) {
    if (signoffBtn) {
      signoffBtn.disabled = false;
      updateRoleActionButtons();
    }
    alert("Failed to submit review state: " + (err.message || err));
  }
}

function updateRoleActionButtons() {
  const allowApprove = canApproveOrSignOff();
  const signoffBtn = document.getElementById("signoff-btn");
  const signoffAllBtn = document.getElementById("signoff-all-btn");
  const saveBtn = document.getElementById("save-changes-btn");

  const totalRecords = maintenanceRegistry.length + sparePartsRegistry.length + troubleshootingRegistry.length;

  if (saveBtn) {
    saveBtn.style.display = totalRecords > 0 ? "inline-flex" : "none";
  }

  if (signoffAllBtn) {
    signoffAllBtn.style.display = (allowApprove && totalRecords > 0) ? "inline-flex" : "none";
  }

  if (signoffBtn) {
    signoffBtn.style.display = totalRecords > 0 ? "inline-flex" : "none";
    if (allowApprove) {
      signoffBtn.title = "Sync review status and partial sign-off to Microsoft Fabric";
      signoffBtn.innerHTML = `<i data-lucide="check"></i><span>Sign-Off</span>`;
    } else {
      signoffBtn.title = "Submit workspace changes for approver review";
      signoffBtn.innerHTML = `<i data-lucide="send"></i><span>Submit for Review</span>`;
    }
  }
  if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
}
window.updateRoleActionButtons = updateRoleActionButtons;

// Wire review toolbar and modal event listeners
const statusFilterSelect = document.getElementById("status-filter");
if (statusFilterSelect) {
  statusFilterSelect.addEventListener("change", (e) => {
    currentStatusFilter = e.target.value;
    renderGridPreservingScroll();
  });
}

const saveChangesBtn = document.getElementById("save-changes-btn");
if (saveChangesBtn) {
  saveChangesBtn.addEventListener("click", (e) => {
    e.preventDefault();
    saveWorkspaceEdits(false);
  });
}

const signoffBtn = document.getElementById("signoff-btn");
if (signoffBtn) {
  signoffBtn.addEventListener("click", (e) => {
    e.preventDefault();
    if (canApproveOrSignOff()) {
      signOffPartialRecords();
    } else {
      submitDocumentForReview();
    }
  });
}

const signoffAllBtn = document.getElementById("signoff-all-btn");
if (signoffAllBtn) {
  signoffAllBtn.addEventListener("click", (e) => {
    e.preventDefault();
    approveAllRecords();
  });
}

// Call updateRoleActionButtons initially
if (typeof updateRoleActionButtons === "function") {
  setTimeout(updateRoleActionButtons, 100);
}

const docMetaBadge = document.getElementById("doc-meta-badge");
if (docMetaBadge) {
  docMetaBadge.addEventListener("click", openDocMetadataModal);
}
const docMetadataClose = document.getElementById("doc-metadata-close");
if (docMetadataClose) {
  docMetadataClose.addEventListener("click", closeDocMetadataModal);
}
const docMetadataDoneBtn = document.getElementById("doc-metadata-done-btn");
if (docMetadataDoneBtn) {
  docMetadataDoneBtn.addEventListener("click", closeDocMetadataModal);
}
const docMetadataModal = document.getElementById("doc-metadata-modal");
if (docMetadataModal) {
  docMetadataModal.addEventListener("click", (e) => {
    if (e.target === docMetadataModal) closeDocMetadataModal();
  });
}

const rejectionModalClose = document.getElementById("rejection-modal-close");
if (rejectionModalClose) {
  rejectionModalClose.addEventListener("click", closeRejectionModal);
}
const rejectionCancelBtn = document.getElementById("rejection-cancel-btn");
if (rejectionCancelBtn) {
  rejectionCancelBtn.addEventListener("click", closeRejectionModal);
}
const rejectionConfirmBtn = document.getElementById("rejection-confirm-btn");
if (rejectionConfirmBtn) {
  rejectionConfirmBtn.addEventListener("click", (e) => {
    e.preventDefault();
    confirmRejection();
  });
}
const rejectionModal = document.getElementById("rejection-modal");
if (rejectionModal) {
  rejectionModal.addEventListener("click", (e) => {
    if (e.target === rejectionModal) closeRejectionModal();
  });
}

// -------------------------------------------------------------
// Share Extraction Modal & Public Shared View (24 Hours)
// -------------------------------------------------------------

async function openShareModal() {
  if (!activeFabricRunId && lastExtractMeta && lastExtractMeta.run_id) {
    activeFabricRunId = lastExtractMeta.run_id;
  }
  const totalRows = maintenanceRegistry.length + sparePartsRegistry.length + troubleshootingRegistry.length;
  if (!activeFabricRunId || totalRows === 0) {
    alert("Please extract or load a document into the workspace first to generate a share link.");
    return;
  }

  const modal = document.getElementById("share-modal");
  const urlInput = document.getElementById("share-url-input");
  const expiryLabel = document.getElementById("share-expiry-label");
  const copyBtn = document.getElementById("share-copy-btn");
  const copyText = document.getElementById("share-copy-text");

  if (modal) modal.hidden = false;
  if (urlInput) urlInput.value = "Generating secure 24-hour link…";
  if (copyBtn) copyBtn.disabled = true;

  try {
    const headers = {};
    if (typeof window.getAuthHeaders === "function") {
      Object.assign(headers, window.getAuthHeaders());
    }
    const resp = await fetch(`${apiBaseUrl}/api/fabric/extracts/${encodeURIComponent(activeFabricRunId)}/share`, {
      method: "POST",
      headers,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const errDetail = typeof data.detail === "string" ? data.detail : (data.detail || `HTTP ${resp.status}`);
      throw new Error(typeof errDetail === "string" ? errDetail : JSON.stringify(errDetail));
    }

    const shareUrl = `${window.location.origin}${window.location.pathname}?share=${encodeURIComponent(data.share_token)}`;
    if (urlInput) {
      urlInput.value = shareUrl;
      urlInput.select();
    }
    if (expiryLabel && data.expires_at) {
      const expDate = new Date(data.expires_at);
      expiryLabel.textContent = `Valid for 24 hours (Expires: ${expDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}, ${expDate.toLocaleDateString()})`;
    }
    if (copyBtn) copyBtn.disabled = false;
  } catch (err) {
    console.error("Share error:", err);
    if (urlInput) urlInput.value = "Failed to generate link: " + (err.message || err);
  }
}

function closeShareModal() {
  const modal = document.getElementById("share-modal");
  if (modal) modal.hidden = true;
}

function copyShareLink() {
  const urlInput = document.getElementById("share-url-input");
  const copyText = document.getElementById("share-copy-text");
  if (!urlInput || !urlInput.value || urlInput.value.startsWith("Generating") || urlInput.value.startsWith("Failed")) return;

  navigator.clipboard.writeText(urlInput.value).then(() => {
    if (copyText) copyText.textContent = "✓ Copied!";
    setTimeout(() => {
      if (copyText) copyText.textContent = "Copy Link";
    }, 2000);
  }).catch(() => {
    urlInput.select();
    document.execCommand("copy");
    if (copyText) copyText.textContent = "✓ Copied!";
    setTimeout(() => {
      if (copyText) copyText.textContent = "Copy Link";
    }, 2000);
  });
}

const shareBtn = document.getElementById("share-btn");
if (shareBtn) {
  shareBtn.addEventListener("click", (e) => {
    e.preventDefault();
    openShareModal();
  });
}
const shareModalClose = document.getElementById("share-modal-close");
if (shareModalClose) {
  shareModalClose.addEventListener("click", closeShareModal);
}
const shareModalDoneBtn = document.getElementById("share-modal-done-btn");
if (shareModalDoneBtn) {
  shareModalDoneBtn.addEventListener("click", closeShareModal);
}
const shareCopyBtn = document.getElementById("share-copy-btn");
if (shareCopyBtn) {
  shareCopyBtn.addEventListener("click", copyShareLink);
}
const shareModal = document.getElementById("share-modal");
if (shareModal) {
  shareModal.addEventListener("click", (e) => {
    if (e.target === shareModal) closeShareModal();
  });
}

async function loadSharedExtract(shareToken) {
  if (!shareToken) return;
  document.body.classList.add("is-shared-viewer");

  setExtractingUi(true, "Loading Shared Extract", "Validating 24-hour security token…");
  if (progressFill) progressFill.style.width = "40%";

  try {
    const resp = await fetch(`${apiBaseUrl}/api/share/${encodeURIComponent(shareToken)}`);
    const contentType = resp.headers.get("content-type") || "";
    if (!contentType.includes("application/json") || !resp.ok) {
      if (!contentType.includes("application/json") && resp.status === 200) {
        throw new Error("Access Denied: You do not have permission to view this shared extract.");
      }
      const data = await resp.json().catch(() => ({}));
      const detail = typeof data.detail === "string" ? data.detail : (data.detail || `HTTP ${resp.status}`);
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    const data = await resp.json().catch(() => ({}));

    const filename = data.filename || (data.meta && data.meta.filename) || "Document";
    lastSourceDocName = filename;
    activeFabricRunId = data.run_id;

    if (progressFill) progressFill.style.width = "90%";
    if (progressStatus) progressStatus.innerText = "Rehydrating registries…";

    applyApiExtractResult(data, { name: filename });

    // Show separate badge cards in header directly to the left of the light/dark toggle
    const viewBadge = document.getElementById("shared-view-badge");
    const timerBadge = document.getElementById("shared-timer-badge");
    const timerSpan = document.getElementById("shared-header-timer");
    if (viewBadge) viewBadge.hidden = false;
    if (timerBadge) timerBadge.hidden = false;
    if (timerSpan && data.expires_at) {
      const expDate = new Date(data.expires_at);
      timerSpan.textContent = `Active until ${expDate.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}, ${expDate.toLocaleDateString()}`;
    }
    if (typeof safeCreateIcons === "function") safeCreateIcons();
    appendChatSystemMessage(`Viewing shared extraction table for **${filename}** (Read-Only Mode).`);
  } catch (err) {
    console.error(err);
    const msg = String(err && err.message ? err.message : err);
    alert(msg);
    setActiveDocBadge("");
    clearExtractingUi();
  }
}

/* -------------------------------------------------------------
 * 6. Application Bootstrapper
 * ------------------------------------------------------------- */

async function loadFabricExtract(runId) {
  if (!runId) return;

  try {
    if (typeof window.requireAuthForApi === "function") window.requireAuthForApi();
  } catch (e) {
    return;
  }

  const authHeaders = (typeof window.getAuthHeaders === "function") ? window.getAuthHeaders() : {};
  setExtractingUi(true, "Loading Document", "Fetching extracted records from Microsoft Fabric…");
  if (progressFill) progressFill.style.width = "35%";

  try {
    const resp = await fetch(`${apiBaseUrl}/api/fabric/extracts/${encodeURIComponent(runId)}`, {
      headers: authHeaders
    });
    const contentType = resp.headers.get("content-type") || "";
    if (!contentType.includes("application/json") || !resp.ok) {
      if (!contentType.includes("application/json") && resp.status === 200) {
        throw new Error("Access Denied: You do not have permission to view this extraction.");
      }
      const data = await resp.json().catch(() => ({}));
      const detail = typeof data.detail === "string" ? data.detail : (data.detail || `HTTP ${resp.status}`);
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    const data = await resp.json().catch(() => ({}));
    const filename = (data.meta && data.meta.filename) || "document.pdf";
    lastSourceDocName = filename;
    if (progressFill) progressFill.style.width = "90%";
    if (progressStatus) progressStatus.innerText = "Rehydrating registries and sign-off status…";
    activeFabricRunId = runId;
    applyApiExtractResult(data, { name: filename });
    appendChatSystemMessage(`Loaded saved extract from Fabric: **${filename}** (Status: **${activeDocumentStatus}**)`);
  } catch (err) {
    console.error(err);
    const msg = String(err && err.message ? err.message : err);
    alert("Could not load Fabric extract: " + msg);
    setActiveDocBadge("");
    clearExtractingUi();
  }
}
window.loadFabricExtract = loadFabricExtract;

async function loadFabricRunFromQuery() {
  let runId = "";
  try {
    const params = new URLSearchParams(window.location.search || "");
    runId = (params.get("fabric_run_id") || "").trim();
  } catch (e) {
    return;
  }
  if (!runId) return;

  try {
    await loadFabricExtract(runId);
  } finally {
    try {
      const url = new URL(window.location.href);
      url.searchParams.delete("fabric_run_id");
      window.history.replaceState({}, "", url.pathname + url.search + url.hash);
    } catch (e) {}
  }
}

/* ---------------- Persistent in-app notifications ---------------- */
let pendingApprovalsTimer = null;
let cachedPendingApprovals = [];
let cachedNotifications = [];

function notificationDeepLink(runId) {
  const rid = encodeURIComponent(String(runId || "").trim());
  return `index.html?fabric_run_id=${rid}`;
}

async function markNotificationRead(notifId) {
  if (!notifId) return;
  try {
    const headers = typeof window.getAuthHeaders === "function" ? window.getAuthHeaders() : {};
    await fetch(`${apiBaseUrl}/api/notifications/${encodeURIComponent(notifId)}/read`, {
      method: "POST",
      headers,
    });
  } catch (e) {}
}

async function fetchNotifications() {
  if (typeof window.isLoggedIn === "function" && !window.isLoggedIn()) {
    return;
  }

  try {
    const headers = typeof window.getAuthHeaders === "function" ? window.getAuthHeaders() : {};
    const resp = await fetch(`${apiBaseUrl}/api/notifications`, { headers });
    if (!resp.ok) return;
    const data = await resp.json().catch(() => ({}));
    const items = Array.isArray(data.items) ? data.items : [];
    cachedNotifications = items;
    cachedPendingApprovals = items.filter(n => n.event_type === "submitted" && !n.read);
    renderNotificationsUi(items, Number(data.unread || 0));
  } catch (err) {
    console.debug("Notifications fetch error:", err);
  }
}

async function fetchPendingApprovals() {
  return fetchNotifications();
}
window.fetchPendingApprovals = fetchPendingApprovals;
window.fetchNotifications = fetchNotifications;

function notificationEventLabel(eventType) {
  switch (String(eventType || "")) {
    case "submitted": return "Submitted for review";
    case "signed_off": return "Signed off";
    case "revision_requested": return "Revision requested";
    case "already_approved": return "Previously signed off";
    default: return "Update";
  }
}

function renderNotificationsUi(items, unreadOverride) {
  const countBadge = document.getElementById("approvals-notif-count");
  const bellBtn = document.getElementById("approvals-notif-btn");
  const headerBadge = document.getElementById("approvals-header-badge");
  const listEl = document.getElementById("approvals-dropdown-list");

  const unread = typeof unreadOverride === "number"
    ? unreadOverride
    : items.filter(n => !n.read).length;
  if (countBadge) {
    countBadge.textContent = String(unread);
    countBadge.hidden = unread === 0;
  }
  if (bellBtn) {
    bellBtn.classList.toggle("has-pending", unread > 0);
  }
  if (headerBadge) {
    headerBadge.textContent = unread === 1 ? "1 New" : `${unread} New`;
  }

  if (!listEl) return;

  if (!items.length) {
    listEl.innerHTML = `<div class="approvals-empty"><i data-lucide="check-circle-2" style="width: 28px; height: 28px; color: var(--accent-green, #10b981); margin-bottom: 0.5rem; display: inline-block;"></i><br>No notifications</div>`;
    if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
    return;
  }

  const role = getUserRole();
  const canQuickApprove = role === "admin" || role === "approver";
  listEl.innerHTML = items.map(n => {
    const runId = n.run_id || "";
    const docName = n.title || "Untitled Document";
    const href = n.url || notificationDeepLink(runId);
    const eventLabel = notificationEventLabel(n.event_type);
    const actor = n.actor_email ? `From ${escapeHTML(n.actor_email)}` : "";
    const unreadClass = n.read ? "" : " is-unread";
    const showApprove = n.event_type === "submitted" && canQuickApprove && runId;
    return `
      <div class="approvals-item${unreadClass}" data-run-id="${escapeHTML(runId)}" data-notif-id="${escapeHTML(n.id || "")}">
        <div class="approvals-item-top">
          <div class="approvals-item-name" title="${escapeHTML(docName)}">
            <i data-lucide="file-text" style="width: 14px; height: 14px; display: inline-block; vertical-align: -2px; margin-right: 4px; color: var(--accent-cyan, #06b6d4);"></i>
            ${escapeHTML(docName)}
          </div>
          <span class="status-pill status-pending" style="font-size: 0.68rem; padding: 0.1rem 0.4rem;">${escapeHTML(eventLabel)}</span>
        </div>
        <div class="approvals-item-meta">
          <span>${actor || escapeHTML(n.body || "")}</span>
        </div>
        <div class="approvals-item-actions">
          <a class="btn btn-sm btn-secondary approvals-review-btn notif-deeplink" href="${escapeHTML(href)}" data-run-id="${escapeHTML(runId)}" data-notif-id="${escapeHTML(n.id || "")}" title="Open document">
            <i data-lucide="external-link"></i>
            <span>Open</span>
          </a>
          ${showApprove ? `<button type="button" class="btn btn-sm btn-primary approvals-quick-approve-btn" data-run-id="${escapeHTML(runId)}" data-notif-id="${escapeHTML(n.id || "")}" title="Approve document">
            <i data-lucide="check"></i>
            <span>Approve</span>
          </button>` : ""}
        </div>
      </div>
    `;
  }).join("");

  if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
}

function renderPendingApprovalsUi(items) {
  renderNotificationsUi(items || cachedNotifications);
}

function initApprovalsNotificationUi() {
  const notifBtn = document.getElementById("approvals-notif-btn");
  const dropdown = document.getElementById("approvals-dropdown");
  const listEl = document.getElementById("approvals-dropdown-list");

  if (notifBtn && dropdown) {
    notifBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const willOpen = dropdown.hidden;
      dropdown.hidden = !willOpen;
      notifBtn.setAttribute("aria-expanded", String(willOpen));
      if (willOpen) fetchNotifications();
    });

    document.addEventListener("click", (e) => {
      if (!e.target.closest("#approvals-notif-wrap")) {
        dropdown.hidden = true;
        notifBtn.setAttribute("aria-expanded", "false");
      }
    });
  }

  if (listEl) {
    listEl.addEventListener("click", async (e) => {
      const reviewBtn = e.target.closest(".approvals-review-btn, .notif-deeplink");
      if (reviewBtn) {
        e.preventDefault();
        e.stopPropagation();
        const runId = reviewBtn.getAttribute("data-run-id");
        const notifId = reviewBtn.getAttribute("data-notif-id");
        if (dropdown) dropdown.hidden = true;
        if (notifId) markNotificationRead(notifId);
        if (runId) {
          try {
            const url = new URL(window.location.href);
            url.searchParams.set("fabric_run_id", runId);
            window.history.replaceState({}, "", url.pathname + url.search + url.hash);
          } catch (err) {}
          await loadFabricExtract(runId);
        }
        return;
      }

      const approveBtn = e.target.closest(".approvals-quick-approve-btn");
      if (approveBtn) {
        e.stopPropagation();
        const runId = approveBtn.getAttribute("data-run-id");
        const notifId = approveBtn.getAttribute("data-notif-id");
        approveBtn.disabled = true;
        approveBtn.innerHTML = `<i data-lucide="loader-2" class="spin"></i><span>Approving…</span>`;
        if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();

        try {
          const headers = { "Content-Type": "application/json" };
          if (typeof window.getAuthHeaders === "function") Object.assign(headers, window.getAuthHeaders());
          const email = getCurrentUserEmail();
          const resp = await fetch(`${apiBaseUrl}/api/fabric/extracts/${encodeURIComponent(runId)}/review-sync`, {
            method: "POST",
            headers,
            body: JSON.stringify({
              document_status: "Approved",
              approved_by: email,
              approved_at: new Date().toISOString(),
            })
          });
          if (!resp.ok) {
            const err = await resp.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${resp.status}`);
          }
          if (notifId) markNotificationRead(notifId);
          if (activeFabricRunId === runId) {
            activeDocumentStatus = "Approved";
            activeApprovedBy = email;
            activeApprovedAt = new Date().toISOString();
            [...maintenanceRegistry, ...sparePartsRegistry, ...troubleshootingRegistry].forEach(r => {
              if (r.status !== "Rejected") {
                r.status = "Approved";
                r.reviewed_by = email;
                r.reviewed_at = new Date().toISOString();
              }
            });
            updateDocMetadataBadge();
            renderGridPreservingScroll();
            appendChatSystemMessage(`Document and all records approved by **${email}**.`);
          }
          await fetchNotifications();
        } catch (err) {
          alert("Approval failed: " + (err.message || err));
          approveBtn.disabled = false;
        }
      }
    });
  }

  if (pendingApprovalsTimer) clearInterval(pendingApprovalsTimer);
  pendingApprovalsTimer = setInterval(fetchNotifications, 30000);
}

/* -------------------------------------------------------------
 * 7. Dual-Storage Audit Trail & Diff Comparison Engine
 * ------------------------------------------------------------- */

function getDiffStatistics() {
  if (!baselineExtraction) {
    return {
      totalAlterations: 0,
      cellsModified: 0,
      rowsAdded: 0,
      rowsDeleted: 0,
      spChanges: 0,
      mtChanges: 0,
      trChanges: 0,
      metaChanges: 0,
      detailed: { spare_parts: [], maintenance: [], troubleshooting: [], metadata: [] }
    };
  }

  let totalAlterations = 0;
  let cellsModified = 0;
  let rowsAdded = 0;
  let rowsDeleted = 0;

  const detailed = { spare_parts: [], maintenance: [], troubleshooting: [], metadata: [] };

  const checkCategory = (regType, currentList) => {
    const baseList = (baselineExtraction && baselineExtraction[regType]) || [];
    let count = 0;
    const cols = CANONICAL_DIFF_COLUMNS[regType] || [];
    const matchedBaseIdx = new Set();

    currentList.forEach((curr, idx) => {
      const base = getBaselineRow(regType, curr, idx);
      const isCustomRow = !base;

      if (isCustomRow) {
        rowsAdded++;
        totalAlterations++;
        count++;
        detailed[regType].push({
          id: curr.id || idx + 1,
          equipment_title: curr.equipment_title || "NA",
          isNew: true,
          isDeleted: false,
          row: curr,
          changes: [],
        });
      } else {
        const bIdx = baseList.indexOf(base);
        if (bIdx >= 0) matchedBaseIdx.add(bIdx);
        const rowChanges = [];

        cols.forEach(col => {
          const cVal = normalizeDiffVal(curr[col]);
          const bVal = normalizeDiffVal(base[col]);
          if (cVal !== bVal) {
            cellsModified++;
            totalAlterations++;
            count++;
            rowChanges.push({
              col,
              colLabel: formatColumnLabel(col),
              original: isEquivalentEmpty(base[col]) ? "NA" : String(base[col]).trim(),
              current: isEquivalentEmpty(curr[col]) ? "NA" : String(curr[col]).trim(),
            });
          }
        });

        if (rowChanges.length > 0) {
          detailed[regType].push({
            id: curr.id || idx + 1,
            equipment_title: curr.equipment_title || base.equipment_title || "NA",
            isNew: false,
            isDeleted: false,
            row: curr,
            baseRow: base,
            changes: rowChanges,
          });
        }
      }
    });

    baseList.forEach((base, bIdx) => {
      if (matchedBaseIdx.has(bIdx)) return;
      rowsDeleted++;
      totalAlterations++;
      count++;
      detailed[regType].push({
        id: base.id || bIdx + 1,
        equipment_title: base.equipment_title || "NA",
        isNew: false,
        isDeleted: true,
        row: null,
        baseRow: base,
        changes: [],
      });
    });

    return count;
  };

  const spChanges = checkCategory("spare_parts", sparePartsRegistry);
  const mtChanges = checkCategory("maintenance", maintenanceRegistry);
  const trChanges = checkCategory("troubleshooting", troubleshootingRegistry);

  // Check metadata changes
  let metaChanges = 0;
  const bMeta = (baselineExtraction && baselineExtraction.doc_metadata) || {};
  const cMeta = activeDocumentMetadata || {};
  const metaFields = [
    { key: "title", label: "Document Title" },
    { key: "oem_manufacturer", label: "OEM / Manufacturer" },
    { key: "equipment_model", label: "Equipment Model / Series" },
    { key: "equipment_type", label: "Equipment Type" },
    { key: "document_version", label: "Revision / Version" },
    { key: "publication_date", label: "Publication Date" },
  ];

  metaFields.forEach(({ key, label }) => {
    const cVal = normalizeDiffVal(cMeta[key]);
    const bVal = normalizeDiffVal(bMeta[key]);
    if (cVal !== bVal) {
      metaChanges++;
      totalAlterations++;
      detailed.metadata.push({
        field: label,
        original: isEquivalentEmpty(bMeta[key]) ? "NA" : String(bMeta[key]).trim(),
        current: isEquivalentEmpty(cMeta[key]) ? "NA" : String(cMeta[key]).trim(),
      });
    }
  });

  return { totalAlterations, cellsModified, rowsAdded, rowsDeleted, spChanges, mtChanges, trChanges, metaChanges, detailed };
}

function updateDiffToolbarButtons() {
  const diffBtn = document.getElementById("diff-view-btn");
  const modalBtn = document.getElementById("diff-compare-modal-btn");
  const badge = document.getElementById("diff-count-badge");

  if (!diffBtn || !modalBtn) return;

  if (baselineExtraction) {
    diffBtn.style.display = "inline-flex";
    modalBtn.style.display = "inline-flex";

    const stats = getDiffStatistics();
    if (badge) {
      badge.innerText = stats.totalAlterations;
      badge.style.display = stats.totalAlterations > 0 ? "inline-flex" : "none";
    }
    if (isDiffViewActive) {
      diffBtn.classList.add("diff-active");
    } else {
      diffBtn.classList.remove("diff-active");
    }
  } else {
    diffBtn.style.display = "none";
    modalBtn.style.display = "none";
  }
}

function renderDiffModalContent(activeTab, changesOnly = true) {
  const container = document.getElementById("diff-modal-content");
  if (!container) return;
  const stats = getDiffStatistics();

  const totalEl = document.getElementById("diff-stat-total");
  const modEl = document.getElementById("diff-stat-modified");
  const addEl = document.getElementById("diff-stat-added");
  const delEl = document.getElementById("diff-stat-deleted");
  const spEl = document.getElementById("diff-tab-count-sp");
  const mtEl = document.getElementById("diff-tab-count-mt");
  const trEl = document.getElementById("diff-tab-count-tr");
  const metaEl = document.getElementById("diff-tab-count-meta");

  if (totalEl) totalEl.innerText = stats.totalAlterations;
  if (modEl) modEl.innerText = stats.cellsModified;
  if (addEl) addEl.innerText = stats.rowsAdded;
  if (delEl) delEl.innerText = stats.rowsDeleted || 0;
  if (spEl) spEl.innerText = stats.spChanges;
  if (mtEl) mtEl.innerText = stats.mtChanges;
  if (trEl) trEl.innerText = stats.trChanges;
  if (metaEl) metaEl.innerText = stats.metaChanges;

  if (activeTab === "metadata") {
    if (stats.detailed.metadata.length === 0) {
      container.innerHTML = `
        <div class="diff-empty-state">
          <i data-lucide="check-circle-2" style="width: 36px; height: 36px; margin: 0 auto 0.5rem; color: #10b981; display: block;"></i>
          <p>No metadata alterations detected. AI baseline matches current document header.</p>
        </div>
      `;
      safeCreateIcons();
      return;
    }
    let html = `
      <div class="diff-row-card" style="border-left: 3px solid #06b6d4;">
        <div class="diff-row-header">
          <span>Document Header & Metadata</span>
          <span class="freq-tag tag-days">${stats.detailed.metadata.length} altered field(s)</span>
        </div>
        <div class="diff-row-changes-grid">
    `;
    stats.detailed.metadata.forEach(m => {
      html += `
        <div class="diff-field-card">
          <div class="diff-field-name">${escapeHTML(m.field)}</div>
          <div class="diff-val-compare">
            <span class="diff-val-ai" title="Original AI baseline"><i data-lucide="bot" style="width: 12px; height: 12px; display: inline-block; vertical-align: middle; margin-right: 4px;"></i>AI: ${escapeHTML(m.original)}</span>
            <span class="diff-val-editor" title="Editor modified value"><i data-lucide="user-check" style="width: 12px; height: 12px; display: inline-block; vertical-align: middle; margin-right: 4px;"></i>Editor: ${escapeHTML(m.current)}</span>
          </div>
        </div>
      `;
    });
    html += `</div></div>`;
    container.innerHTML = html;
    safeCreateIcons();
    return;
  }

  const rows = stats.detailed[activeTab] || [];
  if (rows.length === 0) {
    container.innerHTML = `
      <div class="diff-empty-state">
        <i data-lucide="check-circle-2" style="width: 36px; height: 36px; margin: 0 auto 0.5rem; color: #10b981; display: block;"></i>
        <p>No changes detected in ${formatColumnLabel(activeTab)}. AI baseline matches working records.</p>
      </div>
    `;
    safeCreateIcons();
    return;
  }

  let html = "";
  rows.forEach(r => {
    if (r.isDeleted) {
      const base = r.baseRow || {};
      html += `
        <div class="diff-row-card" style="border-left: 3px solid #ef4444;">
          <div class="diff-row-header">
            <span>#${r.id} &mdash; ${escapeHTML(r.equipment_title)}</span>
            <span class="diff-badge-custom-row" style="background: hsla(0, 85%, 60%, 0.15); color: #ef4444;">− Deleted from working set</span>
          </div>
          <div class="diff-row-changes-grid">
            <div class="diff-field-card" style="grid-column: 1 / -1;">
              <div class="diff-field-name">Original AI Baseline Record (removed)</div>
              <div style="font-size: 0.82rem; color: var(--text-main); display: flex; flex-wrap: wrap; gap: 0.75rem; margin-top: 0.25rem;">
                ${Object.entries(base).filter(([k]) => !['id','pdf_order','quality','confidence','status','reviewed_by','reviewed_at','rejection_reason'].includes(k) && !k.startsWith('_')).map(([k, v]) => `<div><strong style="color: var(--text-muted);">${formatColumnLabel(k)}:</strong> ${escapeHTML(String(v || 'NA'))}</div>`).join('')}
              </div>
            </div>
          </div>
        </div>
      `;
    } else if (r.isNew) {
      html += `
        <div class="diff-row-card" style="border-left: 3px solid #10b981;">
          <div class="diff-row-header">
            <span>#${r.id} &mdash; ${escapeHTML(r.equipment_title)}</span>
            <span class="diff-badge-custom-row">+ Custom Row Added by Editor</span>
          </div>
          <div class="diff-row-changes-grid">
            <div class="diff-field-card" style="grid-column: 1 / -1;">
              <div class="diff-field-name">Newly Added Record Details</div>
              <div style="font-size: 0.82rem; color: var(--text-main); display: flex; flex-wrap: wrap; gap: 0.75rem; margin-top: 0.25rem;">
                ${Object.entries(r.row || {}).filter(([k]) => !['id','pdf_order','quality','confidence','status','reviewed_by','reviewed_at','rejection_reason'].includes(k) && !k.startsWith('_')).map(([k, v]) => `<div><strong style="color: var(--text-muted);">${formatColumnLabel(k)}:</strong> ${escapeHTML(String(v || 'NA'))}</div>`).join('')}
              </div>
            </div>
          </div>
        </div>
      `;
    } else {
      html += `
        <div class="diff-row-card" style="border-left: 3px solid #f59e0b;">
          <div class="diff-row-header">
            <span>#${r.id} &mdash; ${escapeHTML(r.equipment_title)}</span>
            <span class="freq-tag tag-parts" style="background: rgba(245,158,11,0.15); color: #f59e0b;">${r.changes.length} field(s) altered</span>
          </div>
          <div class="diff-row-changes-grid">
      `;
      r.changes.forEach(c => {
        html += `
          <div class="diff-field-card">
            <div class="diff-field-name">${escapeHTML(c.colLabel)}</div>
            <div class="diff-val-compare">
              <span class="diff-val-ai" title="Original AI baseline extraction"><i data-lucide="bot" style="width: 12px; height: 12px; display: inline-block; vertical-align: middle; margin-right: 4px;"></i>AI: ${escapeHTML(c.original)}</span>
              <span class="diff-val-editor" title="Editor modified value"><i data-lucide="user-check" style="width: 12px; height: 12px; display: inline-block; vertical-align: middle; margin-right: 4px;"></i>Editor: ${escapeHTML(c.current)}</span>
            </div>
          </div>
        `;
      });
      html += `</div></div>`;
    }
  });

  container.innerHTML = html;
  safeCreateIcons();
}

function openDiffModal() {
  const modal = document.getElementById("diff-modal");
  if (!modal) return;
  modal.hidden = false;
  currentDiffModalTab = activeRegistryTab || "spare_parts";

  document.querySelectorAll(".diff-tab-btn").forEach(btn => {
    if (btn.getAttribute("data-tab") === currentDiffModalTab) {
      btn.classList.add("active");
    } else {
      btn.classList.remove("active");
    }
  });

  const filterCheckbox = document.getElementById("diff-filter-changes-only");
  renderDiffModalContent(currentDiffModalTab, filterCheckbox ? filterCheckbox.checked : true);
  safeCreateIcons();
}

function initDiffUi() {
  const diffBtn = document.getElementById("diff-view-btn");
  if (diffBtn) {
    diffBtn.addEventListener("click", (e) => {
      e.preventDefault();
      isDiffViewActive = !isDiffViewActive;
      updateDiffToolbarButtons();
      renderGridPreservingScroll();
    });
  }

  const modalBtn = document.getElementById("diff-compare-modal-btn");
  if (modalBtn) {
    modalBtn.addEventListener("click", (e) => {
      e.preventDefault();
      openDiffModal();
    });
  }

  const closeBtn = document.getElementById("diff-modal-close");
  const doneBtn = document.getElementById("diff-modal-done-btn");
  const modal = document.getElementById("diff-modal");
  if (closeBtn && modal) {
    closeBtn.addEventListener("click", () => { modal.hidden = true; });
  }
  if (doneBtn && modal) {
    doneBtn.addEventListener("click", () => { modal.hidden = true; });
  }

  document.querySelectorAll(".diff-tab-btn").forEach(btn => {
    btn.addEventListener("click", (e) => {
      document.querySelectorAll(".diff-tab-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      currentDiffModalTab = btn.getAttribute("data-tab");
      const filterCheckbox = document.getElementById("diff-filter-changes-only");
      renderDiffModalContent(currentDiffModalTab, filterCheckbox ? filterCheckbox.checked : true);
    });
  });

  const filterCheckbox = document.getElementById("diff-filter-changes-only");
  if (filterCheckbox) {
    filterCheckbox.addEventListener("change", () => {
      renderDiffModalContent(currentDiffModalTab, filterCheckbox.checked);
    });
  }
}

async function initApp() {
  initProgressCardDrag();
  renderGrid();
  updateCopilotQuotaBadge();
  initApprovalsNotificationUi();
  initDiffUi();

  try {
    const params = new URLSearchParams(window.location.search || "");
    const shareToken = (params.get("share") || "").trim();
    if (shareToken) {
      await loadSharedExtract(shareToken);
      return;
    }
  } catch (e) {}

  loadFabricRunFromQuery();
}

initApp();

