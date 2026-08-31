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

function getStoredUser() {
  try {
    const raw = sessionStorage.getItem(AUTH_USER_KEY) || localStorage.getItem(AUTH_USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

function getAuthHeaders(extra = {}) {
  const headers = { ...extra };
  const token = getAuthToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

function saveAuthSession(token, user) {
  closeAccessDeniedModal();
  window._accessDeniedShown = false;
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

function getUserRole() {
  if (authState && authState.user && authState.user.role) {
    return String(authState.user.role).toLowerCase();
  }
  const u = getStoredUser();
  if (u && u.role) return String(u.role).toLowerCase();
  return "editor";
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
  // Immediately update bell visibility now that we have a confirmed role.
  applyRoleBasedUi();
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
  // Show/hide notification bell immediately once we have confirmed user role.
  applyRoleBasedUi();
  return user;
}

/* Quality filter is admin-only. Confidence % + Why-this-score popup stay visible for all users. */
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
  // Bell is always visible for authenticated users. Dropdown contents are
  // scoped by GET /api/fabric/pending-approvals (approver mapping / own items).
  if (typeof renderGrid === "function") {
    try { renderGrid(); } catch (e) {}
  }
  if (typeof updateRoleActionButtons === "function") {
    try { updateRoleActionButtons(); } catch (e) {}
  }
  if (typeof window.fetchPendingApprovals === "function") {
    try { window.fetchPendingApprovals(); } catch (e) {}
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
      const roleStr = u.role || "user";
      const roleLabel = roleStr === "admin" ? "Global Admin" : (roleStr.charAt(0).toUpperCase() + roleStr.slice(1));
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

    const histSummary = document.getElementById("history-summary");
    if (histSummary) {
      const showSummary = u.role === "admin";
      histSummary.hidden = !showSummary;
      histSummary.style.display = showSummary ? "" : "none";
    }

    const adminScopeWrap = document.getElementById("history-admin-scope-wrap");
    if (adminScopeWrap) {
      const showScope = u.role === "admin";
      adminScopeWrap.style.display = showScope ? "flex" : "none";
    }

    if (badge) {
      const left = u.copilot_remaining_today;
      const limit = u.copilot_daily_limit;
      badge.textContent = left > 0 ? `AI ${left}/${limit} left` : `AI limit reached`;
      badge.classList.toggle("quota-exhausted", left <= 0);
      badge.title = `Server quota for ${u.email}`;
    }

    applyAssignedModelsToGeminiSelect(u);
    if (typeof window.updateRoleActionButtons === "function") {
      window.updateRoleActionButtons();
    }
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
    const adminScopeWrap = document.getElementById("history-admin-scope-wrap");
    if (adminScopeWrap) adminScopeWrap.style.display = "none";
    const modelSelect = document.getElementById("gemini-model-select");
    if (modelSelect) modelSelect.disabled = false;
    const hint = document.getElementById("gemini-model-policy-hint");
    if (hint) {
      hint.hidden = true;
      hint.textContent = "";
    }
    if (typeof window.updateRoleActionButtons === "function") {
      window.updateRoleActionButtons();
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

function isSharedView() {
  try {
    const params = new URLSearchParams(window.location.search || "");
    return Boolean((params.get("share") || "").trim());
  } catch (_) {
    return false;
  }
}

function openLoginModal(message = "") {
  if (isSharedView()) return; // Anonymous shared link viewers do not need login
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

function openAccessDeniedModal(email = "", reason = "") {
  window._accessDeniedShown = true;
  closeLoginModal();
  const modal = document.getElementById("access-denied-modal");
  const msgEl = document.getElementById("access-denied-message");
  if (msgEl) {
    if (email) {
      msgEl.innerHTML = `The account <strong>${escapeAuthHtml(email)}</strong> is not on the authorized user list for DocuLoom.<br><br><span style="font-size: 0.82rem; color: var(--text-muted);">Please contact your system administrator to be added to the access list in Microsoft Fabric.</span>`;
    } else {
      msgEl.textContent = reason || "Your account is not authorized to access DocuLoom. Please contact your system administrator.";
    }
  }
  if (modal) {
    modal.hidden = false;
    modal.style.zIndex = "10005";
  }
  if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
}

function closeAccessDeniedModal() {
  window._accessDeniedShown = false;
  const modal = document.getElementById("access-denied-modal");
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
const DEFAULT_MODEL_CATALOG = [
  "gemini-3.6-flash",
  "gemini-3.1-pro-preview",
  "gemini-2.5-flash-lite",
  "gemini-2.5-flash",
  "gemini-2.5-pro",
];

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
  tbody.innerHTML = `<tr><td colspan="8">Loading…</td></tr>`;
  try {
    const resp = await fetch(`${apiBase()}/api/admin/users`, { headers: getAuthHeaders() });
    const data = await resp.json().catch(() => ([]));
    if (!resp.ok) {
      const detail = typeof data.detail === "string" ? data.detail : (data.detail || `HTTP ${resp.status}`);
      if (handleAuthFailure(detail)) {
        tbody.innerHTML = `<tr><td colspan="8">Sign in required</td></tr>`;
        updateAdminUsersSummary([]);
        return;
      }
      throw new Error(detail);
    }
    const catalog = (Array.isArray(authState.modelCatalog) && authState.modelCatalog.length)
      ? authState.modelCatalog
      : DEFAULT_MODEL_CATALOG;
    if (catalogEl) {
      catalogEl.textContent = catalog.join(", ") || "—";
    }
    renderAdminCreateModelFields(catalog);
    updateAdminUsersSummary(data);

    // Extract approvers for dropdowns
    const approvers = data.filter(u => u.role === "approver" || u.role === "admin");
    const createApproverSelect = document.getElementById("admin-create-approver");
    if (createApproverSelect) {
      const prevVal = createApproverSelect.value;
      createApproverSelect.innerHTML = `<option value="">-- None (Unassigned) --</option>` + approvers.map(a => `<option value="${escapeAuthHtml(a.email)}">${escapeAuthHtml(a.display_name || a.email)} (${escapeAuthHtml(a.role)})</option>`).join("");
      if (prevVal) createApproverSelect.value = prevVal;
    }

    tbody.innerHTML = data.map(u => {
      const allowed = Array.isArray(u.allowed_models) && u.allowed_models.length
        ? u.allowed_models
        : catalog.slice(0, 1);
      const preferred = allowed.includes(u.preferred_model)
        ? u.preferred_model
        : (allowed[0] || ADMIN_DEFAULT_MODEL);
      const isEditor = u.role === "editor" || u.role === "user";
      const assigned = String(u.assigned_approver || "").trim().toLowerCase();

      return `
      <tr data-user-id="${escapeAuthHtml(u.id)}">
        <td>
          <div class="admin-cell-stack">
            <span class="admin-cell-primary">${escapeAuthHtml(u.display_name || "")}</span>
            <span class="admin-cell-sub">${escapeAuthHtml(u.email)}</span>
          </div>
        </td>
        <td>
          <select class="settings-select admin-role-select" data-field="role" title="User Role" style="padding: 0.25rem 0.5rem; font-size: 0.78rem; min-width: 95px;">
            <option value="admin" ${u.role === "admin" ? "selected" : ""}>Admin</option>
            <option value="approver" ${u.role === "approver" ? "selected" : ""}>Approver</option>
            <option value="editor" ${isEditor ? "selected" : ""}>Editor</option>
            <option value="viewer" ${u.role === "viewer" ? "selected" : ""}>Viewer</option>
          </select>
        </td>
        <td>
          <select class="settings-select admin-approver-select" data-field="assigned_approver" title="Assigned Approver" style="padding: 0.25rem 0.5rem; font-size: 0.78rem; min-width: 140px;" ${!isEditor ? "disabled" : ""}>
            <option value="">-- None --</option>
            ${approvers.map(a => `<option value="${escapeAuthHtml(a.email)}" ${assigned === a.email.toLowerCase() ? "selected" : ""}>${escapeAuthHtml(a.display_name || a.email)}</option>`).join("")}
          </select>
        </td>
        <td>
          <input type="text" class="settings-input admin-sp-folder-input" data-field="sp-folder" value="${escapeAuthHtml(u.sharepoint_folder || '')}" placeholder="Default Library" style="padding: 0.25rem 0.5rem; font-size: 0.78rem; max-width: 140px;" title="Assigned SharePoint Folder or Item ID">
        </td>
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
          <div class="admin-actions-row">
            <button type="button" class="btn btn-secondary admin-save-btn">Save</button>
            <button type="button" class="btn btn-secondary admin-toggle-btn">${u.status === "disabled" ? "Enable" : "Disable"}</button>
          </div>
        </td>
      </tr>`;
    }).join("") || `<tr><td colspan="8">No users</td></tr>`;
    tbody.querySelectorAll("[data-role='model-policy']").forEach((el) => syncDirectoryRowModelUi(el.closest("tr")));
    if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="8">${escapeAuthHtml(err.message)}</td></tr>`;
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

function auditStatusPillClass(status) {
  const s = String(status || "").toLowerCase();
  if (s === "done") return "admin-pill admin-pill-ok";
  if (s === "error") return "admin-pill admin-pill-bad";
  return "admin-pill";
}

function formatAuditUserName(row) {
  const name = String(row?.user_name || "").trim();
  if (name) return name;
  const email = String(row?.user_email || "").trim();
  if (!email || email.toLowerCase() === "anonymous") return email || "—";
  return email.includes("@") ? email.split("@")[0] : email;
}

function normalizeUserFilterQuery(value) {
  return String(value || "").trim().replace(/\s+/g, " ").toLowerCase();
}

function rowMatchesUserNameFilter(row, query) {
  const q = normalizeUserFilterQuery(query);
  if (!q) return true;
  const name = normalizeUserFilterQuery(formatAuditUserName(row));
  const email = String(row?.user_email || "").trim().toLowerCase();
  const local = email.includes("@") ? email.split("@")[0] : email;
  // Match full query, or every token (so "global admin" matches "Global Admin")
  if (name.includes(q) || local.includes(q) || email.includes(q)) return true;
  const tokens = q.split(" ").filter(Boolean);
  if (tokens.length > 1) {
    return tokens.every((t) => name.includes(t) || local.includes(t) || email.includes(t));
  }
  return false;
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
    setMonitorTile(
      "ops-queue",
      "ops-queue-meta",
      ok ? (busy ? "Busy" : "Ready") : "Down",
      ok ? "Extract worker" : "API unreachable",
      ok ? (busy ? "warn" : "ok") : "bad"
    );
  } catch (_) {
    setMonitorTile("mon-api-status", "mon-api-meta", "Down", "Cannot reach API", "bad");
    setMonitorTile("ops-queue", "ops-queue-meta", "Down", "API unreachable", "bad");
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

    // App ops tiles from same window
    const dayAgo = Date.now() - 24 * 60 * 60 * 1000;
    const failDay = errors.filter((r) => {
      const t = Date.parse(r.created_at || r.finished_at || "");
      return Number.isFinite(t) && t >= dayAgo;
    }).length;
    const rows = items.reduce(
      (sum, r) =>
        sum +
        (Number(r.maintenance_count) || 0) +
        (Number(r.spare_parts_count) || 0) +
        (Number(r.troubleshooting_count) || 0),
      0
    );
    const modelCounts = {};
    items.forEach((r) => {
      const key = String(r.gemini_model || r.engine || "").trim() || "—";
      modelCounts[key] = (modelCounts[key] || 0) + 1;
    });
    const topModel = Object.entries(modelCounts).sort((a, b) => b[1] - a[1])[0];

    setMonitorTile(
      "ops-fail-day",
      "ops-fail-day-meta",
      String(failDay),
      "UTC last 24h",
      failDay === 0 ? "ok" : failDay <= 2 ? "warn" : "bad"
    );
    setMonitorTile(
      "ops-rows",
      "ops-rows-meta",
      String(rows),
      total ? `from ${total} runs` : "No runs yet",
      "neutral"
    );
    setMonitorTile(
      "ops-model",
      "ops-model-meta",
      topModel ? topModel[0] : "—",
      topModel ? `${topModel[1]} runs` : "No runs yet",
      "neutral"
    );
  } catch (err) {
    setMonitorTile("mon-runs", "mon-runs-meta", "—", err.message || "Failed to load", "bad");
    setMonitorTile("ops-fail-day", "ops-fail-day-meta", "—", "Failed to load", "bad");
    setMonitorTile("ops-rows", "ops-rows-meta", "—", "Failed to load", "bad");
    setMonitorTile("ops-model", "ops-model-meta", "—", "Failed to load", "bad");
  }

  // Active users
  try {
    const resp = await fetch(`${apiBase()}/api/admin/users`, { headers: getAuthHeaders() });
    const data = await resp.json().catch(() => ([]));
    if (resp.ok && Array.isArray(data)) {
      const active = data.filter((u) => String(u.status || "").toLowerCase() === "active").length;
      setMonitorTile(
        "ops-users",
        "ops-users-meta",
        String(active),
        `${data.length} total`,
        active > 0 ? "ok" : "warn"
      );
    } else {
      setMonitorTile("ops-users", "ops-users-meta", "—", "Unavailable", "neutral");
    }
  } catch (_) {
    setMonitorTile("ops-users", "ops-users-meta", "—", "Unavailable", "neutral");
  }

  // AWS / ops status
  try {
    const resp = await fetch(`${apiBase()}/api/admin/ops-status`, { headers: getAuthHeaders() });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const errDetail = typeof data.detail === "string" ? data.detail : `HTTP ${resp.status}`;
      if (handleAuthFailure(errDetail)) return;
      throw new Error(errDetail);
    }
    const applyOps = (tileKey, valueId, metaId) => {
      const tile = data[tileKey] || {};
      setMonitorTile(
        valueId,
        metaId,
        tile.value || "—",
        tile.meta || tile.detail || "—",
        tile.tone || (tile.ok === false ? "warn" : "neutral")
      );
      const card = document.getElementById(valueId)?.closest(".stat-card");
      if (card && tile.detail) card.title = tile.detail;
    };
    applyOps("ecs", "ops-ecs", "ops-ecs-meta");
    applyOps("cpu", "ops-cpu", "ops-cpu-meta");
    applyOps("memory", "ops-memory", "ops-memory-meta");
    applyOps("alb", "ops-alb", "ops-alb-meta");
    applyOps("audit_s3", "ops-s3", "ops-s3-meta");
    setMonitorTile(
      "ops-region",
      "ops-region-meta",
      data.region || "—",
      data.region ? "AWS region" : "Not set",
      data.region ? "neutral" : "warn"
    );
  } catch (err) {
    ["ops-ecs", "ops-cpu", "ops-memory", "ops-alb", "ops-s3", "ops-region"].forEach((id) => {
      setMonitorTile(id, `${id}-meta`, "—", err.message || "Unavailable", "warn");
    });
  }

  if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
}

function refreshAdminCurrentView() {
  const active = document.querySelector(".admin-nav-btn.active")?.getAttribute("data-admin-view") || "monitor";
  if (active === "users") {
    loadAdminUsers();
    return;
  }
  if (active === "logs") {
    loadAdminExtractLogs();
    loadAdminMonitoring();
    return;
  }
  loadAdminMonitoring();
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
  const overlay = document.getElementById("admin-logs-progress-overlay");
  const pTag = document.getElementById("admin-logs-overlay-tag");
  const pTitle = document.getElementById("admin-logs-progress-title");
  const pStatus = document.getElementById("admin-logs-progress-status");
  const pFill = document.getElementById("admin-logs-progress-fill");

  if (!tbody) return;
  if (detail) {
    detail.hidden = true;
    detail.textContent = "";
  }
  tbody.innerHTML = `<tr><td colspan="9">Loading…</td></tr>`;

  if (overlay) {
    overlay.classList.add("active");
    if (pTag) pTag.innerText = "PROCESSING";
    if (pTitle) pTitle.innerText = "Fetching Fabric Logs";
    if (pStatus) pStatus.innerText = "Querying Microsoft Fabric WH_IDP extraction logs…";
    if (pFill) pFill.style.width = "35%";
  }

  const status = document.getElementById("admin-logs-status")?.value || "";
  const userName = document.getElementById("admin-logs-user")?.value || "";
  const hasUserFilter = Boolean(normalizeUserFilterQuery(userName));
  const qs = new URLSearchParams({ limit: hasUserFilter ? "200" : "50" });
  if (status) qs.set("status", status);
  if (hasUserFilter) qs.set("user_name", userName.trim());
  qs.set("_t", String(Date.now()));

  try {
    const headers = typeof getAuthHeaders === "function" ? getAuthHeaders() : {};
    headers["Cache-Control"] = "no-cache, no-store";
    headers["Pragma"] = "no-cache";
    const resp = await fetch(`${apiBase()}/api/admin/extract-logs?${qs}`, { headers, cache: "no-store" });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const errDetail = typeof data.detail === "string" ? data.detail : (data.detail || `HTTP ${resp.status}`);
      if (handleAuthFailure(errDetail)) {
        tbody.innerHTML = `<tr><td colspan="9">Sign in required</td></tr>`;
        updateAdminLogsSummary([]);
        return;
      }
      throw new Error(typeof errDetail === "string" ? errDetail : JSON.stringify(errDetail));
    }
    let items = Array.isArray(data.items) ? data.items : [];
    // Client-side safety net (covers stale API + typed query refinements)
    if (hasUserFilter) {
      items = items.filter((row) => rowMatchesUserNameFilter(row, userName));
    }
    updateAdminLogsSummary(items);
    if (pFill) pFill.style.width = "90%";
    if (!items.length) {
      const emptyMsg = hasUserFilter || status
        ? "No matching extraction logs."
        : "No extraction logs yet.";
      tbody.innerHTML = `<tr><td colspan="9">${emptyMsg}</td></tr>`;
      return;
    }
    tbody.innerHTML = items.map((row) => {
      const score = row.overall_score == null ? "—" : Number(row.overall_score).toFixed(1);
      const counts = `${row.maintenance_count || 0} / ${row.spare_parts_count || 0} / ${row.troubleshooting_count || 0}`;
      const startAt = row.started_at || row.created_at;
      const endAt = row.finished_at || "";
      const rawDocStatus = String(row.document_status || "Pending Review").trim();
      const isApproved = rawDocStatus.toLowerCase() === "approved";
      const isNeedsRev = rawDocStatus.toLowerCase() === "needs revision" || rawDocStatus.toLowerCase() === "rejected";
      const docStatus = isApproved ? "Approved" : (isNeedsRev ? "Needs Revision" : "Pending Review");
      const statusColor = isApproved ? "#10b981" : (isNeedsRev ? "#ef4444" : "#f59e0b");
      const statusBg = isApproved ? "rgba(16, 185, 129, 0.12)" : (isNeedsRev ? "rgba(239, 68, 68, 0.12)" : "rgba(245, 158, 11, 0.12)");
      const statusBorder = isApproved ? "rgba(16, 185, 129, 0.35)" : (isNeedsRev ? "rgba(239, 68, 68, 0.35)" : "rgba(245, 158, 11, 0.35)");

      return `
      <tr data-log-id="${escapeAuthHtml(row.id)}" class="admin-log-row" title="Click for details">
        <td>${escapeAuthHtml(formatAuditWhen(startAt))}</td>
        <td>${escapeAuthHtml(formatAuditWhen(endAt))}</td>
        <td title="${escapeAuthHtml(row.user_email || "")}">${escapeAuthHtml(formatAuditUserName(row))}</td>
        <td>
          <div style="font-weight: 500;">${escapeAuthHtml(row.filename || "—")}</div>
          <span style="display:inline-block; font-size: 0.74rem; font-weight: 500; padding: 0.1rem 0.45rem; border-radius: 4px; background: ${statusBg}; color: ${statusColor}; border: 1px solid ${statusBorder}; margin-top: 0.25rem;">
            ${escapeAuthHtml(docStatus)}
          </span>
        </td>
        <td><div>${escapeAuthHtml(row.engine || "—")}</div><div class="admin-muted">${escapeAuthHtml(row.parse_strategy || "")}</div></td>
        <td>${escapeAuthHtml(String(score))}</td>
        <td>${escapeAuthHtml(counts)}</td>
        <td>${escapeAuthHtml(formatAuditDuration(row.duration_ms))}</td>
        <td><span class="${auditStatusPillClass(row.status)}">${escapeAuthHtml(formatAuditStatusLabel(row.status))}</span></td>
      </tr>`;
    }).join("");
    if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="9">${escapeAuthHtml(err.message)}</td></tr>`;
    updateAdminLogsSummary([]);
  } finally {
    if (pFill) pFill.style.width = "100%";
    if (overlay) {
      setTimeout(() => {
        overlay.classList.remove("active");
        if (pFill) pFill.style.width = "0%";
      }, 250);
    }
  }
}

let adminLogsUserFilterTimer = null;

function scheduleAdminLogsUserFilter() {
  if (adminLogsUserFilterTimer) clearTimeout(adminLogsUserFilterTimer);
  adminLogsUserFilterTimer = setTimeout(() => {
    adminLogsUserFilterTimer = null;
    loadAdminExtractLogs();
  }, 280);
}

function utcTodayInputValue() {
  return new Date().toISOString().slice(0, 10);
}

function ensureHistoryDayInput() {
  const dayInput = document.getElementById("history-day");
  if (dayInput && !dayInput.value) dayInput.value = utcTodayInputValue();
  return dayInput?.value || utcTodayInputValue();
}

function updateHistorySummary(items) {
  const histSummary = document.getElementById("history-summary");
  const admin = isAdminUser();
  if (histSummary) {
    histSummary.hidden = !admin;
    histSummary.style.display = admin ? "" : "none";
  }
  if (!admin) return;

  const runs = items.length;
  const done = items.filter((r) => (r.status || "done") === "done").length;
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

function openFabricExtractRun(runId, filename) {
  const overlay = document.getElementById("history-progress-overlay");
  const pTag = document.getElementById("history-overlay-tag");
  const pTitle = document.getElementById("history-progress-title");
  const pStatus = document.getElementById("history-progress-status");
  const pFill = document.getElementById("history-progress-fill");
  if (overlay) {
    overlay.classList.add("active");
    if (pTag) pTag.innerText = "PROCESSING";
    if (pTitle) pTitle.innerText = `Processing "${filename || 'Document'}"`;
    if (pStatus) pStatus.innerText = "Initiating document extraction…";
    if (pFill) pFill.style.width = "35%";
    let w = 35;
    const timer = setInterval(() => {
      if (w < 88) {
        w += 14;
        if (pFill) pFill.style.width = `${w}%`;
      }
    }, 150);
  }
  setTimeout(() => {
    window.location.href = `index.html?fabric_run_id=${encodeURIComponent(runId)}`;
  }, 180);
}

async function loadUserExtractHistory() {
  const tbody = document.getElementById("history-body");
  const detail = document.getElementById("history-detail");
  const refreshBtn = document.getElementById("history-refresh-btn");
  const overlay = document.getElementById("history-progress-overlay");
  const pTag = document.getElementById("history-overlay-tag");
  const pTitle = document.getElementById("history-progress-title");
  const pStatus = document.getElementById("history-progress-status");
  const pFill = document.getElementById("history-progress-fill");

  if (!tbody) return;
  if (detail) {
    detail.hidden = true;
    detail.textContent = "";
  }
  if (refreshBtn) {
    const icon = refreshBtn.querySelector("i, svg");
    if (icon) icon.classList.add("spin");
    refreshBtn.style.opacity = "0.75";
  }

  // Activate floating buffer pop-up card
  if (overlay) {
    overlay.classList.add("active");
    if (pTag) pTag.innerText = "PROCESSING";
    if (pTitle) pTitle.innerText = "Fetching Fabric Extracts";
    if (pStatus) pStatus.innerText = "Querying Microsoft Fabric WH_IDP…";
    if (pFill) pFill.style.width = "30%";
  }

  try {
    const headers = typeof getAuthHeaders === "function" ? getAuthHeaders() : {};
    headers["Cache-Control"] = "no-cache, no-store";
    headers["Pragma"] = "no-cache";
    const scopeSelect = document.getElementById("history-scope-select");
    const isAllUsers = scopeSelect && scopeSelect.value === "all";
    const cacheBuster = `_t=${Date.now()}`;
    const qs = isAllUsers ? `limit=100&all_users=true&${cacheBuster}` : `limit=100&${cacheBuster}`;
    const resp = await fetch(`${apiBase()}/api/fabric/extracts?${qs}`, { headers, cache: "no-store" });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      if (resp.status === 401 || (data && data.detail === "Authentication required")) {
        tbody.innerHTML = `
          <tr>
            <td colspan="6" style="text-align: center; padding: 2.5rem 1rem;">
              <p style="margin-bottom: 0.85rem; color: var(--text-muted); font-size: 0.95rem;">Please sign in to view your saved extracts from Microsoft Fabric.</p>
              <button type="button" class="btn btn-primary" onclick="if(typeof openLoginModal==='function')openLoginModal();">Sign in</button>
            </td>
          </tr>`;
        updateHistorySummary([]);
        return;
      }
      const errDetail = typeof data.detail === "string" ? data.detail : (data.detail || `HTTP ${resp.status}`);
      throw new Error(typeof errDetail === "string" ? errDetail : JSON.stringify(errDetail));
    }
    if (data && data.configured === false) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding: 2rem; color: var(--text-muted);">Microsoft Fabric is not configured on the API.</td></tr>`;
      updateHistorySummary([]);
      return;
    }
    const items = Array.isArray(data.items) ? data.items : [];
    updateHistorySummary(items);
    if (!items.length) {
      tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding: 2.5rem 1rem; color: var(--text-muted);">No saved extracts in Fabric yet. Extract a PDF from the workspace first.</td></tr>`;
      return;
    }
    if (pFill) pFill.style.width = "95%";
    tbody.innerHTML = items.map((row) => {
      const score = row.overall_score == null ? "—" : Number(row.overall_score).toFixed(1);
      const counts = `${row.maintenance_count || 0} / ${row.spare_parts_count || 0} / ${row.troubleshooting_count || 0}`;
      const engine = row.engine || "—";
      const when = formatAuditWhen(row.extracted_at);
      const runId = row.run_id || "";
      const docSub = [row.oem_manufacturer, row.doc_title].filter(Boolean).join(" — ");
      const rawDocStatus = String(row.document_status || "Pending Review").trim();
      const isApproved = rawDocStatus.toLowerCase() === "approved";
      const isNeedsRev = rawDocStatus.toLowerCase() === "needs revision" || rawDocStatus.toLowerCase() === "rejected";
      const docStatus = isApproved ? "Approved" : (isNeedsRev ? "Needs Revision" : "Pending Review");
      const statusColor = isApproved ? "#10b981" : (isNeedsRev ? "#ef4444" : "#f59e0b");
      const statusBg = isApproved ? "rgba(16, 185, 129, 0.12)" : (isNeedsRev ? "rgba(239, 68, 68, 0.12)" : "rgba(245, 158, 11, 0.12)");
      const statusBorder = isApproved ? "rgba(16, 185, 129, 0.35)" : (isNeedsRev ? "rgba(239, 68, 68, 0.35)" : "rgba(245, 158, 11, 0.35)");
      const safeFilename = (row.filename || "Document").replace(/'/g, "\\'");

      return `
      <tr data-fabric-run-id="${escapeAuthHtml(runId)}" class="admin-log-row">
        <td>${escapeAuthHtml(when)}</td>
        <td>
          <div style="font-weight: 500;">${escapeAuthHtml(row.filename || "—")}</div>
          ${docSub ? `<div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 0.15rem;">${escapeAuthHtml(docSub)}</div>` : ""}
          <span style="display:inline-block; font-size: 0.74rem; font-weight: 500; padding: 0.1rem 0.45rem; border-radius: 4px; background: ${statusBg}; color: ${statusColor}; border: 1px solid ${statusBorder}; margin-top: 0.25rem;">
            ${escapeAuthHtml(docStatus)}
          </span>
        </td>
        <td>${escapeAuthHtml(engine)}</td>
        <td>${escapeAuthHtml(String(score))}</td>
        <td>${escapeAuthHtml(counts)}</td>
        <td>
          <a class="btn btn-primary history-open-btn" href="index.html?fabric_run_id=${encodeURIComponent(runId)}" onclick="event.preventDefault(); openFabricExtractRun('${escapeAuthHtml(runId)}', '${safeFilename}');">Open</a>
        </td>
      </tr>`;
    }).join("");
    if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding: 2rem; color: var(--accent-red, #ef4444);">${escapeAuthHtml(err.message)}</td></tr>`;
    updateHistorySummary([]);
  } finally {
    if (pFill) pFill.style.width = "100%";
    if (overlay) {
      setTimeout(() => {
        overlay.classList.remove("active");
      }, 250);
    }
    if (refreshBtn) {
      const icon = refreshBtn.querySelector("i, svg");
      if (icon) icon.classList.remove("spin");
      refreshBtn.style.opacity = "1";
    }
  }
}

function initCardDrag(cardId, handleId) {
  const card = document.getElementById(cardId);
  const handle = document.getElementById(handleId);
  if (!card || !handle || handle.dataset.dragBound === "1") return;
  handle.dataset.dragBound = "1";

  let dragging = false;
  let startX = 0, startY = 0, originLeft = 0, originTop = 0;

  handle.addEventListener("pointerdown", (e) => {
    if (!card.classList.contains("active") || (e.button != null && e.button !== 0)) return;
    const rect = card.getBoundingClientRect();
    card.style.left = `${rect.left}px`;
    card.style.top = `${rect.top}px`;
    card.style.transform = "none";
    originLeft = rect.left;
    originTop = rect.top;
    startX = e.clientX;
    startY = e.clientY;
    dragging = true;
    card.classList.add("is-dragging");
    try { handle.setPointerCapture(e.pointerId); } catch (_) {}
    e.preventDefault();
  });

  const onPointerMove = (e) => {
    if (!dragging) return;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    const cardW = card.offsetWidth;
    const cardH = card.offsetHeight;
    const margin = 8;
    const maxLeft = Math.max(margin, window.innerWidth - cardW - margin);
    const maxTop = Math.max(margin, window.innerHeight - cardH - margin);
    const nextLeft = Math.min(maxLeft, Math.max(margin, originLeft + dx));
    const nextTop = Math.min(maxTop, Math.max(margin, originTop + dy));
    card.style.left = `${nextLeft}px`;
    card.style.top = `${nextTop}px`;
  };

  const onPointerUp = (e) => {
    if (!dragging) return;
    dragging = false;
    card.classList.remove("is-dragging");
    try { handle.releasePointerCapture(e.pointerId); } catch (_) {}
  };

  window.addEventListener("pointermove", onPointerMove);
  window.addEventListener("pointerup", onPointerUp);
  window.addEventListener("pointercancel", onPointerUp);
}

function initUserHistoryPage() {
  initCardDrag("history-progress-overlay", "history-progress-drag-handle");
  document.getElementById("history-refresh-btn")?.addEventListener("click", loadUserExtractHistory);
  document.getElementById("history-scope-select")?.addEventListener("change", loadUserExtractHistory);
  if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
  loadUserExtractHistory();
}

function parseGraphUrlClient(url) {
  const clean = String(url || "").trim();
  if (!clean) return { driveId: "", folderId: "" };
  const driveMatch = clean.match(/\/drives\/([^/?#]+)/i);
  const itemMatch = clean.match(/\/items\/([^/?#]+)/i);
  const driveId = driveMatch ? decodeURIComponent(driveMatch[1]) : (clean.startsWith("b!") ? clean : "");
  const folderId = itemMatch ? decodeURIComponent(itemMatch[1]) : "";
  return { driveId, folderId };
}

async function loadAdminSharePointConfig() {
  const urlInput = document.getElementById("admin-sp-url-input");
  const driveInput = document.getElementById("admin-sp-drive-input");
  const folderInput = document.getElementById("admin-sp-folder-input");
  const nameInput = document.getElementById("admin-sp-name-input");
  const syncInput = document.getElementById("admin-sp-sync-input");
  const driveStat = document.getElementById("sp-stat-drive");
  const folderStat = document.getElementById("sp-stat-folder");
  const folderMeta = document.getElementById("sp-stat-folder-meta");
  const liveBadge = document.getElementById("admin-sp-live-badge");

  if (!driveInput) return;
  try {
    const resp = await fetch(`${apiBase()}/api/admin/sharepoint/config`, { headers: getAuthHeaders() });
    if (!resp.ok) return;
    const data = await resp.json();
    const cfg = data.config || {};

    if (urlInput && cfg.graph_endpoint) urlInput.value = cfg.graph_endpoint;
    if (driveInput) driveInput.value = cfg.drive_id || "";
    if (folderInput) folderInput.value = cfg.folder_item_id || "";
    if (nameInput) nameInput.value = cfg.folder_name || "";
    if (syncInput) syncInput.checked = cfg.auto_sync_local_uploads !== false;

    if (driveStat) driveStat.textContent = cfg.drive_id || "Not configured";
    if (folderStat) folderStat.textContent = cfg.folder_item_id ? `Item: ${cfg.folder_item_id.slice(0, 16)}…` : "Root Library";
    if (folderMeta) folderMeta.textContent = cfg.folder_name || "Testing Site";
    if (liveBadge) liveBadge.textContent = cfg.folder_name || "Testing Site";
  } catch (err) {
    console.debug("Could not load SharePoint config:", err);
  }
}

async function saveAdminSharePointConfig(e) {
  if (e) e.preventDefault();
  const urlInput = document.getElementById("admin-sp-url-input");
  const driveInput = document.getElementById("admin-sp-drive-input");
  const folderInput = document.getElementById("admin-sp-folder-input");
  const nameInput = document.getElementById("admin-sp-name-input");
  const syncInput = document.getElementById("admin-sp-sync-input");
  const feedback = document.getElementById("admin-sp-feedback");

  const payload = {
    graph_endpoint: urlInput?.value.trim() || "",
    drive_id: driveInput?.value.trim() || "",
    folder_item_id: folderInput?.value.trim() || "",
    folder_name: nameInput?.value.trim() || "Testing Site",
    auto_sync_local_uploads: syncInput ? syncInput.checked : true,
  };

  if (feedback) {
    feedback.hidden = false;
    feedback.style.color = "var(--text-muted)";
    feedback.textContent = "Saving SharePoint configuration…";
  }

  try {
    const resp = await fetch(`${apiBase()}/api/admin/sharepoint/config`, {
      method: "POST",
      headers: { ...getAuthHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);

    if (feedback) {
      feedback.style.color = "#10b981";
      feedback.textContent = "✓ Configuration saved and applied across all user roles.";
    }
    loadAdminSharePointConfig();
    testAdminSharePointConfig();
  } catch (err) {
    if (feedback) {
      feedback.style.color = "#ef4444";
      feedback.textContent = `✗ Save failed: ${err.message}`;
    }
  }
}

async function testAdminSharePointConfig() {
  const urlInput = document.getElementById("admin-sp-url-input");
  const driveInput = document.getElementById("admin-sp-drive-input");
  const folderInput = document.getElementById("admin-sp-folder-input");
  const previewStatus = document.getElementById("admin-sp-preview-status");
  const filesList = document.getElementById("admin-sp-files-preview-list");
  const feedback = document.getElementById("admin-sp-feedback");

  const payload = {
    graph_endpoint: urlInput?.value.trim() || "",
    drive_id: driveInput?.value.trim() || "",
    folder_item_id: folderInput?.value.trim() || "",
  };

  if (previewStatus) previewStatus.innerHTML = '<span style="color: #38bdf8;">Testing connection to Microsoft Graph…</span>';
  if (filesList) filesList.innerHTML = "";

  try {
    const resp = await fetch(`${apiBase()}/api/admin/sharepoint/test`, {
      method: "POST",
      headers: { ...getAuthHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);

    if (previewStatus) {
      previewStatus.innerHTML = `<span style="color: #10b981; font-weight: 500;">✓ ${escapeAuthHtml(data.message)}</span>`;
    }
    if (filesList && Array.isArray(data.sample_files)) {
      if (data.sample_files.length === 0) {
        filesList.innerHTML = '<li class="admin-muted" style="font-size: 0.82rem;">No PDFs found yet in this directory.</li>';
      } else {
        filesList.innerHTML = data.sample_files.map(name => `
          <li style="font-size: 0.82rem; color: hsla(215, 20%, 85%, 1); display: flex; align-items: center; gap: 0.4rem; background: rgba(255,255,255,0.04); padding: 0.35rem 0.6rem; border-radius: 4px;">
            <i data-lucide="file-text" style="width: 14px; height: 14px; color: var(--color-teal);"></i>
            <span>${escapeAuthHtml(name)}</span>
          </li>
        `).join("");
        if (typeof lucide !== "undefined" && lucide.createIcons) lucide.createIcons();
      }
    }
    if (feedback) {
      feedback.hidden = false;
      feedback.style.color = "#10b981";
      feedback.textContent = `✓ Connected successfully. (${data.files_count} file(s) accessible)`;
    }
  } catch (err) {
    if (previewStatus) {
      previewStatus.innerHTML = `<span style="color: #ef4444;">✗ Connection test failed: ${escapeAuthHtml(err.message)}</span>`;
    }
    if (feedback) {
      feedback.hidden = false;
      feedback.style.color = "#ef4444";
      feedback.textContent = `✗ Connection error: ${err.message}`;
    }
  }
}

function initAdminViews() {
  initCardDrag("admin-logs-progress-overlay", "admin-logs-progress-drag-handle");
  document.querySelectorAll(".admin-nav-btn[data-admin-view]").forEach((btn) => {
    btn.onclick = (e) => {
      e.preventDefault();
      const view = btn.getAttribute("data-admin-view");
      setAdminView(view);
    };
  });
  const refreshBtn = document.getElementById("admin-nav-refresh-btn");
  if (refreshBtn) {
    refreshBtn.onclick = (e) => {
      e.preventDefault();
      const active = document.querySelector(".admin-nav-btn.active")?.getAttribute("data-admin-view") || "monitor";
      setAdminView(active);
    };
  }

  // SharePoint Configuration Event Handlers
  const spForm = document.getElementById("admin-sp-form");
  if (spForm) spForm.onsubmit = saveAdminSharePointConfig;

  const spTestBtn = document.getElementById("admin-sp-test-btn");
  if (spTestBtn) spTestBtn.onclick = testAdminSharePointConfig;

  const spUrlInput = document.getElementById("admin-sp-url-input");
  if (spUrlInput) {
    spUrlInput.addEventListener("input", () => {
      const parsed = parseGraphUrlClient(spUrlInput.value);
      if (parsed.driveId) {
        const driveIn = document.getElementById("admin-sp-drive-input");
        if (driveIn) driveIn.value = parsed.driveId;
      }
      if (parsed.folderId) {
        const folderIn = document.getElementById("admin-sp-folder-input");
        if (folderIn) folderIn.value = parsed.folderId;
      }
    });
  }

  const spResetBtn = document.getElementById("admin-sp-reset-btn");
  if (spResetBtn) {
    spResetBtn.onclick = () => {
      const urlInput = document.getElementById("admin-sp-url-input");
      const folderInput = document.getElementById("admin-sp-folder-input");
      const nameInput = document.getElementById("admin-sp-name-input");
      if (urlInput) urlInput.value = "";
      if (folderInput) folderInput.value = "";
      if (nameInput) nameInput.value = "Testing Site";
      saveAdminSharePointConfig();
    };
  }

  const fromHash = String(window.location.hash || "").replace(/^#/, "");
  setAdminView(fromHash || "monitor");
}
window.initAdminViews = initAdminViews;

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
      · ${escapeAuthHtml(formatAuditUserName(row))}
      · status: <strong style="color: ${row.document_status === 'Approved' ? 'var(--accent-green, #10b981)' : 'inherit'}">${escapeAuthHtml(row.document_status || "Pending Review")}</strong>
      ${row.approved_by ? `· approved by ${escapeAuthHtml(row.approved_by)}` : ""}
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
  if (!box) return;
  const list = (Array.isArray(catalog) && catalog.length) ? catalog : DEFAULT_MODEL_CATALOG;
  const defaultModel = list.includes(ADMIN_DEFAULT_MODEL) ? ADMIN_DEFAULT_MODEL : list[0];
  const previouslyChecked = new Set(getCreateAllowedModels());
  if (!previouslyChecked.size && defaultModel) previouslyChecked.add(defaultModel);
  box.innerHTML = list.map((m) => `
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
  const submitBtn = ev.target.querySelector("button[type='submit']");
  const origBtnText = submitBtn ? submitBtn.textContent : "Create user";
  const email = document.getElementById("admin-create-email")?.value.trim();
  const displayName = document.getElementById("admin-create-name")?.value.trim();
  const role = document.getElementById("admin-create-role")?.value || "editor";
  const assignedApprover = document.getElementById("admin-create-approver")?.value || null;
  const sharepointFolder = document.getElementById("admin-create-sp-folder")?.value.trim() || null;
  const limit = Number(document.getElementById("admin-create-limit")?.value || 5);
  let allowed = getCreateAllowedModels();
  if (!allowed.length) {
    allowed = [ADMIN_DEFAULT_MODEL];
  }
  const preferred = pickCreatePreferredModel(allowed);
  const errEl = document.getElementById("admin-create-error");
  if (errEl) {
    errEl.textContent = "";
    errEl.style.color = "#ef4444";
  }

  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = "Creating user…";
  }

  try {
    const resp = await fetch(`${apiBase()}/api/admin/users`, {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        email,
        display_name: displayName,
        role: role,
        copilot_daily_limit: limit,
        preferred_model: preferred,
        allowed_models: allowed,
        assigned_approver: role === "editor" ? assignedApprover : null,
        sharepoint_folder: sharepointFolder,
      })
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
      if (handleAuthFailure(detail)) return;
      throw new Error(detail || `HTTP ${resp.status}`);
    }
    document.getElementById("admin-create-form")?.reset();
    const limitEl = document.getElementById("admin-create-limit");
    if (limitEl) limitEl.value = String(authState.defaultCopilotLimit || 5);
    renderAdminCreateModelFields(authState.modelCatalog || DEFAULT_MODEL_CATALOG);
    if (errEl) {
      errEl.style.color = "#10b981";
      errEl.textContent = `✓ Created user ${email} successfully.`;
      setTimeout(() => { if (errEl.textContent.includes("✓")) errEl.textContent = ""; }, 4000);
    }
    await loadAdminUsers();
  } catch (err) {
    if (errEl) {
      errEl.style.color = "#ef4444";
      errEl.textContent = err.message;
    }
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = origBtnText;
    }
  }
}

async function initAuthUi() {
  // If access denied modal is currently showing, don't overwrite it with the login modal
  if (window._accessDeniedShown) {
    return;
  }

  // Restore session (sessionStorage first, then localStorage)
  authState.token = getAuthToken();
  try {
    const raw =
      sessionStorage.getItem(AUTH_USER_KEY) || localStorage.getItem(AUTH_USER_KEY);
    if (raw) authState.user = JSON.parse(raw);
  } catch (e) {}

  if (isSharedView()) {
    closeLoginModal();
    document.body.classList.add("is-shared-viewer");
    const loginModal = document.getElementById("login-modal");
    if (loginModal) loginModal.hidden = true;
    return;
  }

  // Sign-in landing is the first page: on the main app (which has the
  // full-screen landing), show it immediately when there is no stored session
  // so the workspace never flashes first. admin.html keeps its compact modal.
  const hasLandingPage = !!document.querySelector(".auth-landing");
  if (hasLandingPage && !authState.token && !window._accessDeniedShown) {
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
    openLoginModal(authState.authRequired ? "Sign in to use DocuLoom" : "");
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
  document.getElementById("admin-create-role")?.addEventListener("change", (e) => {
    const isEditor = e.target.value === "editor";
    const appField = document.getElementById("admin-create-approver-field");
    const appSelect = document.getElementById("admin-create-approver");
    if (appField) appField.style.opacity = isEditor ? "1" : "0.5";
    if (appSelect) {
      appSelect.disabled = !isEditor;
      if (!isEditor) appSelect.value = "";
    }
  });
  document.getElementById("admin-create-form")?.addEventListener("submit", adminCreateUser);
  document.getElementById("admin-nav-refresh-btn")?.addEventListener("click", refreshAdminCurrentView);
  document.getElementById("admin-logs-status")?.addEventListener("change", loadAdminExtractLogs);
  const logsUserFilter = document.getElementById("admin-logs-user");
  if (logsUserFilter) {
    logsUserFilter.addEventListener("input", scheduleAdminLogsUserFilter);
    logsUserFilter.addEventListener("search", () => {
      if (adminLogsUserFilterTimer) clearTimeout(adminLogsUserFilterTimer);
      adminLogsUserFilterTimer = null;
      loadAdminExtractLogs();
    });
    logsUserFilter.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        if (adminLogsUserFilterTimer) clearTimeout(adminLogsUserFilterTimer);
        adminLogsUserFilterTimer = null;
        loadAdminExtractLogs();
      }
    });
  }
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
      const saveBtn = e.target.closest(".admin-save-btn");
      const origText = saveBtn ? saveBtn.textContent : "Save";
      if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.textContent = "Saving…";
      }
      try {
        const { limit, allowed, preferred } = readAdminRowModelPolicy(tr);
        const role = tr.querySelector(".admin-role-select")?.value;
        const approverVal = tr.querySelector(".admin-approver-select")?.value || null;
        const spFolderVal = tr.querySelector(".admin-sp-folder-input")?.value.trim() || null;
        const payload = {
          copilot_daily_limit: limit,
          preferred_model: preferred,
          allowed_models: allowed,
          assigned_approver: role === "editor" ? approverVal : null,
          sharepoint_folder: spFolderVal,
        };
        if (role) payload.role = role;
        const resp = await fetch(`${apiBase()}/api/admin/users/${userId}`, {
          method: "PATCH",
          headers: getAuthHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify(payload)
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok) {
          const detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data);
          if (handleAuthFailure(detail)) return;
          throw new Error(detail || `HTTP ${resp.status}`);
        }
        if (saveBtn) saveBtn.textContent = "✓ Saved";
        await loadAdminUsers();
        if (authState.user && authState.user.id === userId) {
          await refreshMe();
          applyUserPolicyToUi();
        }
      } catch (err) {
        alert(err.message);
        if (saveBtn) {
          saveBtn.disabled = false;
          saveBtn.textContent = origText;
        }
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
    if (e.target.matches(".admin-role-select")) {
      const isEditor = e.target.value === "editor";
      const apprSel = tr.querySelector(".admin-approver-select");
      if (apprSel) {
        apprSel.disabled = !isEditor;
        if (!isEditor) apprSel.value = "";
      }
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
window.getUserRole = getUserRole;
window.getStoredUser = getStoredUser;
function setAdminView(view) {
  const allowed = new Set(["monitor", "users", "logs", "sharepoint"]);
  const next = allowed.has(view) ? view : "monitor";
  document.querySelectorAll(".admin-nav-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.getAttribute("data-admin-view") === next);
  });
  document.querySelectorAll(".admin-view").forEach((panel) => {
    const isTarget = panel.getAttribute("data-admin-panel") === next;
    panel.hidden = !isTarget;
    if (isTarget) {
      panel.style.removeProperty("display");
    } else {
      panel.style.display = "none";
    }
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
  if (next === "sharepoint" && typeof loadAdminSharePointConfig === "function") loadAdminSharePointConfig();
}
window.setAdminView = setAdminView;

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

  // Handle SSO redirect return payload (index.html#sso_token=...&user=... or #auth_error=access_denied)
  try {
    const rawSearch = window.location.search ? window.location.search.substring(1) : "";
    const rawHash = window.location.hash ? window.location.hash.substring(1) : "";
    const params = new URLSearchParams(rawHash || rawSearch);

    const authError = params.get("auth_error");
    const errorEmail = params.get("email");
    const errorReason = params.get("reason");
    if (authError === "access_denied") {
      clearAuthSession();
      window.history.replaceState({}, document.title, window.location.pathname);
      openAccessDeniedModal(errorEmail, errorReason);
    }

    const ssoToken = params.get("sso_token");
    const ssoUserRaw = params.get("user");
    if (ssoToken && ssoUserRaw) {
      const ssoUser = JSON.parse(decodeURIComponent(ssoUserRaw));
      saveAuthSession(ssoToken, ssoUser);
      window.history.replaceState({}, document.title, window.location.pathname);
    }
  } catch (err) {
    console.error("SSO return parameter parse error:", err);
  }

  // Access Denied Modal button handlers
  const accessCloseBtn = document.getElementById("access-denied-close-btn");
  if (accessCloseBtn) {
    accessCloseBtn.addEventListener("click", () => closeAccessDeniedModal());
  }
  const accessSwitchBtn = document.getElementById("access-denied-switch-btn");
  if (accessSwitchBtn) {
    accessSwitchBtn.addEventListener("click", async () => {
      closeAccessDeniedModal();
      clearAuthSession();
      try {
        const resp = await fetch(`${apiBaseUrl}/api/auth/sso/login?prompt=select_account`);
        if (resp.ok) {
          const data = await resp.json();
          if (data.auth_url) {
            window.location.href = data.auth_url;
            return;
          }
        }
      } catch (_) {}
      openLoginModal();
    });
  }

  // SSO Login button handler
  const ssoBtn = document.getElementById("sso-login-btn");
  if (ssoBtn) {
    ssoBtn.addEventListener("click", async (e) => {
      e.preventDefault();
      try {
        const resp = await fetch(`${apiBaseUrl}/api/auth/sso/login`);
        if (!resp.ok) {
          const detail = await resp.json();
          alert(detail.detail || "SSO login is not available.");
          return;
        }
        const data = await resp.json();
        if (data.auth_url) {
          window.location.href = data.auth_url;
        }
      } catch (err) {
        alert("Failed to initiate SSO login: " + (err.message || err));
      }
    });
  }

  if (/admin\.html$/i.test(window.location.pathname)) {
    initAdminViews();
  }

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
