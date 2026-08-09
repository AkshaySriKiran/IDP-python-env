/* =============================================================
 * OmniParse IDP Engine Logic
 * Client-Side Parser, TF-IDF Cog-Search, and SheetJS Export
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

// Initialize document loading with preloaded drawworks manual text (for chatbot)
function initPreloadedContext() {
  loadedPages = [];
}

// Global active filters
let currentTabFilter = "all"; // maintenance intervals
let currentSpareFilter = "all"; // spare part types
let currentSearchQuery = "";
let currentConfidenceFilter = "all";
let highlightRecordIds = [];
let selectedRegistryRowId = null;

// Globals to store actively filtered data for Excel export
let filteredMaintenance = [];
let filteredSpareParts = [];
let filteredTroubleshooting = [];
let lastExtractMeta = null;

function formatConfidenceCell(row) {
  if (!row || row.confidence == null || row.confidence === "") return "—";
  const n = Number(row.confidence);
  if (Number.isNaN(n)) return "—";
  return `${Math.round(n * 100)}%`;
}

function isLowConfidenceRow(row) {
  if (!row || row.confidence == null || row.confidence === "") return false;
  const n = Number(row.confidence);
  return !Number.isNaN(n) && n < 0.7;
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
    apiBaseUrl = String(savedApiBase).replace(/\/$/, "");
  }
} catch (e) {}

async function checkPythonApiHealth() {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 5000);
    const resp = await fetch(`${apiBaseUrl}/api/health`, {
      method: "GET",
      signal: controller.signal
    });
    clearTimeout(timeoutId);
    if (!resp.ok) return { ok: false, busy: false };
    const data = await resp.json();
    return {
      ok: !!(data && data.status === "ok"),
      busy: !!(data && data.busy)
    };
  } catch (e) {
    return { ok: false, busy: false };
  }
}

function canUsePythonApiForFile(file, extension) {
  // Heuristics stay fully client-side. Word still uses Mammoth in the browser.
  if (engineMode === "heuristics") return false;
  if (extension === "doc" || extension === "docx") return false;
  if (!["pdf", "txt", "jpg", "jpeg", "png"].includes(extension)) return false;
  if (engineMode !== "gemini" && engineMode !== "ollama") return false;
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

async function extractViaPythonApi(file, pageCountHint = null) {
  // Prefer admin/local browser key when present; otherwise API uses GEMINI_API_KEY from backend/.env
  refreshAdminTestGeminiKey();
  function buildExtractForm() {
    const form = new FormData();
    form.append("file", file, file.name);
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
  const estimatedPages = (() => {
    if (range.start && range.end) return Math.max(1, range.end - range.start + 1);
    if (range.start && pageCountHint) return Math.max(1, pageCountHint - range.start + 1);
    if (range.end) return range.end;
    if (pageCountHint) return pageCountHint;
    // Rough fallback when page count is unknown (large manuals).
    return Math.max(200, Math.round(file.size / (80 * 1024)));
  })();

  // Full-book runs need many hours. Scale timeout; cap at 24h.
  // Async job+poll path avoids CloudFront's ~120s origin timeout.
  const timeoutMs = Math.min(
    24 * 60 * 60 * 1000,
    Math.max(2 * 60 * 60 * 1000, estimatedPages * 15 * 1000)
  );

  progressStatus.innerText = `Sending to Python API (${apiBaseUrl || "same-origin"})...`;
  progressFill.style.width = "8%";

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
  if (!jobId) throw new Error("API did not return an extraction job id");

  progressStatus.innerText = "Job queued — waiting for API workers...";
  progressFill.style.width = "12%";

  const pollIntervalMs = 2000;
  while (Date.now() - startedAt < timeoutMs) {
    await new Promise(r => setTimeout(r, pollIntervalMs));
    let statusResp;
    try {
      statusResp = await fetch(`${apiBaseUrl}/api/extract/jobs/${encodeURIComponent(jobId)}`, {
        method: "GET",
        headers: authHeaders
      });
    } catch (err) {
      // Transient network blip — keep polling until overall timeout.
      progressStatus.innerText = `Waiting for job status… ${formatElapsed(Date.now() - startedAt)} elapsed`;
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
      throw new Error(detail || `Job status HTTP ${statusResp.status}`);
    }

    const job = await statusResp.json();
    const pct = Math.min(92, 12 + Math.floor((Number(job.progress) || 0) * 80));
    progressFill.style.width = `${pct}%`;
    const msg = job.message || job.status || "running";
    progressStatus.innerText =
      `Python API job ${job.status || "running"}… ${formatElapsed(Date.now() - startedAt)} elapsed` +
      ` — ${msg}`;

    if (job.status === "done") {
      if (!job.result) throw new Error("Job finished but returned no result");
      progressFill.style.width = "85%";
      progressStatus.innerText = "Merging registries into grid...";
      return job.result;
    }
    if (job.status === "error") {
      throw new Error(job.error || job.message || "Extraction job failed");
    }
  }

  throw new Error(
    `Python API job timed out after ${formatElapsed(timeoutMs)}. ` +
    `Use a smaller From/To page range, switch to Native text (if searchable), or try Flash-Lite.`
  );
}

async function extractViaPythonApiSync(form, authHeaders, timeoutMs, estimatedPages, startedAt) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  const tickId = setInterval(() => {
    const elapsed = formatElapsed(Date.now() - startedAt);
    const pct = Math.min(70, 12 + Math.floor(((Date.now() - startedAt) / timeoutMs) * 55));
    progressFill.style.width = `${pct}%`;
    progressStatus.innerText =
      `Python API still working… ${elapsed} elapsed` +
      ` (~${estimatedPages} pages queued — every page is processed, max 5000).`;
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
        `Python API timed out after ${formatElapsed(timeoutMs)}. ` +
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

  progressFill.style.width = "85%";
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
    const maint = (result && result.maintenance) || [];
    const spares = (result && result.spare_parts) || [];
    const trouble = (result && result.troubleshooting) || [];

    maintenanceRegistry = maint.map((row, idx) => ({ ...row, id: row.id || idx + 1 }));
    sparePartsRegistry = spares.map((row, idx) => ({ ...row, id: row.id || idx + 1 }));
    troubleshootingRegistry = trouble.map((row, idx) => ({ ...row, id: row.id || idx + 1 }));

    loadedPages = ((result && result.pages) || []).map(p => ({
      pageNum: p.pageNum,
      text: p.text || ""
    }));

    if (typeof assembleRegistriesInPageOrder === "function") {
      assembleRegistriesInPageOrder();
    }

    const meta = (result && result.meta) || {};
    lastExtractMeta = meta;
    (meta.warnings || []).forEach(w => appendChatSystemMessage(`⚠️ ${w}`));

    if (progressFill) progressFill.style.width = "100%";
    if (progressStatus) progressStatus.innerText = "Extraction finished!";
    setActiveDocBadge(file.name);
    safeCreateIcons();

    preferTabWithResults();
    renderGrid();
    offerSaveExcelAfterExtraction(file);
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
const fileInput = document.getElementById("file-input");
const browseBtn = document.getElementById("browse-btn");
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
        <th style="width: 70px;">Page</th>
        <th style="width: 70px; text-align: center;">Actions</th>
      `;
    } else {
      maintenanceHeaders.innerHTML = `
        <th style="width: 60px;">ID</th>
        <th style="width: 150px;">Equipment Title</th>
        <th style="width: 200px;">Sub-system / Component</th>
        <th style="width: 150px;">Maintenance Routine</th>
        <th>Checks & Instructions</th>
        <th class="confidence-cell" style="width: 80px; text-align: center;">Confidence</th>
        <th style="width: 70px;">Page</th>
        <th style="width: 70px; text-align: center;">Actions</th>
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
function sanitizeVal(val) {
  if (val === null || val === undefined) return "NA";
  const s = String(val).trim();
  if (s === "" || s.toLowerCase() === "null" || s.toLowerCase() === "undefined" || s.toLowerCase() === "na") return "NA";
  return s;
}

// Check if a maintenance row has valid (non-empty/non-NA) content in subsystem_component and checks_instructions

function normalizeExtraction(output) {
  if (!equipmentManifest) return output;
  const mappings = equipmentManifest.normalization_mappings;
  if (!mappings) return output;

  const normalizeRoutine = (routine) => {
    if (!routine || routine === "NA") return "NA";
    const lower = String(routine).toLowerCase();
    for (const mapping of mappings.maintenance_routines) {
      if (mapping.matches.some(m => lower.includes(m))) {
        return mapping.enum;
      }
    }
    return routine;
  };

  const normalizeFreq = (freq) => {
    if (!freq || freq === "NA") return "NA";
    const lower = String(freq).toLowerCase();
    for (const mapping of mappings.spare_parts_frequency) {
      if (mapping.matches.some(m => lower.includes(m))) {
        return mapping.enum;
      }
    }
    return freq;
  };

  if (output.maintenance) {
    output.maintenance.forEach(r => {
      r.maintenance_routine = normalizeRoutine(r.maintenance_routine);
    });
  }
  if (output.spare_parts) {
    output.spare_parts.forEach(r => {
      r.frequency_of_use = normalizeFreq(r.frequency_of_use);
    });
  }
  return output;
}

function looksLikeProcurementOrIndexMeta(text) {
  const s = String(text || "").toLowerCase().trim();
  if (!s) return false;

  // Generic metadata-style language rather than document-specific phrases.
  const metaTokenHits = (s.match(/\b(project|order|serial|manufactur|nameplate|code|index|material|required|identification|reference)\b/g) || []).length;
  const partTokenHits = (s.match(/\b(gasket|seal|bearing|plate|bolt|nut|screw|filter|valve|ring|liner|pump|shaft|gear|coupling|hose)\b/g) || []).length;
  const hasActionVerb = /\b(inspect|check|replace|clean|lubricate|tighten|remove|install|test|flush)\b/.test(s);
  const endsWithPageNum = /(?:\.{2,}\s*)?\d{1,3}$/.test(s);

  // Index/metadata labels usually have metadata tokens, few hardware terms, and no action verbs.
  if (metaTokenHits >= 2 && partTokenHits === 0 && !hasActionVerb) return true;
  if (metaTokenHits >= 3 && !hasActionVerb) return true;
  if (endsWithPageNum && metaTokenHits >= 1 && !hasActionVerb) return true;
  return false;
}

function extractContentTokens(text) {
  const stop = new Set([
    "the", "and", "for", "with", "from", "into", "that", "this", "then", "than",
    "are", "was", "were", "have", "has", "had", "will", "shall", "should", "can",
    "must", "not", "all", "any", "page", "unit", "system", "check", "inspect"
  ]);
  const tokens = String(text || "")
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter(t => t && !stop.has(t) && (t.length >= 4 || /^\d+$/.test(t)));
  return Array.from(new Set(tokens));
}

function isTextGroundedInSource(candidateText, sourceText) {
  const source = String(sourceText || "").toLowerCase();
  if (!source.trim()) return false;
  const tokens = extractContentTokens(candidateText);
  if (tokens.length === 0) return false;

  const matchedTokens = tokens.filter(t => source.includes(t));
  // Short paraphrases need stricter overlap; longer procedure text can be a bit looser.
  const isShort = tokens.length <= 8;
  const tokenThreshold = isShort
    ? Math.max(3, Math.ceil(tokens.length * 0.7))
    : Math.max(2, Math.ceil(tokens.length * 0.5));
  const tokenOk = matchedTokens.length >= tokenThreshold;

  // Require contiguous phrase evidence so index-title word reuse alone is not enough.
  const words = String(candidateText || "")
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .split(/\s+/)
    .filter(w => w.length >= 3);
  let phraseOk = false;
  if (words.length >= 3) {
    for (let i = 0; i <= words.length - 3; i++) {
      const trigram = `${words[i]} ${words[i + 1]} ${words[i + 2]}`.trim();
      if (trigram.length >= 10 && source.includes(trigram)) {
        phraseOk = true;
        break;
      }
    }
  }
  if (!phraseOk && words.length >= 2) {
    for (let i = 0; i <= words.length - 2; i++) {
      const bigram = `${words[i]} ${words[i + 1]}`.trim();
      if (bigram.length >= 12 && source.includes(bigram)) {
        phraseOk = true;
        break;
      }
    }
  }
  return tokenOk && phraseOk;
}

function isCleanMaintenanceRow(row) {
  if (activeEquipmentCategory === "Logbook") {
    const desc = sanitizeVal(row.maintenance_work_description);
    if (desc === "NA") return false;
    return true;
  }
  const comp = sanitizeVal(row.subsystem_component);
  if (comp === "NA") return false;
  const checks = sanitizeVal(row.checks_instructions);
  if (checks === "NA") return false;
  if (looksLikeProcurementOrIndexMeta(checks)) {
    return false;
  }
  return true;
}

// Check if a spare part row has valid (non-empty/non-NA) content in name, code, or drawing model
function isCleanSparePartsRow(row) {
  const name = sanitizeVal(row.part_name);
  const code = sanitizeVal(row.part_number_code);
  const dwg = sanitizeVal(row.drawing_model_no);
  if (name === "NA" && code === "NA" && dwg === "NA") return false;

  const lowerName = name.toLowerCase();
  const lowerCode = code.toLowerCase();
  const lowerDwg = dwg.toLowerCase();
  const hasStrongCode = code !== "NA" && /[0-9]/.test(code) && !lowerCode.includes("na");
  const hasDrawingRef = dwg !== "NA" && !lowerDwg.includes("na");
  if (looksLikeProcurementOrIndexMeta(name) && !hasStrongCode && !hasDrawingRef) {
    return false;
  }
  return true;
}

// Heuristic pre-filter to detect if a page contains keywords indicating maintenance tasks or spare parts
// Heuristic pre-filter to detect if a page contains recommended spare parts lists or tables
function isRecommendedSparePartsPage(pageText) {
  if (!pageText) return false;
  
  // Exclude explicit Table of Contents pages
  if (pageText.toLowerCase().includes("table of contents") || pageText.toLowerCase().includes("index")) {
    return false;
  }
  
  const text = pageText.toLowerCase();
  const cleanText = text.replace(/\s+/g, " ");
  
  // Specific headers/keywords indicating recommended or quick-wear spare parts lists
  return cleanText.includes("recommended (one year) spare parts") || 
         cleanText.includes("recommended spare parts") || 
         cleanText.includes("quick-wear parts") || 
         cleanText.includes("quick - wear parts") || 
         cleanText.includes("consumptive parts") || 
         cleanText.includes("quick-wear and consumptive") ||
         cleanText.includes("quick - wear and consumptive") ||
         cleanText.includes("bearings list of dw") ||
         (cleanText.includes("legend") && cleanText.includes("pos") && cleanText.includes("q.ty"));
}

// Specialized structural spare parts parser for Recommended and Quick-Wear spare parts tables
function parseSparePartsStructurally(text, docName, pageNum = 1) {
  const results = [];
  if (!text) return results;
  const cleanText = text.replace(/\s+/g, " ");
  
  // Find all 10-digit codes
  const codeRegex = /\b\d{10}\b/g;
  let match;
  const codeMatches = [];
  while ((match = codeRegex.exec(cleanText)) !== null) {
    codeMatches.push({
      code: match[0],
      start: match.index,
      end: codeRegex.lastIndex
    });
  }

  if (codeMatches.length === 0) {
    const lowerText = cleanText.toLowerCase();
    const legendIdx = lowerText.indexOf("legend");
    let searchArea = cleanText;
    if (legendIdx !== -1) {
      searchArea = cleanText.substring(legendIdx + "legend".length);
    }
    
    // Regex matching Pos Q.ty Description
    const regexPattern = /\b(\d+)\s+(\d+(?:-\d+)?)\s+([a-zA-Z\s\/\-\&\(\)\.\,\’\'\"\+]+?)(?=\s+\d+\s+\d+(?:-\d+)?\s+|$)/g;
    let matchPair;
    
    let subsystemLocation = "NA";
    if (lowerText.includes("with direct joint")) {
      subsystemLocation = "Direct Joint";
    } else if (lowerText.includes("with extension and one bearing")) {
      subsystemLocation = "Extension & One Bearing";
    } else if (lowerText.includes("with extension and two bearings")) {
      subsystemLocation = "Extension & Two Bearings";
    }
    
    while ((matchPair = regexPattern.exec(searchArea)) !== null) {
      const pos = matchPair[1].trim();
      const qty = matchPair[2].trim();
      const desc = matchPair[3].trim().replace(/\s+/g, " "); // collapse spacing
      
      let categorization = "Critical Spare";
      const lowerDesc = desc.toLowerCase();
      if (lowerDesc.includes("o-ring") || lowerDesc.includes("gasket") || lowerDesc.includes("seal") || lowerDesc.includes("screw") || lowerDesc.includes("washer") || lowerDesc.includes("circlip") || lowerDesc.includes("ring nut") || lowerDesc.includes("bearing")) {
        categorization = "Consumable";
      }
      
      results.push({
        id: 0,
        equipment_title: docName ? docName.replace(/\.[^/.]+$/, "") : "NA",
        subsystem_location: subsystemLocation,
        item_no: pos,
        part_name: desc,
        part_number_code: "NA",
        drawing_model_no: "NA",
        oem_standard_body: "NA",
        part_categorization: categorization,
        quantity: qty,
        recommended_stock_qty: "NA",
        warranty_period: "NA",
        frequency_of_use: "NA",
        page: pageNum
      });
    }
    
    return results;
  }
  
  // Table state tracking
  let currentTable = "Table 15";
  let idxCounter = 1;
  
  // Reconstruct table and index state sequentially based on sparePartsRegistry
  let prevTable = "Table 15";
  let prevIdx = 0;
  if (typeof sparePartsRegistry !== "undefined" && Array.isArray(sparePartsRegistry)) {
    const cleanDocName = docName ? docName.replace(/\.[^/.]+$/, "") : "NA";
    for (let idx = sparePartsRegistry.length - 1; idx >= 0; idx--) {
      const r = sparePartsRegistry[idx];
      if (r.equipment_title === cleanDocName) {
        if (r.frequency_of_use && r.frequency_of_use.includes("Replace every")) {
          prevTable = "Table 16";
        } else {
          prevTable = "Table 15";
        }
        prevIdx = parseInt(r.item_no) || 0;
        break;
      }
    }
  }
  
  currentTable = prevTable;
  idxCounter = prevIdx > 0 ? prevIdx + 1 : 1;
  
  for (let i = 0; i < codeMatches.length; i++) {
    const m = codeMatches[i];
    const code = m.code;
    
    const prevEnd = i > 0 ? codeMatches[i-1].end : 0;
    const preceding = cleanText.substring(prevEnd, m.start).trim();
    
    const nextStart = (i + 1 < codeMatches.length) ? codeMatches[i+1].start : cleanText.length;
    const segment = cleanText.substring(m.end, nextStart).trim();
    
    // Determine table type and index from preceding
    const lowerPre = preceding.toLowerCase();
    if (lowerPre.includes("quick - wear") || lowerPre.includes("quick-wear") || lowerPre.includes("quick_wear")) {
      currentTable = "Table 16";
      idxCounter = 1;
    } else if (lowerPre.includes("recommended")) {
      currentTable = "Table 15";
      idxCounter = 1;
    } else if (lowerPre.includes("bearings list")) {
      currentTable = "Table 14";
      idxCounter = 1;
    }
    
    // Determine row index
    let targetIndex = idxCounter;
    const trailingDigitsMatch = preceding.match(/(\d+(?:\s+\d+)*)\s*$/);
    if (trailingDigitsMatch) {
      const digits = trailingDigitsMatch[1].replace(/\s+/g, "");
      if (digits.endsWith(String(targetIndex))) {
        // match
      } else if (digits.endsWith(String(targetIndex + 1))) {
        targetIndex = targetIndex + 1;
      } else {
        // fallback: parse last 1-2 digits
        const val2 = parseInt(digits.slice(-2));
        if (!isNaN(val2)) {
          targetIndex = val2;
        } else {
          const val1 = parseInt(digits.slice(-1));
          if (!isNaN(val1)) {
            targetIndex = val1;
          }
        }
      }
    }
    
    const rowId = targetIndex;
    idxCounter = rowId + 1;
    
    // We discard Table 14 (Bearings list)
    if (currentTable === "Table 14") {
      continue;
    }
    
    // Parse segment
    let nextIdxStr = String(idxCounter);
    let nextIdxSpaceStr = nextIdxStr.split("").join(" ");
    
    let segmentClean = segment;
    // Strip next index
    const patterns = [
      new RegExp("\\s+" + escapeRegExp(nextIdxSpaceStr) + "$"),
      new RegExp("\\s+" + escapeRegExp(nextIdxStr) + "$")
    ];
    for (const pat of patterns) {
      const matchPat = segmentClean.match(pat);
      if (matchPat) {
        segmentClean = segmentClean.substring(0, matchPat.index).trim();
        break;
      }
    }
    
    // Strip Table 16 header if Table 15 last row
    if (currentTable === "Table 15" && segmentClean.toLowerCase().includes("list of quick")) {
      const matchHeader = segmentClean.match(/\b\d+(?:\s+\d+)?\s+list of quick.*$/i);
      if (matchHeader) {
        segmentClean = segmentClean.substring(0, matchHeader.index).trim();
      }
    }
    
    // Strip Table 17 header or other sections
    if (segmentClean.toLowerCase().includes("quality assurance")) {
      const matchHeader = segmentClean.match(/\b\d+(?:\s+\d+)?\s+quality assurance.*$/i);
      if (matchHeader) {
        segmentClean = segmentClean.substring(0, matchHeader.index).trim();
      }
    }
    
    const tokens = segmentClean.split(/\s+/);
    
    let qty = "NA";
    let warranty = "NA";
    let remark = "NA";
    
    if (currentTable === "Table 16") {
      if (tokens.length >= 2 && ["year", "years", "month", "months", "monthes"].includes(tokens[tokens.length - 1].toLowerCase())) {
        warranty = tokens[tokens.length - 2] + " " + tokens[tokens.length - 1];
        tokens.splice(tokens.length - 2, 2);
      } else if (tokens.length >= 1 && tokens[tokens.length - 1].toLowerCase().includes("year")) {
        warranty = tokens[tokens.length - 1];
        tokens.splice(tokens.length - 1, 1);
      }
    }
    
    // Extract QTY (check last 3 tokens, group consecutive digits)
    const maxChecked = Math.max(0, tokens.length - 3);
    for (let j = tokens.length - 1; j >= maxChecked; j--) {
      if (/^\d+$/.test(tokens[j])) {
        let startJ = j;
        while (startJ > 0 && /^\d+$/.test(tokens[startJ - 1])) {
          startJ--;
        }
        qty = tokens.slice(startJ, j + 1).join(" ");
        remark = tokens.slice(j + 1).join(" ");
        tokens.splice(startJ, tokens.length - startJ);
        break;
      }
    }
    
    // Token classification
    const isCode = (s) => {
      const hasDigitOrSpecial = /[0-9\-\/\.\;×φ]/.test(s);
      const isUpperWord = (s === s.toUpperCase() && s.length >= 2);
      return hasDigitOrSpecial || isUpperWord;
    };
    
    const drawingModel = [];
    const partNameTokens = [];
    const specModel = [];
    
    let state = "standard";
    for (const t of tokens) {
      if (state === "standard") {
        if (isCode(t)) {
          drawingModel.push(t);
        } else {
          state = "name";
          partNameTokens.push(t);
        }
      } else if (state === "name") {
        if (isCode(t)) {
          state = "model";
          specModel.push(t);
        } else {
          partNameTokens.push(t);
        }
      } else if (state === "model") {
        specModel.push(t);
      }
    }
    
    let partName = partNameTokens.join(" ").trim();
    let drawingModelNo = drawingModel.join(" ").trim();
    let mfrCode = specModel.join(" ").trim();
    
    if (!partName && drawingModelNo) {
      drawingModelNo = drawingModel[0];
      mfrCode = drawingModel.slice(1).join(" ");
      partName = "NA";
    }
    
    if (!partName) partName = "NA";
    if (!drawingModelNo) drawingModelNo = "NA";
    if (!mfrCode) mfrCode = "NA";
    
    let categorization = (currentTable === "Table 16") ? "Consumable" : "Critical Spare";
    const lowerName = partName.toLowerCase();
    if (lowerName.includes("filter") || lowerName.includes("seal") || lowerName.includes("stopper") || lowerName.includes("holder") || lowerName.includes("rope") || lowerName.includes("oil")) {
      categorization = "Consumable";
    }
    
    let frequency = "NA";
    if (currentTable === "Table 16") {
      if (warranty !== "NA") {
        frequency = "Replace every " + warranty;
      }
    } else if (currentTable === "Table 15") {
      if (rowId === 6) {
        frequency = "Replace every 6 months";
      } else {
        frequency = "Replace during overhaul / Medium";
      }
    }

    // Opportunistic detection of OEM/governing standard and recommended stock levels
    // from the row's own text (segment + leftover remark tokens). Only ever set when
    // actually present — never guessed — so this can only replace NA with real data.
    const rowRemainderText = `${segment} ${remark}`;
    let oemStandardBody = "NA";
    const standardMatch = rowRemainderText.match(/\b(ISO|DIN|ANSI|API|ASME|JIS|BS|SAE|NEMA|IEC)[\-\s]?\d{0,6}\b/);
    if (standardMatch) oemStandardBody = standardMatch[0];

    let recommendedStockQty = "NA";
    const stockMatch = rowRemainderText.toLowerCase().match(/\b(?:recommended stock|stock level)\D{0,20}?(\d{1,4})\b/);
    if (stockMatch) recommendedStockQty = stockMatch[1];
    
    results.push({
      id: 0,
      equipment_title: docName ? docName.replace(/\.[^/.]+$/, "") : "NA",
      subsystem_location: "NA",
      item_no: String(rowId),
      part_name: partName,
      part_number_code: code,
      drawing_model_no: (drawingModelNo !== "NA" && mfrCode !== "NA") ? (drawingModelNo + " / " + mfrCode) : (drawingModelNo !== "NA" ? drawingModelNo : (mfrCode !== "NA" ? mfrCode : "NA")),
      oem_standard_body: oemStandardBody,
      part_categorization: categorization,
      quantity: qty !== "NA" ? qty : "1",
      recommended_stock_qty: recommendedStockQty,
      warranty_period: warranty,
      frequency_of_use: frequency,
      page: pageNum
    });
  }
  
  return results;
}

function escapeRegExp(string) {
  return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function isLikelyIndexOrTOCPage(pageText, pageNum = null) {
  if (!pageText) return false;

  const text = String(pageText);
  const lower = text.toLowerCase();
  const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);

  if (lower.includes("table of contents")) return true;

  const dotLeaderCount = (text.match(/\.{3,}/g) || []).length;
  const pageRefCount = (lower.match(/\bpage\s+\d{1,3}\b/g) || []).length;
  const contentsWordCount = (lower.match(/\bcontents?\b/g) || []).length;
  const indexWordCount = (lower.match(/\bindex\b/g) || []).length;
  const numberedEntryCount = (text.match(/[A-Za-z][A-Za-z0-9 ,\-\/\(\)]{10,120}(?:\.{2,}\s*|\s{2,})\d{1,3}\b/g) || []).length;
  const sectionEntryCount = (lower.match(/\b(?:chapter|section|appendix|figure|fig\.?|table)\s*[a-z0-9\.\-]{0,12}\s+[a-z][^.!?\n]{0,80}\s+\d{1,3}\b/g) || []).length;
  const tocLineCount = lines.filter(l => /(?:\.{2,}\s*)?\d{1,3}$/.test(l) && /[a-z]/i.test(l) && l.length > 8).length;
  const headingLikeLineCount = lines.filter(l => /^(?:\d+(?:\.\d+)*)\s+[A-Za-z]/.test(l) && !/[.!?]/.test(l)).length;
  const shortLineCount = lines.filter(l => l.split(/\s+/).length <= 14).length;
  const trailingPageNumLineCount = lines.filter(l => /\b\d{1,3}$/.test(l) && l.split(/\s+/).length <= 16).length;
  // Two-column TOCs often leave bare page numbers as their own tokens/lines.
  const barePageNumCount = (text.match(/(?:^|\s)\d{1,3}(?=\s|$)/g) || []).length;
  const sentenceCount = (text.match(/[.!?]/g) || []).length;
  const frontMatter = typeof pageNum === "number" && pageNum <= 8;

  if (dotLeaderCount >= 3) return true;
  if (sectionEntryCount >= 4) return true;
  if ((contentsWordCount > 0 || indexWordCount > 0) && numberedEntryCount >= 4) return true;
  if ((pageRefCount + numberedEntryCount) >= 8 && (dotLeaderCount >= 1 || contentsWordCount > 0 || indexWordCount > 0)) return true;
  // Continuation index pages often have many short heading lines ending with page numbers.
  if (tocLineCount >= 6 && headingLikeLineCount >= 4) return true;
  // Front-matter continuation index: lots of short lines that terminate in page numbers.
  if (frontMatter && trailingPageNumLineCount >= 6 && shortLineCount >= 8) return true;
  // Dense page-number listing with almost no prose sentences (typical TOC / index).
  if (frontMatter && barePageNumCount >= 8 && sentenceCount <= 2 && shortLineCount >= 6) return true;
  if (frontMatter && barePageNumCount >= 10 && sentenceCount <= 3) return true;

  return false;
}

function buildTextFromPdfTextContent(textContent) {
  if (!textContent || !Array.isArray(textContent.items)) return "";
  const items = textContent.items
    .map(item => ({
      str: String(item.str || "").trim(),
      x: Array.isArray(item.transform) ? Number(item.transform[4]) || 0 : 0,
      y: Array.isArray(item.transform) ? Number(item.transform[5]) || 0 : 0,
      hasEOL: Boolean(item.hasEOL)
    }))
    .filter(item => item.str.length > 0);

  if (items.length === 0) return "";

  // Prefer explicit line breaks when available.
  if (items.some(item => item.hasEOL)) {
    const lines = [];
    let currentLine = "";
    items.forEach(item => {
      currentLine += (currentLine ? " " : "") + item.str;
      if (item.hasEOL) {
        lines.push(currentLine.trim());
        currentLine = "";
      }
    });
    if (currentLine) lines.push(currentLine.trim());
    return lines.join("\n");
  }

  // Fallback: cluster items by y-position to reconstruct line-aware text.
  const sorted = [...items].sort((a, b) => {
    if (Math.abs(a.y - b.y) > 1.2) return b.y - a.y;
    return a.x - b.x;
  });
  const lines = [];
  let current = [];
  let currentY = sorted[0].y;
  const yTolerance = 2.0;

  sorted.forEach(item => {
    if (Math.abs(item.y - currentY) > yTolerance) {
      if (current.length > 0) {
        current.sort((a, b) => a.x - b.x);
        lines.push(current.map(t => t.str).join(" ").trim());
      }
      current = [item];
      currentY = item.y;
    } else {
      current.push(item);
    }
  });
  if (current.length > 0) {
    current.sort((a, b) => a.x - b.x);
    lines.push(current.map(t => t.str).join(" ").trim());
  }
  return lines.filter(Boolean).join("\n");
}


// Builds the shared extraction instruction prompt used by every LLM backend (Ollama, Gemini, ...).
// Keeping this in one place ensures the carefully-tuned field-extraction rules (and any future
// fixes to them) automatically apply to every engine instead of drifting out of sync.
function buildExtractionPrompt(text, docName) {
  const cleanDocName = docName ? docName.replace(/\.[^/.]+$/, "") : "NA";
  const isOcrVision = /OCR VISION EXTRACTION/i.test(String(text || ""));
  let systemPrompt = `You are an expert technical parser of industrial engineering manuals.
Your task is to analyze the text page content below and extract:
1. Maintenance routines, checks, and instructions.
2. Spare parts and components referenced in drawings or lists.
3. Troubleshooting tables, problems, and root-cause/solutions.

Group your extractions into three distinct JSON lists: "maintenance", "spare_parts", and "troubleshooting".
CRITICAL INSTRUCTION: If a field is missing, not specified, or not available in the text, you MUST populate it with the string "NA". Do not use null, undefined, or empty values.
${isOcrVision ? `
OCR / SCANNED PAGE RULES (CRITICAL):
- Read EVERY filled row in spare-parts tables. Do not stop after a few sample rows.
- Include dense electrical/mechanical rows (fuses, breakers, relays, fans, kits, etc.).
- Map: Description → part_name, NOV/Part No → part_number_code, Item/Ref No → drawing_model_no, Item No → item_no.
- Cubicle/section headers (e.g. INCOMER CUBICLE) go into subsystem_location for following rows.
- Commissioning / Two-year recommended quantities can go into quantity / recommended_stock_qty when present.
- Prefer completeness over brevity. Output as many spare_parts objects as there are real table rows on this page.
` : ""}
Rules for "maintenance" tasks:
- Extract real maintenance tasks, checks, inspection routines, adjustments, or replacements.
- Clean instructions to remove page headers or random numbers. Pay special attention to tables and bulleted checklists, ensuring each item is extracted accurately.
- For "equipment_title", default to "${cleanDocName}" if the text does not mention a specific equipment.
- For "subsystem_component", you MUST identify a specific, physical sub-system or component. If a checklist implies the component, use that for all its items. If no specific component can be identified, DO NOT extract the task.
- For "maintenance_routine", extract the interval.
- For "checks_instructions", write the procedure or actions in a concise manner.

Rules for "spare_parts":
- Extract items that represent real spare parts, consumables, hardware, or components.
- Extract EVERY numbered table row on the page. Do not sample or stop after a few examples.
- Emit spare_parts in the same top-to-bottom order as the PDF table (Item 1, then 2, then 3, …). Never alphabetize by part name.
- DO NOT extract ordering metadata, procurement fields, or identification labels as parts.
- Reject list labels or ordering metadata unless there is clear evidence of an actual physical part (for example a concrete component name with valid part/drawing reference context).
- For "equipment_title", you MUST extract the explicit Table Title, Header, or Caption directly preceding the parts list (e.g. "EXAMPLE_TABLE_TITLE_DO_NOT_COPY"). Do not use random surrounding text. Default to "${cleanDocName}" if there is absolutely no title.
- For "subsystem_location", identify the specific assembly or sub-system the part belongs to. If the table title explicitly mentions the assembly name, use it here.
- For "part_name", extract the descriptive name of the component or part.
- For "part_categorization", use "Critical Spare", "Consumable", or "Standard Part".
- For "quantity", extract the number of units.
- For "part_number_code": The manufacturer's part number or code. This is often an alphanumeric string (e.g. "H910-416", "30123290", "51300-348-F"), not necessarily a long numeric code. Scan the entire row/segment for it, including columns labeled "P/N", "Part No.", "Code", "Number", or similar.
- For "drawing_model_no": The engineering drawing, reference/location designator (e.g. "U1", "TB2"), or model designator number, if present in the row.
- For "oem_standard_body": The OEM name, manufacturer, or governing standard/body (e.g. "ANSI", "ISO", "DIN") referenced for the part, if present.
- For "recommended_stock_qty", extract stock recommendation levels if present.
- For "warranty_period", extract the warranty duration if mentioned (e.g. "12 months", "1 year").
- For "frequency_of_use", extract how frequently this part is used or should be replaced/inspected.
- IMPORTANT: Every field above must be actively searched for within the row's full text before defaulting to "NA". Only use "NA" when the information is truly absent from that row, not simply because it doesn't fit the example format below.

Rules for "troubleshooting" tasks:
- ONLY extract explicit troubleshooting matrices or tables. DO NOT extract Table of Contents headers, general descriptions, or normal paragraphs as problems.
- A valid problem MUST have a corresponding root cause and solution. If the text does not describe a fault and how to fix it, do NOT extract it.
- For "equipment_title", default to "${cleanDocName}" if not specified.
- For "subsystem_component", identify the specific sub-system.
- For "problem", extract the symptom, fault, or issue described.
- For "root_cause_solution", extract the combined root cause and solution / elimination method.

Response MUST be strictly valid JSON (and only JSON, with no other text before or after).
CRITICAL EXCEPTION: Do NOT return empty arrays if you see actual part names accompanied by alphanumeric codes. You MUST extract them.

CRITICAL INSTRUCTION: DO NOT use the values from the example output. If a field is missing or not found in the text, you MUST output "NA".

Example Output Structure:
{
  "maintenance": [
    {
      "equipment_title": "EXAMPLE_EQUIPMENT_DO_NOT_COPY",
      "subsystem_component": "Main Brake Caliper",
      "maintenance_routine": "Daily",
      "checks_instructions": "Inspect for oil leaks."
    }
  ],
  "spare_parts": [
    {
      "equipment_title": "EXAMPLE_EQUIPMENT_DO_NOT_COPY",
      "subsystem_location": "Regulator",
      "item_no": "1",
      "part_name": "EXAMPLE_PART_NAME_DO_NOT_COPY",
      "part_number_code": "EXAMPLE_CODE",
      "drawing_model_no": "EXAMPLE_DRAWING_OR_REF_DO_NOT_COPY",
      "oem_standard_body": "EXAMPLE_OEM_OR_STANDARD_DO_NOT_COPY",
      "part_categorization": "Consumable",
      "quantity": "1",
      "recommended_stock_qty": "EXAMPLE_STOCK_QTY_DO_NOT_COPY",
      "warranty_period": "EXAMPLE_WARRANTY_DO_NOT_COPY",
      "frequency_of_use": "EXAMPLE_FREQUENCY_DO_NOT_COPY"
    }
  ],
  "troubleshooting": [
    {
      "equipment_title": "EXAMPLE_EQUIPMENT_DO_NOT_COPY",
      "subsystem_component": "Regulator Valve",
      "problem": "Valve does not open",
      "root_cause_solution": "Air lock in line. Bleed air from the system."
    }
  ]
}`;

  if (activeEquipmentCategory === "Logbook") {
    systemPrompt = `You are an expert transcriber of handwritten field history cards and maintenance logbooks.
Your task is to analyze the image or text below and extract historical maintenance log entries exactly as they are written.

These documents are often photographed HISTORY CARDs (e.g. Top Drive / Drawworks electrical). Pages may be sideways or rotated — still read all handwritten rows.

Group your extractions into the "maintenance" list. Return an empty array [] for "spare_parts" and "troubleshooting".
If a field is missing, not specified, or not available in the text, you MUST populate it with the string "NA".

You MUST strictly use the following 5 keys for every entry:
- "date"
- "maintenance_work_description"
- "parts_renewed"
- "attended_by"
- "remarks"

Response MUST be strictly valid JSON (and only JSON, with no other text before or after).
CRITICAL: Even if the page looks like a cover page, or the table is messy and handwritten, DO NOT return empty arrays! You MUST attempt to extract whatever handwritten notes, signatures, or dates are visible into the "maintenance" list. Cover pages with only a title may return an empty maintenance list.

CRITICAL INSTRUCTION: DO NOT use the values from the example output. If a field is missing or not found in the text, you MUST output "NA".

Example Output Structure:
{
  "maintenance": [
    {
      "date": "15 Jan 2023",
      "maintenance_work_description": "Repl. Oil Pump",
      "parts_renewed": "Oil Pump Assy",
      "attended_by": "J. P. H.",
      "remarks": "Tested OK"
    }
  ],
  "spare_parts": [],
  "troubleshooting": []
}`;
  }
  systemPrompt += `\n\n${learnedPatterns.length > 0 ? 
  `CRITICAL LEARNING EXAMPLES:\nThe user has manually corrected past extractions. You MUST strongly weigh these learned patterns when deciding how to extract and format data:\n${JSON.stringify(learnedPatterns, null, 2)}` 
  : ""}

Text to parse:
"""
${text}
"""`;

  return systemPrompt;
}

function extractFirstJsonObject(rawText) {
  const input = String(rawText || "").trim();
  if (!input) return "";

  // Strip common markdown fences before scanning.
  let text = input
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/\s*```$/i, "")
    .trim();

  const start = text.indexOf("{");
  if (start === -1) return "";

  let depth = 0;
  let inString = false;
  let escaping = false;

  for (let i = start; i < text.length; i++) {
    const ch = text[i];

    if (inString) {
      if (escaping) {
        escaping = false;
      } else if (ch === "\\") {
        escaping = true;
      } else if (ch === "\"") {
        inString = false;
      }
      continue;
    }

    if (ch === "\"") {
      inString = true;
      continue;
    }
    if (ch === "{") depth += 1;
    if (ch === "}") {
      depth -= 1;
      if (depth === 0) {
        return text.slice(start, i + 1);
      }
    }
  }

  // Truncated JSON: return best-effort slice from first brace.
  return text.slice(start);
}

function repairTruncatedJson(rawJson) {
  let s = String(rawJson || "").trim();
  if (!s) return s;

  // Normalize smart quotes that break JSON.parse
  s = s.replace(/[\u201C\u201D]/g, '"').replace(/[\u2018\u2019]/g, "'");

  // Drop incomplete trailing property / object fragments (common Gemini cutoff).
  s = s.replace(/,\s*"[^"\n]*$/g, "");
  s = s.replace(/,\s*\{[\s\S]*$/g, "");
  s = s.replace(/:\s*"[^"\n]*$/g, ': "NA"');
  s = s.replace(/:\s*-?\d+(\.\d+)?\s*$/g, ": 0");
  s = s.replace(/,\s*$/g, "");

  // Remove trailing commas before ] or }
  s = s.replace(/,\s*([\]}])/g, "$1");

  let inString = false;
  let escaping = false;
  const stack = [];

  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (inString) {
      if (escaping) escaping = false;
      else if (ch === "\\") escaping = true;
      else if (ch === "\"") inString = false;
      continue;
    }
    if (ch === "\"") {
      inString = true;
      continue;
    }
    if (ch === "{") stack.push("}");
    else if (ch === "[") stack.push("]");
    else if (ch === "}" || ch === "]") {
      if (stack.length && stack[stack.length - 1] === ch) stack.pop();
    }
  }

  if (inString) s += '"';
  // If we closed a string mid-value after a colon with nothing, ensure value exists.
  s = s.replace(/:\s*"$/g, ': "NA"');
  s = s.replace(/,\s*$/g, "");
  s = s.replace(/,\s*([\]}])/g, "$1");

  while (stack.length) {
    s += stack.pop();
  }
  return s;
}

function parseModelJsonResponse(rawResponseText) {
  let cleanResponse = String(rawResponseText || "").trim();
  const candidates = [];

  const firstObject = extractFirstJsonObject(cleanResponse);
  if (firstObject) candidates.push(firstObject);

  // Fallback for older greedy behavior / odd wrappers.
  const greedy = cleanResponse.match(/\{[\s\S]*\}/);
  if (greedy && greedy[0] && !candidates.includes(greedy[0])) {
    candidates.push(greedy[0]);
  }
  if (!candidates.includes(cleanResponse)) {
    candidates.push(cleanResponse);
  }

  // Add repaired variants for truncated / malformed Gemini output.
  const repaired = [];
  candidates.forEach(c => {
    const fixed = repairTruncatedJson(c);
    if (fixed && !candidates.includes(fixed) && !repaired.includes(fixed)) {
      repaired.push(fixed);
    }
  });
  candidates.push(...repaired);

  // Also try cutting back to earlier complete object ends.
  if (firstObject && firstObject.length > 40) {
    for (let i = firstObject.length - 2; i > 40; i--) {
      if (firstObject[i] === "}") {
        const slice = repairTruncatedJson(firstObject.slice(0, i + 1));
        if (slice && !candidates.includes(slice)) candidates.push(slice);
        // Don't generate too many candidates.
        if (candidates.length > 12) break;
      }
    }
  }

  let lastErr = null;
  for (const candidate of candidates) {
    try {
      return { json: JSON.parse(candidate), raw: candidate };
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr || new Error("Unable to parse model JSON response");
}

// Parses/normalizes the raw text response returned by any LLM backend into the app's
// structured { maintenance, spare_parts, troubleshooting } shape. Shared by every engine so the
// mapping, quality filters, and grounding guardrail only need to be maintained in one place.
function processRawModelResponse(rawResponseText, docName, pageNum, base64Image, providerLabel, sourceText = "") {
  const cleanDocName = docName ? docName.replace(/\.[^/.]+$/, "") : "NA";
  let cleanResponse = (rawResponseText || "").trim();
  try {
    const parsed = parseModelJsonResponse(cleanResponse);
    cleanResponse = parsed.raw;
    const resultJson = parsed.json;
    const output = {
      maintenance: [],
      spare_parts: [],
      troubleshooting: []
    };

    if (resultJson.maintenance && Array.isArray(resultJson.maintenance)) {
      output.maintenance = resultJson.maintenance.map(item => {
        if (activeEquipmentCategory === "Logbook") {
          return {
            id: 0,
            date: sanitizeVal(item.date),
            maintenance_work_description: sanitizeVal(item.maintenance_work_description),
            parts_renewed: sanitizeVal(item.parts_renewed),
            attended_by: sanitizeVal(item.attended_by),
            remarks: sanitizeVal(item.remarks),
            page: pageNum
          };
        } else {
          let title = sanitizeVal(item.equipment_title);
          if (title === "NA") title = cleanDocName;
          const pdfOrderRaw = parseInt(item.pdf_order, 10);
          return {
            id: 0,
            equipment_title: title,
            subsystem_component: sanitizeVal(item.subsystem_component),
            maintenance_routine: sanitizeVal(item.maintenance_routine),
            checks_instructions: sanitizeVal(item.checks_instructions),
            page: pageNum,
            pdf_order: Number.isFinite(pdfOrderRaw) && pdfOrderRaw > 0 ? pdfOrderRaw : undefined
          };
        }
      });
      // Fill missing pdf_order from response order after map.
      stampPdfOrder(output.maintenance);
    }

    if (resultJson.spare_parts && Array.isArray(resultJson.spare_parts)) {
      output.spare_parts = resultJson.spare_parts.map((item, idx) => {
        let title = sanitizeVal(item.equipment_title);
        if (title === "NA") title = cleanDocName;
        const pdfOrderRaw = parseInt(item.pdf_order, 10);
        return {
          id: 0,
          equipment_title: title,
          subsystem_location: sanitizeVal(item.subsystem_location),
          item_no: sanitizeVal(item.item_no),
          part_name: sanitizeVal(item.part_name),
          part_number_code: sanitizeVal(item.part_number_code),
          drawing_model_no: sanitizeVal(item.drawing_model_no),
          oem_standard_body: sanitizeVal(item.oem_standard_body),
          part_categorization: sanitizeVal(item.part_categorization),
          quantity: sanitizeVal(item.quantity),
          recommended_stock_qty: sanitizeVal(item.recommended_stock_qty),
          warranty_period: sanitizeVal(item.warranty_period),
          frequency_of_use: sanitizeVal(item.frequency_of_use) === "NA" && item.periodic_use ? sanitizeVal(item.periodic_use) : sanitizeVal(item.frequency_of_use),
          page: pageNum,
          // Preserve model order when provided; otherwise use response array order.
          pdf_order: Number.isFinite(pdfOrderRaw) && pdfOrderRaw > 0 ? pdfOrderRaw : (idx + 1)
        };
      });
    }

    if (resultJson.troubleshooting && Array.isArray(resultJson.troubleshooting)) {
      output.troubleshooting = resultJson.troubleshooting.map((item, idx) => {
        let title = sanitizeVal(item.equipment_title);
        if (title === "NA") title = cleanDocName;
        const pdfOrderRaw = parseInt(item.pdf_order, 10);
        return {
          id: 0,
          equipment_title: title,
          subsystem_component: sanitizeVal(item.subsystem_component),
          problem: sanitizeVal(item.problem),
          root_cause_solution: sanitizeVal(item.root_cause_solution),
          page: pageNum,
          pdf_order: Number.isFinite(pdfOrderRaw) && pdfOrderRaw > 0 ? pdfOrderRaw : (idx + 1)
        };
      });
    }



    // Filter out incomplete/placeholder rows with no valid data
    output.maintenance = output.maintenance.filter(isCleanMaintenanceRow);
    output.spare_parts = output.spare_parts.filter(isCleanSparePartsRow);
    if (output.troubleshooting) {
       output.troubleshooting = output.troubleshooting.filter(r => 
         r.problem !== "NA" && 
         r.root_cause_solution !== "NA" && 
         r.problem.length > 5 && 
         r.root_cause_solution.length > 5 &&
         !r.problem.toLowerCase().includes("... ...") &&
         !r.problem.toLowerCase().includes(". . . .")
       );
    }

    // Guardrail: non-OCR pages must be text-grounded to reduce index/TOC hallucinations.
    if (!base64Image) {
      const sourcePageText = String(sourceText || "");
      if (sourcePageText.trim()) {
        output.maintenance = output.maintenance.filter(r => isTextGroundedInSource(r.checks_instructions, sourcePageText));
        output.spare_parts = output.spare_parts.filter(r => {
          const probe = `${r.part_name} ${r.part_number_code} ${r.drawing_model_no}`;
          return isTextGroundedInSource(probe, sourcePageText);
        });
        output.troubleshooting = output.troubleshooting.filter(r => {
          const probe = `${r.problem} ${r.root_cause_solution}`;
          return isTextGroundedInSource(probe, sourcePageText);
        });
      }
    }

    // After filters, keep a dense within-page pdf_order for stable grid/export sorting.
    stampPdfOrder(output.maintenance);
    stampPdfOrder(output.spare_parts);
    stampPdfOrder(output.troubleshooting);

    return normalizeExtraction(output);
  } catch (parseErr) {
    console.error(`JSON Parsing failed for ${providerLabel || "LLM"} response:`, cleanResponse);
    throw new Error("JSON Parse Error: " + parseErr.message + " | Raw Output: " + cleanResponse.substring(0, 100) + "...");
  }
}

// Query local Ollama API to extract structured parts & maintenance instructions
async function runOllamaExtractor(text, docName, pageNum, base64Image = null) {
  if (!isOllamaMode()) {
    throw new Error("Ollama extractor blocked: current engine mode is " + engineMode);
  }
  const systemPrompt = buildExtractionPrompt(text, docName);

  const fetchBody = {
    model: ollamaModel,
    prompt: systemPrompt,
    stream: false,
    format: "json",
    options: {
      temperature: 0.1
    }
  };
  if (base64Image) {
    fetchBody.images = [base64Image];
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 180000); // 180 seconds timeout

  let response;
  try {
    response = await fetch(`${ollamaUrl}/api/generate`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(fetchBody),
      signal: controller.signal
    });
  } catch (fetchErr) {
    if (fetchErr.name === 'AbortError') {
      throw new Error("Ollama took too long to respond (timeout). The image might be too complex or the model is overloaded.");
    }
    throw fetchErr;
  } finally {
    clearTimeout(timeoutId);
  }

  if (!response.ok) {
    throw new Error(`Ollama API error: ${response.status}`);
  }

  const data = await response.json();
  return processRawModelResponse(data.response, docName, pageNum, base64Image, "Ollama", text);
}

// Query the Google Gemini API (cloud) to extract structured parts & maintenance instructions.
// Uses the same prompt/parsing pipeline as Ollama, so extraction quality/fields stay identical
// regardless of which engine is active — only the transport (REST call + auth) differs.
async function runGeminiExtractor(text, docName, pageNum, base64Image = null, mimeType = "image/jpeg") {
  refreshAdminTestGeminiKey();
  if (!isGeminiMode()) {
    throw new Error("Gemini extractor blocked: current engine mode is " + engineMode);
  }
  const systemPrompt = buildExtractionPrompt(text, docName);
  let modelName = normalizeGeminiModel(geminiModel);

  const parts = [{ text: systemPrompt }];
  if (base64Image) {
    parts.push({ inline_data: { mime_type: mimeType || "image/jpeg", data: base64Image } });
  }

  const fetchBody = {
    contents: [{ role: "user", parts }],
    generationConfig: {
      temperature: 0.1,
      responseMimeType: "application/json",
      // Dense OCR spare tables need more output headroom than text pages.
      maxOutputTokens: base64Image ? 16384 : 8192
    }
  };

  async function postToGemini(activeModel) {
    return fetchGeminiGenerateContent(activeModel, fetchBody, {
      timeoutMs: 180000,
      requestLabel: `page-${pageNum}`
    });
  }

  let response = await postToGemini(modelName);

  // No automatic model fallback on 404 — that doubled traffic across two Flash models
  // in AI Studio usage. Fail loudly so the selected model can be fixed in settings.
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
        `Pick a live model in Settings — automatic fallback is disabled to avoid dual-model token waste.`
      );
    }
    throw new Error(`Gemini API error: ${response.status}${errDetail ? " - " + errDetail : ""}`);
  }

  const data = await response.json();
  const candidate = data.candidates && data.candidates[0];
  const rawText = (candidate && candidate.content && candidate.content.parts && candidate.content.parts[0] && candidate.content.parts[0].text) || "";
  if (!rawText) {
    const blockReason = data.promptFeedback && data.promptFeedback.blockReason;
    throw new Error(`Gemini returned no content${blockReason ? " (blocked: " + blockReason + ")" : " (check API key/model name)"}.`);
  }

  return processRawModelResponse(rawText, docName, pageNum, base64Image, "Gemini", text);
}

// Single entry point used by all extraction call sites — dispatches to whichever cloud/local
// LLM engine is currently selected, so callers don't need to branch on engineMode themselves.
// mimeType is only relevant for Gemini (Ollama's API doesn't require one) and defaults to JPEG,
// which matches the canvas-rendered OCR pages; pass the real file type for uploaded images.
async function runLLMExtractor(text, docName, pageNum, base64Image = null, mimeType = "image/jpeg") {
  if (engineMode === "gemini") {
    return runGeminiExtractor(text, docName, pageNum, base64Image, mimeType);
  }
  if (engineMode === "ollama") {
    return runOllamaExtractor(text, docName, pageNum, base64Image);
  }
  throw new Error("LLM extractor is disabled in Heuristics mode.");
}

// Simple markdown formatter helper for chat replies
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
      // 1. Tab Filter
      if (currentTabFilter !== "all") {
        const routine = String(row.maintenance_routine || "").toLowerCase();
        if (currentTabFilter === "hours" && !routine.includes("hour")) return false;
        if (currentTabFilter === "days" && !routine.includes("day") && !routine.includes("shift") && !routine.includes("week")) return false;
        if (currentTabFilter === "months" && !routine.includes("month")) return false;
        if (currentTabFilter === "years" && !routine.includes("year")) return false;
      }
      
      // 2. Search Text Query
      if (currentSearchQuery) {
        const q = currentSearchQuery.toLowerCase();
        const matchText = activeEquipmentCategory === "Logbook"
          ? `${row.date} ${row.maintenance_work_description} ${row.parts_renewed} ${row.attended_by} ${row.remarks}`.toLowerCase()
          : `${row.equipment_title} ${row.subsystem_component} ${row.maintenance_routine} ${row.checks_instructions}`.toLowerCase();
        if (!matchText.includes(q)) return false;
      }

      // 3. Cognitive Chat Highlight Filter
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
            <td class="page-cell" style="font-weight: 600;">#${row.id}</td>
            <td class="editable" data-col="date" style="font-weight: 500;">${escapeHTML(row.date || "NA")}</td>
            <td class="editable" data-col="maintenance_work_description" style="white-space: normal; max-width: 300px;">${escapeHTML(row.maintenance_work_description || "NA")}</td>
            <td class="editable" data-col="parts_renewed" style="font-weight: 500; font-family: monospace;">${escapeHTML(row.parts_renewed || "NA")}</td>
            <td class="editable" data-col="attended_by">${escapeHTML(row.attended_by || "NA")}</td>
            <td class="editable" data-col="remarks" style="white-space: normal;">${escapeHTML(row.remarks || "NA")}</td>
            <td class="confidence-cell" title="Extraction confidence">${formatConfidenceCell(row)}</td>
            <td class="page-cell editable" data-col="page" style="text-align: center;">Page ${row.page || "NA"}</td>
            <td class="row-actions">
              <button class="row-btn btn-delete" title="Delete record"><i data-lucide="trash-2"></i></button>
            </td>
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
            <td class="page-cell" style="font-weight: 600;">#${row.id}</td>
            <td class="editable" data-col="equipment_title">${escapeHTML(row.equipment_title || "NA")}</td>
            <td class="editable" data-col="subsystem_component" style="font-weight: 500;">${escapeHTML(row.subsystem_component || "NA")}</td>
            <td class="editable" data-col="maintenance_routine"><span class="freq-tag ${tagClass}">${escapeHTML(row.maintenance_routine || "NA")}</span></td>
            <td class="editable" data-col="checks_instructions" style="white-space: normal; max-width: 350px;">${escapeHTML(row.checks_instructions || "NA")}</td>
            <td class="confidence-cell" title="Extraction confidence">${formatConfidenceCell(row)}</td>
            <td class="page-cell editable" data-col="page" style="text-align: center;">Page ${row.page || "NA"}</td>
            <td class="row-actions">
              <button class="row-btn btn-delete" title="Delete record"><i data-lucide="trash-2"></i></button>
            </td>
          `;
          maintenanceTableBody.appendChild(tr);
        });
      }
    }
  } else if (activeRegistryTab === "spare_parts") {
    // Spare Parts Tab
    sparePartsTableBody.innerHTML = "";
    
    filteredSpareParts = sparePartsRegistry.filter(row => {
      // 1. Part-type filter tabs
      if (!matchesSparePartTypeFilter(row, currentSpareFilter)) return false;

      // 2. Search Text Query
      if (currentSearchQuery) {
        const q = currentSearchQuery.toLowerCase();
        const matchText = `${row.equipment_title} ${row.subsystem_location} ${row.item_no} ${row.part_name} ${row.part_number_code} ${row.drawing_model_no} ${row.oem_standard_body} ${row.part_categorization} ${row.quantity} ${row.frequency_of_use}`.toLowerCase();
        if (!matchText.includes(q)) return false;
      }

      // 3. Cognitive Chat Highlight Filter
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
          <td class="page-cell" style="font-weight: 600;">#${row.id}</td>
          <td class="editable" data-col="equipment_title">${escapeHTML(row.equipment_title || "NA")}</td>
          <td class="editable" data-col="subsystem_location">${escapeHTML(row.subsystem_location || "NA")}</td>
          <td class="editable" data-col="item_no" style="font-family: monospace;">${escapeHTML(row.item_no || "NA")}</td>
          <td class="editable" data-col="part_name" style="font-weight: 500;">${escapeHTML(row.part_name || "NA")}</td>
          <td class="editable" data-col="part_number_code" style="font-family: monospace; color: var(--accent-cyan);">${escapeHTML(row.part_number_code || "NA")}</td>
          <td class="editable" data-col="drawing_model_no" style="font-family: monospace;">${escapeHTML(row.drawing_model_no || "NA")}</td>
          <td class="editable" data-col="oem_standard_body">${escapeHTML(row.oem_standard_body || "NA")}</td>
          <td class="editable" data-col="part_categorization" style="color: var(--accent-amber); font-weight: 500;"><span class="freq-tag tag-parts">${escapeHTML(row.part_categorization || "NA")}</span></td>
          <td class="editable" data-col="quantity" style="font-weight: 600; text-align: center; color: var(--text-main);">${escapeHTML(row.quantity || "NA")}</td>
          <td class="editable" data-col="recommended_stock_qty" style="font-weight: 600; text-align: center; color: var(--accent-green);">${escapeHTML(row.recommended_stock_qty || "NA")}</td>
          <td class="editable" data-col="warranty_period">${escapeHTML(row.warranty_period || "NA")}</td>
          <td class="editable" data-col="frequency_of_use" style="text-align: center;">${escapeHTML(row.frequency_of_use || "NA")}</td>
          <td class="confidence-cell" title="Extraction confidence">${formatConfidenceCell(row)}</td>
          <td class="page-cell editable" data-col="page" style="text-align: center;">Page ${row.page || "NA"}</td>
          <td class="row-actions">
            <button class="row-btn btn-delete" title="Delete record"><i data-lucide="trash-2"></i></button>
          </td>
        `;
        sparePartsTableBody.appendChild(tr);
      });
    }
  } else if (activeRegistryTab === "troubleshooting") {
    // Troubleshooting Tab
    troubleshootingTableBody.innerHTML = "";
    
    filteredTroubleshooting = troubleshootingRegistry.filter(row => {
      // 1. Search Text Query
      if (currentSearchQuery) {
        const q = currentSearchQuery.toLowerCase();
        const matchText = `${row.equipment_title} ${row.subsystem_component} ${row.problem} ${row.root_cause_solution}`.toLowerCase();
        if (!matchText.includes(q)) return false;
      }

      // 2. Cognitive Chat Highlight Filter
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
          <td class="page-cell" style="font-weight: 600;">#${row.id}</td>
          <td class="editable" data-col="equipment_title">${escapeHTML(row.equipment_title || "NA")}</td>
          <td class="editable" data-col="subsystem_component" style="font-weight: 500;">${escapeHTML(row.subsystem_component || "NA")}</td>
          <td class="editable" data-col="problem" style="color: var(--accent-amber); font-weight: 500; white-space: normal;">${escapeHTML(row.problem || "NA")}</td>
          <td class="editable" data-col="root_cause_solution" style="white-space: normal;">${escapeHTML(row.root_cause_solution || "NA")}</td>
          <td class="confidence-cell" title="Extraction confidence">${formatConfidenceCell(row)}</td>
          <td class="page-cell editable" data-col="page" style="text-align: center;">Page ${row.page || "NA"}</td>
          <td class="row-actions">
            <button class="row-btn btn-delete" title="Delete record"><i data-lucide="trash-2"></i></button>
          </td>
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

  // Delete row button click
  const deleteBtns = document.querySelectorAll(".data-table .btn-delete");
  deleteBtns.forEach(btn => {
    btn.addEventListener("click", function(e) {
      e.stopPropagation();
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
      if (e.target.closest(".btn-delete") || e.target.closest("td.editing") || e.target.closest("input")) return;
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
      page: "NA"
    } : {
      id: newId,
      equipment_title: "Equipment Title",
      subsystem_component: "Sub-system / Component",
      maintenance_routine: "Monthly",
      checks_instructions: "Required Maintenance Checks / Instructions",
      page: "NA"
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
      page: "NA"
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
      page: "NA"
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
        "Confidence": formatConfidenceCell(r),
        "Source Page Reference": r.page === "NA" || r.page == null ? "NA" : `Page ${r.page}`
      })),
      cols: [
        { wch: 10 }, { wch: 15 }, { wch: 45 }, { wch: 25 },
        { wch: 20 }, { wch: 45 }, { wch: 12 }, { wch: 15 }
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
      "Confidence": formatConfidenceCell(r),
      "Source Page Reference": r.page === "NA" || r.page == null ? "NA" : `Page ${r.page}`
    })),
    cols: [
      { wch: 10 }, { wch: 22 }, { wch: 28 }, { wch: 25 }, { wch: 65 }, { wch: 12 }, { wch: 15 }
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
      "Confidence": formatConfidenceCell(r),
      "Source Page Reference": r.page === "NA" || r.page == null ? "NA" : `Page ${r.page}`
    })),
    cols: [
      { wch: 10 }, { wch: 12 }, { wch: 22 }, { wch: 28 }, { wch: 10 }, { wch: 28 },
      { wch: 25 }, { wch: 22 }, { wch: 20 }, { wch: 20 }, { wch: 12 },
      { wch: 15 }, { wch: 15 }, { wch: 22 }, { wch: 12 }, { wch: 15 }
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
      "Confidence": formatConfidenceCell(r),
      "Source Page Reference": r.page === "NA" || r.page == null ? "NA" : `Page ${r.page}`
    })),
    cols: [
      { wch: 10 }, { wch: 22 }, { wch: 28 }, { wch: 35 }, { wch: 65 }, { wch: 12 }, { wch: 15 }
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
 * 4. Document File Reader Scraper (PDF.js)
 * ------------------------------------------------------------- */

