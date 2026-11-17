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

  function parseRgb(str) {
    if (!str || typeof str !== "string") return null;
    const s = str.trim();
    const rgbMatch = s.match(/^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,/\s]+([\d.]+%?))?\s*\)$/i);
    if (rgbMatch) {
      return {
        r: Math.round(Number(rgbMatch[1])),
        g: Math.round(Number(rgbMatch[2])),
        b: Math.round(Number(rgbMatch[3])),
      };
    }
    const srgbMatch = s.match(/^color\(\s*srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)/i);
    if (srgbMatch) {
      return {
        r: Math.round(Math.min(1, Math.max(0, Number(srgbMatch[1]))) * 255),
        g: Math.round(Math.min(1, Math.max(0, Number(srgbMatch[2]))) * 255),
        b: Math.round(Math.min(1, Math.max(0, Number(srgbMatch[3]))) * 255),
      };
    }
    const hex = s.replace(/^#/, "");
    if (/^[0-9a-f]{3}$/i.test(hex)) {
      return {
        r: parseInt(hex[0] + hex[0], 16),
        g: parseInt(hex[1] + hex[1], 16),
        b: parseInt(hex[2] + hex[2], 16),
      };
    }
    if (/^[0-9a-f]{6}$/i.test(hex)) {
      return {
        r: parseInt(hex.slice(0, 2), 16),
        g: parseInt(hex.slice(2, 4), 16),
        b: parseInt(hex.slice(4, 6), 16),
      };
    }
    return null;
  }

  function rgbString(c) {
    return `rgb(${c.r}, ${c.g}, ${c.b})`;
  }

  function mixRgb(fgCss, bgCss, weight) {
    const fg = parseRgb(fgCss);
    const bg = parseRgb(bgCss);
    if (!fg || !bg) return fgCss || bgCss;
    const w = Math.max(0, Math.min(1, weight));
    return rgbString({
      r: Math.round(fg.r * w + bg.r * (1 - w)),
      g: Math.round(fg.g * w + bg.g * (1 - w)),
      b: Math.round(fg.b * w + bg.b * (1 - w)),
    });
  }

  function resolveColor(expr, fallback) {
    const el = probeEl();
    if (!el) {
      const fb = parseRgb(fallback);
      return fb ? rgbString(fb) : fallback;
    }
    el.style.color = "";
    el.style.color = expr;
    const computed = getComputedStyle(el).color;
    const parsed = parseRgb(computed) || parseRgb(fallback);
    return parsed ? rgbString(parsed) : (computed || fallback);
  }

  function charDisplayUnits(ch) {
    const code = ch.codePointAt(0) || 0;
    return code > 0x2E7F ? 1 : 0.56;
  }

  function wrapTitleLines(text, maxUnits = 17.5, maxLines = 2) {
    const str = String(text || "").trim();
    if (!str) return "";
    const lines = [];
    let current = "";
    let units = 0;
    const chars = Array.from(str);
    for (let i = 0; i < chars.length; i++) {
      const ch = chars[i];
      if (ch === "\n") {
        lines.push(current.trimEnd());
        current = "";
        units = 0;
        if (lines.length >= maxLines) break;
        continue;
      }
      const u = charDisplayUnits(ch);
      if (units + u > maxUnits && current.length > 0) {
        const spaceIdx = current.lastIndexOf(" ");
        if (spaceIdx > current.length * 0.45 && /[\x20-\x7E]$/.test(ch)) {
          lines.push(current.slice(0, spaceIdx).trimEnd());
          const rest = current.slice(spaceIdx + 1) + ch;
          current = rest;
          units = Array.from(rest).reduce((acc, c) => acc + charDisplayUnits(c), 0);
        } else {
          lines.push(current.trimEnd());
          current = ch === " " ? "" : ch;
          units = ch === " " ? 0 : u;
        }
        if (lines.length === maxLines - 1) {
          let tail = current;
          let tailUnits = units;
          let j = i + 1;
          while (j < chars.length && tailUnits + charDisplayUnits(chars[j]) <= maxUnits - 1.1) {
            tail += chars[j];
            tailUnits += charDisplayUnits(chars[j]);
            j++;
          }
          if (j < chars.length) {
            tail = `${tail.trimEnd()}…`;
          }
          lines.push(tail);
          return lines.join("\n");
        }
      } else {
        current += ch;
        units += u;
      }
    }
    if (current && lines.length < maxLines) {
      lines.push(current.trimEnd());
    }
    return lines.join("\n");
  }

  let palantirNodeRegistered = false;
  function ensurePalantirNodeRegistered() {
    if (palantirNodeRegistered) return true;
    if (!window.G6 || typeof window.G6.register !== "function" || typeof window.G6.Rect !== "function" || !window.G6.Label || !window.G6.ExtensionCategory) {
      return false;
    }

    class PalantirLawNode extends window.G6.Rect {
      render(attributes = this.parsedAttributes, container = this) {
        super.render(attributes, container);
        const RectShape = this.getShape("key")?.constructor;
        const LabelShape = window.G6.Label;
        if (!RectShape || !LabelShape) return;

        const [width, height] = this.getSize(attributes);
        const left = -width / 2;
        const top = -height / 2;
        const r = Number.isFinite(attributes.radius) ? attributes.radius : 10;
        const innerR = Math.max(0, r - 1);
        const nodeOpacity = attributes.opacity ?? 1;
        const headerHeight = 26;
        const accentColor = attributes.accentColor || attributes.stroke;

        this.upsert(
          "header-bg",
          RectShape,
          {
            x: left + 1,
            y: top + 1,
            width: width - 2,
            height: headerHeight,
            radius: [innerR, innerR, 0, 0],
            fill: attributes.headerFill || attributes.fill,
            opacity: nodeOpacity,
            zIndex: 1,
          },
          container,
        );

        this.upsert(
          "header-divider",
          RectShape,
          {
            x: left + 1,
            y: top + headerHeight + 1,
            width: width - 2,
            height: 1,
            fill: accentColor,
            opacity: nodeOpacity * 0.42,
            zIndex: 2,
          },
          container,
        );

        this.upsert(
          "accent-rail",
          RectShape,
          {
            x: left + 1,
            y: top + 1,
            width: 5,
            height: height - 2,
            radius: [innerR, 0, 0, innerR],
            fill: accentColor,
            opacity: nodeOpacity,
            zIndex: 3,
          },
          container,
        );

        this.upsert(
          "type-dot",
          RectShape,
          {
            x: left + 13,
            y: top + 11,
            width: 6,
            height: 6,
            radius: 3,
            fill: accentColor,
            opacity: nodeOpacity,
            zIndex: 4,
          },
          container,
        );

        const typeLabel = attributes.typeLabel || "";
        this.upsert(
          "type-tag",
          LabelShape,
          typeLabel
            ? {
                x: left + 23,
                y: top + headerHeight / 2 + 1,
                text: typeLabel,
                fill: attributes.headerLabelFill || accentColor,
                fontSize: 10.5,
                fontWeight: 700,
                textAlign: "left",
                textBaseline: "middle",
                maxLines: 1,
                wordWrap: false,
                wordWrapWidth: width - 118,
                background: false,
                opacity: nodeOpacity,
                zIndex: 4,
              }
            : false,
          container,
        );

        const metricLabel = attributes.metricLabel || "";
        this.upsert(
          "metric-tag",
          LabelShape,
          metricLabel
            ? {
                x: left + width - 11,
                y: top + headerHeight / 2 + 1,
                text: metricLabel,
                fill: attributes.metricFill || attributes.headerLabelFill || accentColor,
                fontSize: 10,
                fontWeight: 600,
                textAlign: "right",
                textBaseline: "middle",
                maxLines: 1,
                wordWrap: false,
                wordWrapWidth: 98,
                background: false,
                opacity: nodeOpacity,
                zIndex: 4,
              }
            : false,
          container,
        );

        const bodyTitle = attributes.bodyTitle || "";
        const bodyCenterY = top + headerHeight + 1 + (height - headerHeight - 2) / 2;
        this.upsert(
          "title-text",
          LabelShape,
          bodyTitle
            ? {
                x: left + 14,
                y: bodyCenterY,
                text: bodyTitle,
                fill: attributes.bodyLabelFill || attributes.labelFill,
                fontSize: 12.5,
                fontWeight: attributes.titleFontWeight || 600,
                lineHeight: 17,
                textAlign: "left",
                textBaseline: "middle",
                maxLines: 2,
                wordWrap: false,
                wordWrapWidth: width - 26,
                background: false,
                opacity: nodeOpacity,
                zIndex: 4,
              }
            : false,
          container,
        );
      }
    }

    window.G6.register(window.G6.ExtensionCategory.NODE, "palantir-law-node", PalantirLawNode);
    palantirNodeRegistered = true;
    return true;
  }

  let envciteThemeRegistered = false;
  function ensureEnvciteThemeRegistered() {
    if (envciteThemeRegistered) return true;
    if (!window.G6 || typeof window.G6.register !== "function" || !window.G6.ExtensionCategory?.THEME) {
      return false;
    }
    window.G6.register(window.G6.ExtensionCategory.THEME, "envcite", {
      background: "transparent",
      node: {
        style: {
          halo: false,
        },
        state: {},
        animation: {
          enter: "fade",
          exit: "fade",
          show: "fade",
          hide: "fade",
          expand: "node-expand",
          collapse: "node-collapse",
          update: [{ fields: ["x", "y", "fill", "stroke"] }],
          translate: [{ fields: ["x", "y"] }],
        },
      },
      edge: {
        style: {
          halo: false,
          increasedLineWidthForHitTesting: 2,
        },
        state: {},
        animation: {
          enter: "fade",
          exit: "fade",
          expand: "path-in",
          collapse: "path-out",
          show: "fade",
          hide: "fade",
          update: [{ fields: ["sourceNode", "targetNode"] }, { fields: ["stroke"], shape: "key" }],
          translate: [{ fields: ["sourceNode", "targetNode"] }],
        },
      },
      combo: {
        style: {},
        state: {},
      },
    });
    envciteThemeRegistered = true;
    return true;
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
    const effectiveDark = isDark ? themeAttr !== "light" : themeAttr === "dark";
    const bg = resolveColor("var(--ep-bg)", effectiveDark ? "#0B1120" : "#F8FAFC");
    const surface = resolveColor("var(--ep-surface)", effectiveDark ? "#111827" : "#FFFFFF");
    const surfaceRaised = resolveColor("var(--ep-surface-raised)", effectiveDark ? "#1E293B" : "#FFFFFF");
    const surfaceMuted = resolveColor("var(--ep-surface-muted)", effectiveDark ? "#273449" : "#F1F5F9");
    const primary = resolveColor("var(--ep-primary)", effectiveDark ? "#60A5FA" : "#005FC4");
    const success = resolveColor("var(--ep-success)", effectiveDark ? "#34D399" : "#047857");
    const info = resolveColor("var(--ep-info)", effectiveDark ? "#A78BFA" : "#4338CA");
    const warning = resolveColor("var(--ep-warning)", effectiveDark ? "#FBBF24" : "#A14A07");
    const error = resolveColor("var(--ep-error)", effectiveDark ? "#F87171" : "#B91C1C");
    const accent = resolveColor("var(--ep-accent)", effectiveDark ? "#2DD4BF" : "#0D9488");
    const borderSubtle = resolveColor("var(--ep-border-subtle)", effectiveDark ? "#334155" : "#E2E8F0");
    const borderControl = resolveColor("var(--ep-border-control)", effectiveDark ? "#94A3B8" : "#64748B");
    const text = resolveColor("var(--ep-text)", effectiveDark ? "#F8FAFC" : "#0F172A");
    const textSecondary = resolveColor("var(--ep-text-secondary)", effectiveDark ? "#CBD5E1" : "#475569");
    const onPrimary = resolveColor("var(--ep-on-primary)", effectiveDark ? "#0B1120" : "#FFFFFF");

    cachedPaletteValue = {
      isDark: effectiveDark,
      bg,
      text,
      textSecondary,
      surface,
      surfaceRaised,
      surfaceMuted,
      borderSubtle,
      borderControl,
      primary,
      onPrimary,
      primaryTint: mixRgb(primary, surfaceRaised, effectiveDark ? 0.14 : 0.06),
      primaryHeader: mixRgb(primary, surfaceRaised, effectiveDark ? 0.28 : 0.14),
      success,
      successTint: mixRgb(success, surfaceRaised, effectiveDark ? 0.14 : 0.06),
      successHeader: mixRgb(success, surfaceRaised, effectiveDark ? 0.28 : 0.14),
      info,
      infoTint: mixRgb(info, surfaceRaised, effectiveDark ? 0.14 : 0.06),
      warning,
      warningTint: mixRgb(warning, surfaceRaised, effectiveDark ? 0.15 : 0.07),
      warningHeader: mixRgb(warning, surfaceRaised, effectiveDark ? 0.29 : 0.15),
      error,
      errorTint: mixRgb(error, surfaceRaised, effectiveDark ? 0.16 : 0.07),
      errorHeader: mixRgb(error, surfaceRaised, effectiveDark ? 0.29 : 0.15),
      accent,
      accentTint: mixRgb(accent, surfaceRaised, effectiveDark ? 0.14 : 0.06),
      accentHeader: mixRgb(accent, surfaceRaised, effectiveDark ? 0.28 : 0.14),
    };
    return cachedPaletteValue;
  }

  function truncateTitle(title, maxChars) {
    const raw = (title || "").trim();
    if (raw.length <= maxChars) return raw;
    return `${raw.slice(0, maxChars - 1)}…`;
  }

  function formatNodeCardLabel(n, labels) {
    const shortTitle = truncateTitle(n.title, 34);
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

    const shouldGroup = nodes.length > 12;
    const hasFocus = nodes.some((n) => n.focus);
    const minTotalLeaves = hasFocus && nodes.length <= 22 ? 4 : 2;
    const hiddenNodeIds = new Set();
    const groupMembership = new Map();
    const childGroupMap = new Map();
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

        if (leafChildren.length < minTotalLeaves) return;

        const parentTier = Number.isFinite(parent.hierarchyTier) ? parent.hierarchyTier : 1;
        const addGroupCapsule = (groupId, displayType, groupItems) => {
          const isExpanded = expandedGroups.has(groupId);
          const expandTxt = labels.expandGroupSuffix || "點擊展開";
          const collapseTxt = labels.collapseGroupSuffix || "收合";
          groupNodes.push({
            id: groupId,
            title: `${isExpanded ? "▼" : "▶"} ${displayType} (${groupItems.length}) · ${isExpanded ? collapseTxt : expandTxt}`,
            documentType: displayType,
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
          if (!childGroupMap.has(parent.id)) childGroupMap.set(parent.id, []);
          childGroupMap.get(parent.id).push({
            groupId,
            count: groupItems.length,
            isExpanded,
          });
          groupItems.forEach((item) => {
            groupMembership.set(item.id, groupId);
            if (!isExpanded) hiddenNodeIds.add(item.id);
          });
        };

        const byDocType = new Map();
        leafChildren.forEach((child) => {
          const docType = (child.documentType || labels.otherDocType || "其他授權子法").trim();
          if (!byDocType.has(docType)) byDocType.set(docType, []);
          byDocType.get(docType).push(child);
        });

        const remainingSingletons = [];
        byDocType.forEach((groupItems, docType) => {
          if (groupItems.length >= 2) {
            addGroupCapsule(`grp__${parent.id}__${docType}`, docType, groupItems);
          } else if (groupItems.length === 1) {
            remainingSingletons.push(groupItems[0]);
          }
        });

        if (remainingSingletons.length >= 2) {
          addGroupCapsule(
            `grp__${parent.id}__OTHER`,
            labels.otherDocType || "其他授權子法",
            remainingSingletons,
          );
        }
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

    return { visibleNodes, visibleEdges, groupMembership, childGroupMap };
  }

  function computeStufenbauPositions(nodes, edges, containerWidth, labels) {
    if (!nodes || nodes.length === 0) return { positions: new Map(), clusters: [] };

    const clusters = nodes
      .filter((n) => !n.isGroupCapsule && n.truncatedChildren > 0 && n.expandHref)
      .map((n) => {
        const parentTier = Number.isFinite(n.hierarchyTier) ? n.hierarchyTier : 1;
        return {
          id: `cluster__${n.id}`,
          parentId: n.id,
          hierarchyTier: Math.min(3, parentTier + 1),
          localId: `~cluster__${n.localId || n.id}`,
          label: `+${n.truncatedChildren} ${labels.clusterSuffix || "部授權法規（點擊展開）"}`,
          expandHref: n.expandHref,
          isClusterStub: true,
        };
      });

    const layoutNodes = [...nodes, ...clusters];
    const layoutEdges = [
      ...edges,
      ...clusters.map((c) => ({ source: c.parentId, target: c.id })),
    ];

    const byId = new Map(layoutNodes.map((n) => [n.id, n]));
    const parentsMap = new Map(layoutNodes.map((n) => [n.id, []]));
    const childrenMap = new Map(layoutNodes.map((n) => [n.id, []]));
    layoutEdges.forEach((e) => {
      if (byId.has(e.source) && byId.has(e.target)) {
        parentsMap.get(e.target).push(e.source);
        childrenMap.get(e.source).push(e.target);
      }
    });

    const tiers = Array.from(
      new Set(layoutNodes.map((n) => (Number.isFinite(n.hierarchyTier) ? n.hierarchyTier : 1))),
    ).sort((a, b) => a - b);

    const subColGapX = 304;
    const baseTierGapX = 396;
    const rowGapY = 108;
    const positions = new Map();
    const yLookup = new Map();
    const tierBuckets = new Map();

    // Precompute column widths per tier so dense tiers wrap into 2-3 sub-columns instead of a 4000px tower.
    const tierLayouts = tiers.map((tier) => {
      const tierNodes = layoutNodes.filter(
        (n) => (Number.isFinite(n.hierarchyTier) ? n.hierarchyTier : 1) === tier,
      );
      const subCols = tierNodes.length > 7 ? Math.min(3, Math.ceil(tierNodes.length / 7)) : 1;
      return { tier, tierNodes, subCols, width: (subCols - 1) * subColGapX };
    });

    const totalSpanX = tierLayouts.reduce((sum, tl, i) => sum + tl.width + (i > 0 ? baseTierGapX : 0), 0);
    let cursorX = -totalSpanX / 2;

    tierLayouts.forEach(({ tier, tierNodes, subCols, width }) => {
      const kindRank = (n) => (n.isClusterStub ? 2 : n.isGroupCapsule ? 1 : 0);
      tierNodes.sort((a, b) => {
        const pA = parentsMap.get(a.id).filter((pid) => yLookup.has(pid));
        const pB = parentsMap.get(b.id).filter((pid) => yLookup.has(pid));
        const avgA = pA.length > 0 ? pA.reduce((sum, pid) => sum + yLookup.get(pid), 0) / pA.length : 0;
        const avgB = pB.length > 0 ? pB.reduce((sum, pid) => sum + yLookup.get(pid), 0) / pB.length : 0;
        if (Math.abs(avgA - avgB) > 1) return avgA - avgB;
        const rankDiff = kindRank(a) - kindRank(b);
        if (rankDiff !== 0) return rankDiff;
        if (Boolean(a.focus) !== Boolean(b.focus)) return a.focus ? -1 : 1;
        if ((b.totalChildren || 0) !== (a.totalChildren || 0)) return (b.totalChildren || 0) - (a.totalChildren || 0);
        return (a.localId || a.id || "").localeCompare(b.localId || b.id || "");
      });

      const rowsPerCol = Math.ceil(tierNodes.length / subCols);
      const totalHeight = (rowsPerCol - 1) * rowGapY;
      const startY = -totalHeight / 2;
      const tierCenterX = cursorX + width / 2;
      tierBuckets.set(tier, { x: tierCenterX, subCols, nodes: tierNodes });

      tierNodes.forEach((n, idx) => {
        const colIdx = subCols > 1 ? Math.floor(idx / rowsPerCol) : 0;
        const rowIdx = subCols > 1 ? idx % rowsPerCol : idx;
        const x = cursorX + colIdx * subColGapX;
        const y = startY + rowIdx * rowGapY;
        positions.set(n.id, { x, y });
        yLookup.set(n.id, y);
      });

      cursorX += width + baseTierGapX;
    });

    // Bottom-up barycenter pass only for single-column tiers so multi-column grids keep neat alignment.
    for (let i = tiers.length - 2; i >= 0; i--) {
      const bucket = tierBuckets.get(tiers[i]);
      if (!bucket || bucket.nodes.length === 0 || bucket.subCols > 1) continue;
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
      return String(a?.title || a?.label || aId).localeCompare(String(b?.title || b?.label || bId), "zh-Hant");
    };

    const roots = nodes
      .filter((n) => (inDegree.get(n.id) || 0) === 0)
      .map((n) => n.id)
      .sort(byTierThenTitle);

    const visited = new Set();
    const subtrees = [];

    function collectSubtree(id, depth, acc) {
      if (visited.has(id)) return;
      visited.add(id);
      acc.push({ id, depth });
      const children = (childrenByParent.get(id) || []).slice().sort(byTierThenTitle);
      for (const childId of children) {
        collectSubtree(childId, depth + 1, acc);
      }
    }

    for (const rootId of roots) {
      if (visited.has(rootId)) continue;
      const acc = [];
      collectSubtree(rootId, 0, acc);
      if (acc.length > 0) subtrees.push(acc);
    }
    for (const n of nodes) {
      if (visited.has(n.id)) continue;
      const acc = [];
      collectSubtree(n.id, 0, acc);
      if (acc.length > 0) subtrees.push(acc);
    }

    const totalRows = subtrees.reduce((sum, s) => sum + s.length, 0);
    const numCols = subtrees.length >= 3 && totalRows > 22
      ? 3
      : subtrees.length >= 2 && totalRows > 8
        ? 2
        : 1;

    const colSlots = Array.from({ length: numCols }, () => []);
    const colRows = new Array(numCols).fill(0);
    for (const subtree of subtrees) {
      let targetCol = 0;
      for (let c = 1; c < numCols; c++) {
        if (colRows[c] < colRows[targetCol]) targetCol = c;
      }
      const gap = colSlots[targetCol].length > 0 ? 0.25 : 0;
      const startRow = colRows[targetCol] + gap;
      colSlots[targetCol].push({ subtree, startRow });
      colRows[targetCol] = startRow + subtree.length;
    }

    const indentStepX = 52;
    const colGapX = 460;
    const rowGapY = 104;
    const maxColRows = Math.max(...colRows, 1);
    const startX = -((numCols - 1) * colGapX) / 2;
    const startY = -((maxColRows - 1) * rowGapY) / 2;

    for (let c = 0; c < numCols; c++) {
      const baseX = startX + c * colGapX;
      for (const entry of colSlots[c]) {
        entry.subtree.forEach((item, idx) => {
          positions.set(item.id, {
            x: baseX + Math.min(item.depth, 3) * indentStepX,
            y: startY + (entry.startRow + idx) * rowGapY,
          });
        });
      }
    }

    return positions;
  }

  function buildTooltipContent(item, labels) {
    const raw = item?.data?.raw;
    const root = document.createElement("div");
    root.className = "ep-graph-tooltip";
    if (!raw) {
      const fallbackTitle = item?.data?.cardLabel || item?.style?.labelText || item?.data?.label || item?.id || "";
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

  function formatCardSlots(n, labels) {
    const tier = Number.isFinite(n.hierarchyTier) ? n.hierarchyTier : 1;
    const tierTag = tier <= 0 ? "T0" : tier === 1 ? "T1" : tier === 2 ? "T2" : "T3";
    const defaultType = tier <= 0
      ? (labels.tier0Short || "憲法")
      : tier === 1
        ? (labels.tier1Short || "法律")
        : tier === 2
          ? (labels.tier2Short || "法規命令")
          : (labels.tier3Short || "再授權");
    const docLabel = n.documentType || defaultType;
    const prefix = n.invalidationCount > 0 ? "⚠ " : n.focus ? "★ " : "";
    const typeLabel = `${prefix}${tierTag} · ${docLabel}`;

    const metrics = [];
    if (n.invalidationCount > 0) metrics.push(`⚠ ${n.invalidationCount}`);
    if (n.articleCount > 0) metrics.push(`${n.articleCount} 條`);
    if (n.totalChildren > 0 && metrics.length < 2) metrics.push(`${n.totalChildren} ${labels.childMarker || "子法"}`);
    if (n.guidanceCount > 0 && metrics.length < 2) metrics.push(`${labels.guidanceMarker || "函釋"} ${n.guidanceCount}`);

    return {
      typeLabel,
      metricLabel: metrics.join(" · "),
      bodyTitle: wrapTitleLines(n.title || n.id, 17.5, 2),
    };
  }

  function nodeColors(d, palette) {
    if (d.isCluster === "yes") {
      if (d.isGroupCapsule === "yes") {
        const capFill = d.isExpanded
          ? palette.successTint
          : mixRgb(palette.success, palette.surfaceRaised, palette.isDark ? 0.15 : 0.07);
        return {
          fill: capFill,
          stroke: palette.success,
          lineWidth: 1.8,
          lineDash: d.isExpanded ? undefined : [5, 4],
          labelFill: palette.isDark
            ? mixRgb(palette.success, palette.text, 0.36)
            : mixRgb(palette.success, palette.text, 0.44),
          fontWeight: 600,
        };
      }
      return {
        fill: mixRgb(palette.accent, palette.surfaceRaised, palette.isDark ? 0.15 : 0.07),
        stroke: palette.accent,
        lineWidth: 1.8,
        lineDash: [5, 4],
        labelFill: palette.isDark
          ? mixRgb(palette.accent, palette.text, 0.36)
          : mixRgb(palette.accent, palette.text, 0.48),
        fontWeight: 600,
      };
    }

    let accentColor;
    let baseFill;
    let headerFill;
    let lineWidth = 2.0;
    let lineDash;

    if ((d.invalidations || 0) > 0) {
      accentColor = palette.error;
      baseFill = palette.errorTint;
      headerFill = palette.errorHeader;
      lineWidth = 2.4;
    } else if (d.tier === 0) {
      accentColor = palette.warning;
      baseFill = palette.warningTint;
      headerFill = palette.warningHeader;
      lineWidth = 2.2;
    } else if (d.tier === 1) {
      accentColor = palette.primary;
      baseFill = palette.primaryTint;
      headerFill = palette.primaryHeader;
      lineWidth = 2.2;
    } else if (d.tier === 2) {
      accentColor = palette.success;
      baseFill = palette.successTint;
      headerFill = palette.successHeader;
      lineWidth = 2.0;
    } else {
      accentColor = palette.accent;
      baseFill = palette.accentTint;
      headerFill = palette.accentHeader;
      lineWidth = 1.8;
      lineDash = [6, 3];
    }

    const isFocus = d.isFocus === "yes";
    const fill = isFocus
      ? mixRgb(accentColor, palette.surfaceRaised, palette.isDark ? 0.22 : 0.10)
      : baseFill;
    const focusHeader = isFocus
      ? mixRgb(accentColor, palette.surfaceRaised, palette.isDark ? 0.34 : 0.17)
      : headerFill;
    const stroke = isFocus
      ? (palette.isDark ? mixRgb(palette.primary, palette.text, 0.22) : palette.primary)
      : accentColor;
    const headerLabelFill = palette.isDark
      ? mixRgb(accentColor, palette.text, 0.38)
      : mixRgb(accentColor, palette.text, 0.36);

    return {
      fill,
      stroke,
      accentColor,
      headerFill: focusHeader,
      headerLabelFill,
      metricFill: palette.textSecondary,
      bodyLabelFill: palette.text,
      labelFill: palette.text,
      lineWidth: isFocus ? 2.8 : lineWidth,
      lineDash,
      fontWeight: isFocus ? 700 : 600,
      titleFontWeight: isFocus ? 700 : 600,
    };
  }

  function buildG6Data(nodes, edges, clusters, positions, palette, labels) {
    const usePalantirCard = ensurePalantirNodeRegistered();
    const useEnvciteTheme = ensureEnvciteThemeRegistered();
    const sharedPorts = [
      { key: "in", placement: [0, 0.5] },
      { key: "out", placement: [1, 0.5] },
      { key: "bottom", placement: [0.09, 1] },
    ];
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
            type: "rect",
            data,
            style: {
              x: pos.x,
              y: pos.y,
              size: [240, 44],
              radius: 22,
              halo: false,
              fill: c.fill,
              stroke: c.stroke,
              lineWidth: c.lineWidth,
              lineDash: c.lineDash,
              ports: sharedPorts,
              labelText: n.title,
              labelFill: c.labelFill,
              labelFontSize: 12,
              labelFontWeight: 600,
              labelMaxWidth: 216,
              labelWordWrap: true,
              labelPlacement: "center",
              labelTextAlign: "center",
              labelTextBaseline: "middle",
              cursor: "pointer",
            },
          };
        }

        const slots = formatCardSlots(n, labels);
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
          type: usePalantirCard ? "palantir-law-node" : "rect",
          data,
          style: {
            x: pos.x,
            y: pos.y,
            size: n.focus ? [284, 92] : [276, 88],
            radius: 10,
            halo: false,
            fill: c.fill,
            stroke: c.stroke,
            lineWidth: c.lineWidth,
            lineDash: c.lineDash || undefined,
            accentColor: c.accentColor,
            headerFill: c.headerFill,
            headerLabelFill: c.headerLabelFill,
            metricFill: c.metricFill,
            bodyLabelFill: c.bodyLabelFill,
            titleFontWeight: c.titleFontWeight,
            typeLabel: slots.typeLabel,
            metricLabel: slots.metricLabel,
            bodyTitle: slots.bodyTitle,
            ports: sharedPorts,
            shadowColor: palette.isDark ? "rgba(2, 6, 23, 0.56)" : "rgba(15, 23, 42, 0.12)",
            shadowBlur: n.focus ? 18 : 10,
            shadowOffsetY: 3,
            labelText: usePalantirCard ? "" : data.cardLabel,
            labelFill: c.labelFill,
            labelFontSize: 12.5,
            labelFontWeight: c.fontWeight,
            labelLineHeight: 17,
            labelMaxWidth: 248,
            labelMaxLines: 3,
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
          type: "rect",
          data,
          style: {
            x: pos.x,
            y: pos.y,
            size: [240, 44],
            radius: 22,
            halo: false,
            fill: c.fill,
            stroke: c.stroke,
            lineWidth: c.lineWidth,
            lineDash: c.lineDash,
            ports: sharedPorts,
            labelText: cl.label,
            labelFill: c.labelFill,
            labelFontSize: 12,
            labelFontWeight: 600,
            labelMaxWidth: 216,
            labelWordWrap: true,
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

  function renderInspector(inspectorEl, rawNode, allNodes, allEdges, labels, callbacks) {
    if (!inspectorEl) return;
    clearEl(inspectorEl);

    if (!rawNode) {
      const head = addEl(inspectorEl, "div", "ep-inspector-head");
      addEl(head, "h3", "ep-inspector-title", labels.guideTitle || "");
      addEl(head, "p", "ep-page-description", labels.selectPrompt || "");

      const guideList = addEl(inspectorEl, "ul", "ep-inspector-guide-list");
      [labels.guideStep1, labels.guideStep2, labels.guideStep3].filter(Boolean).forEach((step) => {
        addEl(guideList, "li", "ep-inspector-guide-item", step);
      });

      const outCounts = new Map();
      (allEdges || []).forEach((e) => {
        outCounts.set(e.source, (outCounts.get(e.source) || 0) + 1);
      });
      const topParents = (allNodes || [])
        .filter((n) => (n.hierarchyTier ?? 1) <= 1 && ((n.totalChildren || 0) > 0 || (outCounts.get(n.id) || 0) > 0))
        .slice()
        .sort((a, b) => (b.totalChildren || outCounts.get(b.id) || 0) - (a.totalChildren || outCounts.get(a.id) || 0))
        .slice(0, 6);

      if (topParents.length > 0 && callbacks?.focusNodeOnCanvas) {
        const box = addEl(inspectorEl, "div", "ep-inspector-rel-group");
        addEl(box, "h4", "ep-inspector-rel-heading", labels.quickJumpHeading || "");
        const quickList = addEl(box, "div", "ep-inspector-quick-list");
        topParents.forEach((parent) => {
          const count = parent.totalChildren || outCounts.get(parent.id) || 0;
          const btn = addEl(quickList, "button", "ep-inspector-quick-btn", null);
          btn.type = "button";
          addEl(btn, "span", "ep-inspector-quick-title", parent.title);
          addEl(btn, "span", "ep-badge", (labels.childrenCountText || "{n}").replace("{n}", String(count)));
          btn.addEventListener("click", () => callbacks.focusNodeOnCanvas(parent.id));
        });
      }
      return;
    }

    const head = addEl(inspectorEl, "div", "ep-inspector-head");
    const badges = addEl(head, "div", "ep-inspector-badges");
    const tierBadge = addEl(badges, "span", "ep-badge", tierName(rawNode.hierarchyTier, labels));
    tierBadge.dataset.tone = rawNode.hierarchyTier === 0
      ? "warning"
      : rawNode.hierarchyTier === 1
        ? "primary"
        : rawNode.hierarchyTier === 2
          ? "success"
          : "info";
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
    const nodeGroups = callbacks?.getNodeGroups ? callbacks.getNodeGroups(rawNode.id) : [];
    if (nodeGroups.length > 0 && callbacks?.toggleNodeGroups) {
      const hiddenCount = nodeGroups.reduce((sum, g) => sum + (g.isExpanded ? 0 : g.count), 0);
      const toggleLabel = hiddenCount > 0
        ? (labels.expandAllGroupsAction || "展開全部子法分組 (+{n})").replace("{n}", String(hiddenCount))
        : (labels.collapseAllGroupsAction || "收合子法分組");
      const toggleBtn = addEl(actions, "button", "ep-btn ep-btn-primary", toggleLabel);
      toggleBtn.type = "button";
      toggleBtn.addEventListener("click", () => {
        callbacks.toggleNodeGroups(rawNode.id);
      });
    }
    if (rawNode.focusHref && !rawNode.focus) {
      const focusLink = addEl(actions, "a", nodeGroups.length > 0 ? "ep-btn ep-btn-quiet" : "ep-btn ep-btn-primary", labels.focusButton || "以此法為中心重建圖譜");
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
      payload = JSON.parse(container.dataset.canonGraph || "{}");
    } catch {
      return;
    }
    const nodes = payload.nodes || [];
    const edges = payload.edges || [];
    const labels = payload.labels || {};
    if (nodes.length === 0) {
      return;
    }

    // 還在隱藏的分頁或還沒排版（寬度 0）時先不建圖：以 0 寬排出來的位置與視角都是錯的，等有寬度再建。
    if (container.clientWidth === 0 && typeof ResizeObserver === "function") {
      if (container.dataset.graphWait !== "1") {
        container.dataset.graphWait = "1";
        const waiter = new ResizeObserver(() => {
          if (container.clientWidth === 0) return;
          waiter.disconnect();
          delete container.dataset.graphWait;
          initGraph(container);
        });
        waiter.observe(container);
      }
      return;
    }

    container.dataset.graphReady = "1";
    // 視角定好之前不給人看：G6 一建圖就以縮放 1 畫出實際大小的卡片，隨後才縮到適合視窗，使用者會看到「先放大、再縮小」。
    // 定好第一個視角後才淡入；逾時也要放行，圖不能因為初始視角卡住而一直藏著。
    container.dataset.graphState = "loading";
    let revealed = false;
    const revealTimer = window.setTimeout(() => reveal(), 2500);
    function reveal() {
      if (revealed) return;
      revealed = true;
      window.clearTimeout(revealTimer);
      // 等兩個 frame，定好視角的那一畫面先畫出來再淡入。
      window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
        container.dataset.graphState = "ready";
      }));
    }
    const host = container.closest("[data-graph-host]");
    const inspectorEl = host ? host.querySelector("[data-graph-inspector]") : null;

    let currentLayout = payload.mode === "concentric" ? "concentric" : "stufenbau";
    let showEdgeLabels = edges.length <= 16;
    let selectedNodeId = null;
    const expandedGroups = new Set();
    let visibleModelCache = null;

    const palette = themePalette();

    function computeCurrentGraphData() {
      visibleModelCache = buildVisibleGraphModel(nodes, edges, expandedGroups, labels);
      const { visibleNodes, visibleEdges } = visibleModelCache;
      const { positions, clusters } = computeStufenbauPositions(visibleNodes, visibleEdges, container.clientWidth, labels);
      return buildG6Data(visibleNodes, visibleEdges, clusters, positions, themePalette(), labels);
    }

    const graphData = computeCurrentGraphData();

    const usePalantirCard = ensurePalantirNodeRegistered();
    const useEnvciteTheme = ensureEnvciteThemeRegistered();

    const edgeStyleMapper = (d) => {
      const kind = d.data?.kind || "delegation";
      const isSub = kind === "sub_delegation";
      const isClusterLink = kind === "cluster_link" || kind === "group_link";
      const pal = themePalette();
      const useStufenbauPorts = currentLayout === "stufenbau";
      const useIndentedPorts = currentLayout === "indented";
      const baseStroke = isSub
        ? pal.accent
        : isClusterLink
          ? pal.success
          : mixRgb(pal.primary, pal.borderControl, pal.isDark ? 0.45 : 0.38);
      return {
        stroke: baseStroke,
        lineWidth: isClusterLink ? 1.6 : 1.9,
        lineDash: isSub ? [6, 4] : isClusterLink ? [5, 4] : undefined,
        endArrow: !isClusterLink,
        endArrowSize: 7,
        sourcePort: useStufenbauPorts ? "out" : useIndentedPorts ? "bottom" : undefined,
        targetPort: (useStufenbauPorts || useIndentedPorts) ? "in" : undefined,
        router: useIndentedPorts ? { type: "orth", padding: 8 } : undefined,
        radius: useIndentedPorts ? 6 : undefined,
        opacity: isClusterLink ? 0.78 : 0.80,
        labelText: showEdgeLabels ? (d.data?.anchorLabel || "") : "",
        labelFill: pal.text,
        labelFontSize: 11,
        labelFontWeight: 600,
        labelAutoRotate: false,
        labelWordWrap: true,
        labelMaxWidth: 128,
        labelBackground: true,
        labelBackgroundFill: pal.surfaceRaised,
        labelBackgroundFillOpacity: 0.94,
        labelBackgroundStroke: isSub ? pal.accent : pal.borderSubtle,
        labelBackgroundLineWidth: 1,
        labelPadding: [2, 6],
        labelBackgroundRadius: 4,
      };
    };

    function buildNodeConfig(pal) {
      return {
        type: (d) => (d?.data?.isCluster === "yes" || !usePalantirCard ? "rect" : "palantir-law-node"),
        style: {
          halo: false,
        },
        state: {
          selected: {
            opacity: 1,
            fillOpacity: 1,
            strokeOpacity: 1,
            labelFill: pal.text,
            labelFillOpacity: 1,
            labelOpacity: 1,
            bodyLabelFill: pal.text,
            metricFill: pal.textSecondary,
            lineWidth: 3.2,
            stroke: pal.primary,
            halo: true,
            haloStroke: pal.primary,
            haloLineWidth: 6,
            haloStrokeOpacity: 0.42,
            haloLineDash: [],
            shadowColor: pal.isDark ? "rgba(2, 6, 23, 0.72)" : "rgba(15, 23, 42, 0.24)",
            shadowBlur: 22,
          },
          active: {
            opacity: 1,
            fillOpacity: 1,
            strokeOpacity: 1,
            labelFill: pal.text,
            labelFillOpacity: 1,
            labelOpacity: 1,
            bodyLabelFill: pal.text,
            metricFill: pal.textSecondary,
            lineWidth: 2.8,
            stroke: pal.primary,
            halo: false,
          },
          inactive: {
            opacity: 0.86,
            fillOpacity: 0.94,
            strokeOpacity: 0.72,
            labelFill: pal.text,
            labelFillOpacity: 0.94,
            labelOpacity: 0.94,
            bodyLabelFill: pal.text,
            metricFill: pal.textSecondary,
            halo: false,
          },
        },
      };
    }

    function buildEdgeConfig(mode, pal) {
      return {
        type: mode === "concentric" ? "quadratic" : mode === "indented" ? "polyline" : "cubic-horizontal",
        style: edgeStyleMapper,
        state: {
          active: {
            lineWidth: 2.8,
            stroke: pal.primary,
            strokeOpacity: 1,
            opacity: 1,
            halo: false,
            labelText: (d) => d.data?.anchorLabel || "",
            labelFill: pal.text,
            labelFillOpacity: 1,
            labelOpacity: 1,
            labelAutoRotate: false,
            labelWordWrap: true,
            labelMaxWidth: 128,
            labelBackground: true,
            labelBackgroundFill: pal.surfaceRaised,
            labelBackgroundFillOpacity: 0.96,
            labelBackgroundRadius: 4,
            labelPadding: [2, 6],
          },
          inactive: {
            opacity: 0.36,
            strokeOpacity: 0.45,
            labelOpacity: 0.45,
            labelFill: pal.textSecondary,
          },
        },
      };
    }

    const graph = new window.G6.Graph({
      container,
      theme: useEnvciteTheme ? "envcite" : undefined,
      data: graphData,
      autoResize: true,
      padding: 28,
      zoomRange: [0.25, 2.2],
      node: buildNodeConfig(palette),
      edge: buildEdgeConfig(currentLayout, palette),
      behaviors: [
        "drag-canvas",
        {
          type: "zoom-canvas",
          enable: (e) => {
            const orig = e?.originalEvent;
            const isFs = Boolean(document.fullscreenElement && host?.contains(document.fullscreenElement));
            return isFs || Boolean(orig?.ctrlKey || orig?.metaKey);
          },
        },
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
            } else {
              clearSelectionStates();
            }
          },
        },
      ],
      plugins: [
        {
          type: "minimap",
          key: "canon-minimap",
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
          key: "canon-tooltip",
          enable: (e) => e.targetType === "node",
          getContent: (e, items) => buildTooltipContent(items?.[0], labels),
        },
        {
          type: "fullscreen",
          key: "canon-fullscreen",
        },
        {
          type: "contextmenu",
          key: "canon-contextmenu",
          enable: (e) => e.targetType === "node",
          getItems: (e) => {
            const nodeId = e?.target?.id;
            const datum = nodeId ? graph.getNodeData(nodeId) : null;
            if (datum?.data?.isGroupCapsule === "yes") {
              return [{ name: labels.toggleGroupAction || "展開／收合授權子法", value: "toggle-capsule" }];
            }
            const raw = datum?.data?.raw;
            const items = [{ name: labels.inspectAction || "檢視法學詳情", value: "inspect" }];
            if (raw && (visibleModelCache?.childGroupMap.get(raw.id)?.length || 0) > 0) {
              items.push({ name: labels.toggleGroupAction || "展開／收合授權子法", value: "toggle-groups" });
            }
            if (raw?.focusHref && !raw.focus) {
              items.push({ name: labels.focusButton || "以此法為中心重建圖譜", value: "focus" });
            }
            if (raw?.structureHref) {
              items.push({ name: labels.structureButton || "檢視編章節條文", value: "structure" });
            }
            items.push({ name: labels.copyNameAction || "複製法規名稱", value: "copy" });
            return items;
          },
          onClick: async (value, target, current) => {
            const nodeId = current?.id;
            if (!nodeId) return;
            const datum = graph.getNodeData(nodeId);
            const raw = datum?.data?.raw;
            if (value === "toggle-capsule" && datum?.data?.groupId) {
              await toggleSingleGroup(datum.data.groupId, datum.data.parentId);
            } else if (value === "toggle-groups") {
              await toggleGroupsForNode(nodeId);
            } else if (value === "inspect") {
              selectNodeById(nodeId);
              if (host) host.dataset.inspectorOpen = "true";
            } else if (value === "focus" && raw?.focusHref) {
              window.location.assign(raw.focusHref);
            } else if (value === "structure" && raw?.structureHref) {
              window.location.assign(raw.structureHref);
            } else if (value === "copy") {
              const title = raw?.title || datum?.data?.raw?.title || datum?.data?.cardLabel || datum?.style?.labelText || nodeId;
              if (navigator.clipboard?.writeText) {
                navigator.clipboard.writeText(title).catch(() => {});
              }
            }
          },
        },
      ],
    });

    container._g6Instance = graph;

    container.addEventListener("wheel", (evt) => {
      const isFs = Boolean(document.fullscreenElement && host?.contains(document.fullscreenElement));
      if (evt.ctrlKey || evt.metaKey || isFs) {
        evt.preventDefault();
        container.dataset.viewportTouched = "1";
      }
    }, { passive: false });
    // 使用者拖曳過畫布就不再替他重新適應視窗（見 ResizeObserver）：視角是他自己擺的。
    container.addEventListener("pointerdown", () => {
      container.dataset.viewportTouched = "1";
    });

    async function fitViewport({ forceFitAll = false, focusTargetId = null } = {}) {
      try {
        delete container.dataset.viewportTouched;
        await graph.fitView(undefined, false);
        const minZoom = currentLayout === "indented" ? 0.72 : 0.74;
        const targetZoom = currentLayout === "indented" ? 0.82 : 0.84;
        if (!forceFitAll && graph.getZoom() < minZoom) {
          await graph.zoomTo(targetZoom, false);
          const topParent = nodes
            .filter((n) => (n.hierarchyTier ?? 1) <= 1)
            .slice()
            .sort((a, b) => (b.totalChildren || 0) - (a.totalChildren || 0))[0];
          const target = focusTargetId
            || selectedNodeId
            || (nodes.find((n) => n.focus) || topParent || nodes[0])?.id;
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
      const focusNode = nodes.find((n) => n.focus) || null;
      const states = {};
      graph.getNodeData().forEach((n) => {
        states[n.id] = (focusNode && n.id === focusNode.id) ? ["selected"] : [];
      });
      graph.getEdgeData().forEach((e) => { states[e.id] = []; });
      graph.setElementState(states, false);
    }

    const inspectorCallbacks = {
      getNodeGroups: (id) => visibleModelCache?.childGroupMap.get(id) || [],
      toggleNodeGroups: (id) => { toggleGroupsForNode(id); },
      focusNodeOnCanvas: async (nodeId) => {
        if (!nodeId || !graph.getNodeData(nodeId)) return;
        selectNodeById(nodeId);
        if (host) {
          delete host.dataset.inspectorDismissed;
          host.dataset.inspectorOpen = "true";
        }
        if (graph.getZoom() < 0.78) {
          await graph.zoomTo(0.86);
        }
        await graph.focusElement(nodeId).catch(() => {});
      },
    };

    function collectLineage(rootId) {
      const inAdj = new Map();
      const outAdj = new Map();
      graph.getEdgeData().forEach((e) => {
        if (!inAdj.has(e.target)) inAdj.set(e.target, []);
        inAdj.get(e.target).push(e);
        if (!outAdj.has(e.source)) outAdj.set(e.source, []);
        outAdj.get(e.source).push(e);
      });

      const connectedEdgeIds = new Set();
      const lineageNodeIds = new Set([rootId]);

      const walk = (startId, adj, nextKey) => {
        const visited = new Set([startId]);
        const queue = [startId];
        while (queue.length > 0) {
          const cur = queue.shift();
          for (const edge of adj.get(cur) || []) {
            connectedEdgeIds.add(edge.id);
            const next = edge[nextKey];
            lineageNodeIds.add(next);
            if (!visited.has(next)) {
              visited.add(next);
              queue.push(next);
            }
          }
        }
      };

      walk(rootId, inAdj, "source");
      walk(rootId, outAdj, "target");
      return { connectedEdgeIds, lineageNodeIds };
    }

    function selectNodeById(nodeId) {
      const nodeDatum = graph.getNodeData(nodeId);
      if (!nodeDatum) {
        selectedNodeId = null;
        clearSelectionStates();
        renderInspector(inspectorEl, null, nodes, edges, labels, inspectorCallbacks);
        return;
      }
      if (nodeDatum.data?.isCluster === "yes") return;

      selectedNodeId = nodeId;
      const { connectedEdgeIds, lineageNodeIds } = collectLineage(nodeId);

      const states = {};
      graph.getNodeData().forEach((n) => {
        if (n.id === nodeId) states[n.id] = ["selected"];
        else if (lineageNodeIds.has(n.id)) states[n.id] = ["active"];
        else states[n.id] = ["inactive"];
      });
      graph.getEdgeData().forEach((e) => {
        states[e.id] = connectedEdgeIds.has(e.id) ? ["active"] : ["inactive"];
      });
      graph.setElementState(states, false);
      renderInspector(inspectorEl, nodeDatum.data?.raw || null, nodes, edges, labels, inspectorCallbacks);
    }

    async function toggleSingleGroup(groupId, parentId) {
      if (!groupId) return;
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
    }

    async function toggleGroupsForNode(nodeId) {
      const groups = visibleModelCache?.childGroupMap.get(nodeId) || [];
      if (groups.length === 0) return;
      const anyCollapsed = groups.some((g) => !expandedGroups.has(g.groupId));
      groups.forEach((g) => {
        if (anyCollapsed) expandedGroups.add(g.groupId);
        else expandedGroups.delete(g.groupId);
      });
      graph.setData(computeCurrentGraphData());
      await graph.render();
      await applyLayout(currentLayout, nodeId);
      if (graph.getNodeData(nodeId)) {
        selectNodeById(nodeId);
      }
    }

    async function applyLayout(mode, focusTargetId = null) {
      currentLayout = mode;
      graph.setEdge(buildEdgeConfig(mode, themePalette()));
      if (mode === "concentric") {
        const focusNode = nodes.find((n) => n.focus) || nodes.find((n) => n.hierarchyTier === 1) || nodes[0];
        graph.setLayout({
          type: "concentric",
          preventOverlap: true,
          nodeSize: [284, 96],
          minNodeSpacing: 56,
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
        visibleModelCache = buildVisibleGraphModel(nodes, edges, expandedGroups, labels);
        const { visibleNodes, visibleEdges } = visibleModelCache;
        const { clusters } = computeStufenbauPositions(visibleNodes, visibleEdges, container.clientWidth, labels);
        const positions = computeIndentedPositions(
          [...visibleNodes, ...clusters],
          [...visibleEdges, ...clusters.map((c) => ({ source: c.parentId, target: c.id }))],
        );
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
        visibleModelCache = buildVisibleGraphModel(nodes, edges, expandedGroups, labels);
        const { visibleNodes, visibleEdges } = visibleModelCache;
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
      const focusNode = nodes.find((n) => n.focus);
      await fitViewport({
        forceFitAll: mode !== "indented" && (visibleModelCache?.visibleNodes.length || 0) <= 12 && !focusNode && !focusTargetId,
        focusTargetId,
      });
      updateToolbarState();
    }

    graph.on("node:click", async (evt) => {
      const id = evt.target?.id;
      if (!id) return;
      const nodeDatum = graph.getNodeData(id);
      if (nodeDatum?.data?.isGroupCapsule === "yes") {
        await toggleSingleGroup(nodeDatum.data.groupId, nodeDatum.data.parentId);
        return;
      }
      if (nodeDatum?.data?.isCluster === "yes") {
        const href = nodeDatum.data?.expandHref;
        if (href) window.location.assign(href);
        return;
      }
      selectNodeById(id);
      if (host) {
        delete host.dataset.inspectorDismissed;
        host.dataset.inspectorOpen = "true";
      }
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
      const focusNode = nodes.find((n) => n.focus) || null;
      renderInspector(inspectorEl, focusNode, nodes, edges, labels, inspectorCallbacks);
    });

    if (searchEl) {
      let searchTimer = 0;
      searchEl.addEventListener("input", () => {
        window.clearTimeout(searchTimer);
        searchTimer = window.setTimeout(async () => {
          const term = (searchEl.value || "").trim().toLowerCase();
          if (!term) {
            selectedNodeId = null;
            clearSelectionStates();
            return;
          }
          const match = nodes.find((n) =>
            (n.title || "").toLowerCase().includes(term)
            || (n.localId || "").toLowerCase().includes(term),
          );
          if (!match) return;

          if (!graph.getNodeData(match.id)) {
            const targetGroupId = visibleModelCache?.groupMembership.get(match.id);
            if (targetGroupId) {
              expandedGroups.add(targetGroupId);
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

    graph.render().then(async () => {
      const focusNode = nodes.find((n) => n.focus) || null;
      const topParent = nodes
        .filter((n) => (n.hierarchyTier ?? 1) <= 1)
        .slice()
        .sort((a, b) => (b.totalChildren || 0) - (a.totalChildren || 0))[0] || null;
      renderInspector(inspectorEl, focusNode, nodes, edges, labels, inspectorCallbacks);
      clearSelectionStates();
      if (currentLayout === "concentric" || currentLayout === "indented") {
        await applyLayout(currentLayout, (focusNode || topParent)?.id || null);
      } else {
        await fitViewport({
          forceFitAll: currentLayout !== "indented" && (visibleModelCache?.visibleNodes.length || 0) <= 12 && !focusNode,
          focusTargetId: (focusNode || topParent || nodes[0])?.id || null,
        });
        updateToolbarState();
      }
    }).catch(() => {}).finally(reveal);

    if (host && host.dataset.graphBound !== "1") {
      host.dataset.graphBound = "1";
      let isFullscreen = false;

      host.addEventListener("click", (evt) => {
        const filterBtn = evt.target.closest("[data-graph-filter-tier]");
        if (filterBtn) {
          applyTierFilter(filterBtn.dataset.graphFilterTier);
          return;
        }
        const openBtn = evt.target.closest("[data-inspector-open-btn]");
        if (openBtn) {
          delete host.dataset.inspectorDismissed;
          host.dataset.inspectorOpen = "true";
          return;
        }
        const closeBtn = evt.target.closest("[data-inspector-close]");
        if (closeBtn) {
          host.dataset.inspectorDismissed = "true";
          host.dataset.inspectorOpen = "false";
          return;
        }
        const btn = evt.target.closest("[data-graph-action]");
        if (!btn) return;
        const activeGraph = container._g6Instance || graph;
        const action = btn.dataset.graphAction;
        if (action === "fit") fitViewport({ forceFitAll: true });
        else if (action === "zoom-in" || action === "zoom-out") {
          container.dataset.viewportTouched = "1";
          activeGraph.zoomBy(action === "zoom-in" ? 1.2 : 0.84).catch(() => {});
        }
        else if (action === "fullscreen") {
          const fsPlugin = activeGraph.getPluginInstance?.("canon-fullscreen");
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
          activeGraph.setEdge(buildEdgeConfig(currentLayout, themePalette()));
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
        } else if (evt.key === "+" || evt.key === "=" || evt.key === "-") {
          container.dataset.viewportTouched = "1";
          activeGraph.zoomBy(evt.key === "-" ? 0.84 : 1.2).catch(() => {});
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
            // 關閉檢視面板、收合側欄都會改變畫布寬度：還沒被使用者動過視角才重新適應，否則維持他擺的位置，不要跳。
            if (container.dataset.viewportTouched !== "1") fitViewport();
          }
        });
      });
      observer.observe(container);
    }

    const refreshTheme = () => {
      if (!container.isConnected) return;
      const nextPalette = themePalette(true);
      const nodeUpdates = graph.getNodeData().map((n) => {
        const c = nodeColors(n.data || {}, nextPalette);
        return {
          id: n.id,
          style: {
            fill: c.fill,
            stroke: c.stroke,
            accentColor: c.accentColor,
            headerFill: c.headerFill,
            headerLabelFill: c.headerLabelFill,
            metricFill: c.metricFill,
            bodyLabelFill: c.bodyLabelFill,
            labelFill: c.labelFill,
            shadowColor: nextPalette.isDark ? "rgba(2, 6, 23, 0.56)" : "rgba(15, 23, 42, 0.12)",
          },
        };
      });
      graph.setNode(buildNodeConfig(nextPalette));
      graph.updateNodeData(nodeUpdates);
      graph.setEdge(buildEdgeConfig(currentLayout, nextPalette));
      graph.draw().catch(() => {});
      if (selectedNodeId) selectNodeById(selectedNodeId);
    };

    if (window.matchMedia) {
      const mql = window.matchMedia("(prefers-color-scheme: dark)");
      if (typeof mql.addEventListener === "function") mql.addEventListener("change", refreshTheme);
    }

    if (typeof MutationObserver !== "undefined" && document.documentElement) {
      let lastThemeAttr = document.documentElement.dataset.theme || "";
      const themeObserver = new MutationObserver(() => {
        if (!container.isConnected) {
          themeObserver.disconnect();
          return;
        }
        const currentThemeAttr = document.documentElement.dataset.theme || "";
        if (currentThemeAttr !== lastThemeAttr) {
          lastThemeAttr = currentThemeAttr;
          refreshTheme();
        }
      });
      themeObserver.observe(document.documentElement, { attributes: true });
    }
  }

  function initPresets() {
    const presetButtons = Array.from(document.querySelectorAll("[data-reasoning-preset]"));
    presetButtons.forEach((btn) => {
      if (btn.dataset.presetBound === "1") return;
      btn.dataset.presetBound = "1";
      btn.addEventListener("click", () => {
        const form = btn.closest("form") || document.querySelector("[data-reasoning-form]") || btn.closest(".ep-card")?.querySelector("form");
        if (!form) return;
        presetButtons.forEach((other) => {
          other.setAttribute("aria-pressed", other === btn ? "true" : "false");
        });
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
          const details = form.querySelector("details.ep-tree-manual");
          if (details) details.open = true;
        } else if (preset === "benefit") {
          setVal("Input.ConductEndedOn", today);
          setVal("Input.CriminalOffense", "false");
          setVal("Input.CourtConfiscated", "false");
          setVal("Input.GainedBenefit", "50000");
        }
      });
    });
  }

  function initStructureFilter() {
    document.querySelectorAll("[data-structure-workspace]").forEach((workspace) => {
      if (workspace.dataset.filterBound === "1") return;
      workspace.dataset.filterBound = "1";
      const input = workspace.querySelector("[data-structure-filter]");
      const rows = Array.from(workspace.querySelectorAll(".ep-structure-table-wrap tbody tr"));
      const chips = Array.from(workspace.querySelectorAll(".ep-structure-box .ep-article-chip"));
      if (!input) return;

      const applyFilter = () => {
        const term = (input.value || "").trim().toLowerCase();
        rows.forEach((row) => {
          const text = (row.textContent || "").toLowerCase();
          row.hidden = Boolean(term) && !text.includes(term);
        });
        chips.forEach((chip) => {
          const text = (chip.textContent || "").toLowerCase();
          const matched = Boolean(term) && text.includes(term);
          chip.classList.toggle("is-match", matched);
          chip.classList.toggle("is-dim", Boolean(term) && !matched);
          if (matched) {
            let parent = chip.closest("details");
            while (parent && workspace.contains(parent)) {
              parent.open = true;
              parent = parent.parentElement?.closest("details");
            }
          }
        });
      };

      let timer = 0;
      input.addEventListener("input", () => {
        window.clearTimeout(timer);
        timer = window.setTimeout(applyFilter, 80);
      });

      workspace.addEventListener("click", (evt) => {
        const chip = evt.target.closest(".ep-article-chip");
        if (!chip) return;
        const label = (chip.textContent || "").trim();
        if (!label) return;
        input.value = input.value.trim() === label ? "" : label;
        applyFilter();
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
    document.querySelectorAll("[data-canon-graph]").forEach(initGraph);
    initPresets();
    initStructureFilter();
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
