(() => {
  function cssVar(name, fallback) {
    const val = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return val || fallback;
  }

  function initGraph(container) {
    if (!container || container.dataset.graphReady === "1" || typeof window.cytoscape !== "function") {
      return;
    }
    let payload;
    try {
      payload = JSON.parse(container.dataset.ontologyGraph || "{}");
    } catch {
      return;
    }
    const nodes = payload.nodes || [];
    const edges = payload.edges || [];
    if (nodes.length === 0) {
      return;
    }

    container.dataset.graphReady = "1";

    const text = cssVar("--ep-text", "#e5e7eb");
    const muted = cssVar("--ep-text-muted", "#9ca3af");
    const surface = cssVar("--ep-surface", "#181a1f");
    const border = cssVar("--ep-border", "#30343d");
    const accent = cssVar("--ep-accent", "#60a5fa");
    const accentTint = cssVar("--ep-accent-tint", "#1e3a5f");

    const elements = [
      ...nodes.map((n) => ({
        data: {
          id: n.id,
          label: n.label,
          kind: n.kind,
          href: n.href,
          active: n.active ? "yes" : "no",
        },
      })),
      ...edges.map((e, i) => ({
        data: { id: `e_${i}`, source: e.source, target: e.target },
      })),
    ];

    const cy = window.cytoscape({
      container,
      elements,
      minZoom: 0.35,
      maxZoom: 2.2,
      wheelSensitivity: 0.2,
      layout: {
        name: edges.length > 0 ? "breadthfirst" : "grid",
        directed: true,
        padding: 24,
        spacingFactor: 1.25,
      },
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            color: text,
            "font-size": "12px",
            "text-valign": "bottom",
            "text-margin-y": 6,
            "text-max-width": "130px",
            "text-wrap": "ellipsis",
            "background-color": surface,
            "border-width": 2,
            "border-color": border,
            width: 24,
            height: 24,
          },
        },
        {
          selector: 'node[kind = "constitution"], node[kind = "statute"], node[kind = "law"]',
          style: {
            "background-color": accentTint,
            "border-color": accent,
            width: 30,
            height: 30,
          },
        },
        {
          selector: 'node[active = "yes"]',
          style: {
            "background-color": accent,
            "border-color": text,
            "border-width": 3,
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.5,
            "line-color": muted,
            "target-arrow-color": muted,
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
          },
        },
      ],
    });

    cy.on("tap", "node", (evt) => {
      const href = evt.target.data("href");
      if (href) {
        window.location.assign(href);
      }
    });

    const host = container.closest("[data-graph-host]");
    if (host) {
      host.addEventListener("click", (evt) => {
        const btn = evt.target.closest("[data-graph-action]");
        if (!btn) return;
        const action = btn.dataset.graphAction;
        if (action === "fit") cy.fit(undefined, 24);
        else if (action === "zoom-in") cy.zoom(cy.zoom() * 1.2);
        else if (action === "zoom-out") cy.zoom(cy.zoom() * 0.85);
      });
    }
  }

  function initPresets() {
    document.querySelectorAll("[data-reasoning-preset]").forEach((btn) => {
      if (btn.dataset.presetBound === "1") return;
      btn.dataset.presetBound = "1";
      btn.addEventListener("click", () => {
        const form = btn.closest("form") || document.querySelector("[data-reasoning-form]");
        if (!form) return;
        const preset = btn.dataset.reasoningPreset;
        const today = new Date().toISOString().slice(0, 10);
        const setVal = (name, val) => {
          const el = form.querySelector(`[name="${name}"]`);
          if (el) el.value = val;
        };
        setVal("Input.ConductOn", today);
        setVal("Input.DecidedOn", today);
        if (preset === "daily") {
          setVal("Input.ConductEndedOn", "");
          setVal("Input.CriminalOffense", "false");
          setVal("Input.CourtConfiscated", "false");
          setVal("Input.GainedBenefit", "");
        } else if (preset === "incident") {
          setVal("Input.ConductEndedOn", today);
          setVal("Input.CriminalOffense", "");
          setVal("Input.CourtConfiscated", "");
          setVal("Input.GainedBenefit", "");
        } else if (preset === "benefit") {
          setVal("Input.ConductEndedOn", today);
          setVal("Input.CriminalOffense", "false");
          setVal("Input.CourtConfiscated", "false");
          setVal("Input.GainedBenefit", "50000");
        }
      });
    });
  }

  function boot() {
    document.querySelectorAll("[data-ontology-graph]").forEach(initGraph);
    initPresets();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
  if (window.Blazor && typeof window.Blazor.addEventListener === "function") {
    window.Blazor.addEventListener("enhancedload", boot);
  }
})();
