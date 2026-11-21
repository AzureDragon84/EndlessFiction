/* Endless Fiction — session, fetch and formatting shared by both pages. */
(() => {
  "use strict";

  const SESSION_KEY = "ef_session";
  const JOB_STATUS = { 0: "queued", 1: "running", 2: "done", 3: "failed", 4: "canceled", 5: "paused" };
  const JOB_TYPE = {
    12: "chapter translation",
    13: "chapter translation batch",
    2: "novel translation",
    3: "novel translation batch",
    7: "full novel translation",
    8: "full novel translation batch",
  };
  const MODE_NAME = { raw: "Raw", web: "Web", web_plus: "Web+", ai: "AI" };

  class AuthError extends Error {}

  const state = { session: null, admin: false };

  const $ = (id) => document.getElementById(id);

  function parseMaybe(value) {
    if (typeof value !== "string") return value;
    const text = value.trim();
    if (!text || !'"{['.includes(text[0])) return value;
    try {
      return JSON.parse(text);
    } catch (_) {
      return value;
    }
  }

  // The site's SPA keeps its session in redux-persist (`auth` slice), so a
  // reader already signed in on the site never sees a second login form.
  function fromSiteStore() {
    for (const storage of [localStorage, sessionStorage]) {
      let raw = null;
      try {
        raw = storage.getItem("persist:auth");
      } catch (_) {
        continue;
      }
      if (!raw) continue;
      let data = null;
      try {
        data = JSON.parse(raw);
      } catch (_) {
        continue;
      }
      const user = parseMaybe(data.user) || parseMaybe(data.me);
      let token = parseMaybe(data.authToken);
      let owner = user && typeof user === "object" ? user : null;
      if (!(typeof token === "string" && token.trim())) {
        // Current shape: `auth` is itself a JSON string holding
        // `{user, token, scopes, tokenExpiresAt}`.
        const auth = parseMaybe(data.auth);
        if (auth && typeof auth === "object" && typeof auth.token === "string") {
          token = auth.token;
          owner = auth.user || owner;
        }
      }
      if (typeof token === "string" && token.trim()) {
        return { token: token.trim(), user: owner && typeof owner === "object" ? owner : null };
      }
    }
    return null;
  }

  function stored() {
    try {
      const saved = JSON.parse(localStorage.getItem(SESSION_KEY) || "null");
      if (saved && saved.token) return saved;
    } catch (_) {
      /* fall through to the site store */
    }
    return fromSiteStore();
  }

  state.session = stored();

  function persist() {
    if (!state.session) return;
    try {
      localStorage.setItem(SESSION_KEY, JSON.stringify(state.session));
    } catch (_) {
      /* private mode: keep the in-memory session */
    }
  }

  function clear() {
    state.session = null;
    state.admin = false;
    try {
      localStorage.removeItem(SESSION_KEY);
    } catch (_) {
      /* ignore */
    }
  }

  async function api(path, options = {}) {
    const headers = Object.assign({ "Content-Type": "application/json" }, options.headers || {});
    if (state.session && state.session.token) {
      headers.Authorization = `Bearer ${state.session.token}`;
    }
    const response = await fetch(path, Object.assign({}, options, { headers }));
    const text = await response.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch (_) {
      data = text;
    }
    if (response.status === 401) {
      clear();
      throw new AuthError("Your session expired. Sign in again.");
    }
    if (!response.ok) {
      const detail = data && typeof data === "object" ? data.detail || data.error : null;
      const message =
        typeof detail === "string"
          ? detail
          : detail
            ? JSON.stringify(detail)
            : data && typeof data === "string"
              ? data
              : response.statusText || `HTTP ${response.status}`;
      throw new Error(message);
    }
    return data;
  }

  async function me() {
    const user = await api("/api/auth/me");
    state.admin = isAdmin(user);
    return user;
  }

  function isAdmin(user) {
    const role = user && user.role;
    return role === "admin" || role === "local";
  }

  function applyUser(user) {
    document.querySelectorAll("[data-user-name]").forEach((node) => {
      node.textContent = (user && (user.name || user.email)) || "";
    });
    document.querySelectorAll("[data-admin-only]").forEach((node) => {
      node.hidden = !isAdmin(user);
    });
  }

  function setText(id, value) {
    const node = $(id);
    if (node) node.textContent = value == null ? "" : String(value);
  }

  async function gate(onReady) {
    const overlay = $("login");
    const form = $("loginForm");
    const app = $("app");

    const showLogin = (message) => {
      state.session = null;
      if (app) app.hidden = true;
      if (overlay) overlay.hidden = false;
      setText("loginError", message || "");
      const field = $("loginEmail");
      if (field) field.focus();
    };

    const enter = async (user) => {
      state.session = Object.assign({}, state.session, { user });
      state.admin = isAdmin(user);
      persist();
      if (overlay) overlay.hidden = true;
      if (app) app.hidden = false;
      applyUser(user);
      await onReady(user);
    };

    if (form && !form.dataset.bound) {
      form.dataset.bound = "1";
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        setText("loginError", "");
        const button = form.querySelector("button[type=submit]");
        if (button) button.disabled = true;
        try {
          const data = await api("/api/auth/login", {
            method: "POST",
            body: JSON.stringify({
              email: ($("loginEmail") || {}).value.trim(),
              password: ($("loginPassword") || {}).value,
            }),
          });
          state.session = { token: data.token, user: data.user };
          await enter(data.user);
        } catch (error) {
          setText("loginError", error.message);
        } finally {
          if (button) button.disabled = false;
        }
      });
    }

    if (!state.session) return showLogin("");

    try {
      await enter(await me());
    } catch (error) {
      if (error instanceof AuthError) return showLogin(error.message);
      if (state.session.user) {
        if (overlay) overlay.hidden = true;
        if (app) app.hidden = false;
        applyUser(state.session.user);
        await onReady(state.session.user);
        return;
      }
      showLogin(error.message);
    }
  }

  function logout() {
    clear();
    location.assign("/");
  }

  function toast(message, kind = "") {
    let host = $("toasts");
    if (!host) {
      host = document.createElement("div");
      host.id = "toasts";
      document.body.appendChild(host);
    }
    const node = document.createElement("div");
    node.className = `toast ${kind}`.trim();
    node.textContent = message;
    host.appendChild(node);
    setTimeout(() => node.remove(), kind === "error" ? 8000 : 4000);
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(
      /[&<>"']/g,
      (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch],
    );
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/"/g, "&quot;");
  }

  function num(value) {
    if (value === null || value === undefined || value === "") return "—";
    return Number(value).toLocaleString();
  }

  function jobStatus(value) {
    return JOB_STATUS[value] || `status ${value}`;
  }

  function jobType(value) {
    return JOB_TYPE[value] || `type ${value}`;
  }

  function modeName(value) {
    return MODE_NAME[value] || String(value || "");
  }

  function timeAgo(value) {
    if (!value) return "—";
    const stamp = Number(value);
    const diff = Date.now() - stamp;
    if (!Number.isFinite(diff)) return "—";
    const minutes = Math.round(diff / 60000);
    if (minutes < 1) return "just now";
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    return new Date(stamp).toLocaleDateString();
  }

  window.EF = {
    api,
    gate,
    me,
    logout,
    isAdmin,
    applyUser,
    escapeHtml,
    escapeAttr,
    num,
    jobStatus,
    jobType,
    modeName,
    timeAgo,
    setText,
    toast,
    AuthError,
    state,
  };
})();