// Drop zone hover drag indicators
function openFilePicker() {
  if (!fileInput) return;
  if (isExtracting) {
    alert("An extraction is already in progress. Please wait for it to finish or cancel it first.");
    return;
  }
  try {
    fileInput.value = "";
    fileInput.click();
  } catch (err) {
    console.error("Could not open file picker", err);
    alert("Could not open the file picker. Try drag-and-drop, or refresh the page.");
  }
}

if (dropZone && fileInput) {
  ['dragenter', 'dragover'].forEach(eventName => {
    dropZone.addEventListener(eventName, (e) => {
      e.preventDefault();
      dropZone.classList.add('dragover');
    }, false);
  });

  ['dragleave', 'drop'].forEach(eventName => {
    dropZone.addEventListener(eventName, (e) => {
      e.preventDefault();
      dropZone.classList.remove('dragover');
    }, false);
  });

  dropZone.addEventListener('drop', (e) => {
    const dt = e.dataTransfer;
    const files = dt.files;
    if (files && files.length > 0) {
      handleFileUpload(files[0]);
    }
  });

  // Click card to browse (skip page-range controls and native browse label)
  dropZone.addEventListener('click', (e) => {
    if (isExtracting || e.target.closest('#progress-overlay')) return;
    if (e.target.closest('#page-range-row')) return;
    if (e.target.closest('#browse-btn') || e.target.closest('label[for="file-input"]')) return;
    openFilePicker();
  });
}

