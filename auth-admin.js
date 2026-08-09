/* =============================================================
 * Auth + Global Admin UI (login, session, user management)
 * ============================================================= */

const AUTH_TOKEN_KEY = "omniparse_auth_token";
const AUTH_USER_KEY = "omniparse_auth_user";
// v2: default flipped to light — new key so browsers that auto-saved "dark" pick up the new default
const THEME_STORAGE_KEY = "idp_theme_v2";

let authState = {
  token: null,
  user: null,
  authRequired: false,
  modelCatalog: [],
  defaultCopilotLimit: 5
};

function getPreferredTheme() {
  try {
    const saved = localStorage.getItem(THEME_STORAGE_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch (e) {}
  return "light";
}

function applyTheme(theme) {
  const next = theme === "light" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  try {
    localStorage.setItem(THEME_STORAGE_KEY, next);
  } catch (e) {}

  const toggleBtn = document.getElementById("theme-toggle-btn");
  if (toggleBtn) {
    toggleBtn.title = next === "dark" ? "Switch to light mode" : "Switch to dark mode";
    toggleBtn.setAttribute(
      "aria-label",
      next === "dark" ? "Switch to light mode" : "Switch to dark mode"
    );
  }
  if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
}

function initThemeToggle() {
  applyTheme(getPreferredTheme());
  const toggleBtn = document.getElementById("theme-toggle-btn");
  if (!toggleBtn || toggleBtn.dataset.themeBound === "1") return;
  toggleBtn.dataset.themeBound = "1";
  toggleBtn.addEventListener("click", () => {
    const current =
      document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
    applyTheme(current === "dark" ? "light" : "dark");
  });
}

function getAuthToken() {
  try {
    return sessionStorage.getItem(AUTH_TOKEN_KEY) || localStorage.getItem(AUTH_TOKEN_KEY) || "";
  } catch (e) {
    return "";
  }
}

function getAuthHeaders(extra = {}) {
  const headers = { ...extra };
  const token = getAuthToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

function saveAuthSession(token, user) {
  authState.token = token;
  authState.user = user;
  try {
    sessionStorage.setItem(AUTH_TOKEN_KEY, token);
    sessionStorage.setItem(AUTH_USER_KEY, JSON.stringify(user));
    // Mirror to localStorage so admin.html / refresh keep the session on this origin.
    localStorage.setItem(AUTH_TOKEN_KEY, token);
    localStorage.setItem(AUTH_USER_KEY, JSON.stringify(user));
  } catch (e) {}
}

function clearAuthSession() {
  authState.token = null;
  authState.user = null;
  try {
    sessionStorage.removeItem(AUTH_TOKEN_KEY);
    sessionStorage.removeItem(AUTH_USER_KEY);
    localStorage.removeItem(AUTH_TOKEN_KEY);
    localStorage.removeItem(AUTH_USER_KEY);
  } catch (e) {}
}

function isLoggedIn() {
  return !!(authState.token && authState.user);
}

function isAdminUser() {
  return isLoggedIn() && authState.user && authState.user.role === "admin";
}

function isLocalHost(hostname) {
  return !hostname || hostname === "localhost" || hostname === "127.0.0.1";
}

function apiBase() {
  let host = "";
  try {
    host = typeof location !== "undefined" ? location.hostname : "";
  } catch (e) {}

  // On CloudFront / deployed hosts always use same-origin /api — never localhost.
  if (!isLocalHost(host)) return "";

  // Prefer app.js apiBaseUrl when defined — including "" for same-origin.
  if (typeof apiBaseUrl === "string") return String(apiBaseUrl).replace(/\/$/, "");
  try {
    const saved =
      localStorage.getItem("omniparse_api_base") || localStorage.getItem("idp_api_base");
    if (saved !== null && saved !== undefined) return String(saved).replace(/\/$/, "");
  } catch (e) {}
  return "http://127.0.0.1:8001";
}

function handleAuthFailure(detail) {
  const msg = typeof detail === "string" ? detail : "";
  if (/invalid or expired token|authentication required|user inactive/i.test(msg)) {
    clearAuthSession();
    applyUserPolicyToUi();
    openLoginModal("Session expired — sign in again");
    return true;
  }
  return false;
}

async function fetchAuthStatus() {
  const resp = await fetch(`${apiBase()}/api/auth/status`, {
    headers: getAuthHeaders()
  });
  if (!resp.ok) throw new Error(`Auth status HTTP ${resp.status}`);
  return resp.json();
}

async function loginWithPassword(email, password) {
  let resp;
  try {
    resp = await fetch(`${apiBase()}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password })
    });
  } catch (err) {
    throw new Error(
      "Cannot reach the API to sign in. Hard-refresh the page. " +
      "If this persists, clear site data for this CloudFront URL (cached API base may point at localhost)."
    );
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail || `Login failed (${resp.status})`);
  saveAuthSession(data.access_token, data.user);
  return data.user;
}

async function refreshMe() {
  if (!getAuthToken()) return null;
  const resp = await fetch(`${apiBase()}/api/auth/me`, { headers: getAuthHeaders() });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    clearAuthSession();
    if (resp.status === 401) handleAuthFailure(data.detail || "Authentication required");
    return null;
  }
  const user = await resp.json();
  authState.user = user;
  try {
    sessionStorage.setItem(AUTH_USER_KEY, JSON.stringify(user));
    localStorage.setItem(AUTH_USER_KEY, JSON.stringify(user));
  } catch (e) {}
  return user;
}

/* Row-level confidence scores and the quality filter are admin-only. */
function applyRoleBasedUi() {
  const admin = isAdminUser();
  document.body.classList.toggle("hide-quality-ui", !admin);
  if (!admin) {
    // Reset any active quality filter so rows aren't invisibly filtered out.
    const confSel = document.getElementById("confidence-filter");
    if (confSel && confSel.value !== "all") {
      confSel.value = "all";
      confSel.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }
}

function applyUserPolicyToUi() {
  applyRoleBasedUi();
  if (typeof window.refreshAdminTestGeminiKey === "function") {
    window.refreshAdminTestGeminiKey();
  }
  const badge = document.getElementById("copilot-quota-badge");
  const loginBtn = document.getElementById("auth-login-btn");
  const logoutBtn = document.getElementById("auth-logout-btn");
  const adminBtn = document.getElementById("auth-admin-btn");
  const historyBtn = document.getElementById("auth-history-btn");
  const profileMenu = document.getElementById("profile-menu");
  const profileName = document.getElementById("profile-name");
  const profileAvatar = document.getElementById("profile-avatar");
  const profileDropName = document.getElementById("profile-dropdown-name");
  const profileDropEmail = document.getElementById("profile-dropdown-email");
  const profileDropMeta = document.getElementById("profile-dropdown-meta");

  closeProfileMenu();

  if (isLoggedIn()) {
    const u = authState.user;
    const label = u.display_name || (u.email || "").split("@")[0] || "User";
    const initial = String(label).trim().charAt(0).toUpperCase() || "U";

    if (loginBtn) loginBtn.hidden = true;
    if (profileMenu) profileMenu.hidden = false;
    if (profileName) profileName.textContent = label;
    if (profileAvatar) profileAvatar.textContent = initial;
    if (profileDropName) profileDropName.textContent = label;
    if (profileDropEmail) profileDropEmail.textContent = u.email || "";
    if (profileDropMeta) {
      const roleLabel = u.role === "admin" ? "Global Admin" : "User";
      profileDropMeta.textContent = `${roleLabel} · AI ${u.copilot_remaining_today}/${u.copilot_daily_limit} left`;
    }
    if (logoutBtn) logoutBtn.hidden = false;

    // Admin console only inside profile menu for Global Admin
    if (adminBtn) {
      const showAdmin = u.role === "admin";
      adminBtn.hidden = !showAdmin;
      adminBtn.style.display = showAdmin ? "" : "none";
      adminBtn.setAttribute("aria-hidden", showAdmin ? "false" : "true");
    }
    if (historyBtn) {
      historyBtn.hidden = false;
      historyBtn.style.display = "";
    }

    if (badge) {
      const left = u.copilot_remaining_today;
      const limit = u.copilot_daily_limit;
      badge.textContent = left > 0 ? `AI ${left}/${limit} left` : `AI limit reached`;
      badge.classList.toggle("quota-exhausted", left <= 0);
      badge.title = `Server quota for ${u.email}`;
    }

    applyAssignedModelsToGeminiSelect(u);
  } else {
    if (loginBtn) loginBtn.hidden = false;
    if (profileMenu) profileMenu.hidden = true;
    if (logoutBtn) logoutBtn.hidden = true;
    if (adminBtn) {
      adminBtn.hidden = true;
      adminBtn.style.display = "none";
      adminBtn.setAttribute("aria-hidden", "true");
    }
    if (historyBtn) {
      historyBtn.hidden = true;
      historyBtn.style.display = "none";
    }
    const modelSelect = document.getElementById("gemini-model-select");
    if (modelSelect) modelSelect.disabled = false;
    const hint = document.getElementById("gemini-model-policy-hint");
    if (hint) {
      hint.hidden = true;
      hint.textContent = "";
    }
  }

  if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
}

function applyAssignedModelsToGeminiSelect(user) {
  const modelSelect = document.getElementById("gemini-model-select");
  const hint = document.getElementById("gemini-model-policy-hint");
  if (!modelSelect || !user) return;

  const allowed = Array.isArray(user.allowed_models) && user.allowed_models.length
    ? user.allowed_models.slice()
    : (user.preferred_model ? [user.preferred_model] : []);
  if (!allowed.length) return;

  const preferred = allowed.includes(user.preferred_model)
    ? user.preferred_model
    : allowed[0];

  // Prefer app.js policy helper when available (keeps labels + geminiModel in sync).
  if (typeof window.applyAssignedGeminiModels === "function") {
    window.applyAssignedGeminiModels(allowed, preferred, user.role === "admin");
  } else {
    modelSelect.innerHTML = "";
    allowed.forEach((id) => {
      const opt = document.createElement("option");
      opt.value = id;
      opt.textContent = id;
      modelSelect.appendChild(opt);
    });
    modelSelect.value = preferred;
    modelSelect.disabled = user.role !== "admin" && allowed.length <= 1;
    if (typeof geminiModel !== "undefined") {
      // eslint-disable-next-line no-undef
      geminiModel = preferred;
    }
  }

  // Keep the policy hint hidden — the dropdown itself shows what's available.
  if (hint) {
    hint.hidden = true;
    hint.textContent = "";
  }
}

function closeProfileMenu() {
  const dropdown = document.getElementById("profile-dropdown");
  const trigger = document.getElementById("profile-trigger");
  if (dropdown) dropdown.hidden = true;
  if (trigger) trigger.setAttribute("aria-expanded", "false");
}

function toggleProfileMenu() {
  const dropdown = document.getElementById("profile-dropdown");
  const trigger = document.getElementById("profile-trigger");
  if (!dropdown || !trigger) return;
  const open = dropdown.hidden;
  dropdown.hidden = !open;
  trigger.setAttribute("aria-expanded", open ? "true" : "false");
}

function openLoginModal(message = "") {
  const modal = document.getElementById("login-modal");
  const err = document.getElementById("login-error");
  if (err) err.textContent = message || "";
  if (modal) modal.hidden = false;
  const email = document.getElementById("login-email");
  if (email) email.focus();
}

function closeLoginModal() {
  const modal = document.getElementById("login-modal");
  if (modal) modal.hidden = true;
}

function openAdminModal() {
  if (!isAdminUser()) {
    openLoginModal("Admin login required");
    return;
  }
  // Full page admin console (not a popup)
  window.location.href = "admin.html";
}

const ADMIN_DEFAULT_MODEL = "gemini-3.6-flash";

function updateAdminUsersSummary(users) {
  const list = Array.isArray(users) ? users : [];
  const set = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };
  const active = list.filter((u) => String(u.status || "").toLowerCase() === "active").length;
  const disabled = list.filter((u) => String(u.status || "").toLowerCase() === "disabled").length;
  const admins = list.filter((u) => String(u.role || "").toLowerCase() === "admin").length;
  const copilotUsed = list.reduce((sum, u) => sum + (Number(u.copilot_used_today) || 0), 0);
  const models = new Set();
  list.forEach((u) => {
    const allowed = Array.isArray(u.allowed_models) ? u.allowed_models : [];
    allowed.forEach((m) => {
      if (m) models.add(String(m));
    });
    if (u.preferred_model) models.add(String(u.preferred_model));
  });
  set("users-stat-total", String(list.length));
  set("users-stat-active", String(active));
  set("users-stat-disabled", String(disabled));
  set("users-stat-admins", String(admins));
  set("users-stat-copilot", String(copilotUsed));
  set("users-stat-models", String(models.size));
}

async function loadAdminUsers() {
  const tbody = document.getElementById("admin-users-body");
  const catalogEl = document.getElementById("admin-model-catalog");
  if (!tbody) return;
  tbody.innerHTML = `<tr><td colspan="6">Loading…</td></tr>`;
  try {
    const resp = await fetch(`${apiBase()}/api/admin/users`, { headers: getAuthHeaders() });
    const data = await resp.json().catch(() => ([]));
    if (!resp.ok) {
      const detail = typeof data.detail === "string" ? data.detail : (data.detail || `HTTP ${resp.status}`);
      if (handleAuthFailure(detail)) {
        tbody.innerHTML = `<tr><td colspan="6">Sign in required</td></tr>`;
        updateAdminUsersSummary([]);
        return;
      }
      throw new Error(detail);
    }
    const catalog = authState.modelCatalog || [];
    if (catalogEl) {
      catalogEl.textContent = catalog.join(", ") || "—";
    }
    renderAdminCreateModelFields(catalog);
    updateAdminUsersSummary(data);
    tbody.innerHTML = data.map(u => {
      const allowed = Array.isArray(u.allowed_models) && u.allowed_models.length
        ? u.allowed_models
        : catalog.slice(0, 1);
      const preferred = allowed.includes(u.preferred_model)
        ? u.preferred_model
        : (allowed[0] || ADMIN_DEFAULT_MODEL);
      return `
      <tr data-user-id="${escapeAuthHtml(u.id)}">
        <td>
          <div class="admin-cell-stack">
            <span class="admin-cell-primary">${escapeAuthHtml(u.email)}</span>
            <span class="admin-cell-sub">${escapeAuthHtml(u.display_name || "")}</span>
          </div>
        </td>
        <td><span class="admin-pill">${escapeAuthHtml(u.role)}</span></td>
        <td>${escapeAuthHtml(u.status)}</td>
        <td>
          <div class="admin-cell-stack">
            <input type="number" min="0" max="100" class="settings-input admin-limit-input" value="${Number(u.copilot_daily_limit) || 0}" data-field="limit">
            <span class="admin-cell-sub">${Number(u.copilot_used_today) || 0} used today</span>
          </div>
        </td>
        <td class="admin-models-cell">
          <div class="admin-cell-stack" data-role="model-policy" data-allowed="${escapeAuthHtml(allowed.join("|"))}" data-preferred="${escapeAuthHtml(preferred)}">
            <label class="admin-allocate-label">
              <span>Allocate Model</span>
              <select class="settings-select admin-model-select" data-field="add-model" title="Allocate Model">
                <option value="">Select model…</option>
                ${catalog.map((m) => `<option value="${escapeAuthHtml(m)}">${escapeAuthHtml(m)}</option>`).join("")}
              </select>
            </label>
            <span class="admin-assigned-models" data-role="assigned-models"></span>
          </div>
        </td>
        <td class="admin-actions">
          <button type="button" class="btn btn-secondary admin-save-btn">Save</button>
          <button type="button" class="btn btn-secondary admin-toggle-btn">${u.status === "disabled" ? "Enable" : "Disable"}</button>
        </td>
      </tr>`;
    }).join("") || `<tr><td colspan="6">No users</td></tr>`;
    tbody.querySelectorAll("[data-role='model-policy']").forEach((el) => syncDirectoryRowModelUi(el.closest("tr")));
    if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6">${escapeAuthHtml(err.message)}</td></tr>`;
    updateAdminUsersSummary([]);
  }
}

function formatAuditDuration(ms) {
  const n = Number(ms) || 0;
  if (n < 1000) return `${n} ms`;
  if (n < 60000) return `${(n / 1000).toFixed(1)} s`;
  return `${(n / 60000).toFixed(1)} min`;
}

function formatAuditStatusLabel(status) {
  const s = String(status || "").toLowerCase();
  if (s === "done") return "pass";
  return status || "—";
}

function formatAuditWhen(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleString();
  } catch (_) {
    return String(iso);
  }
}

function setMonitorTile(valueId, metaId, value, meta, tone) {
  const valueEl = document.getElementById(valueId);
  const metaEl = document.getElementById(metaId);
  const card = valueEl && (valueEl.closest(".stat-card") || valueEl.closest(".admin-monitor-tile"));
  if (valueEl) valueEl.textContent = value;
  if (metaEl) {
    metaEl.textContent = meta || "";
    metaEl.hidden = !meta;
  }
  if (card) {
    if (meta) card.title = meta;
    if (tone) card.setAttribute("data-tone", tone);
  }
}

async function loadAdminMonitoring() {
  const grid = document.getElementById("admin-monitor-grid");
  if (!grid) return;

  const lastErr = document.getElementById("mon-last-error");
  if (lastErr) {
    lastErr.hidden = true;
    lastErr.textContent = "";
  }

  // API health
  try {
    const controller = new AbortController();
    const t = setTimeout(() => controller.abort(), 4000);
    const resp = await fetch(`${apiBase()}/api/health`, { signal: controller.signal });
    clearTimeout(t);
    const data = await resp.json().catch(() => ({}));
    const ok = resp.ok && data && data.status === "ok";
    const busy = !!(data && data.busy);
    setMonitorTile(
      "mon-api-status",
      "mon-api-meta",
      ok ? (busy ? "Busy" : "Ready") : "Down",
      ok ? `v${data.version || "—"}` : "Cannot reach API",
      ok ? (busy ? "warn" : "ok") : "bad"
    );
  } catch (_) {
    setMonitorTile("mon-api-status", "mon-api-meta", "Down", "Cannot reach API", "bad");
  }

  // Recent extract summary (unfiltered window)
  try {
    const resp = await fetch(`${apiBase()}/api/admin/extract-logs?limit=100`, { headers: getAuthHeaders() });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const errDetail = typeof data.detail === "string" ? data.detail : `HTTP ${resp.status}`;
      if (handleAuthFailure(errDetail)) return;
      throw new Error(errDetail);
    }
    const items = Array.isArray(data.items) ? data.items : [];
    const total = items.length;
    const errors = items.filter((r) => r.status === "error");
    const done = items.filter((r) => r.status === "done");
    const successPct = total ? Math.round((done.length / total) * 100) : null;

    const scores = done
      .map((r) => Number(r.overall_score))
      .filter((n) => Number.isFinite(n));
    const avgScore = scores.length
      ? scores.reduce((a, b) => a + b, 0) / scores.length
      : null;
    const lowScore = scores.filter((n) => n < 50).length;

    const durations = items
      .map((r) => Number(r.duration_ms))
      .filter((n) => Number.isFinite(n) && n >= 0);
    const avgMs = durations.length
      ? durations.reduce((a, b) => a + b, 0) / durations.length
      : null;

    setMonitorTile(
      "mon-runs",
      "mon-runs-meta",
      String(total),
      total ? `${done.length} pass · ${errors.length} failed` : "No runs yet",
      total ? "neutral" : "neutral"
    );

    setMonitorTile(
      "mon-success",
      "mon-success-meta",
      successPct == null ? "—" : `${successPct}%`,
      total ? `${done.length}/${total} succeeded` : "—",
      successPct == null ? "neutral" : successPct >= 90 ? "ok" : successPct >= 70 ? "warn" : "bad"
    );

    setMonitorTile(
      "mon-score",
      "mon-score-meta",
      avgScore == null ? "—" : `${avgScore.toFixed(1)}%`,
      scores.length ? `from ${scores.length} scored runs` : "—",
      avgScore == null ? "neutral" : avgScore > 80 ? "ok" : avgScore >= 50 ? "warn" : "bad"
    );

    setMonitorTile(
      "mon-duration",
      "mon-duration-meta",
      avgMs == null ? "—" : formatAuditDuration(avgMs),
      durations.length ? "mean of recent runs" : "—",
      "neutral"
    );

    setMonitorTile(
      "mon-low",
      "mon-low-meta",
      String(lowScore),
      "Score < 50%",
      lowScore === 0 ? "ok" : lowScore <= 2 ? "warn" : "bad"
    );

    if (errors.length && lastErr) {
      const latest = errors[0];
      lastErr.hidden = false;
      lastErr.textContent =
        `Latest failure: ${latest.filename || "document"} · ${latest.user_email || "anonymous"} · ` +
        `${formatAuditWhen(latest.created_at)}` +
        (latest.error ? ` — ${String(latest.error).slice(0, 180)}` : "");
    }
  } catch (err) {
    setMonitorTile("mon-runs", "mon-runs-meta", "—", err.message || "Failed to load", "bad");
  }
}

function updateAdminLogsSummary(items) {
  const list = Array.isArray(items) ? items : [];
  const set = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };
  const done = list.filter((r) => r.status === "done");
  const failed = list.filter((r) => r.status === "error");
  const rows = list.reduce(
    (sum, r) => sum + (Number(r.maintenance_count) || 0) + (Number(r.spare_parts_count) || 0) + (Number(r.troubleshooting_count) || 0),
    0
  );
  const scores = done.map((r) => Number(r.overall_score)).filter((n) => Number.isFinite(n));
  const avgScore = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
  const durations = list.map((r) => Number(r.duration_ms)).filter((n) => Number.isFinite(n) && n >= 0);
  const avgMs = durations.length ? durations.reduce((a, b) => a + b, 0) / durations.length : null;
  set("logs-stat-runs", String(list.length));
  set("logs-stat-done", String(done.length));
  set("logs-stat-failed", String(failed.length));
  set("logs-stat-rows", String(rows));
  set("logs-stat-score", avgScore == null ? "—" : avgScore.toFixed(1));
  set("logs-stat-duration", avgMs == null ? "—" : formatAuditDuration(avgMs));
}

async function loadAdminExtractLogs() {
  const tbody = document.getElementById("admin-logs-body");
  const detail = document.getElementById("admin-logs-detail");
  if (!tbody) return;
  if (detail) {
    detail.hidden = true;
    detail.textContent = "";
  }
  tbody.innerHTML = `<tr><td colspan="8">Loading…</td></tr>`;
  const status = document.getElementById("admin-logs-status")?.value || "";
  const email = document.getElementById("admin-logs-email")?.value?.trim() || "";
  const qs = new URLSearchParams({ limit: "50" });
  if (status) qs.set("status", status);
  if (email) qs.set("user_email", email);
  try {
    const resp = await fetch(`${apiBase()}/api/admin/extract-logs?${qs}`, { headers: getAuthHeaders() });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const errDetail = typeof data.detail === "string" ? data.detail : (data.detail || `HTTP ${resp.status}`);
      if (handleAuthFailure(errDetail)) {
        tbody.innerHTML = `<tr><td colspan="8">Sign in required</td></tr>`;
        updateAdminLogsSummary([]);
        return;
      }
      throw new Error(typeof errDetail === "string" ? errDetail : JSON.stringify(errDetail));
    }
    const items = Array.isArray(data.items) ? data.items : [];
    updateAdminLogsSummary(items);
    if (!items.length) {
      tbody.innerHTML = `<tr><td colspan="8">No extraction logs yet.</td></tr>`;
      return;
    }
    tbody.innerHTML = items.map((row) => {
      const score = row.overall_score == null ? "—" : Number(row.overall_score).toFixed(1);
      const counts = `${row.maintenance_count || 0} / ${row.spare_parts_count || 0} / ${row.troubleshooting_count || 0}`;
      const statusClass = row.status === "done" ? "admin-pill admin-pill-ok" : "admin-pill admin-pill-bad";
      return `
      <tr data-log-id="${escapeAuthHtml(row.id)}" class="admin-log-row" title="Click for details">
        <td>${escapeAuthHtml(formatAuditWhen(row.created_at))}</td>
        <td>${escapeAuthHtml(row.user_email || "—")}</td>
        <td>${escapeAuthHtml(row.filename || "—")}</td>
        <td><div>${escapeAuthHtml(row.engine || "—")}</div><div class="admin-muted">${escapeAuthHtml(row.parse_strategy || "")}</div></td>
        <td>${escapeAuthHtml(String(score))}</td>
        <td>${escapeAuthHtml(counts)}</td>
        <td>${escapeAuthHtml(formatAuditDuration(row.duration_ms))}</td>
        <td><span class="${statusClass}">${escapeAuthHtml(formatAuditStatusLabel(row.status))}</span></td>
      </tr>`;
    }).join("");
    if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="8">${escapeAuthHtml(err.message)}</td></tr>`;
    updateAdminLogsSummary([]);
  }
}

function utcTodayInputValue() {
  return new Date().toISOString().slice(0, 10);
}

function ensureHistoryDayInput() {
  const dayInput = document.getElementById("history-day");
  if (dayInput && !dayInput.value) dayInput.value = utcTodayInputValue();
  return dayInput?.value || utcTodayInputValue();
}

function updateHistorySummary(items, dayLabel) {
  const runs = items.length;
  const done = items.filter((r) => r.status === "done").length;
  const rows = items.reduce(
    (sum, r) => sum + (Number(r.maintenance_count) || 0) + (Number(r.spare_parts_count) || 0) + (Number(r.troubleshooting_count) || 0),
    0
  );
  const scored = items.filter((r) => r.overall_score != null);
  const avg = scored.length
    ? scored.reduce((sum, r) => sum + Number(r.overall_score), 0) / scored.length
    : null;

  const set = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  };
  set("hist-runs", String(runs));
  set("hist-success", String(done));
  set("hist-rows", String(rows));
  set("hist-score", avg == null ? "—" : avg.toFixed(1));
}

async function loadUserExtractHistory() {
  const tbody = document.getElementById("history-body");
  const detail = document.getElementById("history-detail");
  if (!tbody) return;
  if (detail) {
    detail.hidden = true;
    detail.textContent = "";
  }
  if (!isLoggedIn()) {
    tbody.innerHTML = `<tr><td colspan="7">Sign in to see your extracts.</td></tr>`;
    updateHistorySummary([], "Today");
    return;
  }
  tbody.innerHTML = `<tr><td colspan="7">Loading…</td></tr>`;
  const day = ensureHistoryDayInput();
  const status = document.getElementById("history-status")?.value || "";
  const qs = new URLSearchParams({ limit: "100", day });
  if (status) qs.set("status", status);
  try {
    const resp = await fetch(`${apiBase()}/api/me/extract-history?${qs}`, { headers: getAuthHeaders() });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const errDetail = typeof data.detail === "string" ? data.detail : (data.detail || `HTTP ${resp.status}`);
      if (handleAuthFailure(errDetail)) {
        tbody.innerHTML = `<tr><td colspan="7">Sign in required</td></tr>`;
        updateHistorySummary([], day);
        return;
      }
      throw new Error(typeof errDetail === "string" ? errDetail : JSON.stringify(errDetail));
    }
    const items = Array.isArray(data.items) ? data.items : [];
    updateHistorySummary(items, day);
    if (!items.length) {
      tbody.innerHTML = `<tr><td colspan="7">No extracts for ${escapeAuthHtml(day)}.</td></tr>`;
      return;
    }
    tbody.innerHTML = items.map((row) => {
      const score = row.overall_score == null ? "—" : Number(row.overall_score).toFixed(1);
      const counts = `${row.maintenance_count || 0} / ${row.spare_parts_count || 0} / ${row.troubleshooting_count || 0}`;
      const statusClass = row.status === "done" ? "admin-pill admin-pill-ok" : "admin-pill admin-pill-bad";
      const model = row.gemini_model || row.engine || "—";
      return `
      <tr data-history-id="${escapeAuthHtml(row.id)}" class="admin-log-row" title="Click for details">
        <td>${escapeAuthHtml(formatAuditWhen(row.created_at || row.started_at))}</td>
        <td>${escapeAuthHtml(row.filename || "—")}</td>
        <td>${escapeAuthHtml(model)}</td>
        <td>${escapeAuthHtml(String(score))}</td>
        <td>${escapeAuthHtml(counts)}</td>
        <td>${escapeAuthHtml(formatAuditDuration(row.duration_ms))}</td>
        <td><span class="${statusClass}">${escapeAuthHtml(formatAuditStatusLabel(row.status))}</span></td>
      </tr>`;
    }).join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="7">${escapeAuthHtml(err.message)}</td></tr>`;
    updateHistorySummary([], day);
  }
}

