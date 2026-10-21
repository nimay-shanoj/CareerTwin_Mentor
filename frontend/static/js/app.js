/* ==========================================================================
   CareerTwin – shared front-end runtime
   Loaded on every page (public and authenticated).
   Provides: CSRF-aware fetch, toasts, theme toggle, sidebar, modal helpers.
   ========================================================================== */

(function () {
  "use strict";

  /* ---------------------------------------------------------------------
     CSRF
     --------------------------------------------------------------------- */
  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  /**
   * fetch() wrapper that attaches the CSRF header, parses JSON and turns
   * non-2xx responses into thrown Errors carrying the API error payload.
   *
   * @param {string} url
   * @param {object} [options]  standard fetch options
   * @param {boolean} [raw]     when true the raw Response is returned
   */
  async function fetchJSON(url, options, raw) {
    const opts = Object.assign({}, options || {});
    opts.headers = Object.assign({}, opts.headers || {});
    opts.credentials = opts.credentials || "same-origin";

    const method = (opts.method || "GET").toUpperCase();
    if (method !== "GET" && method !== "HEAD") {
      opts.headers["X-CSRFToken"] = csrfToken();
    }
    if (opts.body && !(opts.body instanceof FormData) && !opts.headers["Content-Type"]) {
      opts.headers["Content-Type"] = "application/json";
    }

    const response = await fetch(url, opts);
    if (raw) return response;

    let data = null;
    const text = await response.text();
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (err) {
        data = { error: text };
      }
    }

    if (!response.ok) {
      const error = new Error(
        (data && (data.error || data.message)) || `Request failed (${response.status})`
      );
      error.status = response.status;
      error.code = data && data.code;
      error.payload = data;
      throw error;
    }
    return data;
  }

  /* ---------------------------------------------------------------------
     Toasts
     --------------------------------------------------------------------- */
  const TOAST_ICONS = {
    success: "M20 6 9 17l-5-5",
    error: "M18 6 6 18M6 6l12 12",
    info: "M12 16v-4M12 8h.01",
    warning: "M12 9v4m0 4h.01M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z",
  };

  function toastRegion() {
    let region = document.getElementById("toast-region");
    if (!region) {
      region = document.createElement("div");
      region.id = "toast-region";
      region.className = "toast-region";
      region.setAttribute("aria-live", "polite");
      region.setAttribute("aria-atomic", "false");
      document.body.appendChild(region);
    }
    return region;
  }

  function toast(message, type, timeout) {
    const kind = type || "info";
    const region = toastRegion();
    const el = document.createElement("div");
    el.className = `toast toast--${kind}`;
    el.setAttribute("role", kind === "error" ? "alert" : "status");

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "toast__icon");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "2");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", TOAST_ICONS[kind] || TOAST_ICONS.info);
    svg.appendChild(path);

    const text = document.createElement("span");
    text.className = "toast__message";
    text.textContent = message;

    el.appendChild(svg);
    el.appendChild(text);
    region.appendChild(el);

    window.setTimeout(() => {
      el.classList.add("toast--leaving");
      window.setTimeout(() => el.remove(), 250);
    }, timeout || 4000);

    return el;
  }

  /* ---------------------------------------------------------------------
     Theme (light / dark)
     --------------------------------------------------------------------- */
  const THEME_KEY = "careertwin-theme";

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch (err) {
      /* storage disabled – theme still applies for this page load */
    }
    document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
      btn.setAttribute("aria-pressed", String(theme === "dark"));
      const label = btn.querySelector("[data-theme-label]");
      if (label) label.textContent = theme === "dark" ? "Light mode" : "Dark mode";
    });
  }

  function initTheme() {
    let stored = null;
    try {
      stored = localStorage.getItem(THEME_KEY);
    } catch (err) {
      stored = null;
    }
    const prefersDark =
      window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    applyTheme(stored || (prefersDark ? "dark" : "light"));

    document.querySelectorAll("[data-theme-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const current = document.documentElement.getAttribute("data-theme");
        applyTheme(current === "dark" ? "light" : "dark");
      });
    });
  }

  /* ---------------------------------------------------------------------
     Sidebar / mobile drawer
     --------------------------------------------------------------------- */
  function initSidebar() {
    const toggle = document.querySelector("[data-sidebar-toggle]");
    const sidebar = document.querySelector("[data-sidebar]");
    const scrim = document.querySelector("[data-sidebar-scrim]");
    if (!toggle || !sidebar) return;

    const setOpen = (open) => {
      sidebar.classList.toggle("is-open", open);
      if (scrim) scrim.classList.toggle("is-open", open);
      toggle.setAttribute("aria-expanded", String(open));
      document.body.classList.toggle("no-scroll", open);
    };

    toggle.addEventListener("click", () => {
      setOpen(!sidebar.classList.contains("is-open"));
    });
    if (scrim) {
      scrim.addEventListener("click", () => setOpen(false));
    }
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") setOpen(false);
    });

    // Collapsed (icon-only) mode on desktop
    const collapseBtn = document.querySelector("[data-sidebar-collapse]");
    if (collapseBtn) {
      collapseBtn.addEventListener("click", () => {
        const collapsed = document.body.classList.toggle("sidebar-collapsed");
        collapseBtn.setAttribute("aria-pressed", String(collapsed));
        try {
          localStorage.setItem("careertwin-sidebar", collapsed ? "1" : "0");
        } catch (err) {
          /* ignore */
        }
      });
      try {
        if (localStorage.getItem("careertwin-sidebar") === "1") {
          document.body.classList.add("sidebar-collapsed");
          collapseBtn.setAttribute("aria-pressed", "true");
        }
      } catch (err) {
        /* ignore */
      }
    }
  }

  /* ---------------------------------------------------------------------
     Modals
     --------------------------------------------------------------------- */
  const FOCUSABLE =
    'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function openModal(modal) {
    if (!modal) return;
    modal.hidden = false;
    document.body.classList.add("modal-open");
    const focusables = modal.querySelectorAll(FOCUSABLE);
    if (focusables.length) focusables[0].focus();
    modal._returnFocus = document.activeElement;
  }

  function closeModal(modal) {
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("modal-open");
    if (modal._returnFocus && modal._returnFocus.focus) modal._returnFocus.focus();
  }

  function initModals() {
    document.querySelectorAll("[data-modal-open]").forEach((trigger) => {
      trigger.addEventListener("click", (e) => {
        e.preventDefault();
        const modal = document.getElementById(trigger.getAttribute("data-modal-open"));
        openModal(modal);
      });
    });

    document.querySelectorAll("[data-modal-close]").forEach((trigger) => {
      trigger.addEventListener("click", (e) => {
        e.preventDefault();
        closeModal(trigger.closest(".modal"));
      });
    });

    document.querySelectorAll(".modal").forEach((modal) => {
      modal.addEventListener("click", (e) => {
        if (e.target === modal) closeModal(modal);
      });
    });

    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      const open = document.querySelector(".modal:not([hidden])");
      if (open) {
        closeModal(open);
        return;
      }
      // Focus trap
      const modal = document.querySelector(".modal:not([hidden])");
      if (modal) return;
    });

    // Focus trap inside an open modal
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Tab") return;
      const modal = document.querySelector(".modal:not([hidden])");
      if (!modal) return;
      const focusables = Array.from(modal.querySelectorAll(FOCUSABLE)).filter(
        (el) => el.offsetParent !== null
      );
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    });
  }

  /* ---------------------------------------------------------------------
     Scroll reveal (landing page)
     --------------------------------------------------------------------- */
  function initReveal() {
    const reveals = document.querySelectorAll(".reveal");
    if (!reveals.length) return;
    if (!("IntersectionObserver" in window)) {
      reveals.forEach((el) => el.classList.add("visible"));
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry, i) => {
          if (entry.isIntersecting) {
            window.setTimeout(() => entry.target.classList.add("visible"), i * 70);
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.1, rootMargin: "0px 0px -40px 0px" }
    );
    reveals.forEach((el) => observer.observe(el));
  }

  /* ---------------------------------------------------------------------
     Password visibility toggles
     --------------------------------------------------------------------- */
  function initPasswordToggles() {
    document.querySelectorAll("[data-password-toggle]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const input = document.getElementById(btn.getAttribute("data-password-toggle"));
        if (!input) return;
        const showing = input.type === "text";
        input.type = showing ? "password" : "text";
        btn.setAttribute("aria-pressed", String(!showing));
        const label = btn.querySelector("[data-toggle-label]");
        if (label) label.textContent = showing ? "Show" : "Hide";
      });
    });
  }

  /* ---------------------------------------------------------------------
     Public API
     --------------------------------------------------------------------- */
  window.CT = {
    fetchJSON: fetchJSON,
    csrfToken: csrfToken,
    toast: toast,
    openModal: openModal,
    closeModal: closeModal,
    initTheme: initTheme,
  };

  document.addEventListener("DOMContentLoaded", () => {
    initTheme();
    initSidebar();
    initModals();
    initReveal();
    initPasswordToggles();
  });
})();