// Native <label for="file-input"> already opens the picker; keep a JS fallback too
if (browseBtn && fileInput) {
  browseBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    // label[for] handles the picker natively; only force-click if needed
    if (browseBtn.tagName !== "LABEL") {
      e.preventDefault();
      openFilePicker();
    }
  });
}

if (fileInput) {
  fileInput.addEventListener('change', (e) => {
    if (e.target.files && e.target.files.length > 0) {
      handleFileUpload(e.target.files[0]);
      e.target.value = "";
    }
  });
}


const MAX_UPLOAD_SIZE_BYTES = 1024 * 1024 * 1024; // 1GB, matches UI copy

async function handleFileUpload(file) {
  if (isExtracting) {
    alert("An extraction is already in progress. Please wait for it to finish or cancel it first.");
    return;
  }

  if (typeof window.requireAuthForApi === "function") {
    try {
      window.requireAuthForApi();
    } catch (e) {
      return;
    }
  }

  const extension = file.name.split('.').pop().toLowerCase();
  
  if (extension !== 'pdf' && extension !== 'txt' && extension !== 'doc' && extension !== 'docx' && extension !== 'jpg' && extension !== 'jpeg' && extension !== 'png') {
    alert("Unsupported file format! Please upload a PDF, Word (DOC/DOCX), TXT, or Image (JPG/PNG).");
    return;
  }

  if (file.size > MAX_UPLOAD_SIZE_BYTES) {
    alert(`File is too large (${(file.size / (1024 * 1024)).toFixed(1)}MB). Maximum supported size is 1GB.`);
    return;
  }

  // Starting a new document replaces the previous registries rather than merging into them
  if (maintenanceRegistry.length > 0 || sparePartsRegistry.length > 0 || troubleshootingRegistry.length > 0) {
    const proceed = confirm(`Loading "${file.name}" will clear the current registry data (${maintenanceRegistry.length} maintenance, ${sparePartsRegistry.length} spare parts, ${troubleshootingRegistry.length} troubleshooting records). Continue?`);
    if (!proceed) return;
  }
  maintenanceRegistry = [];
  sparePartsRegistry = [];
  troubleshootingRegistry = [];
  highlightRecordIds = [];
  lastSourceDocName = file.name;
  renderGrid();

  // Show processing UI immediately (lock + overlay + compact upload card)
  setActiveDocBadge(file.name);
  setExtractingUi(true, `Processing "${file.name}"`, "Checking Python API...");

  let extractFinishedCleanly = false;
  try {
    // Prefer FastAPI for Gemini/Ollama PDF/TXT/image work; keep browser path otherwise.
    if (canUsePythonApiForFile(file, extension)) {
      if (progressStatus) progressStatus.innerText = "Checking Python API (5s timeout)...";
      const apiHealth = await checkPythonApiHealth();
      if (apiHealth.ok) {
        if (apiHealth.busy) {
          setActiveDocBadge("");
          alert(
            "API is already busy with another extraction.\n\n" +
            "In the API terminal press Ctrl+C, then run ./start-api.sh again.\n" +
            "Then upload the full manual again (one job at a time)."
          );
          return;
        }

        let pageCountHint = null;

        if (extension === "pdf") {
          // Page count helps the timeout estimate; skip if it takes too long on huge files.
          try {
            if (progressStatus) progressStatus.innerText = "Counting PDF pages (can take a minute on large manuals)...";
            pageCountHint = await Promise.race([
              countPdfPages(file),
              new Promise((_, reject) => setTimeout(() => reject(new Error("page-count-timeout")), 90000))
            ]);
          } catch (e) {
            console.warn("Could not count PDF pages before upload:", e);
            pageCountHint = null;
          }
          const okLarge = await confirmLargePdfIfNeeded(pageCountHint, file.size);
          if (!okLarge) {
            setActiveDocBadge("");
            appendChatSystemMessage("Extraction cancelled.");
            return;
          }
        }
        if (progressStatus) progressStatus.innerText = "Python API online — extract started. Keep this tab open...";
        const result = await extractViaPythonApi(file, pageCountHint);
        applyApiExtractResult(result, file);
        extractFinishedCleanly = true;
        return;
      }
      appendChatSystemMessage(
        `ℹ️ Python API not reachable at **${apiBaseUrl}** (busy, down, or frozen). ` +
        `Press **Ctrl+C** in the API terminal, run \`./start-api.sh\` again, or continuing with in-browser extractor.`
      );
    } else if (engineMode === "heuristics") {
      appendChatSystemMessage("ℹ️ Heuristics mode uses the in-browser extractor.");
    } else if (extension === "doc" || extension === "docx") {
      appendChatSystemMessage("ℹ️ Word documents use the in-browser Mammoth extractor.");
    }

    if (progressStatus) progressStatus.innerText = "Initializing file reader (browser fallback)...";

    if (extension === 'pdf') {
      await extractPDFText(file);
    } else if (extension === 'txt') {
      await extractTXTText(file);
    } else if (extension === 'doc' || extension === 'docx') {
      await extractWordText(file);
    } else {
      await extractImageText(file);
    }
    extractFinishedCleanly = true;
  } catch (error) {
    console.error(error);
    const msg = String(error && error.message ? error.message : error);
    if (/gemini api key required/i.test(msg)) {
      alert(
        "Upload failed: Gemini API key required.\n\n" +
        "Set GEMINI_API_KEY in backend/.env and restart ./start-api.sh."
      );
    } else {
      alert(`Error parsing document: ${msg}`);
    }
    setActiveDocBadge("");
  } finally {
    // API path clears UI inside applyApiExtractResult; browser parsers clear themselves.
    // Always unlock if something returned early or threw before those paths ran.
    if (!extractFinishedCleanly && isExtracting) {
      clearExtractingUi();
    }
  }
}