async function showUserExtractHistoryDetail(recordId) {
  const detail = document.getElementById("history-detail");
  if (!detail || !recordId) return;
  detail.hidden = false;
  detail.textContent = "Loading details…";
  try {
    const resp = await fetch(`${apiBase()}/api/me/extract-history/${encodeURIComponent(recordId)}`, {
      headers: getAuthHeaders()
    });
    const row = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const errDetail = typeof row.detail === "string" ? row.detail : `HTTP ${resp.status}`;
      if (handleAuthFailure(errDetail)) return;
      throw new Error(errDetail);
    }
    const warnings = Array.isArray(row.warnings) && row.warnings.length
      ? row.warnings.join(" · ")
      : "none";
    const pages = row.page_start || row.page_end
      ? `pages ${row.page_start || "?"}–${row.page_end || "?"}`
      : `processed ${row.pages_processed || 0}/${row.pages_total || 0}`;
    const rows = `${row.maintenance_count || 0} maint · ${row.spare_parts_count || 0} spare · ${row.troubleshooting_count || 0} trouble`;
    detail.innerHTML = `
      <strong>${escapeAuthHtml(row.filename || "document")}</strong>
      · ${escapeAuthHtml(row.engine || "")}
      · ${escapeAuthHtml(pages)}
      · ${escapeAuthHtml(rows)}
      · category ${escapeAuthHtml(row.equipment_category || "Default")}
      · score ${(row.overall_score == null ? "—" : Number(row.overall_score).toFixed(1))}
      ${row.error ? `<div class="auth-error">${escapeAuthHtml(row.error)}</div>` : ""}
      <div class="admin-muted">Warnings: ${escapeAuthHtml(warnings)}</div>
    `;
  } catch (err) {
    detail.textContent = err.message;
  }
}

