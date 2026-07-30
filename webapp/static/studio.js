(function () {
  "use strict";

  function scrollToTarget(target) {
    const el = document.querySelector(target);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function bindAnchorNavigation() {
    document.querySelectorAll('a[href^="#"]').forEach((link) => {
      link.addEventListener("click", (event) => {
        const href = link.getAttribute("href");
        if (!href || href === "#") return;
        const target = document.querySelector(href);
        if (!target) return;
        event.preventDefault();
        history.replaceState(null, "", href);
        scrollToTarget(href);
      });
    });
  }

  function bindPlanSourceSwitcher() {
    const tabs = Array.from(document.querySelectorAll("[data-plan-source]"));
    const panels = Array.from(document.querySelectorAll("[data-plan-panel]"));
    if (!tabs.length || !panels.length) return;

    const activate = (source) => {
      tabs.forEach((tab) => {
        const active = tab.dataset.planSource === source;
        tab.classList.toggle("is-active", active);
        tab.setAttribute("aria-pressed", active ? "true" : "false");
      });
      panels.forEach((panel) => {
        panel.hidden = panel.dataset.planPanel !== source;
      });
      const status = document.getElementById("plan_status");
      if (status) {
        if (source === "upload") status.textContent = "Choose a JSON plan file, then confirm the race setup.";
        else if (source === "text") status.textContent = "Paste the written plan and build its JSON before generating.";
        else {
          const selected = document.getElementById("plan_preset");
          status.textContent = selected && selected.value
            ? "Plan selected. Confirm the race setup, then generate your preview."
            : "Choose a plan from the library first.";
        }
      }
    };

    tabs.forEach((tab) => {
      tab.setAttribute("aria-pressed", tab.classList.contains("is-active") ? "true" : "false");
      tab.addEventListener("click", () => activate(tab.dataset.planSource));
    });
  }

  function bindDisclosureIcons() {
    document.querySelectorAll("details.disclosure").forEach((detail) => {
      const icon = detail.querySelector(".summary-icon");
      const update = () => {
        if (icon) icon.textContent = detail.open ? "−" : "+";
      };
      detail.addEventListener("toggle", update);
      update();
    });
  }

  function bindActiveSection() {
    const sections = Array.from(document.querySelectorAll(".observe-section"));
    const links = Array.from(document.querySelectorAll("[data-section-link]"));
    if (!sections.length || !links.length || !("IntersectionObserver" in window)) return;

    const observer = new IntersectionObserver((entries) => {
      const visible = entries
        .filter((entry) => entry.isIntersecting)
        .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (!visible) return;
      links.forEach((link) => {
        link.classList.toggle("is-active", link.dataset.sectionLink === visible.target.id);
      });
    }, { rootMargin: "-18% 0px -62% 0px", threshold: [0.02, 0.2, 0.5] });

    sections.forEach((section) => observer.observe(section));
  }

  function markEmptyLogs() {
    document.querySelectorAll(".empty-state[data-empty]").forEach((node) => {
      const update = () => node.classList.toggle("is-empty", !node.textContent.trim());
      const observer = new MutationObserver(update);
      observer.observe(node, { childList: true, characterData: true, subtree: true });
      update();
    });
  }

  function handleLegacyRoutes() {
    const path = window.location.pathname;
    if (window.location.hash) return;
    if (path === "/plan") history.replaceState(null, "", "/#plan");
    if (path === "/fit-editor") history.replaceState(null, "", "/#editor");
  }

  window.addEventListener("DOMContentLoaded", () => {
    handleLegacyRoutes();
    bindAnchorNavigation();
    bindPlanSourceSwitcher();
    bindDisclosureIcons();
    bindActiveSection();
    markEmptyLogs();
    if (window.location.hash) {
      window.requestAnimationFrame(() => scrollToTarget(window.location.hash));
    }
  });
})();