// Read Word manuals (.docx via Mammoth; legacy .doc needs conversion to .docx)
async function extractWordText(file) {
  const extension = file.name.split(".").pop().toLowerCase();

  // Mammoth only supports OOXML .docx. Legacy binary .doc cannot be parsed in-browser.
  if (extension === "doc") {
    throw new Error(
      'Legacy .doc format is not supported in the browser. Open the file in Word or Google Docs and "Save As" / "Download as" .docx, then upload again.'
    );
  }

  if (typeof mammoth === "undefined" || !mammoth.extractRawText) {
    throw new Error("Word parser (mammoth.js) failed to load. Check your network connection and refresh the page.");
  }

  progressStatus.innerText = "Extracting text from Word document...";
  progressFill.style.width = "15%";

  const arrayBuffer = await file.arrayBuffer();
  const result = await mammoth.extractRawText({ arrayBuffer });
  const text = String(result.value || "").trim();

  if (!text) {
    throw new Error("No readable text found in this Word document.");
  }

  if (result.messages && result.messages.length > 0) {
    console.warn("Mammoth Word parse notes:", result.messages);
  }

  // Reuse the TXT extraction / chunking / LLM pipeline with the original Word filename
  const textFile = new File([text], file.name, { type: "text/plain" });
  return extractTXTText(textFile);
}