function initUserHistoryPage() {
  ensureHistoryDayInput();
  document.getElementById("history-refresh-btn")?.addEventListener("click", loadUserExtractHistory);
  document.getElementById("history-status")?.addEventListener("change", loadUserExtractHistory);
  document.getElementById("history-day")?.addEventListener("change", loadUserExtractHistory);
  document.getElementById("history-body")?.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-history-id]");
    if (!tr) return;
    showUserExtractHistoryDetail(tr.getAttribute("data-history-id"));
  });
  if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
  loadUserExtractHistory();
}

async function showAdminExtractLogDetail(recordId) {
  const detail = document.getElementById("admin-logs-detail");
  if (!detail || !recordId) return;
  detail.hidden = false;
  detail.textContent = "Loading details…";
  try {
    const resp = await fetch(`${apiBase()}/api/admin/extract-logs/${encodeURIComponent(recordId)}`, {
      headers: getAuthHeaders()
    });
    const row = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const errDetail = typeof row.detail === "string" ? row.detail : `HTTP ${resp.status}`;
      if (handleAuthFailure(errDetail)) return;
      throw new Error(errDetail);
    }
    const warnings = Array.isArray(row.warnings) && row.warnings.length
      ? row.warnings.join(" · ")
      : "none";
    const pages = row.page_start || row.page_end
      ? `pages ${row.page_start || "?"}–${row.page_end || "?"}`
      : `processed ${row.pages_processed || 0}/${row.pages_total || 0}`;
    detail.innerHTML = `
      <strong>${escapeAuthHtml(row.filename || "document")}</strong>
      · ${escapeAuthHtml(row.user_email || "anonymous")}
      · ${escapeAuthHtml(row.engine || "")}
      · ${escapeAuthHtml(pages)}
      · category ${escapeAuthHtml(row.equipment_category || "Default")}
      · grounding ${(row.grounding_pass_rate == null ? "—" : Number(row.grounding_pass_rate).toFixed(2))}
      · drop ${(row.filter_drop_rate == null ? "—" : Number(row.filter_drop_rate).toFixed(2))}
      · low-conf ${row.low_confidence_count == null ? "—" : row.low_confidence_count}
      ${row.error ? `<div class="auth-error">${escapeAuthHtml(row.error)}</div>` : ""}
      <div class="admin-muted">Warnings: ${escapeAuthHtml(warnings)}</div>
    `;
  } catch (err) {
    detail.textContent = err.message;
  }
}

