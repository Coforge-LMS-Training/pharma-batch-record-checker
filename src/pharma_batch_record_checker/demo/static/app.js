// Batch Record Review: small progressive enhancements. Every page works
// without this file; it adds the mobile menu, the calendar, instant search,
// clickable rows, finding filters, and a scroll-aware step list.
(function () {
  "use strict";

  document.documentElement.classList.remove("no-js");

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // ---- Mobile menu ----------------------------------------------------
  const toggle = $(".nav-toggle");
  const links = $("#nav-links");
  if (toggle && links) {
    const icon = $("use", toggle);
    const setOpen = (open) => {
      links.classList.toggle("is-open", open);
      toggle.setAttribute("aria-expanded", String(open));
      if (icon) icon.setAttribute("href", open ? "#i-x" : "#i-menu");
    };
    toggle.addEventListener("click", () =>
      setOpen(toggle.getAttribute("aria-expanded") !== "true"));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });
  }

  // ---- Clickable table rows -------------------------------------------
  $$("tr[data-href]").forEach((row) => {
    row.addEventListener("click", (e) => {
      if (e.target.closest("a, button")) return;
      window.location.href = row.dataset.href;
    });
  });

  // ---- Instant search on the batch list -------------------------------
  const search = $("[data-live-search]");
  const table = $("table[data-filterable]");
  if (search && table) {
    const noMatch = $("[data-no-match]");
    const filter = () => {
      const needle = search.value.trim().toLowerCase();
      let shown = 0;
      $$("tbody tr", table).forEach((row) => {
        const hit = !needle || (row.dataset.search || "").includes(needle);
        row.hidden = !hit;
        if (hit) shown += 1;
      });
      if (noMatch) noMatch.hidden = shown !== 0;
    };
    search.addEventListener("input", filter);
  }

  // ---- Calendar ------------------------------------------------------
  const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December"];
  const DOW = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"];
  const pad = (n) => String(n).padStart(2, "0");
  const iso = (y, m, d) => `${y}-${pad(m + 1)}-${pad(d)}`;
  const label = (value) => {
    const [y, m, d] = value.split("-").map(Number);
    const date = new Date(Date.UTC(y, m - 1, d));
    return date.toLocaleDateString("en-GB", {
      weekday: "short", day: "numeric", month: "short", year: "numeric", timeZone: "UTC",
    });
  };

  $$("[data-datepicker]").forEach((root) => {
    const native = $("[data-date-input]", root);
    if (!native) return;
    const form = root.closest("form");
    let reviewDays = [];
    try { reviewDays = JSON.parse(root.dataset.reviewDays || "[]"); } catch (_) { /* none */ }
    const marked = new Set(reviewDays);

    // Keep the value in a hidden field; show a button instead of the native box.
    const hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.name = native.name;
    hidden.value = native.value;
    native.closest("label").replaceWith(hidden);

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "date-trigger";
    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-expanded", "false");
    root.appendChild(trigger);

    const pop = document.createElement("div");
    pop.className = "date-pop";
    pop.setAttribute("role", "dialog");
    pop.setAttribute("aria-label", "Choose a review day (UTC)");
    pop.hidden = true;
    root.appendChild(pop);

    const now = new Date();
    const todayIso = iso(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
    const start = hidden.value || reviewDays[0] || todayIso;
    let viewY = Number(start.slice(0, 4));
    let viewM = Number(start.slice(5, 7)) - 1;

    const renderTrigger = () => {
      trigger.innerHTML =
        '<svg class="icon" aria-hidden="true"><use href="#i-calendar"/></svg>' +
        (hidden.value
          ? `<span>${label(hidden.value)}</span>`
          : '<span class="placeholder">Any review day</span>') +
        '<svg class="icon chev" aria-hidden="true"><use href="#i-chevron-down"/></svg>';
      trigger.setAttribute("aria-label",
        hidden.value ? `Reviewed on ${label(hidden.value)}. Change day` : "Filter by review day");
    };

    const choose = (value) => {
      hidden.value = value;
      renderTrigger();
      close();
      if (form) form.submit();
    };

    const render = () => {
      const first = new Date(Date.UTC(viewY, viewM, 1));
      const offset = (first.getUTCDay() + 6) % 7; // Monday first
      const daysIn = new Date(Date.UTC(viewY, viewM + 1, 0)).getUTCDate();
      const prevDays = new Date(Date.UTC(viewY, viewM, 0)).getUTCDate();
      let cells = DOW.map((d) => `<span class="dp-dow" aria-hidden="true">${d}</span>`).join("");
      for (let i = 0; i < 42; i += 1) {
        let y = viewY; let m = viewM; let d = i - offset + 1; let other = false;
        if (d < 1) { m -= 1; d += prevDays; other = true; }
        else if (d > daysIn) { m += 1; d -= daysIn; other = true; }
        if (m < 0) { m = 11; y -= 1; }
        if (m > 11) { m = 0; y += 1; }
        const value = iso(y, m, d);
        const cls = ["dp-day"];
        if (other) cls.push("is-other");
        if (value === todayIso) cls.push("is-today");
        if (marked.has(value)) cls.push("has-review");
        const extra = marked.has(value) ? ", has reviews" : "";
        cells += `<button type="button" class="${cls.join(" ")}" data-value="${value}" ` +
          `aria-pressed="${value === hidden.value}" aria-label="${label(value)}${extra}">${d}</button>`;
      }
      pop.innerHTML =
        `<div class="dp-head"><button type="button" class="dp-nav" data-step="-1" aria-label="Previous month">` +
        `<svg class="icon" aria-hidden="true"><use href="#i-chevron-left"/></svg></button>` +
        `<span aria-live="polite">${MONTHS[viewM]} ${viewY}</span>` +
        `<button type="button" class="dp-nav" data-step="1" aria-label="Next month">` +
        `<svg class="icon" aria-hidden="true"><use href="#i-chevron-right"/></svg></button></div>` +
        `<div class="dp-grid">${cells}</div>` +
        `<div class="dp-foot"><span><span class="legend-dot"></span>Has reviews</span>` +
        `<span><button type="button" data-action="today">Today</button>` +
        `<button type="button" data-action="clear">Any day</button></span></div>`;
    };

    const open = () => {
      pop.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
      render();
      const target = $('.dp-day[aria-pressed="true"]', pop) || $(".dp-day.is-today", pop)
        || $(".dp-day:not(.is-other)", pop);
      if (target) target.focus();
    };
    function close() {
      pop.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
    }

    trigger.addEventListener("click", () => (pop.hidden ? open() : close()));
    pop.addEventListener("click", (e) => {
      const nav = e.target.closest("[data-step]");
      if (nav) {
        viewM += Number(nav.dataset.step);
        if (viewM < 0) { viewM = 11; viewY -= 1; }
        if (viewM > 11) { viewM = 0; viewY += 1; }
        render();
        return;
      }
      const day = e.target.closest(".dp-day");
      if (day) { choose(day.dataset.value); return; }
      const action = e.target.closest("[data-action]");
      if (action) choose(action.dataset.action === "today" ? todayIso : "");
    });
    pop.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { close(); trigger.focus(); }
    });
    document.addEventListener("click", (e) => {
      if (!pop.hidden && !root.contains(e.target)) close();
    });

    renderTrigger();
  });

  // ---- Finding filters -------------------------------------------------
  const chips = $("[data-chips]");
  if (chips) {
    chips.hidden = false;
    const cards = $$(".step-card");
    const noMatch = $(".findings [data-no-match]");
    chips.addEventListener("click", (e) => {
      const chip = e.target.closest("[data-filter]");
      if (!chip) return;
      const want = chip.dataset.filter;
      $$("[data-filter]", chips).forEach((c) => c.setAttribute("aria-pressed", String(c === chip)));
      // Filter by the step's result; every finding on a step shares it.
      let shown = 0;
      cards.forEach((card) => {
        const hit = want === "all" || card.dataset.status === want;
        card.hidden = !hit;
        if (hit) shown += 1;
      });
      if (noMatch) noMatch.hidden = shown !== 0;
    });
  }

  // ---- Highlight the step in view --------------------------------------
  const spyLinks = $$("[data-spy]");
  if (spyLinks.length && "IntersectionObserver" in window) {
    const byId = new Map(spyLinks.map((a) => [a.dataset.spy, a]));
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        spyLinks.forEach((a) => a.removeAttribute("aria-current"));
        const link = byId.get(entry.target.id);
        if (link) link.setAttribute("aria-current", "true");
      });
    }, { rootMargin: "-30% 0px -60% 0px" });
    byId.forEach((_, id) => { const el = document.getElementById(id); if (el) observer.observe(el); });
  }

  // ---- Back to top -------------------------------------------------------
  const toTop = $("[data-to-top]");
  if (toTop) {
    const onScroll = () => toTop.classList.toggle("is-visible", window.scrollY > 600);
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }
})();