// Read plain text manual
function extractTXTText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = async function(e) {
      const text = e.target.result;
      
      // Setup loaded pages as simple single block
      loadedPages = [{ pageNum: 1, text: text }];
      isExtracting = true;
      abortExtraction = false;
      
      try {
        let maintCount = 0;
        let sparesCount = 0;
        let troubleCount = 0;
        let llmChunksProcessed = 0;
        let totalChunksCount = 0;
        
        if (engineMode === "ollama" || engineMode === "gemini") {
          const engineLabel = engineMode === "gemini" ? `Gemini (${geminiModel})` : `Ollama (${ollamaModel})`;
          const maxChunkSize = 8000;
          if (text.length > maxChunkSize) {
            let chunks = [];
            let i = 0;
            while (i < text.length) {
              let end = i + maxChunkSize;
              if (end < text.length) {
                // Find nearest newline within the last 500 chars of the chunk
                const searchWindow = text.substring(Math.max(i, end - 500), end);
                const lastNewline = searchWindow.lastIndexOf('\n');
                if (lastNewline !== -1) {
                  end = end - 500 + lastNewline + 1; // Split right after newline
                }
              }
              chunks.push(text.substring(i, end));
              i = end;
            }
            totalChunksCount = chunks.length;
            appendChatSystemMessage(`Text manual is large. Splitting into **${chunks.length} chunks** for ${engineLabel} processing...`);
            
            for (let idx = 0; idx < chunks.length; idx++) {
              if (abortExtraction) {
                appendChatSystemMessage("Extraction aborted by user.");
                break;
              }
              llmChunksProcessed++;
              progressStatus.innerText = `Processing chunk ${idx + 1} of ${chunks.length} with ${engineLabel}...`;
              progressFill.style.width = `${Math.round(((idx + 1) / chunks.length) * 100)}%`;
              
              const result = await runLLMExtractor(chunks[idx], file.name, 1);
              if (result.maintenance && result.maintenance.length > 0) {
                maintCount += result.maintenance.length;
                const startingId = maintenanceRegistry.length > 0 ? Math.max(...maintenanceRegistry.map(r => r.id)) + 1 : 1;
                result.maintenance.forEach((r, rIdx) => r.id = startingId + rIdx);
                maintenanceRegistry = [...maintenanceRegistry, ...result.maintenance];
              }
              if (result.spare_parts && result.spare_parts.length > 0) {
                sparesCount += result.spare_parts.length;
                const startingId = sparePartsRegistry.length > 0 ? Math.max(...sparePartsRegistry.map(r => r.id)) + 1 : 1;
                result.spare_parts.forEach((r, rIdx) => r.id = startingId + rIdx);
                sparePartsRegistry = [...sparePartsRegistry, ...result.spare_parts];
              }
              if (result.troubleshooting && result.troubleshooting.length > 0) {
                troubleCount += result.troubleshooting.length;
                const startingId = troubleshootingRegistry.length > 0 ? Math.max(...troubleshootingRegistry.map(r => r.id)) + 1 : 1;
                result.troubleshooting.forEach((r, rIdx) => r.id = startingId + rIdx);
                troubleshootingRegistry = [...troubleshootingRegistry, ...result.troubleshooting];
              }
              renderGrid();
            }
          } else {
              llmChunksProcessed = 1;
              totalChunksCount = 1;
              progressStatus.innerText = `Extracting using ${engineLabel}...`;
              progressFill.style.width = "50%";
              const result = await runLLMExtractor(text, file.name, 1);
              if (result.maintenance && result.maintenance.length > 0) {
                maintCount += result.maintenance.length;
                const startingId = maintenanceRegistry.length > 0 ? Math.max(...maintenanceRegistry.map(r => r.id)) + 1 : 1;
                result.maintenance.forEach((r, rIdx) => r.id = startingId + rIdx);
                maintenanceRegistry = [...maintenanceRegistry, ...result.maintenance];
              }
              if (result.spare_parts && result.spare_parts.length > 0) {
                sparesCount += result.spare_parts.length;
                const startingId = sparePartsRegistry.length > 0 ? Math.max(...sparePartsRegistry.map(r => r.id)) + 1 : 1;
                result.spare_parts.forEach((r, rIdx) => r.id = startingId + rIdx);
                sparePartsRegistry = [...sparePartsRegistry, ...result.spare_parts];
              }
              if (result.troubleshooting && result.troubleshooting.length > 0) {
                troubleCount += result.troubleshooting.length;
                const startingId = troubleshootingRegistry.length > 0 ? Math.max(...troubleshootingRegistry.map(r => r.id)) + 1 : 1;
                result.troubleshooting.forEach((r, rIdx) => r.id = startingId + rIdx);
                troubleshootingRegistry = [...troubleshootingRegistry, ...result.troubleshooting];
              }
          }
        } else {
          // Heuristics Mode
          const result = runRuleExtractorHeuristics(text, file.name);
          if (result.maintenance && result.maintenance.length > 0) {
            maintCount += result.maintenance.length;
            const startingId = maintenanceRegistry.length > 0 ? Math.max(...maintenanceRegistry.map(r => r.id)) + 1 : 1;
            result.maintenance.forEach((r, rIdx) => r.id = startingId + rIdx);
            maintenanceRegistry = [...maintenanceRegistry, ...result.maintenance];
          }
          if (result.spare_parts && result.spare_parts.length > 0) {
            sparesCount += result.spare_parts.length;
            const startingId = sparePartsRegistry.length > 0 ? Math.max(...sparePartsRegistry.map(r => r.id)) + 1 : 1;
            result.spare_parts.forEach((r, rIdx) => r.id = startingId + rIdx);
            sparePartsRegistry = [...sparePartsRegistry, ...result.spare_parts];
          }
          if (result.troubleshooting && result.troubleshooting.length > 0) {
            troubleCount += result.troubleshooting.length;
            const startingId = troubleshootingRegistry.length > 0 ? Math.max(...troubleshootingRegistry.map(r => r.id)) + 1 : 1;
            result.troubleshooting.forEach((r, rIdx) => r.id = startingId + rIdx);
            troubleshootingRegistry = [...troubleshootingRegistry, ...result.troubleshooting];
          }
        }
        
        progressFill.style.width = "100%";
        progressStatus.innerText = `Complete!`;
        
        setTimeout(() => {
          clearExtractingUi();
          setActiveDocBadge(file.name);
          
          const labelModeText = engineMode === "ollama" ? `local LLM (${ollamaModel}) processing ${llmChunksProcessed} / ${totalChunksCount} chunks` : engineMode === "gemini" ? `Gemini API (${geminiModel}) processing ${llmChunksProcessed} / ${totalChunksCount} chunks` : "heuristics";
          appendChatSystemMessage(`Successfully parsed text manual **"${file.name}"** using **${labelModeText}**! Extracted **${maintCount}** tasks, **${sparesCount}** spare parts, and **${troubleCount}** troubleshooting issues into the registries.`);
          preferTabWithResults();
          renderGrid();
          offerSaveExcelAfterExtraction(file);
          resolve();
        }, 1000);
        
      } catch (err) {
        console.error("LLM text parsing failed:", err);
        alert(`${engineMode === "gemini" ? "Gemini API" : "Ollama"} parsing failed: ${err.message}. Falling back to client Heuristics.`);
        const fallbackResult = runRuleExtractorHeuristics(text, file.name);
        if (fallbackResult.maintenance && fallbackResult.maintenance.length > 0) {
          const startingId = maintenanceRegistry.length > 0 ? Math.max(...maintenanceRegistry.map(r => r.id)) + 1 : 1;
          fallbackResult.maintenance.forEach((r, rIdx) => r.id = startingId + rIdx);
          maintenanceRegistry = [...maintenanceRegistry, ...fallbackResult.maintenance];
        }
        if (fallbackResult.spare_parts && fallbackResult.spare_parts.length > 0) {
          const startingId = sparePartsRegistry.length > 0 ? Math.max(...sparePartsRegistry.map(r => r.id)) + 1 : 1;
          fallbackResult.spare_parts.forEach((r, rIdx) => r.id = startingId + rIdx);
          sparePartsRegistry = [...sparePartsRegistry, ...fallbackResult.spare_parts];
        }
        clearExtractingUi();
        preferTabWithResults();
        renderGrid();
        resolve();
      }
    };
    reader.onerror = () => {
      clearExtractingUi();
      reject(new Error("File reading failed."));
    };
    reader.readAsText(file);
  });
}