function getCreateAllowedModels() {
  return [...document.querySelectorAll("#admin-create-allowed-box input[type='checkbox']:checked")]
    .map((el) => el.value)
    .filter(Boolean);
}

function pickCreatePreferredModel(allowed) {
  if (!allowed.length) return "";
  if (allowed.includes(ADMIN_DEFAULT_MODEL)) return ADMIN_DEFAULT_MODEL;
  return allowed[0];
}

function renderAdminCreateModelFields(catalog) {
  const box = document.getElementById("admin-create-allowed-box");
  if (!catalog.length || !box) return;
  const defaultModel = catalog.includes(ADMIN_DEFAULT_MODEL) ? ADMIN_DEFAULT_MODEL : catalog[0];
  const previouslyChecked = new Set(getCreateAllowedModels());
  if (!previouslyChecked.size && defaultModel) previouslyChecked.add(defaultModel);
  box.innerHTML = catalog.map((m) => `
    <label class="admin-check">
      <input type="checkbox" value="${escapeAuthHtml(m)}" ${previouslyChecked.has(m) ? "checked" : ""}>
      <span>${escapeAuthHtml(m)}</span>
    </label>
  `).join("");
}

function getDirectoryRowAllowed(tr) {
  const box = tr && tr.querySelector("[data-role='model-policy']");
  if (!box) return [];
  return String(box.getAttribute("data-allowed") || "")
    .split("|")
    .map((m) => m.trim())
    .filter(Boolean);
}

