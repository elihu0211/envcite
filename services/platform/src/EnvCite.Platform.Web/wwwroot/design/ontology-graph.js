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

  function buildVisibleGraphModel(nodes, edges, expandedGroups, labels) {
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const incomingByTarget = new Map();
    const outgoingBySource = new Map();
    edges.forEach((e) => {
      if (!byId.has(e.source) || !byId.has(e.target)) return;
      if (!incomingByTarget.has(e.target)) incomingByTarget.set(e.target, []);
      incomingByTarget.get(e.target).push(e);
      if (!outgoingBySource.has(e.source)) outgoingBySource.set(e.source, []);
      outgoingBySource.get(e.source).push(e);
    });

    const shouldGroup = nodes.length > 14;
    const hiddenNodeIds = new Set();
    const groupNodes = [];
    const groupEdges = [];

    if (shouldGroup) {
      nodes.forEach((parent) => {
        const outEdges = outgoingBySource.get(parent.id) || [];
        const leafChildren = outEdges
          .map((e) => byId.get(e.target))
          .filter((child) => {
            if (!child || child.focus) return false;
            const tier = Number.isFinite(child.hierarchyTier) ? child.hierarchyTier : 1;
            const childOut = outgoingBySource.get(child.id) || [];
            const childIn = incomingByTarget.get(child.id) || [];
            return tier >= 2 && childOut.length === 0 && childIn.length === 1;
          });

        if (leafChildren.length <= 5) return;

        const byDocType = new Map();
        leafChildren.forEach((child) => {
          const docType = (child.documentType || labels.otherDocType || "其他子法").trim();
          if (!byDocType.has(docType)) byDocType.set(docType, []);
          byDocType.get(docType).push(child);
        });

        byDocType.forEach((groupItems, docType) => {
          if (groupItems.length < 2) return;
          const groupId = `grp__${parent.id}__${docType}`;
          const isExpanded = expandedGroups.has(groupId);
          const parentTier = Number.isFinite(parent.hierarchyTier) ? parent.hierarchyTier : 1;
          groupNodes.push({
            id: groupId,
            title: `${isExpanded ? "▼" : "▶"} ${docType} (${groupItems.length})`,
            documentType: docType,
            hierarchyTier: Math.min(3, parentTier + 1),
            isGroupCapsule: true,
            isExpanded,
            groupId,
            parentId: parent.id,
            count: groupItems.length,
          });
          groupEdges.push({
            source: parent.id,
            target: groupId,
            kind: "group_link",
            anchorArticles: [],
          });
          if (!isExpanded) {
            groupItems.forEach((item) => hiddenNodeIds.add(item.id));
          }
        });
      });
    }

    const visibleNodes = [
      ...nodes.filter((n) => !hiddenNodeIds.has(n.id)),
      ...groupNodes,
    ];
    const visibleIds = new Set(visibleNodes.map((n) => n.id));
    const visibleEdges = [
      ...edges.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target)),
      ...groupEdges,
    ];

    return { visibleNodes, visibleEdges };
  }

  function computeStufenbauPositions(nodes, edges, containerWidth, labels) {
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const parentsMap = new Map(nodes.map((n) => [n.id, []]));
    const childrenMap = new Map(nodes.map((n) => [n.id, []]));
    edges.forEach((e) => {
      if (byId.has(e.source) && byId.has(e.target)) {
        parentsMap.get(e.target).push(e.source);
        childrenMap.get(e.source).push(e.target);
      }
    });

    const tiers = Array.from(
      new Set(nodes.map((n) => (Number.isFinite(n.hierarchyTier) ? n.hierarchyTier : 1))),
    ).sort((a, b) => a - b);

    const tierGapX = 320;
    const rowGapY = 86;
    const positions = new Map();
    const yLookup = new Map();
    const tierBuckets = new Map();

    tiers.forEach((tier, tierIdx) => {
      const tierNodes = nodes.filter(
        (n) => (Number.isFinite(n.hierarchyTier) ? n.hierarchyTier : 1) === tier,
      );
      tierNodes.sort((a, b) => {
        const pA = parentsMap.get(a.id).filter((pid) => yLookup.has(pid));
        const pB = parentsMap.get(b.id).filter((pid) => yLookup.has(pid));
        const avgA = pA.length > 0 ? pA.reduce((sum, pid) => sum + yLookup.get(pid), 0) / pA.length : 0;
        const avgB = pB.length > 0 ? pB.reduce((sum, pid) => sum + yLookup.get(pid), 0) / pB.length : 0;
        if (Math.abs(avgA - avgB) > 1) return avgA - avgB;
        if (Boolean(a.isGroupCapsule) !== Boolean(b.isGroupCapsule)) return a.isGroupCapsule ? -1 : 1;
        if (Boolean(a.focus) !== Boolean(b.focus)) return a.focus ? -1 : 1;
        if ((b.totalChildren || 0) !== (a.totalChildren || 0)) return (b.totalChildren || 0) - (a.totalChildren || 0);
        return (a.localId || a.id || "").localeCompare(b.localId || b.id || "");
      });

      const totalHeight = (tierNodes.length - 1) * rowGapY;
      const startY = -totalHeight / 2;
      const x = (tierIdx - (tiers.length - 1) / 2) * tierGapX;
      tierBuckets.set(tier, { x, nodes: tierNodes });

      tierNodes.forEach((n, idx) => {
        const y = startY + idx * rowGapY;
        positions.set(n.id, { x, y });
        yLookup.set(n.id, y);
      });
    });

    // Bottom-up barycenter pass: align parent nodes to the vertical midpoint of their children
    for (let i = tiers.length - 2; i >= 0; i--) {
      const bucket = tierBuckets.get(tiers[i]);
      if (!bucket || bucket.nodes.length === 0) continue;
      const desired = bucket.nodes.map((n) => {
        const kids = (childrenMap.get(n.id) || []).filter((cid) => yLookup.has(cid));
        const base = yLookup.get(n.id) || 0;
        const targetY = kids.length > 0
          ? kids.reduce((sum, cid) => sum + yLookup.get(cid), 0) / kids.length
          : base;
        return { node: n, targetY };
      });
      desired.sort((a, b) => a.targetY - b.targetY);
      for (let idx = 1; idx < desired.length; idx++) {
        if (desired[idx].targetY - desired[idx - 1].targetY < rowGapY) {
          desired[idx].targetY = desired[idx - 1].targetY + rowGapY;
        }
      }
      const meanY = desired.reduce((sum, d) => sum + d.targetY, 0) / desired.length;
      desired.forEach((d) => {
        const y = d.targetY - meanY;
        positions.set(d.node.id, { x: bucket.x, y });
        yLookup.set(d.node.id, y);
      });
    }

    if (nodes.length === 0) return { positions, clusters: [] };
    const clusters = nodes
      .filter((n) => !n.isGroupCapsule && n.truncatedChildren > 0 && n.expandHref)
      .map((n) => {
        const pPos = positions.get(n.id) || { x: 0, y: 0 };
        const clusterId = `cluster__${n.id}`;
        positions.set(clusterId, { x: pPos.x + tierGapX, y: pPos.y });
        return {
          id: clusterId,
          parentId: n.id,
          label: `+${n.truncatedChildren} ${labels.clusterSuffix || "部授權法規（點擊展開）"}`,
          expandHref: n.expandHref,
        };
      });
    return { positions, clusters };
  }

  function computeIndentedPositions(nodes, edges) {
    const positions = new Map();
    if (!nodes || nodes.length === 0) return positions;

    const nodeMap = new Map(nodes.map((n) => [n.id, n]));
    const childrenByParent = new Map();
    const inDegree = new Map(nodes.map((n) => [n.id, 0]));

    for (const e of edges || []) {
      if (!nodeMap.has(e.source) || !nodeMap.has(e.target) || e.source === e.target) continue;
      if (!childrenByParent.has(e.source)) childrenByParent.set(e.source, []);
      childrenByParent.get(e.source).push(e.target);
      inDegree.set(e.target, (inDegree.get(e.target) || 0) + 1);
    }

    const byTierThenTitle = (aId, bId) => {
      const a = nodeMap.get(aId);
      const b = nodeMap.get(bId);
      const tierDiff = (a?.hierarchyTier ?? 99) - (b?.hierarchyTier ?? 99);
      if (tierDiff !== 0) return tierDiff;
      return String(a?.title || aId).localeCompare(String(b?.title || bId), "zh-Hant");
    };

    const roots = nodes
      .filter((n) => (inDegree.get(n.id) || 0) === 0)
      .map((n) => n.id)
      .sort(byTierThenTitle);

    const visited = new Set();
    let row = 0;

    function visit(id, depth) {
      if (visited.has(id)) return;
      visited.add(id);
      positions.set(id, { x: depth * 110, y: row * 82 });
      row += 1;
      const children = (childrenByParent.get(id) || []).slice().sort(byTierThenTitle);
      for (const childId of children) {
        visit(childId, depth + 1);
      }
    }

    for (const rootId of roots) visit(rootId, 0);
    for (const n of nodes) {
      if (!visited.has(n.id)) visit(n.id, 0);
    }

    return positions;
  }

  function buildTooltipContent(item, labels) {
    const raw = item?.data?.raw;
    const root = document.createElement("div");
    root.className = "ep-graph-tooltip";
    if (!raw) {
      const fallbackTitle = item?.style?.labelText || item?.data?.label || item?.id || "";
      addEl(root, "div", "ep-graph-tooltip-title", fallbackTitle);
      return root;
    }

    addEl(root, "div", "ep-graph-tooltip-title", raw.title || item?.id || "");
    const tierText = tierName(raw.hierarchyTier, labels);
    const idPart = raw.localId ? ` · ${raw.localId}` : "";
    addEl(root, "div", "ep-graph-tooltip-meta", `${tierText}${idPart}`);

    const parts = [];
    if (Number.isFinite(raw.articleCount)) {
      parts.push((labels.articlesText || "{n} 條條文").replace("{n}", String(raw.articleCount)));
    }
    if (Number.isFinite(raw.totalChildren) && raw.totalChildren > 0) {
      parts.push((labels.childrenCountText || "{n} 部子法規").replace("{n}", String(raw.totalChildren)));
    }
    if (Number.isFinite(raw.invalidationCount) && raw.invalidationCount > 0) {
      parts.push((labels.invalidationBadge || "{n} 項違憲失效").replace("{n}", String(raw.invalidationCount)));
    }
    if (parts.length > 0) {
      addEl(root, "div", "ep-graph-tooltip-meta", parts.join(" · "));
    }
    return root;
  }

  function nodeColors(d, palette) {
    if (d.isCluster === "yes") {
      return {
        fill: palette.surfaceMuted,
        stroke: palette.primary,
        lineWidth: 1.5,
        lineDash: [5, 4],
        labelFill: palette.primary,
        fontWeight: 600,
      };
    }
    if (d.isFocus === "yes") {
      return {
        fill: palette.primary,
        stroke: palette.text,
        lineWidth: 2.5,
        labelFill: palette.onPrimary,
        fontWeight: 700,
      };
    }
    if ((d.invalidations || 0) > 0) {
      return {
        fill: palette.errorTint,
        stroke: palette.error,
        lineWidth: 2.2,
        labelFill: palette.text,
        fontWeight: 600,
      };
    }
    if (d.tier === 0) {
      return { fill: palette.warningTint, stroke: palette.warning, lineWidth: 2, labelFill: palette.text, fontWeight: 600 };
    }
    if (d.tier === 1) {
      return { fill: palette.primaryTint, stroke: palette.primary, lineWidth: 2, labelFill: palette.text, fontWeight: 600 };
    }
    if (d.tier === 2) {
      return { fill: palette.infoTint, stroke: palette.info, lineWidth: 1.5, labelFill: palette.text, fontWeight: 500 };
    }
    return {
      fill: palette.accentTint,
      stroke: palette.accent,
      lineWidth: 1.5,
      lineDash: [5, 3],
      labelFill: palette.text,
      fontWeight: 500,
    };
  }

  function buildG6Data(nodes, edges, clusters, positions, palette, labels) {
    const g6Nodes = [
      ...nodes.map((n) => {
        const pos = positions.get(n.id) || { x: 0, y: 0 };
        if (n.isGroupCapsule) {
          const data = {
            cardLabel: n.title,
            tier: n.hierarchyTier || 2,
            invalidations: 0,
            isFocus: "no",
            isCluster: "yes",
            isGroupCapsule: "yes",
            groupId: n.groupId,
            parentId: n.parentId,
            isExpanded: n.isExpanded,
          };
          const c = nodeColors(data, palette);
          return {
            id: n.id,
            data,
            style: {
              x: pos.x,
              y: pos.y,
              size: [204, 42],
              radius: 20,
              fill: n.isExpanded ? palette.primaryTint : c.fill,
              stroke: c.stroke,
              lineWidth: c.lineWidth,
              lineDash: n.isExpanded ? undefined : c.lineDash,
              ports: [{ key: "in", placement: [0, 0.5] }, { key: "out", placement: [1, 0.5] }],
              labelText: n.title,
              labelFill: c.labelFill,
              labelFontSize: 12,
              labelFontWeight: 600,
              labelPlacement: "center",
              labelTextAlign: "center",
              labelTextBaseline: "middle",
              cursor: "pointer",
            },
          };
        }

        const data = {
          cardLabel: formatNodeCardLabel(n, labels),
          tier: Number.isFinite(n.hierarchyTier) ? n.hierarchyTier : 1,
          invalidations: n.invalidationCount || 0,
          isFocus: n.focus ? "yes" : "no",
          isCluster: "no",
          raw: n,
        };
        const c = nodeColors(data, palette);
        return {
          id: n.id,
          data,
          style: {
            x: pos.x,
            y: pos.y,
            size: n.focus ? [220, 72] : [208, 66],
            radius: 10,
            fill: c.fill,
            stroke: c.stroke,
            lineWidth: c.lineWidth,
            lineDash: c.lineDash || undefined,
            ports: [{ key: "in", placement: [0, 0.5] }, { key: "out", placement: [1, 0.5] }],
            shadowColor: "rgba(0, 0, 0, 0.14)",
            shadowBlur: n.focus ? 16 : 8,
            shadowOffsetY: 3,
            labelText: data.cardLabel,
            labelFill: c.labelFill,
            labelFontSize: 12,
            labelFontWeight: c.fontWeight,
            labelLineHeight: 17,
            labelMaxWidth: 186,
            labelWordWrap: true,
            labelPlacement: "center",
            labelTextAlign: "center",
            labelTextBaseline: "middle",
            cursor: "pointer",
          },
        };
      }),
      ...clusters.map((cl) => {
        const pos = positions.get(cl.id) || { x: 0, y: 0 };
        const data = {
          cardLabel: cl.label,
          tier: 9,
          invalidations: 0,
          isFocus: "no",
          isCluster: "yes",
          expandHref: cl.expandHref,
        };
        const c = nodeColors(data, palette);
        return {
          id: cl.id,
          data,
          style: {
            x: pos.x,
            y: pos.y,
            size: [204, 40],
            radius: 20,
            fill: c.fill,
            stroke: c.stroke,
            lineWidth: c.lineWidth,
            lineDash: c.lineDash,
            ports: [{ key: "in", placement: [0, 0.5] }, { key: "out", placement: [1, 0.5] }],
            labelText: cl.label,
            labelFill: c.labelFill,
            labelFontSize: 12,
            labelFontWeight: 600,
            labelPlacement: "center",
            labelTextAlign: "center",
            labelTextBaseline: "middle",
            cursor: "pointer",
          },
        };
      }),
    ];

    const g6Edges = [
      ...edges.map((e, i) => ({
        id: `e_${i}`,
        source: e.source,
        target: e.target,
        data: {
          kind: e.kind || "delegation",
          anchorLabel: Array.isArray(e.anchorArticles) && e.anchorArticles.length > 0 ? e.anchorArticles.join("、") : "",
        },
      })),
      ...clusters.map((c, i) => ({
        id: `ec_${i}`,
        source: c.parentId,
        target: c.id,
        data: {
          kind: "cluster_link",
          anchorLabel: "",
        },
      })),
    ];

    return { nodes: g6Nodes, edges: g6Edges };
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
    if (!container || container.dataset.graphReady === "1" || !window.G6 || typeof window.G6.Graph !== "function") {
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
    let showEdgeLabels = edges.length <= 16;
    let selectedNodeId = null;
    const expandedGroups = new Set();

    const palette = themePalette();

    function computeCurrentGraphData() {
      const { visibleNodes, visibleEdges } = buildVisibleGraphModel(nodes, edges, expandedGroups, labels);
      const { positions, clusters } = computeStufenbauPositions(visibleNodes, visibleEdges, container.clientWidth, labels);
      return buildG6Data(visibleNodes, visibleEdges, clusters, positions, themePalette(), labels);
    }

    const graphData = computeCurrentGraphData();

    const edgeStyleMapper = (d) => {
      const kind = d.data?.kind || "delegation";
      const isSub = kind === "sub_delegation";
      const isClusterLink = kind === "cluster_link" || kind === "group_link";
      const pal = themePalette();
      const usePorts = currentLayout === "stufenbau";
      return {
        stroke: isSub ? pal.warning : isClusterLink ? pal.primary : pal.borderControl,
        lineWidth: isClusterLink ? 1.5 : 1.8,
        lineDash: isSub ? [6, 4] : isClusterLink ? [4, 4] : undefined,
        endArrow: !isClusterLink,
        endArrowSize: 7,
        sourcePort: usePorts ? "out" : undefined,
        targetPort: usePorts ? "in" : undefined,
        opacity: isClusterLink ? 0.55 : 0.42,
        labelText: showEdgeLabels ? (d.data?.anchorLabel || "") : "",
        labelFill: pal.textSecondary,
        labelFontSize: 11,
        labelBackground: true,
        labelBackgroundFill: pal.surface,
        labelBackgroundOpacity: 0.9,
        labelPadding: [2, 5],
        labelBackgroundRadius: 4,
      };
    };

    const graph = new window.G6.Graph({
      container,
      data: graphData,
      autoResize: true,
      autoFit: "view",
      padding: 28,
      zoomRange: [0.25, 2.2],
      node: {
        type: "rect",
        state: {
          selected: {
            lineWidth: 3.2,
            stroke: palette.text,
            shadowColor: "rgba(0, 0, 0, 0.28)",
            shadowBlur: 18,
          },
          active: {
            lineWidth: 2.6,
            stroke: palette.primary,
          },
          inactive: {
            opacity: 0.28,
          },
        },
      },
      edge: {
        type: currentLayout === "stufenbau" ? "cubic-horizontal" : "quadratic",
        style: edgeStyleMapper,
        state: {
          active: {
            lineWidth: 2.8,
            stroke: palette.primary,
            opacity: 1,
            labelText: (d) => d.data?.anchorLabel || "",
          },
          inactive: {
            opacity: 0.14,
          },
        },
      },
      behaviors: [
        "drag-canvas",
        "zoom-canvas",
        "drag-element",
        {
          type: "hover-activate",
          enable: (e) => e.targetType === "node",
          degree: 1,
          state: "active",
          inactiveState: "inactive",
          onHover: (e) => {
            if (e?.view?.setCursor) e.view.setCursor("pointer");
          },
          onHoverEnd: (e) => {
            if (e?.view?.setCursor) e.view.setCursor("default");
            if (selectedNodeId) {
              selectNodeById(selectedNodeId);
            }
          },
        },
      ],
      plugins: [
        {
          type: "minimap",
          key: "ontology-minimap",
          size: [148, 92],
          position: "bottom-right",
          containerStyle: {
            background: palette.surface,
          },
          maskStyle: {
            fill: palette.primaryTint,
            stroke: palette.primary,
            fillOpacity: 0.35,
          },
        },
        {
          type: "tooltip",
          key: "ontology-tooltip",
          enable: (e) => e.targetType === "node",
          getContent: (e, items) => buildTooltipContent(items?.[0], labels),
        },
        {
          type: "fullscreen",
          key: "ontology-fullscreen",
        },
        {
          type: "contextmenu",
          key: "ontology-contextmenu",
          enable: (e) => e.targetType === "node",
          getItems: (e) => {
            const nodeId = e?.target?.id;
            const datum = nodeId ? graph.getNodeData(nodeId) : null;
            const raw = datum?.data?.raw;
            const items = [{ name: "檢視法學詳情", value: "inspect" }];
            if (raw?.focusHref && !raw.focus) {
              items.push({ name: "以此法為中心重建圖譜", value: "focus" });
            }
            if (raw?.structureHref) {
              items.push({ name: "檢視編章節條文", value: "structure" });
            }
            items.push({ name: "複製法規名稱", value: "copy" });
            return items;
          },
          onClick: (value, target, current) => {
            const nodeId = current?.id;
            if (!nodeId) return;
            const datum = graph.getNodeData(nodeId);
            const raw = datum?.data?.raw;
            if (value === "inspect") {
              selectNodeById(nodeId);
              if (host) host.dataset.inspectorOpen = "true";
            } else if (value === "focus" && raw?.focusHref) {
              window.location.assign(raw.focusHref);
            } else if (value === "structure" && raw?.structureHref) {
              window.location.assign(raw.structureHref);
            } else if (value === "copy") {
              const title = raw?.title || datum?.style?.labelText || nodeId;
              if (navigator.clipboard?.writeText) {
                navigator.clipboard.writeText(title).catch(() => {});
              }
            }
          },
        },
      ],
    });

    container._g6Instance = graph;

    async function fitViewport({ forceFitAll = false, focusTargetId = null } = {}) {
      try {
        await graph.fitView(undefined, false);
        if (!forceFitAll && graph.getZoom() < 0.72) {
          await graph.zoomTo(0.78, false);
          const target = focusTargetId
            || selectedNodeId
            || (nodes.find((n) => n.focus) || nodes.find((n) => n.hierarchyTier === 1) || nodes[0])?.id;
          if (target && graph.getNodeData(target)) {
            await graph.focusElement(target, false);
          }
        }
      } catch {
        // ignore transient viewport errors during unmount
      }
    }

    const swimlanesEl = host ? host.querySelector("[data-graph-swimlanes]") : null;
    const searchEl = host ? host.querySelector("[data-graph-search]") : null;

    let activeTierFilter = null;

    function syncFilterButtons() {
      if (!host) return;
      host.querySelectorAll("[data-graph-filter-tier]").forEach((btn) => {
        btn.setAttribute("aria-pressed", btn.dataset.graphFilterTier === activeTierFilter ? "true" : "false");
      });
    }

    function applyTierFilter(filterKey) {
      activeTierFilter = activeTierFilter === filterKey ? null : filterKey;
      syncFilterButtons();
      if (!activeTierFilter) {
        if (selectedNodeId && graph.getNodeData(selectedNodeId)) {
          selectNodeById(selectedNodeId);
        } else {
          clearSelectionStates();
        }
        return;
      }

      const matchedIds = new Set();
      graph.getNodeData().forEach((n) => {
        const raw = n.data?.raw;
        if (!raw) return;
        const tierStr = String(Number.isFinite(raw.hierarchyTier) ? raw.hierarchyTier : 1);
        const matches = activeTierFilter === "invalid"
          ? raw.invalidationCount > 0
          : tierStr === activeTierFilter;
        if (matches) matchedIds.add(n.id);
      });

      const states = {};
      graph.getNodeData().forEach((n) => {
        states[n.id] = matchedIds.has(n.id) ? ["active"] : ["inactive"];
      });
      graph.getEdgeData().forEach((e) => {
        states[e.id] = (matchedIds.has(e.source) || matchedIds.has(e.target)) ? ["active"] : ["inactive"];
      });
      graph.setElementState(states, false);
    }

    function renderSwimlanes() {
      if (!swimlanesEl) return;
      swimlanesEl.hidden = false;
      clearEl(swimlanesEl);
      const tierCounts = new Map();
      let invalidatedTotal = 0;
      nodes.forEach((n) => {
        const t = Number.isFinite(n.hierarchyTier) ? n.hierarchyTier : 1;
        tierCounts.set(t, (tierCounts.get(t) || 0) + 1);
        if (n.invalidationCount > 0) invalidatedTotal += 1;
      });
      const activeTiers = Array.from(tierCounts.keys()).sort((a, b) => a - b);
      activeTiers.forEach((tier, idx) => {
        if (idx > 0 && currentLayout === "stufenbau") {
          addEl(swimlanesEl, "span", "ep-hint", "→");
        }
        const pill = addEl(swimlanesEl, "button", "ep-swimlane-pill", `${tierName(tier, labels)} (${tierCounts.get(tier)})`);
        pill.type = "button";
        pill.dataset.tier = String(Math.min(tier, 3));
        pill.dataset.graphFilterTier = String(tier);
        pill.setAttribute("aria-pressed", activeTierFilter === String(tier) ? "true" : "false");
      });
      if (invalidatedTotal > 0) {
        const invPill = addEl(swimlanesEl, "button", "ep-swimlane-pill", `⚠ ${invalidatedTotal}`);
        invPill.type = "button";
        invPill.dataset.graphFilterTier = "invalid";
        invPill.setAttribute("aria-pressed", activeTierFilter === "invalid" ? "true" : "false");
      }
      if (swimlanesEl.dataset.graphSummary) {
        addEl(swimlanesEl, "span", "ep-swimlane-summary", swimlanesEl.dataset.graphSummary);
      }
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
      renderSwimlanes();
    }

    function clearSelectionStates() {
      const states = {};
      graph.getNodeData().forEach((n) => { states[n.id] = []; });
      graph.getEdgeData().forEach((e) => { states[e.id] = []; });
      graph.setElementState(states, false);
    }

    function selectNodeById(nodeId) {
      const nodeDatum = graph.getNodeData(nodeId);
      if (!nodeDatum) {
        selectedNodeId = null;
        clearSelectionStates();
        renderInspector(inspectorEl, null, nodes, edges, labels);
        return;
      }
      if (nodeDatum.data?.isCluster === "yes") return;

      selectedNodeId = nodeId;
      const connectedEdgeIds = new Set();
      const neighborNodeIds = new Set([nodeId]);
      graph.getEdgeData().forEach((e) => {
        if (e.source === nodeId || e.target === nodeId) {
          connectedEdgeIds.add(e.id);
          neighborNodeIds.add(e.source);
          neighborNodeIds.add(e.target);
        }
      });

      const states = {};
      graph.getNodeData().forEach((n) => {
        if (n.id === nodeId) states[n.id] = ["selected"];
        else if (neighborNodeIds.has(n.id)) states[n.id] = ["active"];
        else states[n.id] = ["inactive"];
      });
      graph.getEdgeData().forEach((e) => {
        states[e.id] = connectedEdgeIds.has(e.id) ? ["active"] : ["inactive"];
      });
      graph.setElementState(states, false);
      renderInspector(inspectorEl, nodeDatum.data?.raw || null, nodes, edges, labels);
    }

    async function applyLayout(mode, focusTargetId = null) {
      currentLayout = mode;
      graph.setEdge({
        type: mode === "concentric" ? "quadratic" : "cubic-horizontal",
        style: edgeStyleMapper,
      });
      if (mode === "concentric") {
        const focusNode = nodes.find((n) => n.focus) || nodes.find((n) => n.hierarchyTier === 1) || nodes[0];
        graph.setLayout({
          type: "concentric",
          preventOverlap: true,
          nodeSize: [216, 76],
          minNodeSpacing: 44,
          sortBy: (d) => {
            if (d.data?.isCluster === "yes") return 1;
            const raw = d.data?.raw;
            if (!raw) return 2;
            if (focusNode && raw.id === focusNode.id) return 100;
            if (raw.hierarchyTier <= 1) return 50;
            return 10;
          },
        });
        await graph.layout();
      } else if (mode === "indented") {
        const { visibleNodes, visibleEdges } = buildVisibleGraphModel(nodes, edges, expandedGroups, labels);
        const positions = computeIndentedPositions(visibleNodes, visibleEdges);
        const updates = [];
        graph.getNodeData().forEach((n) => {
          const p = positions.get(n.id);
          if (p) updates.push({ id: n.id, style: { x: p.x, y: p.y } });
        });
        if (updates.length > 0) {
          graph.updateNodeData(updates);
          await graph.draw();
        }
      } else {
        const { visibleNodes, visibleEdges } = buildVisibleGraphModel(nodes, edges, expandedGroups, labels);
        const updated = computeStufenbauPositions(visibleNodes, visibleEdges, container.clientWidth, labels);
        const updates = [];
        graph.getNodeData().forEach((n) => {
          const p = updated.positions.get(n.id);
          if (p) updates.push({ id: n.id, style: { x: p.x, y: p.y } });
        });
        if (updates.length > 0) {
          graph.updateNodeData(updates);
          await graph.draw();
        }
      }
      await fitViewport({ focusTargetId });
      updateToolbarState();
    }

    graph.on("node:click", async (evt) => {
      const id = evt.target?.id;
      if (!id) return;
      const nodeDatum = graph.getNodeData(id);
      if (nodeDatum?.data?.isGroupCapsule === "yes") {
        const groupId = nodeDatum.data.groupId;
        const parentId = nodeDatum.data.parentId;
        if (expandedGroups.has(groupId)) {
          expandedGroups.delete(groupId);
        } else {
          expandedGroups.add(groupId);
        }
        graph.setData(computeCurrentGraphData());
        await graph.render();
        await applyLayout(currentLayout, groupId || parentId);
        if (parentId && graph.getNodeData(parentId)) {
          selectNodeById(parentId);
        }
        return;
      }
      if (nodeDatum?.data?.isCluster === "yes") {
        const href = nodeDatum.data?.expandHref;
        if (href) window.location.assign(href);
        return;
      }
      selectNodeById(id);
      if (host) host.dataset.inspectorOpen = "true";
    });

    graph.on("node:dblclick", (evt) => {
      const id = evt.target?.id;
      if (!id) return;
      const raw = graph.getNodeData(id)?.data?.raw;
      if (raw?.focusHref) {
        window.location.assign(raw.focusHref);
      }
    });

    graph.on("canvas:click", () => {
      selectedNodeId = null;
      if (host) host.dataset.inspectorOpen = "false";
      clearSelectionStates();
      const initial = nodes.find((n) => n.focus) || nodes[0] || null;
      renderInspector(inspectorEl, initial, nodes, edges, labels);
    });

    if (searchEl) {
      let searchTimer = 0;
      searchEl.addEventListener("input", () => {
        window.clearTimeout(searchTimer);
        searchTimer = window.setTimeout(async () => {
          const term = (searchEl.value || "").trim().toLowerCase();
          if (!term) {
            clearSelectionStates();
            return;
          }
          const match = nodes.find((n) =>
            (n.title || "").toLowerCase().includes(term)
            || (n.localId || "").toLowerCase().includes(term),
          );
          if (!match) return;

          if (!graph.getNodeData(match.id)) {
            const parentEdge = edges.find((e) => e.target === match.id);
            const docType = (match.documentType || labels.otherDocType || "其他子法").trim();
            if (parentEdge) {
              expandedGroups.add(`grp__${parentEdge.source}__${docType}`);
              graph.setData(computeCurrentGraphData());
              await graph.render();
              await applyLayout(currentLayout, match.id);
            }
          }

          if (graph.getNodeData(match.id)) {
            selectNodeById(match.id);
            if (graph.getZoom() < 0.72) {
              await graph.zoomTo(0.82);
            }
            await graph.focusElement(match.id).catch(() => {});
          }
        }, 140);
      });
    }

    graph.render().then(() => {
      const initialNode = nodes.find((n) => n.focus) || nodes.find((n) => n.hierarchyTier === 1) || nodes[0] || null;
      if (initialNode) {
        selectNodeById(initialNode.id);
      }
      if (currentLayout === "concentric") {
        applyLayout("concentric");
      } else {
        fitViewport();
        updateToolbarState();
      }
    }).catch(() => {});

    if (host && host.dataset.graphBound !== "1") {
      host.dataset.graphBound = "1";
      let isFullscreen = false;

      host.addEventListener("click", (evt) => {
        const filterBtn = evt.target.closest("[data-graph-filter-tier]");
        if (filterBtn) {
          applyTierFilter(filterBtn.dataset.graphFilterTier);
          return;
        }
        const closeBtn = evt.target.closest("[data-inspector-close]");
        if (closeBtn) {
          host.dataset.inspectorOpen = "false";
          return;
        }
        const btn = evt.target.closest("[data-graph-action]");
        if (!btn) return;
        const activeGraph = container._g6Instance || graph;
        const action = btn.dataset.graphAction;
        if (action === "fit") fitViewport({ forceFitAll: true });
        else if (action === "zoom-in") activeGraph.zoomBy(1.2).catch(() => {});
        else if (action === "zoom-out") activeGraph.zoomBy(0.84).catch(() => {});
        else if (action === "fullscreen") {
          const fsPlugin = activeGraph.getPluginInstance?.("ontology-fullscreen");
          if (fsPlugin) {
            if (isFullscreen || document.fullscreenElement) {
              fsPlugin.exit();
              isFullscreen = false;
            } else {
              fsPlugin.request();
              isFullscreen = true;
            }
            btn.setAttribute("aria-pressed", isFullscreen ? "true" : "false");
          }
        }
        else if (action === "layout-stufenbau") applyLayout("stufenbau");
        else if (action === "layout-concentric") applyLayout("concentric");
        else if (action === "layout-indented") applyLayout("indented");
        else if (action === "toggle-edge-labels") {
          showEdgeLabels = !showEdgeLabels;
          activeGraph.setEdge({
            type: currentLayout === "concentric" ? "quadratic" : "cubic-horizontal",
            style: edgeStyleMapper,
          });
          activeGraph.draw().catch(() => {});
          updateToolbarState();
        }
      });

      document.addEventListener("keydown", (evt) => {
        if (!container.isConnected) return;
        const tag = evt.target?.tagName;
        if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || evt.target?.isContentEditable) {
          return;
        }
        const activeGraph = container._g6Instance || graph;
        if (evt.key === "Escape") {
          host.dataset.inspectorOpen = "false";
          selectedNodeId = null;
          activeTierFilter = null;
          syncFilterButtons();
          clearSelectionStates();
        } else if (evt.key === "0") {
          fitViewport({ forceFitAll: true });
        } else if (evt.key === "+" || evt.key === "=") {
          activeGraph.zoomBy(1.2).catch(() => {});
        } else if (evt.key === "-") {
          activeGraph.zoomBy(0.84).catch(() => {});
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
            graph.resize();
            fitViewport();
          }
        });
      });
      observer.observe(container);
    }

    if (window.matchMedia) {
      const mql = window.matchMedia("(prefers-color-scheme: dark)");
      const refreshTheme = () => {
        const nextPalette = themePalette(true);
        const nodeUpdates = graph.getNodeData().map((n) => {
          const c = nodeColors(n.data || {}, nextPalette);
          return {
            id: n.id,
            style: {
              fill: c.fill,
              stroke: c.stroke,
              labelFill: c.labelFill,
            },
          };
        });
        graph.updateNodeData(nodeUpdates);
        graph.setEdge({ type: "quadratic", style: edgeStyleMapper });
        graph.draw().catch(() => {});
        if (selectedNodeId) selectNodeById(selectedNodeId);
      };
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

  function initDrawerToggles() {
    const root = document.querySelector("[data-workbench-root]");
    if (!root || root.dataset.drawerBound === "1") return;
    root.dataset.drawerBound = "1";

    const setOpen = (open) => {
      root.dataset.catalogOpen = open ? "true" : "false";
      document.querySelectorAll('[data-workbench-toggle="catalog"]').forEach((btn) => {
        btn.setAttribute("aria-expanded", open ? "true" : "false");
      });
    };

    if (!root.dataset.catalogOpen) {
      setOpen(false);
    }

    document.querySelectorAll('[data-workbench-toggle="catalog"]').forEach((btn) => {
      btn.addEventListener("click", () => {
        setOpen(root.dataset.catalogOpen !== "true");
      });
    });

    document.querySelectorAll('[data-workbench-close="catalog"]').forEach((el) => {
      el.addEventListener("click", () => setOpen(false));
    });

    document.addEventListener("keydown", (evt) => {
      if (evt.key === "Escape" && root.dataset.catalogOpen === "true") {
        setOpen(false);
      }
    });
  }

  function boot() {
    document.querySelectorAll("[data-ontology-graph]").forEach(initGraph);
    initPresets();
    initDrawerToggles();
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