// Resolve the optional "From Page" / "To Page" inputs into a valid, clamped
// [start, end] range for the given document. Blank/invalid inputs fall back
// to parsing the entire document (start=1, end=totalPages).
function resolvePageRange(totalPages) {
  const MAX_PAGES = 5000;
  let start = parseInt(pageRangeStartInput && pageRangeStartInput.value, 10);
  let end = parseInt(pageRangeEndInput && pageRangeEndInput.value, 10);
  const hasStart = !isNaN(start) && start > 0;
  const hasEnd = !isNaN(end) && end > 0;

  if (!hasStart && !hasEnd) {
    const cappedEnd = Math.min(totalPages, MAX_PAGES);
    return {
      start: 1,
      end: cappedEnd,
      isPartial: cappedEnd < totalPages
    };
  }

  if (!hasStart) start = 1;
  if (!hasEnd) end = totalPages;

  // Clamp into valid document bounds, and swap if entered backwards
  start = Math.max(1, Math.min(start, totalPages));
  end = Math.max(1, Math.min(end, totalPages));
  if (end < start) {
    const tmp = start;
    start = end;
    end = tmp;
  }

  if ((end - start + 1) > MAX_PAGES) {
    end = start + MAX_PAGES - 1;
    end = Math.min(end, totalPages);
  }

  return { start, end, isPartial: (start !== 1 || end !== totalPages) };
}

function getLLMConcurrency() {
  // Tier 1: 8x caused ~50% success (mostly 404 fallback churn + 503 overload).
  // Cap at 4; 503/429 retries stay per-request. Ollama stays sequential.
  return engineMode === "gemini" ? 4 : 1;
}