function setDirectoryRowAllowed(tr, allowed, preferredHint) {
  const box = tr && tr.querySelector("[data-role='model-policy']");
  if (!box) return;
  const unique = [...new Set(allowed.filter(Boolean))];
  const preferred = unique.includes(preferredHint)
    ? preferredHint
    : (unique.includes(box.getAttribute("data-preferred") || "") ? box.getAttribute("data-preferred") : unique[0] || "");
  box.setAttribute("data-allowed", unique.join("|"));
  box.setAttribute("data-preferred", preferred || "");
  syncDirectoryRowModelUi(tr);
}

function syncDirectoryRowModelUi(tr) {
  if (!tr) return;
  const box = tr.querySelector("[data-role='model-policy']");
  const addSelect = tr.querySelector("select[data-field='add-model']");
  const label = tr.querySelector("[data-role='assigned-models']");
  if (!box) return;
  const allowed = getDirectoryRowAllowed(tr);
  let preferred = box.getAttribute("data-preferred") || "";
  if (!allowed.includes(preferred)) preferred = allowed[0] || "";
  box.setAttribute("data-preferred", preferred);

  if (addSelect) {
    const catalog = authState.modelCatalog || [];
    addSelect.innerHTML = `<option value="">Select model…</option>` + catalog.map((m) => {
      const taken = allowed.includes(m);
      return `<option value="${escapeAuthHtml(m)}" ${taken ? "disabled" : ""}>${escapeAuthHtml(m)}${taken ? " (assigned)" : ""}</option>`;
    }).join("");
    addSelect.value = "";
  }

  if (label) {
    label.innerHTML = allowed.length
      ? allowed.map((m) =>
        `<span class="admin-assigned-chip">${escapeAuthHtml(m)}<button type="button" class="admin-assigned-remove" data-remove-model="${escapeAuthHtml(m)}" title="Remove ${escapeAuthHtml(m)}" aria-label="Remove ${escapeAuthHtml(m)}">×</button></span>`
      ).join("")
      : "";
  }
}

