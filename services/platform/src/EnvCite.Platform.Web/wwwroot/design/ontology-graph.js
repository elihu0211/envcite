(() => {
  const probeEl = () => {
    let el = document.getElementById("ep-graph-color-probe");
    if (!el && document.body) {
      el = document.createElement("span");
      el.id = "ep-graph-color-probe";
      el.style.position = "absolute";
      el.style.visibility = "hidden";
      el.style.pointerEvents = "none";
      document.body.appendChild(el);
    }
    return el;
  };

  function resolveColor(expr, fallback) {
    const el = probeEl();
    if (!el) return fallback;
    el.style.color = "";
    el.style.color = expr;
    const computed = getComputedStyle(el).color;
    return computed || fallback;
  }

  let cachedPaletteKey = null;
  let cachedPaletteValue = null;

  function themePalette(forceRefresh = false) {
    const isDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    const themeAttr = document.documentElement ? (document.documentElement.dataset.theme || "") : "";
    const key = `${themeAttr}|${isDark ? "dark" : "light"}`;
    if (!forceRefresh && cachedPaletteValue && cachedPaletteKey === key) {
      return cachedPaletteValue;
    }
    cachedPaletteKey = key;
    cachedPaletteValue = {
      text: resolveColor("var(--ep-text)", "#1D1D1F"),
      textSecondary: resolveColor("var(--ep-text-secondary)", "#55555B"),
      surface: resolveColor("var(--ep-surface)", "#FFFFFF"),
      surfaceRaised: resolveColor("var(--ep-surface-raised)", "#FFFFFF"),
      surfaceMuted: resolveColor("var(--ep-surface-muted)", "#F0F0F3"),
      borderSubtle: resolveColor("var(--ep-border-subtle)", "#E3E3E8"),
      borderControl: resolveColor("var(--ep-border-control)", "#86868B"),
      primary: resolveColor("var(--ep-primary)", "#005FC4"),
      onPrimary: resolveColor("var(--ep-on-primary)", "#FFFFFF"),
      primaryTint: resolveColor("color-mix(in srgb, var(--ep-primary) 14%, var(--ep-surface))", "#E8F1FB"),
      info: resolveColor("var(--ep-info)", "#1D52AF"),
      infoTint: resolveColor("color-mix(in srgb, var(--ep-info) 12%, var(--ep-surface))", "#EAF0FA"),
      warning: resolveColor("var(--ep-warning)", "#AC5107"),
      warningTint: resolveColor("color-mix(in srgb, var(--ep-warning) 12%, var(--ep-surface))", "#FDF2E9"),
      error: resolveColor("var(--ep-error)", "#AC312C"),
      errorTint: resolveColor("color-mix(in srgb, var(--ep-error) 12%, var(--ep-surface))", "#FCECEB"),
      accent: resolveColor("var(--ep-accent)", "#14B8A6"),
      accentTint: resolveColor("color-mix(in srgb, var(--ep-accent) 14%, var(--ep-surface))", "#E6F8F5"),
    };
    return cachedPaletteValue;
  }

  function truncateTitle(title, maxChars) {
    const raw = (title || "").trim();
    if (raw.length <= maxChars) return raw;
    return `${raw.slice(0, maxChars - 1)}…`;
  }

  function formatNodeCardLabel(n, labels) {
    const shortTitle = truncateTitle(n.title, 22);
    const metaParts = [];
    if (n.documentType) metaParts.push(n.documentType);
    if (n.invalidationCount > 0) metaParts.push(`⚠ ${n.invalidationCount}`);
    else if (n.guidanceCount > 0) metaParts.push(`${labels.guidanceMarker || "函釋"} ${n.guidanceCount}`);
    if (n.totalChildren > 0) metaParts.push(`${labels.childMarker || "子法"} ${n.totalChildren}`);
    return metaParts.length > 0 ? `${shortTitle}\n${metaParts.join(" · ")}` : shortTitle;
  }

  function computeStufenbauPositions(nodes, edges, containerWidth, labels) {
    const width = Math.max(containerWidth || 760, 600);
    const colPitch = 232;
    const subRowPitch = 96;
    const tierGap = 72;
    const maxCols = Math.max(2, Math.min(5, Math.floor((width - 40) / colPitch)));

    const byId = new Map(nodes.map((n) => [n.id, n]));
    const parentsMap = new Map(nodes.map((n) => [n.id, []]));
    edges.forEach((e) => {
      if (byId.has(e.source) && byId.has(e.target)) {
        parentsMap.get(e.target).push(e.source);
      }
    });

    const tiers = Array.from(new Set(nodes.map((n) => Number.isFinite(n.hierarchyTier) ? n.hierarchyTier : 1))).sort((a, b) => a - b);
    const positions = new Map();
    const xLookup = new Map();
    let currentY = 64;

    tiers.forEach((tier) => {
      const tierNodes = nodes.filter((n) => (Number.isFinite(n.hierarchyTier) ? n.hierarchyTier : 1) === tier);
      tierNodes.sort((a, b) => {
        const pA = parentsMap.get(a.id).filter((pid) => xLookup.has(pid));
        const pB = parentsMap.get(b.id).filter((pid) => xLookup.has(pid));
        const avgA = pA.length > 0 ? pA.reduce((sum, pid) => sum + xLookup.get(pid), 0) / pA.length : 0;
        const avgB = pB.length > 0 ? pB.reduce((sum, pid) => sum + xLookup.get(pid), 0) / pB.length : 0;
        if (Math.abs(avgA - avgB) > 1) return avgA - avgB;
        if (a.focus !== b.focus) return a.focus ? -1 : 1;
        if ((b.totalChildren || 0) !== (a.totalChildren || 0)) return (b.totalChildren || 0) - (a.totalChildren || 0);
        return (a.localId || "").localeCompare(b.localId || "");
      });

      const subRowCount = Math.max(1, Math.ceil(tierNodes.length / maxCols));
      const perRow = Math.ceil(tierNodes.length / subRowCount);

      for (let r = 0; r < subRowCount; r++) {
        const slice = tierNodes.slice(r * perRow, (r + 1) * perRow);
        const startX = -((slice.length - 1) * colPitch) / 2;
        slice.forEach((n, idx) => {
          const x = startX + idx * colPitch;
          const y = currentY;
          positions.set(n.id, { x, y });
          xLookup.set(n.id, x);
        });
        currentY += subRowPitch;
      }

      const capsulesInTier = tierNodes.filter((n) => n.truncatedChildren > 0);
      if (capsulesInTier.length > 0) {
        capsulesInTier.forEach((parentNode) => {
          const parentPos = positions.get(parentNode.id);
          if (parentPos) {
            positions.set(`cluster__${parentNode.id}`, {
              x: parentPos.x,
              y: currentY - 8,
            });
          }
        });
        currentY += 68;
      }

      currentY += tierGap - 20;
    });

    if (nodes.length === 0) return { positions, clusters: [] };
    const clusters = nodes
      .filter((n) => n.truncatedChildren > 0 && n.expandHref)
      .map((n) => ({
        id: `cluster__${n.id}`,
        parentId: n.id,
        label: `+${n.truncatedChildren} ${labels.clusterSuffix || "部授權法規（點擊展開）"}`,
        expandHref: n.expandHref,
      }));
    return { positions, clusters };
  }

  function buildStylesheet(palette, showEdgeLabels) {
    return [
      {
        selector: "node",
        style: {
          shape: "round-rectangle",
          width: 204,
          height: 64,
          padding: "8px",
          label: "data(cardLabel)",
          color: palette.text,
          "font-size": "12px",
          "font-weight": 500,
          "line-height": 1.32,
          "text-valign": "center",
          "text-halign": "center",
          "text-wrap": "wrap",
          "text-max-width": "184px",
          "background-color": palette.surfaceRaised,
          "border-width": 1.5,
          "border-color": palette.borderControl,
          "transition-property": "border-color, border-width, opacity, background-color",
          "transition-duration": "0.15s",
        },
      },
      {
        selector: "node[tier = 0]",
        style: {
          "background-color": palette.warningTint,
          "border-color": palette.warning,
          "border-width": 2,
        },
      },
      {
        selector: "node[tier = 1]",
        style: {
          "background-color": palette.primaryTint,
          "border-color": palette.primary,
          "border-width": 2,
        },
      },
      {
        selector: "node[tier = 2]",
        style: {
          "background-color": palette.infoTint,
          "border-color": palette.info,
          "border-width": 1.5,
        },
      },
      {
        selector: "node[tier >= 3]",
        style: {
          "background-color": palette.accentTint,
          "border-color": palette.accent,
          "border-style": "dashed",
          "border-width": 1.5,
        },
      },
      {
        selector: "node[invalidations > 0]",
        style: {
          "background-color": palette.errorTint,
          "border-color": palette.error,
          "border-width": 2.5,
        },
      },
      {
        selector: 'node[isFocus = "yes"]',
        style: {
          "background-color": palette.primary,
          color: palette.onPrimary,
          "border-color": palette.text,
          "border-width": 3,
          "font-weight": 700,
          width: 216,
          height: 70,
        },
      },
      {
        selector: 'node[isCluster = "yes"]',
        style: {
          shape: "round-rectangle",
          width: 196,
          height: 36,
          "background-color": palette.surfaceMuted,
          "border-color": palette.primary,
          "border-style": "dashed",
          "border-width": 1.5,
          color: palette.primary,
          "font-size": "12px",
          "font-weight": 600,
        },
      },
      {
        selector: "node.is-selected",
        style: {
          "border-color": palette.text,
          "border-width": 3.5,
        },
      },
      {
        selector: "edge",
        style: {
          width: 1.8,
          "line-color": palette.borderControl,
          "target-arrow-color": palette.borderControl,
          "target-arrow-shape": "triangle",
          "arrow-scale": 0.9,
          "curve-style": "bezier",
          "control-point-step-size": 36,
          opacity: 0.78,
          label: showEdgeLabels ? "data(anchorLabel)" : "",
          "font-size": "11px",
          color: palette.textSecondary,
          "text-background-color": palette.surface,
          "text-background-opacity": 0.9,
          "text-background-padding": "2px",
          "text-rotation": "autorotate",
        },
      },
      {
        selector: 'edge[kind = "sub_delegation"]',
        style: {
          "line-style": "dashed",
          "line-color": palette.warning,
          "target-arrow-color": palette.warning,
        },
      },
      {
        selector: 'edge[kind = "cluster_link"]',
        style: {
          "line-style": "dotted",
          "line-color": palette.primary,
          "target-arrow-shape": "none",
          opacity: 0.75,
        },
      },
      {
        selector: "edge.is-highlighted",
        style: {
          width: 2.8,
          "line-color": palette.primary,
          "target-arrow-color": palette.primary,
          opacity: 1,
          label: "data(anchorLabel)",
        },
      },
      {
        selector: ".is-dimmed",
        style: {
          opacity: 0.25,
        },
      },
    ];
  }

  function tierName(tier, labels) {
    if (tier === 0) return labels.tier0 || "Tier 0 憲法";
    if (tier === 1) return labels.tier1 || "Tier 1 法律／條例";
    if (tier === 2) return labels.tier2 || "Tier 2 法規命令";
    return labels.tier3 || "Tier 3 再授權規則";
  }

  function clearEl(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function addEl(parent, tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined && text !== null) el.textContent = text;
    parent.appendChild(el);
    return el;
  }

  function renderInspector(inspectorEl, rawNode, allNodes, allEdges, labels) {
    if (!inspectorEl) return;
    clearEl(inspectorEl);

    if (!rawNode) {
      addEl(inspectorEl, "p", "ep-page-description", labels.selectPrompt || "");
      return;
    }

    const head = addEl(inspectorEl, "div", "ep-inspector-head");
    const badges = addEl(head, "div", "ep-inspector-badges");
    const tierBadge = addEl(badges, "span", "ep-badge", tierName(rawNode.hierarchyTier, labels));
    tierBadge.dataset.tone = rawNode.hierarchyTier === 0 ? "warning" : rawNode.hierarchyTier === 1 ? "info" : "neutral";
    if (rawNode.documentType) {
      addEl(badges, "span", "ep-badge", rawNode.documentType);
    }
    if (rawNode.invalidationCount > 0) {
      const invBadge = addEl(badges, "span", "ep-badge", (labels.invalidationBadge || "{n} 項違憲失效").replace("{n}", String(rawNode.invalidationCount)));
      invBadge.dataset.tone = "error";
    }

    addEl(head, "h3", "ep-inspector-title", rawNode.title);
    const metaParts = [`${rawNode.jurisdiction} / ${rawNode.idScheme} / ${rawNode.localId}`];
    if (rawNode.dateDocument) metaParts.push(rawNode.dateDocument);
    addEl(head, "p", "ep-inspector-key ep-mono", metaParts.join(" · "));

    const stats = addEl(inspectorEl, "div", "ep-inspector-metrics");
    addEl(stats, "span", "ep-badge", (labels.articlesText || "{n} 條條文").replace("{n}", String(rawNode.articleCount || 0)));
    addEl(stats, "span", "ep-badge", (labels.childrenCountText || "{n} 部子法規").replace("{n}", String(rawNode.totalChildren || 0)));
    if (rawNode.guidanceCount > 0) {
      const gBadge = addEl(stats, "span", "ep-badge", (labels.guidanceCountText || "{n} 件有效行政函釋（行政程序法第 159 條）").replace("{n}", String(rawNode.guidanceCount)));
      gBadge.dataset.tone = "info";
    }

    if (rawNode.invalidationCount > 0) {
      const alert = addEl(inspectorEl, "p", "ep-alert ep-alert-error", labels.invalidationSource || "資料來源：憲法法庭；未列出不等於無違憲失效。");
      alert.setAttribute("role", "status");
    }

    if (rawNode.truncatedChildren > 0) {
      const note = addEl(inspectorEl, "p", "ep-hint", (labels.truncatedNote || "").replace("{n}", String(rawNode.truncatedChildren)));
      note.dataset.truncated = "true";
    }

    const actions = addEl(inspectorEl, "div", "ep-inspector-actions");
    if (rawNode.focusHref && !rawNode.focus) {
      const focusLink = addEl(actions, "a", "ep-btn ep-btn-primary", labels.focusButton || "以此法為中心重建圖譜");
      focusLink.href = rawNode.focusHref;
    }
    if (rawNode.structureHref) {
      const structLink = addEl(actions, "a", rawNode.focus ? "ep-btn ep-btn-primary" : "ep-btn ep-btn-quiet", labels.structureButton || "檢視編章節條文");
      structLink.href = rawNode.structureHref;
    }
    if (rawNode.truncatedChildren > 0 && rawNode.expandHref) {
      const expandLink = addEl(actions, "a", "ep-btn ep-btn-quiet", (labels.expandButton || "展開完整 +{n}").replace("{n}", String(rawNode.truncatedChildren)));
      expandLink.href = rawNode.expandHref;
    }

    const nodeById = new Map(allNodes.map((n) => [n.id, n]));
    const parents = allEdges.filter((e) => e.target === rawNode.id).map((e) => ({ edge: e, peer: nodeById.get(e.source) })).filter((x) => x.peer);
    const children = allEdges.filter((e) => e.source === rawNode.id).map((e) => ({ edge: e, peer: nodeById.get(e.target) })).filter((x) => x.peer);

    if (parents.length === 0 && children.length === 0) {
      addEl(inspectorEl, "p", "ep-hint", labels.noEdgesText || "");
      return;
    }

    const relations = addEl(inspectorEl, "div", "ep-inspector-relations");

    const buildRelationGroup = (headingText, items) => {
      if (items.length === 0) return;
      const box = addEl(relations, "div", "ep-inspector-rel-group");
      addEl(box, "h4", "ep-inspector-rel-heading", headingText);
      const list = addEl(box, "ul", "ep-inspector-rel-list");
      items.forEach(({ edge, peer }) => {
        const li = addEl(list, "li", "ep-inspector-rel-item");
        if (peer.focusHref) {
          const link = addEl(li, "a", "ep-inspector-rel-link", peer.title);
          link.href = peer.focusHref;
        } else {
          addEl(li, "span", "ep-inspector-rel-link", peer.title);
        }
        const kindLabel = edge.kind === "sub_delegation" ? (labels.subDelegationKind || "再授權") : (labels.delegationKind || "法律直接授權");
        const anchors = Array.isArray(edge.anchorArticles) && edge.anchorArticles.length > 0
          ? `${labels.anchorPrefix || "授權條文："}${edge.anchorArticles.join("、")}`
          : kindLabel;
        addEl(li, "span", "ep-hint", `${kindLabel} · ${anchors}`);
      });
    };

    buildRelationGroup(labels.parentsHeading || "授權母法依據（上位規範）", parents);
    buildRelationGroup(labels.childrenHeading || "授權訂定子法規（下位規範）", children);
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
    const labels = payload.labels || {};
    if (nodes.length === 0) {
      return;
    }

    container.dataset.graphReady = "1";
    const host = container.closest("[data-graph-host]");
    const inspectorEl = host ? host.querySelector("[data-graph-inspector]") : null;

    let currentLayout = payload.mode === "concentric" ? "concentric" : "stufenbau";
    let showEdgeLabels = edges.length <= 20;

    const { positions, clusters } = computeStufenbauPositions(nodes, edges, container.clientWidth, labels);

    const elements = [
      ...nodes.map((n) => ({
        data: {
          id: n.id,
          cardLabel: formatNodeCardLabel(n, labels),
          tier: Number.isFinite(n.hierarchyTier) ? n.hierarchyTier : 1,
          invalidations: n.invalidationCount || 0,
          isFocus: n.focus ? "yes" : "no",
          isCluster: "no",
          raw: n,
        },
        position: positions.get(n.id) || { x: 0, y: 0 },
      })),
      ...clusters.map((c) => ({
        data: {
          id: c.id,
          cardLabel: c.label,
          tier: 9,
          invalidations: 0,
          isFocus: "no",
          isCluster: "yes",
          expandHref: c.expandHref,
        },
        position: positions.get(c.id) || { x: 0, y: 0 },
      })),
      ...edges.map((e, i) => ({
        data: {
          id: `e_${i}`,
          source: e.source,
          target: e.target,
          kind: e.kind || "delegation",
          anchorLabel: Array.isArray(e.anchorArticles) && e.anchorArticles.length > 0 ? e.anchorArticles.join("、") : "",
        },
      })),
      ...clusters.map((c, i) => ({
        data: {
          id: `ec_${i}`,
          source: c.parentId,
          target: c.id,
          kind: "cluster_link",
          anchorLabel: "",
        },
      })),
    ];

    const cy = window.cytoscape({
      container,
      elements,
      minZoom: 0.25,
      maxZoom: 2.0,
      wheelSensitivity: 0.18,
      textureOnViewport: true,
      motionBlur: false,
      layout: { name: "preset", fit: false },
      style: buildStylesheet(themePalette(), showEdgeLabels),
    });
    container._cyInstance = cy;

    function fitViewport() {
      cy.resize();
      cy.fit(undefined, 28);
      if (cy.zoom() > 1.05) {
        cy.zoom(1.0);
        cy.center();
      }
    }

    function applyLayout(mode) {
      currentLayout = mode;
      if (mode === "concentric") {
        const focusNode = nodes.find((n) => n.focus) || nodes.find((n) => n.hierarchyTier === 1) || nodes[0];
        cy.layout({
          name: "concentric",
          fit: true,
          padding: 36,
          minNodeSpacing: 52,
          avoidOverlap: true,
          concentric: (ele) => {
            if (ele.data("isCluster") === "yes") return 1;
            const raw = ele.data("raw");
            if (!raw) return 2;
            if (focusNode && raw.id === focusNode.id) return 10;
            if (raw.hierarchyTier <= 1) return 7;
            return 4;
          },
          levelWidth: () => 2,
          animate: false,
        }).run();
      } else {
        const updated = computeStufenbauPositions(nodes, edges, container.clientWidth, labels);
        cy.batch(() => {
          cy.nodes().forEach((ele) => {
            const p = updated.positions.get(ele.id());
            if (p) ele.position(p);
          });
        });
      }
      fitViewport();
      updateToolbarState();
    }

    function selectNodeById(nodeId) {
      const ele = cy.getElementById(nodeId);
      if (!ele || ele.empty()) {
        cy.batch(() => cy.elements().removeClass("is-selected is-highlighted is-dimmed"));
        renderInspector(inspectorEl, null, nodes, edges, labels);
        return;
      }
      if (ele.data("isCluster") === "yes") return;
      const hood = ele.closedNeighborhood();
      cy.batch(() => {
        cy.elements().removeClass("is-selected is-highlighted is-dimmed");
        ele.addClass("is-selected");
        cy.elements().difference(hood).addClass("is-dimmed");
        ele.connectedEdges().addClass("is-highlighted");
      });
      renderInspector(inspectorEl, ele.data("raw"), nodes, edges, labels);
    }

    function updateToolbarState() {
      if (!host) return;
      host.querySelectorAll('[data-graph-action^="layout-"]').forEach((btn) => {
        const targetMode = btn.dataset.graphAction.replace("layout-", "");
        btn.setAttribute("aria-pressed", targetMode === currentLayout ? "true" : "false");
      });
      const edgeToggle = host.querySelector('[data-graph-action="toggle-edge-labels"]');
      if (edgeToggle) {
        edgeToggle.setAttribute("aria-pressed", showEdgeLabels ? "true" : "false");
      }
    }

    cy.on("tap", "node", (evt) => {
      const target = evt.target;
      if (target.data("isCluster") === "yes") {
        const href = target.data("expandHref");
        if (href) window.location.assign(href);
        return;
      }
      selectNodeById(target.id());
    });

    cy.on("dbltap", "node", (evt) => {
      const raw = evt.target.data("raw");
      if (raw && raw.focusHref) {
        window.location.assign(raw.focusHref);
      }
    });

    cy.on("tap", (evt) => {
      if (evt.target === cy) {
        cy.batch(() => cy.elements().removeClass("is-selected is-highlighted is-dimmed"));
        const initial = nodes.find((n) => n.focus) || nodes[0] || null;
        renderInspector(inspectorEl, initial, nodes, edges, labels);
      }
    });

    const initialNode = nodes.find((n) => n.focus) || nodes.find((n) => n.hierarchyTier === 1) || nodes[0] || null;
    if (initialNode) {
      selectNodeById(initialNode.id);
    }
    fitViewport();
    updateToolbarState();

    if (host && host.dataset.graphBound !== "1") {
      host.dataset.graphBound = "1";
      host.addEventListener("click", (evt) => {
        const btn = evt.target.closest("[data-graph-action]");
        if (!btn) return;
        const activeCy = container._cyInstance || cy;
        const action = btn.dataset.graphAction;
        if (action === "fit") fitViewport();
        else if (action === "zoom-in") activeCy.zoom({ level: activeCy.zoom() * 1.2, renderedPosition: { x: container.clientWidth / 2, y: container.clientHeight / 2 } });
        else if (action === "zoom-out") activeCy.zoom({ level: activeCy.zoom() * 0.84, renderedPosition: { x: container.clientWidth / 2, y: container.clientHeight / 2 } });
        else if (action === "layout-stufenbau") applyLayout("stufenbau");
        else if (action === "layout-concentric") applyLayout("concentric");
        else if (action === "toggle-edge-labels") {
          showEdgeLabels = !showEdgeLabels;
          activeCy.style(buildStylesheet(themePalette(), showEdgeLabels));
          updateToolbarState();
        }
      });
    }

    if (typeof ResizeObserver === "function") {
      let lastWidth = container.clientWidth;
      let resizeRaf = 0;
      const observer = new ResizeObserver(() => {
        if (resizeRaf) return;
        resizeRaf = window.requestAnimationFrame(() => {
          resizeRaf = 0;
          const w = container.clientWidth;
          if (Math.abs(w - lastWidth) > 32) {
            lastWidth = w;
            if (currentLayout === "stufenbau") {
              applyLayout("stufenbau");
            } else {
              fitViewport();
            }
          }
        });
      });
      observer.observe(container);
    }

    if (window.matchMedia) {
      const mql = window.matchMedia("(prefers-color-scheme: dark)");
      const refreshTheme = () => cy.style(buildStylesheet(themePalette(true), showEdgeLabels));
      if (typeof mql.addEventListener === "function") mql.addEventListener("change", refreshTheme);
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