async function mapWithConcurrency(items, concurrency, worker) {
  const results = new Array(items.length);
  let nextIndex = 0;

  async function runner() {
    while (nextIndex < items.length) {
      if (abortExtraction) return;
      const current = nextIndex++;
      results[current] = await worker(items[current], current);
    }
  }

  const poolSize = Math.max(1, Math.min(concurrency, items.length || 1));
  await Promise.all(Array.from({ length: poolSize }, () => runner()));
  return results;
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

function mergeExtractionResult(result, counts) {
  if (!result) return;
  if (result.maintenance && result.maintenance.length > 0) {
    counts.maint += result.maintenance.length;
    const startingId = maintenanceRegistry.length > 0 ? Math.max(...maintenanceRegistry.map(r => r.id)) + 1 : 1;
    result.maintenance.forEach((r, rIdx) => { r.id = startingId + rIdx; });
    maintenanceRegistry = [...maintenanceRegistry, ...result.maintenance];
  }
  if (result.spare_parts && result.spare_parts.length > 0) {
    counts.spares += result.spare_parts.length;
    const startingId = sparePartsRegistry.length > 0 ? Math.max(...sparePartsRegistry.map(r => r.id)) + 1 : 1;
    result.spare_parts.forEach((r, rIdx) => { r.id = startingId + rIdx; });
    sparePartsRegistry = [...sparePartsRegistry, ...result.spare_parts];
  }
  if (result.troubleshooting && result.troubleshooting.length > 0) {
    counts.trouble += result.troubleshooting.length;
    const startingId = troubleshootingRegistry.length > 0 ? Math.max(...troubleshootingRegistry.map(r => r.id)) + 1 : 1;
    result.troubleshooting.forEach((r, rIdx) => { r.id = startingId + rIdx; });
    troubleshootingRegistry = [...troubleshootingRegistry, ...result.troubleshooting];
  }
  assembleRegistriesInPageOrder();
}

// Page-by-page PDF read with single-page LLM parser, parallelized via concurrency pool.
// Full page text is sent (no truncation). Grid refreshes after every page.
function extractPDFText(file) {
  return new Promise((resolve, reject) => {
    const fileReader = new FileReader();
    fileReader.onload = async function() {
      const typedarray = new Uint8Array(this.result);
      isExtracting = true;
      abortExtraction = false;
      
      try {
        const pdf = await pdfjsLib.getDocument(typedarray).promise;
        const totalPages = pdf.numPages;
        const { start: rangeStart, end: rangeEnd, isPartial: isPartialRange } = resolvePageRange(totalPages);
        loadedPages = [];
        let compiledText = "";
        const counts = { maint: 0, spares: 0, trouble: 0 };
        let llmPagesProcessed = 0;
        const llmJobs = [];
        const useLLM = engineMode === "ollama" || engineMode === "gemini";
        const engineLabel = engineMode === "gemini" ? "Gemini" : "Ollama";
        const concurrency = useLLM ? getLLMConcurrency() : 1;

        if (isPartialRange) {
          appendChatSystemMessage(`Parsing only pages **${rangeStart}\u2013${rangeEnd}** of **${totalPages}** total pages, as requested.`);
        }
        if (useLLM) {
          appendChatSystemMessage(
            engineMode === "gemini"
              ? `Single-page parser with **${concurrency}x Gemini concurrency**, **per-request 429/503 backoff** (no model fallback on 404), **page-ordered assembly**, and **debounced grid refresh**.`
              : `Single-page parser with **${engineLabel}** (one page at a time for local accuracy).`
          );
        }

        // Read pages and queue single-page extraction jobs (full text, no truncation).
        for (let pageNum = rangeStart; pageNum <= rangeEnd; pageNum++) {
          if (abortExtraction) {
            appendChatSystemMessage("Extraction stopped by user request.");
            break;
          }

          progressTitle.innerText = isPartialRange
            ? `Parsing Page ${pageNum} of ${totalPages} (Range ${rangeStart}-${rangeEnd})`
            : `Parsing Page ${pageNum} of ${totalPages}`;
          const progressPercent = Math.round(((pageNum - rangeStart + 1) / (rangeEnd - rangeStart + 1)) * 40);
          progressFill.style.width = `${progressPercent}%`;
          progressStatus.innerText = useLLM ? `Reading page ${pageNum}...` : "Extracting layout string layers...";

          const page = await pdf.getPage(pageNum);
          const textContent = await page.getTextContent();
          const nativePageText = buildTextFromPdfTextContent(textContent);
          let pageText = nativePageText;
          let base64Image = null;

          // Scanned manuals often have no text layer. Auto-OCR when native text is empty/weak
          // so Native mode does not silently skip real spare-parts pages.
          const nativeLen = (nativePageText || "").trim().length;
          const forceLogbookOcr = useLLM && activeEquipmentCategory === "Logbook";
          const forceOcrForScan = useLLM && nativeLen < 40;
          const useOcr = useLLM && (parseStrategy === "ocr" || forceOcrForScan || forceLogbookOcr);
          if (forceLogbookOcr && pageNum === rangeStart) {
            appendChatSystemMessage(
              "ℹ️ **Field History / Logbook**: forcing **OCR Vision** (history cards are image scans). Portrait pages are auto-rotated when needed."
            );
          } else if (forceOcrForScan && parseStrategy !== "ocr" && pageNum === rangeStart) {
            appendChatSystemMessage(`⚠️ **Scanned PDF detected**: little/no selectable text. Auto-enabling **OCR Vision** for this document so spare-parts tables can be read from page images.`);
          }

          // Always build native text first so TOC/index detection works even in OCR mode.
          // Render OCR images at 2x scale for dense NOV-style spare lists.
          if (useOcr) {
            const baseViewport = page.getViewport({ scale: 1.0 });
            // Portrait image-only pages are often sideways photos of landscape history cards.
            const rotate =
              (forceLogbookOcr || forceOcrForScan) &&
              nativeLen < 40 &&
              baseViewport.height > baseViewport.width
                ? 90
                : 0;
            const viewport = page.getViewport({ scale: 2.0, rotation: rotate });
            const canvas = document.createElement("canvas");
            const ctx = canvas.getContext("2d");
            canvas.height = viewport.height;
            canvas.width = viewport.width;
            await page.render({ canvasContext: ctx, viewport }).promise;
            base64Image = canvas.toDataURL("image/jpeg", 0.92).split(",")[1];
            pageText = nativePageText && nativePageText.trim().length > 0
              ? nativePageText
              : "OCR VISION EXTRACTION - Use provided image to extract text.";
          }

          loadedPages.push({ pageNum, text: pageText });
          compiledText += ` ${pageText}`;

          if (useLLM) {
            if (engineMode === "ollama" && useOcr && pageNum === rangeStart) {
              const lowerModel = ollamaModel.toLowerCase();
              if (!lowerModel.includes("vision") && !lowerModel.includes("llava") && !lowerModel.includes("minicpm") && !lowerModel.includes("qwen")) {
                appendChatSystemMessage(`⚠️ **Model Warning**: You are using OCR Vision mode with **${ollamaModel}**, which appears to be a text-only model! Vision extraction will fail and return 0 results. Please select a vision model (e.g., \`llama3.2-vision\` or \`llava\`).`);
              }
            }
            // Process every page — no TOC / keyword skipping.
            llmJobs.push({
              pageNum,
              pageText, // full page text — no truncation
              base64Image
            });
          } else {
            const result = runRuleExtractorHeuristics(pageText, file.name, pageNum);
            mergeExtractionResult(result, counts);
            scheduleRenderGrid();
          }
        }

        // Parallel single-page extraction (one page per request; concurrency-limited).
        if (useLLM && llmJobs.length > 0 && !abortExtraction) {
          let completed = 0;
          llmPagesProcessed = llmJobs.length;
          progressStatus.innerText = `${engineLabel}: extracting ${llmJobs.length} pages (${concurrency}x concurrent, single-page parser)...`;

          let mergeChain = Promise.resolve();
          const mergeSafe = (result) => {
            mergeChain = mergeChain.then(() => {
              // mergeExtractionResult also re-sorts registries by page number.
              mergeExtractionResult(result, counts);
            });
            return mergeChain;
          };

          await mapWithConcurrency(llmJobs, concurrency, async (job) => {
            if (abortExtraction) return null;
            try {
              const result = await runLLMExtractor(job.pageText, file.name, job.pageNum, job.base64Image);
              await mergeSafe(result);
            } catch (err) {
              console.warn(`${engineLabel} failed on Page ${job.pageNum}:`, err);
              if (job.base64Image) {
                appendChatSystemMessage(`⚠️ **Page ${job.pageNum} Warning**: Failed to parse with ${engineLabel}. Skipping page...`);
              } else {
                appendChatSystemMessage(`⚠️ **Page ${job.pageNum} Warning**: Failed to parse with ${engineLabel} (${err.message}). Falling back to heuristics for this page...`);
                const fallbackResult = runRuleExtractorHeuristics(job.pageText, file.name, job.pageNum);
                await mergeSafe(fallbackResult);
              }
            } finally {
              completed += 1;
              const extractPercent = 40 + Math.round((completed / llmJobs.length) * 60);
              progressFill.style.width = `${extractPercent}%`;
              progressTitle.innerText = `${engineLabel}: ${completed}/${llmJobs.length} pages`;
              progressStatus.innerText = `Single-page extract (${concurrency} concurrent)...`;
              // Debounced refresh — avoids thrashing when many pages finish together.
              scheduleRenderGrid(completed === llmJobs.length);
            }
            return null;
          });
          await mergeChain;
          assembleRegistriesInPageOrder();
          scheduleRenderGrid(true);
        } else if (!useLLM) {
          assembleRegistriesInPageOrder();
          scheduleRenderGrid(true);
        }

        if (abortExtraction) {
          appendChatSystemMessage("Extraction stopped by user request.");
        }

        progressFill.style.width = "100%";
        progressStatus.innerText = `Extraction finished!`;
        
        setTimeout(() => {
          clearExtractingUi();
          setActiveDocBadge(file.name);
          
          const pagesInRange = rangeEnd - rangeStart + 1;
          const labelModeText = engineMode === "ollama"
            ? `local LLM (${ollamaModel}) processing ${llmPagesProcessed} / ${pagesInRange} pages (single-page)`
            : engineMode === "gemini"
              ? `Gemini API (${geminiModel}) processing ${llmPagesProcessed} / ${pagesInRange} pages (${concurrency}x concurrent, 429/503-retry, no 404 fallback)`
              : "heuristics";
          const rangeLabel = isPartialRange ? `pages ${rangeStart}-${rangeEnd} of ${totalPages}` : `${totalPages} pages`;
          appendChatSystemMessage(`Completed client-side PDF processing for **"${file.name}"** (${rangeLabel}) using **${labelModeText}**. Extracted **${counts.maint}** tasks, **${counts.spares}** spare parts, and **${counts.trouble}** troubleshooting issues into the registries.`);
          
          if (counts.maint === 0 && counts.spares === 0 && counts.trouble === 0 && compiledText.trim().length < 200) {
            appendChatSystemMessage(`⚠️ **Document Scan Warning**: No searchable text layers were detected in **"${file.name}"**. The PDF may be composed of scanned page images. Please ensure the manual has selectable text or try converting it to a plain text (.txt) file.`);
          }
          
          preferTabWithResults();
          renderGrid();
          offerSaveExcelAfterExtraction(file);
          resolve();
        }, 400);

      } catch (err) {
        clearExtractingUi();
        reject(err);
      }
    };
    
    fileReader.readAsArrayBuffer(file);
  });
}

async function extractImageText(file) {
  // isExtracting lock is already claimed by handleFileUpload() before this runs
  return new Promise((resolve, reject) => {
    const fileReader = new FileReader();
    
    fileReader.onload = async function() {
      try {
        const base64Data = fileReader.result.split(',')[1];
        
        const engineLabel = engineMode === "gemini" ? `Gemini (${geminiModel})` : `Ollama (${ollamaModel})`;
        progressFill.style.width = "50%";
        progressStatus.innerText = `Analyzing image with ${engineLabel}...`;
        
        let maintCount = 0;
        let sparesCount = 0;
        let troubleCount = 0;
        let notesCount = 0;

        if (engineMode === "ollama" || engineMode === "gemini") {
          try {
            const result = await runLLMExtractor("OCR VISION EXTRACTION", file.name, 1, base64Data, file.type || "image/jpeg");
            if (result.maintenance && result.maintenance.length > 0) {
              maintCount += result.maintenance.length;
              const startingId = maintenanceRegistry.length > 0 ? Math.max(...maintenanceRegistry.map(r => r.id)) + 1 : 1;
              result.maintenance.forEach((r, rIdx) => r.id = startingId + rIdx);
              maintenanceRegistry = [...maintenanceRegistry, ...result.maintenance];
            }
            if (result.spare_parts && result.spare_parts.length > 0) {
              sparesCount += result.spare_parts.length;
              const startingId = sparePartsRegistry.length > 0 ? Math.max(...sparePartsRegistry.map(r => r.id)) + 1 : 1;
              result.spare_parts.forEach((r, rIdx) => r.id = startingId + rIdx);
              sparePartsRegistry = [...sparePartsRegistry, ...result.spare_parts];
            }
            if (result.troubleshooting && result.troubleshooting.length > 0) {
              troubleCount += result.troubleshooting.length;
              const startingId = troubleshootingRegistry.length > 0 ? Math.max(...troubleshootingRegistry.map(r => r.id)) + 1 : 1;
              result.troubleshooting.forEach((r, rIdx) => r.id = startingId + rIdx);
              troubleshootingRegistry = [...troubleshootingRegistry, ...result.troubleshooting];
            }
            renderGrid();
          } catch (err) {
            console.warn(`${engineLabel} failed on image:`, err);
            appendChatSystemMessage(`⚠️ **Image Warning**: Failed to parse with ${engineLabel}. ${engineMode === "ollama" ? "Ensure you are using a vision model." : "Check your API key and model name."}`);
          }
        } else {
          appendChatSystemMessage(`⚠️ **Image Processing**: Heuristics engine cannot process images. Please select 'Ollama' or 'Gemini API' mode instead.`);
        }
        
        progressFill.style.width = "100%";
        progressStatus.innerText = `Extraction finished!`;
        
        setTimeout(() => {
          clearExtractingUi();
          setActiveDocBadge(file.name);
          
          appendChatSystemMessage(`Completed client-side image processing for **"${file.name}"** using **${engineLabel}**. Extracted **${maintCount}** tasks, **${sparesCount}** spare parts, and **${troubleCount}** troubleshooting issues into the registries.`);
          
          preferTabWithResults();
          renderGrid();
          offerSaveExcelAfterExtraction(file);
          resolve();
        }, 1200);

      } catch (err) {
        clearExtractingUi();
        reject(err);
      }
    };
    
    fileReader.readAsDataURL(file);
  });
}

// Cognitive Contextual Text Extraction Heuristics
function runRuleExtractorHeuristics(text, docName, pageNum = 1) {
  if (isLikelyIndexOrTOCPage(text, pageNum)) {
    return {
      maintenance: [],
      spare_parts: [],
      troubleshooting: []
    };
  }

  if (isRecommendedSparePartsPage(text)) {
    const spareParts = parseSparePartsStructurally(text, docName, pageNum);
    return {
      maintenance: [],
      spare_parts: spareParts,
      troubleshooting: []
    };
  }

  const partKeywords = ["bearing", "filter", "friction plate", "pad", "disc", "valve", "coupling", "seal", "clamp", "stopper", "nut", "bolt", "accumulator", "gasket", "spring", "hose", "pipe", "pump", "block", "roller", "screw", "pin", "wire", "rope", "plug", "motor", "gear", "reducer", "coupler", "fitting", "caliper", "drum", "shaft", "skid", "plates", "groove", "gearbox", "sump", "oil", "grease", "lubricant", "engine", "compressor", "air cleaner", "battery", "radiator", "tank", "cable", "winch", "tophead", "coolant", "fuel", "hydraulic"];

  // 1. Logbook Heuristics Mode
  if (activeEquipmentCategory === "Logbook") {
    const output = {
      maintenance: [],
      spare_parts: [],
      troubleshooting: []
    };
    const lines = text.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
    const dateRegex = /\b(?:\d{1,2}[-/.\s](?:[A-Za-z]{3,10}|\d{1,2})[-/.\s]\d{2,4}|\d{4}[-/.\s]\d{1,2}[-/.\s]\d{1,2})\b/i;

    lines.forEach(line => {
      if (line.length < 10) return;
      if (/date|work description|parts renewed|attended|remarks/i.test(line) && line.split(/\s+/).length < 6) return;
      
      const dateMatch = line.match(dateRegex);
      const dateStr = dateMatch ? dateMatch[0] : "NA";
      
      let workDesc = line;
      if (dateMatch) {
        workDesc = line.replace(dateRegex, "").trim();
      }
      workDesc = workDesc.replace(/^[\s|:\-]+/, "").trim();
      
      const partsFound = [];
      partKeywords.forEach(pk => {
        if (new RegExp(`\\b${pk}s?\\b`, 'i').test(line)) {
          partsFound.push(pk.charAt(0).toUpperCase() + pk.slice(1));
        }
      });
      const partsRenewed = partsFound.length > 0 ? partsFound.join(", ") : "NA";
      
      let attendedBy = "NA";
      const byMatch = line.match(/\bby\s+([A-Za-z\s\.\-]{2,15})\b/i);
      if (byMatch) {
        attendedBy = byMatch[1].trim();
      } else {
        const endInitialsMatch = line.match(/\b([A-Z\.\-]{2,5})\b\s*$/);
        if (endInitialsMatch) {
          attendedBy = endInitialsMatch[1].trim();
        }
      }
      
      output.maintenance.push({
        id: 0,
        date: dateStr,
        maintenance_work_description: workDesc,
        parts_renewed: partsRenewed,
        attended_by: attendedBy,
        remarks: "NA",
        page: pageNum
      });
    });
    
    output.maintenance = output.maintenance.filter(isCleanMaintenanceRow);
    return normalizeExtraction(output);
  }

  // 2. Standard Equipment Heuristics Mode
  const output = {
    maintenance: [],
    spare_parts: [],
    troubleshooting: []
  };

  const lowerText = text.toLowerCase();
  
  // Structured Troubleshooting Table extraction
  if (lowerText.includes("symptom") && lowerText.includes("cause") && (lowerText.includes("elimination") || lowerText.includes("remedy") || lowerText.includes("solution"))) {
    const lines = text.split(/\r?\n/).map(l => l.trim()).filter(l => l.length > 0);
    let inTable = false;
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      const lowerLine = line.toLowerCase();
      if (lowerLine.includes("symptom") && (lowerLine.includes("cause") || lowerLine.includes("reason"))) {
        inTable = true;
        continue;
      }
      if (inTable && line.length > 15) {
        let parts = line.split(/\t|\||\s{3,}/).map(p => p.trim()).filter(Boolean);
        if (parts.length >= 2) {
          let problem = parts[0];
          let solution = parts.slice(1).join(" - ");
          let comp = isolateComponent(line);
          if (comp === "NA") {
            comp = isolateComponent(problem) || "System Component";
          }
          output.troubleshooting.push({
            id: 0,
            equipment_title: docName ? docName.replace(/\.[^/.]+$/, "") : "NA",
            subsystem_component: comp,
            problem: problem,
            root_cause_solution: solution,
            page: pageNum
          });
        }
      }
    }
  }
  
  // Sentences splitter
  const sentences = text.split(/(?<=[.?!])\s+/);
  
  // List of keywords indicating maintenance checks
  const keywords = ["replace", "lubricate", "grease", "inspect", "check", "clean", "torque", "coaxiality", "tighten", "weld", "drain", "replenish", "flush", "tighten"];

  // Keywords/verbs used for the prose-based troubleshooting fallback below
  const problemKeywords = ["fault", "failure", "fails", "failed", "malfunction", "leak", "leaking", "leaks", "noise", "noisy", "overheat", "overheating", "vibration", "vibrates", "error", "trip", "trips", "tripped", "stall", "stalls", "jam", "jammed", "does not", "doesn't", "won't", "will not", "unable to", "abnormal", "excessive", "low pressure", "high pressure", "high temperature", "burnt", "burn out", "seized", "worn out", "broken", "cracked", "loose", "not working", "won't start", "will not start"];
  const causeIndicators = ["caused by", "due to", "because of", "results from", "is due to"];
  const fixActionVerbs = ["check", "replace", "clean", "tighten", "reset", "adjust", "inspect", "repair", "lubricate", "bleed", "drain", "recalibrate", "realign", "re-torque", "flush", "refill", "top up", "clear", "remove", "install", "re-seat"];
  const causeSplitRegex = new RegExp("\\b(" + causeIndicators.join("|") + ")\\b", "i");
  const fixVerbRegex = new RegExp("\\b(" + fixActionVerbs.join("|") + ")\\b", "i");
  const consumedAsFixIdx = new Set(); // sentences already used as the "fix" half of a prior problem sentence
  
  let lastSeenComponent = "System Component"; // Contextual tracking

  for (let sIdx = 0; sIdx < sentences.length; sIdx++) {
    const sentence = sentences[sIdx];
    let cleanSentence = sentence.trim().replace(/^(\d+[\.\)\-\s]*)+/i, "").trim();
    if (cleanSentence.startsWith("S") && cleanSentence.length < 5) continue;
    
    const lowerS = cleanSentence.toLowerCase();

    // Discard generic table headings, section headers, or figure captions
    const isHeaderOrIndicator = /^\b(table|figure|fig|section|drawing|dwg|no)\b|^\d+(\.\d+)*\b/i.test(cleanSentence);
    const isGenericHeader = /check items|maintenance regulations|troubleshooting methods|common troubles|trouble phenomena|check before|inspection before|periodic maintenance/i.test(lowerS);
    const isTOCLine = /\.{3,}/.test(cleanSentence) || /\.\s*\.\s*\.\s*\./.test(cleanSentence);
    const isLikelyIndexEntry = /(page\s*)?\d{1,3}$/.test(lowerS) && cleanSentence.length < 170 && !/[;:]/.test(cleanSentence);
    if (isHeaderOrIndicator || isGenericHeader || isTOCLine || isLikelyIndexEntry) continue;

    let componentMatch = isolateComponent(cleanSentence);
    if (componentMatch !== "NA") {
        lastSeenComponent = componentMatch;
    }

    const hasKeyword = keywords.some(kw => lowerS.includes(kw));
    const hasPart = partKeywords.some(pk => lowerS.includes(pk));
    
    // 1. Maintenance Check Extraction
    if (hasKeyword && cleanSentence.length > 20 && cleanSentence.length < 250) {
      let component = componentMatch !== "NA" ? componentMatch : lastSeenComponent;
      
      // Resolve Routine
      let routine = "Monthly";
      if (lowerS.includes("hour")) {
        const hoursMatch = lowerS.match(/(\d{2,5})\s*hours/);
        routine = hoursMatch ? `Every ${hoursMatch[1]} Hours` : "Periodic Hours";
      } else if (lowerS.includes("month")) {
        const monthsMatch = lowerS.match(/(\d+)\s*months?/);
        routine = monthsMatch ? `Every ${monthsMatch[1]} Months` : "Monthly";
      } else if (lowerS.includes("week")) {
        routine = "Weekly";
      } else if (lowerS.includes("daily") || lowerS.includes("shift")) {
        routine = "Daily / Shift";
      } else if (lowerS.includes("yearly") || lowerS.includes("annual")) {
        routine = "Yearly";
      }
      
      output.maintenance.push({
        id: 0,
        equipment_title: docName ? docName.replace(/\.[^/.]+$/, "") : "NA",
        subsystem_component: component,
        maintenance_routine: routine,
        checks_instructions: cleanSentence,
        page: pageNum
      });
    }

    // 2. Spare Parts Extraction
    if (hasPart && (lowerS.includes("spare") || lowerS.includes("part no") || lowerS.includes("model") || lowerS.includes("type") || lowerS.includes("replace") || lowerS.includes("drawing"))) {
      let partName = isolateComponent(cleanSentence);

      // A sentence can carry more than one reference code (e.g. a part number AND a
      // separate drawing/model number). Collect all of them instead of just the first.
      const allCodeMatches = cleanSentence.match(/\b[A-Z0-9]{4,15}-[A-Z0-9\-]{2,15}\b/g) || [];
      let refCode = "NA";
      let drawingModelNo = "NA";
      if (allCodeMatches.length > 0) {
        refCode = allCodeMatches[0];
        if (allCodeMatches.length > 1) drawingModelNo = allCodeMatches[1];
      } else {
        const fagMatch = lowerS.match(/\b\d{5,10}\b/);
        if (fagMatch) refCode = fagMatch[0];
      }
      // An explicit "drawing/dwg/model" label always wins over the positional guess above.
      const dwgLabelMatch = cleanSentence.match(/\b(?:dwg|drawing|model)[\.:\s#]*\s*([A-Za-z0-9][A-Za-z0-9\-\/]{1,20})/i);
      if (dwgLabelMatch) drawingModelNo = dwgLabelMatch[1];

      // Item / position number, e.g. "Item 12", "Pos. 4", "Ref No. 7"
      let itemNo = "NA";
      const itemMatch = cleanSentence.match(/\b(?:item|pos|position|ref)\.?\s*(?:no\.?)?\s*[:#]?\s*(\d{1,3})\b/i);
      if (itemMatch) itemNo = itemMatch[1];

      // Quantity actually stated in the text, e.g. "qty 2", "2 pcs", "2 units each"
      let quantity = "NA";
      const qtyMatch = lowerS.match(/\b(?:qty|quantity)[\.:\s]*(\d{1,4})\b/) ||
        lowerS.match(/\b(\d{1,4})\s*(?:pcs|pieces|units|nos|off|each)\b/);
      if (qtyMatch) quantity = qtyMatch[1];

      // Recommended stock level, only when explicitly mentioned (never fabricated)
      let recommendedStockQty = "NA";
      const stockMatch = lowerS.match(/\b(?:recommended stock|stock level|keep|maintain)\D{0,20}?(\d{1,4})\s*(?:pcs|pieces|units|in stock|on hand|off)?\b/);
      if (stockMatch) recommendedStockQty = stockMatch[1];

      // OEM / governing standard body, e.g. ISO 9001, DIN 934, API, ASME
      let oemStandardBody = "NA";
      const standardMatch = cleanSentence.match(/\b(ISO|DIN|ANSI|API|ASME|JIS|BS|SAE|NEMA|IEC)[\-\s]?\d{0,6}\b/);
      if (standardMatch) oemStandardBody = standardMatch[0];

      // Warranty duration, e.g. "12 months warranty", "warranty period of 1 year"
      let warrantyPeriod = "NA";
      const warrantyMatch = lowerS.match(/(\d{1,3}\s*(?:years?|months?))\s*warranty/) ||
        lowerS.match(/warranty\D{0,15}?(\d{1,3}\s*(?:years?|months?))/);
      if (warrantyMatch) warrantyPeriod = warrantyMatch[1];

      // Replacement/usage frequency, e.g. "replace every 6 months", "every 500 hours"
      let frequencyOfUse = "NA";
      const freqMatch = lowerS.match(/every\s+(\d{1,5}\s*(?:hours?|months?|weeks?|years?|days?))/);
      if (freqMatch) frequencyOfUse = `Replace every ${freqMatch[1]}`;

      // Reuse the same contextual component tracking used for maintenance rows above,
      // instead of a generic placeholder that carries no real information.
      const subsystemLocation = componentMatch !== "NA" ? componentMatch : (lastSeenComponent !== "System Component" ? lastSeenComponent : "NA");

      output.spare_parts.push({
        id: 0,
        equipment_title: docName ? docName.replace(/\.[^/.]+$/, "") : "NA",
        subsystem_location: subsystemLocation,
        item_no: itemNo,
        part_name: partName,
        part_number_code: refCode,
        drawing_model_no: drawingModelNo,
        oem_standard_body: oemStandardBody,
        part_categorization: lowerS.includes("oil") || lowerS.includes("filter") || lowerS.includes("grease") ? "Consumable" : "Critical Spare",
        quantity: quantity !== "NA" ? quantity : "1",
        recommended_stock_qty: recommendedStockQty,
        warranty_period: warrantyPeriod,
        frequency_of_use: frequencyOfUse,
        page: pageNum
      });
    }

    // 3. Prose-based Troubleshooting Fallback
    // Catches problem/cause/fix narratives that aren't in a literal "Symptom | Cause | Elimination" table,
    // which the structured table extractor above cannot see.
    if (!consumedAsFixIdx.has(sIdx)) {
      // Guard against negated phrasing ("no fault found", "without leaks", "free of vibration"),
      // which mentions a problem keyword while explicitly stating the problem is absent.
      const hasProblem = problemKeywords.some(pk => {
        const idx = lowerS.indexOf(pk);
        if (idx === -1) return false;
        const preceding = lowerS.substring(Math.max(0, idx - 25), idx);
        const isNegated = /\b(no|not|without|free of|absence of|never)\s+(?:any\s+)?(?:signs?\s+of\s+)?$/.test(preceding);
        return !isNegated;
      });
      if (hasProblem) {
        let problemPart = "";
        let solutionPart = "";

        const causeMatch = cleanSentence.match(causeSplitRegex);
        const fixMatch = cleanSentence.match(fixVerbRegex);

        if (causeMatch && causeMatch.index > 5) {
          problemPart = cleanSentence.substring(0, causeMatch.index).trim();
          solutionPart = cleanSentence.substring(causeMatch.index).trim();
        } else if (fixMatch && fixMatch.index > 5) {
          problemPart = cleanSentence.substring(0, fixMatch.index).trim();
          solutionPart = cleanSentence.substring(fixMatch.index).trim();
        } else if (sIdx + 1 < sentences.length) {
          // No split found within this sentence — check if the NEXT sentence reads like the fix,
          // e.g. "Pump fails to build pressure." followed by "Check the relief valve setting."
          const nextClean = sentences[sIdx + 1].trim().replace(/^(\d+[\.\)\-\s]*)+/i, "").trim();
          const nextLower = nextClean.toLowerCase();
          const nextHasProblem = problemKeywords.some(pk => nextLower.includes(pk));
          const nextHasFix = fixActionVerbs.some(fv => nextLower.includes(fv)) || causeIndicators.some(ci => nextLower.includes(ci));
          if (!nextHasProblem && nextHasFix && nextClean.length > 5 && nextClean.length < 250) {
            problemPart = cleanSentence;
            solutionPart = nextClean;
            consumedAsFixIdx.add(sIdx + 1);
          }
        }

        if (problemPart.length > 5 && solutionPart.length > 5 && problemPart.length < 250 && solutionPart.length < 250) {
          let comp = componentMatch !== "NA" ? componentMatch : lastSeenComponent;
          output.troubleshooting.push({
            id: 0,
            equipment_title: docName ? docName.replace(/\.[^/.]+$/, "") : "NA",
            subsystem_component: comp,
            problem: problemPart,
            root_cause_solution: solutionPart,
            page: pageNum
          });
        }
      }
    }
  }

  // Filter out incomplete/placeholder rows with no valid data
  output.maintenance = output.maintenance.filter(isCleanMaintenanceRow);
  output.spare_parts = output.spare_parts.filter(isCleanSparePartsRow);
  if (output.troubleshooting) {
    output.troubleshooting = output.troubleshooting.filter(r => 
      r.problem !== "NA" && 
      r.root_cause_solution !== "NA" && 
      r.problem.length > 5 && 
      r.root_cause_solution.length > 5
    );
  }
  return normalizeExtraction(output);
}

function isolateComponent(sentence) {
  const lowerS = sentence.toLowerCase();
  
  // High-fidelity physical parts dictionary
  const partClasses = (equipmentManifest && equipmentManifest.categories[activeEquipmentCategory]) 
    ? equipmentManifest.categories[activeEquipmentCategory].partClasses 
    : [];

  // Try to find matching physical part term from the sentence
  for (const group of partClasses) {
    for (const term of group.terms) {
      if (lowerS.includes(term)) {
        // Return capitalized matching term
        return term.split(" ").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
      }
    }
  }

  // The user wants to discard rows with NA in Sub-system / Component column.
  // Instead of falling back to random word extraction or generic "System Component",
  // we return "NA" when no specific known component is identified.
  return "NA";
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

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closePageContextModal();
    closeQualityScoreModal();
  }
});

/* -------------------------------------------------------------
 * 6. Application Bootstrapper
 * ------------------------------------------------------------- */

function initApp() {
  initPreloadedContext();
  initProgressCardDrag();
  renderGrid();
  updateCopilotQuotaBadge();
}

initApp();