function readAdminRowModelPolicy(tr) {
  const limit = Number(tr.querySelector("[data-field='limit']")?.value);
  const allowed = getDirectoryRowAllowed(tr);
  if (!allowed.length) {
    throw new Error("Assign at least one model");
  }
  const box = tr.querySelector("[data-role='model-policy']");
  let preferred = (box && box.getAttribute("data-preferred")) || "";
  if (!allowed.includes(preferred)) {
    preferred = allowed[0];
  }
  return { limit, allowed, preferred };
}

function escapeAuthHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function adminCreateUser(ev) {
  ev.preventDefault();
  const email = document.getElementById("admin-create-email").value.trim();
  const password = document.getElementById("admin-create-password").value;
  const displayName = document.getElementById("admin-create-name").value.trim();
  const limit = Number(document.getElementById("admin-create-limit").value || 5);
  const allowed = getCreateAllowedModels();
  const preferred = pickCreatePreferredModel(allowed);
  const errEl = document.getElementById("admin-create-error");
  if (errEl) errEl.textContent = "";
  if (!allowed.length) {
    if (errEl) errEl.textContent = "Select at least one model to allocate";
    return;
  }
  try {
    const resp = await fetch(`${apiBase()}/api/admin/users`, {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        email,
        password,
        display_name: displayName,
        role: "user",
        copilot_daily_limit: limit,
        preferred_model: preferred,
        allowed_models: allowed
      })
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
      if (handleAuthFailure(detail)) return;
      throw new Error(detail || `HTTP ${resp.status}`);
    }
    document.getElementById("admin-create-form").reset();
    document.getElementById("admin-create-limit").value = String(authState.defaultCopilotLimit || 5);
    renderAdminCreateModelFields(authState.modelCatalog || []);
    await loadAdminUsers();
  } catch (err) {
    if (errEl) errEl.textContent = err.message;
  }
}

async function initAuthUi() {
  // Restore session (sessionStorage first, then localStorage)
  authState.token = getAuthToken();
  try {
    const raw =
      sessionStorage.getItem(AUTH_USER_KEY) || localStorage.getItem(AUTH_USER_KEY);
    if (raw) authState.user = JSON.parse(raw);
  } catch (e) {}

  // Sign-in landing is the first page: on the main app (which has the
  // full-screen landing), show it immediately when there is no stored session
  // so the workspace never flashes first. admin.html keeps its compact modal.
  const hasLandingPage = !!document.querySelector(".auth-landing");
  if (hasLandingPage && !authState.token) {
    openLoginModal();
  }

  try {
    const status = await fetchAuthStatus();
    authState.authRequired = !!status.auth_required;
    authState.modelCatalog = status.model_catalog || [];
    authState.defaultCopilotLimit = status.default_copilot_limit || 5;
    if (status.user) {
      authState.user = status.user;
    } else if (authState.token) {
      await refreshMe();
    }
  } catch (e) {
    console.warn("Auth status unavailable:", e);
  }

  applyUserPolicyToUi();

  if (!isLoggedIn() && (hasLandingPage || authState.authRequired)) {
    openLoginModal(authState.authRequired ? "Sign in to use OmniParse IDP" : "");
  } else if (isLoggedIn()) {
    closeLoginModal();
  }

  const loginForm = document.getElementById("login-form");
  if (loginForm) {
    loginForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const email = document.getElementById("login-email").value.trim();
      const password = document.getElementById("login-password").value;
      const err = document.getElementById("login-error");
      if (err) err.textContent = "";
      try {
        await loginWithPassword(email, password);
        closeLoginModal();
        applyUserPolicyToUi();
        if (typeof appendChatSystemMessage === "function") {
          appendChatSystemMessage(`Signed in as **${authState.user.email}** (${authState.user.role}).`);
        }
      } catch (ex) {
        if (err) err.textContent = ex.message;
      }
    });
  }

  document.getElementById("auth-login-btn")?.addEventListener("click", () => openLoginModal());
  document.getElementById("auth-logout-btn")?.addEventListener("click", () => {
    clearAuthSession();
    closeProfileMenu();
    applyUserPolicyToUi();
    if (/admin\.html$/i.test(window.location.pathname) || /history\.html$/i.test(window.location.pathname)) {
      window.location.href = "index.html";
      return;
    }
    openLoginModal(authState.authRequired ? "Signed out" : "");
  });
  document.getElementById("auth-history-btn")?.addEventListener("click", () => {
    closeProfileMenu();
  });
  document.getElementById("auth-admin-btn")?.addEventListener("click", () => {
    closeProfileMenu();
    openAdminModal();
  });
  document.getElementById("profile-trigger")?.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleProfileMenu();
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#profile-menu")) closeProfileMenu();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeProfileMenu();
  });
  document.getElementById("login-close")?.addEventListener("click", () => {
    if (!authState.authRequired) closeLoginModal();
  });
  document.getElementById("admin-create-form")?.addEventListener("submit", adminCreateUser);
  document.getElementById("admin-refresh-btn")?.addEventListener("click", loadAdminUsers);
  document.getElementById("admin-monitor-refresh-btn")?.addEventListener("click", () => {
    loadAdminMonitoring();
    loadAdminExtractLogs();
  });
  document.getElementById("admin-logs-refresh-btn")?.addEventListener("click", () => {
    loadAdminExtractLogs();
    loadAdminMonitoring();
  });
  document.getElementById("admin-logs-status")?.addEventListener("change", loadAdminExtractLogs);
  document.getElementById("admin-logs-email")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      loadAdminExtractLogs();
    }
  });
  document.getElementById("admin-logs-body")?.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-log-id]");
    if (!tr) return;
    showAdminExtractLogDetail(tr.getAttribute("data-log-id"));
  });

  document.getElementById("admin-users-body")?.addEventListener("click", async (e) => {
    const tr = e.target.closest("tr[data-user-id]");
    if (!tr) return;
    const userId = tr.getAttribute("data-user-id");
    if (e.target.closest(".admin-save-btn")) {
      try {
        const { limit, allowed, preferred } = readAdminRowModelPolicy(tr);
        const resp = await fetch(`${apiBase()}/api/admin/users/${userId}`, {
          method: "PATCH",
          headers: getAuthHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({
            copilot_daily_limit: limit,
            preferred_model: preferred,
            allowed_models: allowed
          })
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
          const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
          if (handleAuthFailure(detail)) return;
          throw new Error(detail || `HTTP ${resp.status}`);
        }
        await loadAdminUsers();
        if (authState.user && authState.user.id === userId) {
          await refreshMe();
          applyUserPolicyToUi();
        }
      } catch (err) {
        alert(err.message);
      }
    }
    if (e.target.closest(".admin-toggle-btn")) {
      const enable = e.target.textContent.trim() === "Enable";
      try {
        const resp = await fetch(`${apiBase()}/api/admin/users/${userId}`, {
          method: "PATCH",
          headers: getAuthHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ status: enable ? "active" : "disabled" })
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
          const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
          if (handleAuthFailure(detail)) return;
          throw new Error(detail || `HTTP ${resp.status}`);
        }
        await loadAdminUsers();
      } catch (err) {
        alert(err.message);
      }
    }
  });

  document.getElementById("admin-users-body")?.addEventListener("change", (e) => {
    const tr = e.target.closest("tr[data-user-id]");
    if (!tr) return;
    if (e.target.matches("select[data-field='add-model']")) {
      const model = e.target.value;
      if (!model) return;
      const allowed = getDirectoryRowAllowed(tr);
      if (!allowed.includes(model)) allowed.push(model);
      // Latest allocated model becomes preferred/default.
      setDirectoryRowAllowed(tr, allowed, model);
    }
  });

  document.getElementById("admin-users-body")?.addEventListener("click", (e) => {
    const removeBtn = e.target.closest("[data-remove-model]");
    if (!removeBtn) return;
    const tr = removeBtn.closest("tr[data-user-id]");
    if (!tr) return;
    e.preventDefault();
    const model = removeBtn.getAttribute("data-remove-model");
    const next = getDirectoryRowAllowed(tr).filter((m) => m !== model);
    if (!next.length) {
      alert("Keep at least one assigned model");
      return;
    }
    setDirectoryRowAllowed(tr, next);
  });
}

window.getAuthHeaders = getAuthHeaders;
window.isLoggedIn = isLoggedIn;
window.authState = authState;
window.refreshMe = refreshMe;
window.applyUserPolicyToUi = applyUserPolicyToUi;
function setAdminView(view) {
  const allowed = new Set(["monitor", "users", "logs"]);
  const next = allowed.has(view) ? view : "monitor";
  document.querySelectorAll(".admin-nav-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-admin-view") === next);
  });
  document.querySelectorAll(".admin-view").forEach((panel) => {
    panel.hidden = panel.getAttribute("data-admin-panel") !== next;
  });
  try {
    if (window.location.hash !== `#${next}`) {
      history.replaceState(null, "", `#${next}`);
    }
  } catch (_) {}
  if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
  if (next === "monitor" && typeof loadAdminMonitoring === "function") loadAdminMonitoring();
  if (next === "users" && typeof loadAdminUsers === "function") loadAdminUsers();
  if (next === "logs" && typeof loadAdminExtractLogs === "function") loadAdminExtractLogs();
}

function initAdminViews() {
  const nav = document.querySelector(".admin-nav");
  if (!nav) return;
  nav.addEventListener("click", (e) => {
    const btn = e.target.closest(".admin-nav-btn[data-admin-view]");
    if (!btn) return;
    setAdminView(btn.getAttribute("data-admin-view"));
  });
  const fromHash = String(window.location.hash || "").replace(/^#/, "");
  setAdminView(fromHash || "monitor");
}

window.requireAuthForApi = function requireAuthForApi() {
  if (authState.authRequired && !isLoggedIn()) {
    openLoginModal("Sign in required");
    throw new Error("Sign in required");
  }
};

/* Subtle craft signature — encoded + DOM-locked (reappears if removed). */
(function installCraftSignatureLock() {
  const SIG = atob("QWtzaGF5IFJ5YWxp");
  const NODE_ID = "idp-craft-sig";
  let armed = false;

  function paint() {
    if (!document.body) return;
    let node = document.getElementById(NODE_ID);
    if (!node) {
      node = document.createElement("div");
      node.id = NODE_ID;
      node.setAttribute("aria-hidden", "true");
      node.setAttribute("data-craft", "1");
      document.body.appendChild(node);
    }
    if (node.textContent !== SIG) node.textContent = SIG;
    if (node.parentElement !== document.body) document.body.appendChild(node);
    try {
      node.style.setProperty("pointer-events", "none", "important");
      node.style.setProperty("user-select", "none", "important");
      node.style.setProperty("opacity", "0.05", "important");
    } catch (_) {}
  }

  function arm() {
    if (armed || !document.body) return;
    armed = true;
    paint();
    const obs = new MutationObserver(() => paint());
    obs.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
    setInterval(paint, 4000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", arm, { once: true });
  } else {
    arm();
  }
})();

document.addEventListener("DOMContentLoaded", () => {
  initThemeToggle();
  initAuthUi().then(() => {
    if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
    if (/admin\.html$/i.test(window.location.pathname)) {
      if (!isAdminUser()) {
        window.location.replace("index.html");
        return;
      }
      initAdminViews();
    }
    if (/history\.html$/i.test(window.location.pathname)) {
      if (!isLoggedIn()) {
        openLoginModal("Sign in to view your extracts");
      }
      initUserHistoryPage();
    }
  });
});
