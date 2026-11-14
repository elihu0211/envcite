using System.Globalization;
using System.Text.Json;

using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;

namespace EnvCite.Platform.Web.Components.Portal;

public sealed partial class LawCorpus(
    ITenantModules modules,
    ILawCorpusService lawCorpusService,
    INormativeReasoningService reasoningService)
{
    private const int MaxVisibleTreeRoots = 250;

    private static readonly string[] DomainCodes = ["O", "ALL", "A", "D", "G", "J", "K", "L", "N"];

    [SupplyParameterFromQuery(Name = "jurisdiction")] public string? Jurisdiction { get; set; }
    [SupplyParameterFromQuery(Name = "idScheme")] public string? IdScheme { get; set; }
    [SupplyParameterFromQuery(Name = "localId")] public string? LocalId { get; set; }
    [SupplyParameterFromQuery(Name = "asOf")] public string? AsOf { get; set; }
    [SupplyParameterFromQuery(Name = "q")] public string? Query { get; set; }
    [SupplyParameterFromQuery(Name = "cat")] public string? Category { get; set; }
    [SupplyParameterFromQuery(Name = "upDepth")] public int? UpDepth { get; set; }
    [SupplyParameterFromQuery(Name = "downDepth")] public int? DownDepth { get; set; }
    [SupplyParameterFromQuery(Name = "childLimit")] public int? ChildLimit { get; set; }
    [SupplyParameterFromQuery(Name = "docType")] public string? DocType { get; set; }
    [SupplyParameterFromQuery(Name = "tab")] public string? ActiveTabQuery { get; set; }

    private bool _treeLoaded;
    private string? _treeError;
    private IReadOnlyList<PortalLawTreeNode> _treeRoots = [];
    private IReadOnlyList<PortalLawTreeNode> _filteredRoots = [];
    private IReadOnlyList<PortalLawTreeNode> _visibleRoots = [];
    private PortalLawGraphResult? _graphResult;
    private PortalLawCorpusResult? _result;
    private PortalReasoningOutcome? _reasoningOutcome;
    private string? _lookupError;
    private readonly HashSet<string> _activeBranchKeys = new(StringComparer.Ordinal);

    private ITenantModules Modules => modules;

    private bool HasCorpus => modules.IsEnabled(Tenant, ModuleKeys.LawCorpus);
    private bool HasReasoning => modules.IsEnabled(Tenant, ModuleKeys.NormativeReasoning);
    private bool HasSelectedLaw =>
        !string.IsNullOrWhiteSpace(Jurisdiction)
        && !string.IsNullOrWhiteSpace(IdScheme)
        && !string.IsNullOrWhiteSpace(LocalId);

    private int ResolvedUpDepth => Math.Clamp(UpDepth ?? 2, 1, 4);
    private int ResolvedDownDepth => Math.Clamp(DownDepth ?? 2, 1, 4);
    private int ResolvedChildLimit => Math.Clamp(ChildLimit ?? 20, 1, 60);

    private string SelectedCategoryValue
    {
        get
        {
            if (string.Equals(Category, "ALL", StringComparison.OrdinalIgnoreCase))
            {
                return "ALL";
            }

            if (!string.IsNullOrWhiteSpace(Category) && Category.Trim().Length == 1 && char.IsAsciiLetterUpper(Category.Trim()[0]))
            {
                return Category.Trim();
            }

            if (!string.IsNullOrWhiteSpace(Query) || HasSelectedLaw)
            {
                return "ALL";
            }

            return "O";
        }
    }

    private string? EffectiveCategoryPrefix => SelectedCategoryValue == "ALL" ? null : SelectedCategoryValue;

    private string ResolvedTab
    {
        get
        {
            if (_reasoningOutcome is not null || !string.IsNullOrEmpty(Error))
            {
                return HasReasoning ? "reasoning" : "graph";
            }

            if (string.Equals(ActiveTabQuery, "structure", StringComparison.OrdinalIgnoreCase))
            {
                return "structure";
            }

            if (string.Equals(ActiveTabQuery, "reasoning", StringComparison.OrdinalIgnoreCase) && HasReasoning)
            {
                return "reasoning";
            }

            return "graph";
        }
    }

    private string CatalogTitle => L["法律樹結構 ({0})", _filteredRoots.Count];

    private string GraphSectionTitle => _graphResult?.Focus is not null && FindFocusNode(_graphResult) is { } focus
        ? L["授權知識圖譜 · {0}", focus.Title]
        : SelectedCategoryValue == "ALL"
            ? L["授權知識圖譜 · 全域總覽"]
            : L["授權知識圖譜 · {0}", DomainLabel(SelectedCategoryValue)];

    private string DomainLabel(string code) => code switch
    {
        "O" => L["O · 環境保護"],
        "ALL" => L["全部領域"],
        "A" => L["A · 憲政體制"],
        "D" => L["D · 內政營建"],
        "G" => L["G · 財政賦稅"],
        "J" => L["J · 經濟能源"],
        "K" => L["K · 交通運輸"],
        "L" => L["L · 衛生食藥"],
        "N" => L["N · 勞動職安"],
        _ => code,
    };

    private IReadOnlyList<SelectOption> CategoryOptions =>
        [.. DomainCodes.Select(code => new SelectOption(code, DomainLabel(code)))];

    private IReadOnlyList<(string Code, string Label)> QuickCategoryLinks =>
        [.. DomainCodes.Select(code => (code, DomainLabel(code)))];

    private IReadOnlyList<TabItem> WorkbenchTabs
    {
        get
        {
            var items = new List<TabItem>
            {
                new("graph", L["授權知識圖譜"], _graphResult?.Nodes.Count),
                new("structure", L["編章節條文"], _result?.Articles.Count),
            };
            if (HasReasoning)
            {
                items.Add(new("reasoning", L["合規推論試算"], _reasoningOutcome?.Verdicts.Count));
            }

            return items;
        }
    }

    private IReadOnlyList<SelectOption> TriStateOptions =>
    [
        new(string.Empty, L["未知（省略）"]),
        new("true", L["是"]),
        new("false", L["否"]),
    ];

    private IReadOnlyList<StatItem> GraphStats(PortalLawGraphResult g, PortalLawGraphNode? focus) =>
    [
        new(L["圖譜節點"], g.Nodes.Count.ToString(CultureInfo.CurrentCulture), focus?.DocumentType),
        new(L["授權連線"], g.Edges.Count.ToString(CultureInfo.CurrentCulture), g.Edges.Any(e => e.Kind == "sub_delegation") ? L["含再授權鏈"].Value : null),
        new(L["罰則條文連動"], g.ViolationLinks.Count.ToString(CultureInfo.CurrentCulture)),
        new(L["有效行政函釋"], (focus?.GuidanceCount ?? g.GuidanceLinks.Count).ToString(CultureInfo.CurrentCulture), L["行政程序法第 159 條"]),
    ];

    private IReadOnlyList<StatItem> StructureStats(PortalLawCorpusResult law, PortalLawGraphNode? focus) =>
    [
        new(L["條文總數"], law.Articles.Count.ToString(CultureInfo.CurrentCulture)),
        new(L["結構章節"], law.Structure.Count.ToString(CultureInfo.CurrentCulture)),
        new(L["直接授權子法"], (focus?.TotalChildren ?? 0).ToString(CultureInfo.CurrentCulture)),
        new(L["有效行政函釋"], (focus?.GuidanceCount ?? 0).ToString(CultureInfo.CurrentCulture)),
    ];

    private IReadOnlyList<StatItem> SummaryStats(PortalReasoningOutcome r) =>
    [
        new(L["裁處時結論"], r.Verdicts.Count.ToString(CultureInfo.CurrentCulture)),
        new(L["期間法律變更組"], r.EarlierCount.ToString(CultureInfo.CurrentCulture)),
        new(L["違憲失效排除"], r.InvalidatedCount.ToString(CultureInfo.CurrentCulture)),
        new(L["授權鏈缺口"], r.DelegationGapCount.ToString(CultureInfo.CurrentCulture)),
        new(L["語料未涵蓋法規"], r.UncoveredLawCount.ToString(CultureInfo.CurrentCulture)),
    ];

    protected override async Task OnInitializedAsync()
    {
        base.OnInitialized();
        var today = DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8)).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        if (string.IsNullOrEmpty(Input.ConductOn))
        {
            Input.ConductOn = today;
        }

        if (string.IsNullOrEmpty(Input.DecidedOn))
        {
            Input.DecidedOn = today;
        }

        if (!HasCorpus)
        {
            return;
        }

        var asOf = string.IsNullOrWhiteSpace(AsOf) ? null : AsOf.Trim();
        var treeTask = lawCorpusService.TreeByCategoryAsync(User, null, EffectiveCategoryPrefix, asOf, CancellationToken.None);

        if (HasSelectedLaw)
        {
            var focusKey = new PortalLawKey(Jurisdiction!, IdScheme!, LocalId!);
            var graphTask = lawCorpusService.GraphAsync(
                User,
                new PortalLawGraphQuery(
                    focusKey,
                    asOf,
                    CategoryPrefix: null,
                    UpstreamDepth: ResolvedUpDepth,
                    DownstreamDepth: ResolvedDownDepth,
                    ChildLimit: ResolvedChildLimit,
                    ChildDocumentType: string.IsNullOrWhiteSpace(DocType) ? null : DocType.Trim()),
                CancellationToken.None);
            var browseTask = lawCorpusService.BrowseAsync(
                User, Jurisdiction, IdScheme, LocalId, asOf, CancellationToken.None);

            await Task.WhenAll(treeTask, graphTask, browseTask);

            var treeOutcome = await treeTask;
            ApplyTreeOutcome(treeOutcome);

            var graphOutcome = await graphTask;
            if (graphOutcome.IsOk && graphOutcome.Value is { } graph)
            {
                _graphResult = graph;
            }

            var outcome = await browseTask;
            if (outcome.IsOk)
            {
                _result = outcome.Value;
            }
            else
            {
                _lookupError = outcome.Localized() ?? L["查詢法規失敗。"];
            }

            CollectActiveBranch(NodeKey(Jurisdiction!, IdScheme!, LocalId!));
        }
        else
        {
            var graphTask = lawCorpusService.GraphAsync(
                User,
                new PortalLawGraphQuery(
                    Focus: null,
                    AsOf: asOf,
                    CategoryPrefix: EffectiveCategoryPrefix,
                    UpstreamDepth: 2,
                    DownstreamDepth: 2,
                    ChildLimit: 24),
                CancellationToken.None);

            await Task.WhenAll(treeTask, graphTask);

            var treeOutcome = await treeTask;
            ApplyTreeOutcome(treeOutcome);

            var graphOutcome = await graphTask;
            if (graphOutcome.IsOk && graphOutcome.Value is { } graph)
            {
                _graphResult = graph;
            }
        }
    }

    private void ApplyTreeOutcome(AdminOutcome<PortalLawTreeResult> treeOutcome)
    {
        if (treeOutcome.IsOk && treeOutcome.Value is { } tree)
        {
            _treeLoaded = tree.CatalogAvailable;
            _treeRoots = tree.Roots;
            _filteredRoots = FilterNodes(_treeRoots, Query);
            _visibleRoots = _filteredRoots.Count > MaxVisibleTreeRoots
                ? [.. _filteredRoots.Take(MaxVisibleTreeRoots)]
                : _filteredRoots;
        }
        else if (!treeOutcome.IsOk)
        {
            _treeError = treeOutcome.Localized() ?? L["查詢法規失敗。"].Value;
        }
    }

    private async Task EvaluateAsync()
    {
        if (!HasReasoning)
        {
            return;
        }

        var evaluated = await reasoningService.EvaluateAsync(
            User,
            Input.ToInput(),
            CancellationToken.None);

        if (!evaluated.IsOk)
        {
            Error = evaluated.Localized() ?? L["推論評估失敗，請稍後再試。"];
            return;
        }

        _reasoningOutcome = evaluated.Value;
    }

    private string BuildGraphJson(PortalLawGraphResult graph)
    {
        var nodes = graph.Nodes.Select(n => new
        {
            id = NodeKey(n.Key.Jurisdiction, n.Key.IdScheme, n.Key.LocalId),
            jurisdiction = n.Key.Jurisdiction,
            idScheme = n.Key.IdScheme,
            localId = n.Key.LocalId,
            title = n.Title,
            uri = n.Uri,
            normKind = n.NormKind,
            documentType = n.DocumentType,
            dateDocument = n.DateDocument ?? string.Empty,
            hierarchyTier = n.HierarchyTier,
            totalChildren = n.TotalChildren,
            truncatedChildren = n.TruncatedChildren,
            articleCount = n.ArticleCount,
            invalidationCount = n.InvalidationCount,
            guidanceCount = n.GuidanceCount,
            focus = graph.Focus is not null
                && string.Equals(graph.Focus.Jurisdiction, n.Key.Jurisdiction, StringComparison.Ordinal)
                && string.Equals(graph.Focus.IdScheme, n.Key.IdScheme, StringComparison.Ordinal)
                && string.Equals(graph.Focus.LocalId, n.Key.LocalId, StringComparison.Ordinal),
            focusHref = BuildFocusUrl(n.Key, tab: "graph", childLimit: 20, docType: string.Empty),
            structureHref = BuildFocusUrl(n.Key, tab: "structure"),
            expandHref = n.TruncatedChildren > 0 ? BuildFocusUrl(n.Key, tab: "graph", childLimit: 60) : null,
        });

        var edges = graph.Edges.Select(e => new
        {
            source = NodeKey(e.Source.Jurisdiction, e.Source.IdScheme, e.Source.LocalId),
            target = NodeKey(e.Target.Jurisdiction, e.Target.IdScheme, e.Target.LocalId),
            kind = e.Kind,
            anchorArticles = e.AnchorArticles,
            allRooted = e.AllRooted,
        });

        var labels = new
        {
            tier0 = L["Tier 0 憲法"].Value,
            tier1 = L["Tier 1 法律／條例"].Value,
            tier2 = L["Tier 2 法規命令"].Value,
            tier3 = L["Tier 3 再授權規則"].Value,
            guidanceMarker = L["函釋"].Value,
            childMarker = L["子法"].Value,
            clusterSuffix = L["部授權法規（點擊展開）"].Value,
            focusButton = L["以此法為中心重建圖譜"].Value,
            structureButton = L["檢視編章節條文"].Value,
            expandButton = L["展開完整 +{n} 部授權法規"].Value,
            parentsHeading = L["授權母法依據（上位規範）"].Value,
            childrenHeading = L["授權訂定子法規（下位規範）"].Value,
            delegationKind = L["法律直接授權"].Value,
            subDelegationKind = L["再授權"].Value,
            anchorPrefix = L["授權條文："].Value,
            invalidationBadge = L["{n} 項違憲失效"].Value,
            invalidationSource = L["資料來源：憲法法庭；未列出不等於無違憲失效。"].Value,
            guidanceCountText = L["{n} 件有效行政函釋（行政程序法第 159 條）"].Value,
            articlesText = L["{n} 條條文"].Value,
            childrenCountText = L["{n} 部子法規"].Value,
            truncatedNote = L["尚有 {n} 部下位法規未納入目前上限；點擊「展開完整」或藍色膠囊可顯示全部。"].Value,
            noEdgesText = L["此範圍內尚無直接授權連線。"].Value,
            selectPrompt = L["點選圖譜中的任一法規範節點檢視法學位階、授權條文依據與關係細節；雙擊節點可直接以其為中心深入展開。"].Value,
        };

        return JsonSerializer.Serialize(new { nodes, edges, labels });
    }

    private static IReadOnlyList<PortalLawTreeNode> FilterNodes(IReadOnlyList<PortalLawTreeNode> nodes, string? q)
    {
        if (string.IsNullOrWhiteSpace(q))
        {
            return nodes;
        }

        var term = q.Trim();
        var matched = new List<PortalLawTreeNode>();
        foreach (var n in nodes)
        {
            var filteredChildren = FilterNodes(n.Children, term);
            var selfMatches = n.Title.Contains(term, StringComparison.OrdinalIgnoreCase)
                || n.LocalId.Contains(term, StringComparison.OrdinalIgnoreCase);
            if (selfMatches || filteredChildren.Count > 0)
            {
                matched.Add(n with { Children = selfMatches && filteredChildren.Count == 0 ? n.Children : filteredChildren });
            }
        }

        return matched;
    }

    private string BuildNodeHref(PortalLawTreeNode node) =>
        BuildUrl(
            node.Jurisdiction,
            node.IdScheme,
            node.LocalId,
            tab: ResolvedTab == "reasoning" ? "graph" : ResolvedTab,
            cat: SelectedCategoryValue,
            upDepth: ResolvedUpDepth,
            downDepth: ResolvedDownDepth,
            childLimit: 20,
            docType: string.Empty);

    private string BuildFocusUrl(
        PortalLawKey key,
        string? tab = null,
        int? upDepth = null,
        int? downDepth = null,
        int? childLimit = null,
        string? docType = null) =>
        BuildUrl(
            key.Jurisdiction,
            key.IdScheme,
            key.LocalId,
            tab: tab ?? "graph",
            cat: SelectedCategoryValue,
            upDepth: upDepth ?? ResolvedUpDepth,
            downDepth: downDepth ?? ResolvedDownDepth,
            childLimit: childLimit ?? ResolvedChildLimit,
            docType: docType ?? DocType);

    private string CategoryHref(string code) =>
        BuildUrl(
            jurisdiction: null,
            idScheme: null,
            localId: null,
            tab: ResolvedTab == "structure" ? "graph" : ResolvedTab,
            cat: code,
            upDepth: ResolvedUpDepth,
            downDepth: ResolvedDownDepth,
            childLimit: ResolvedChildLimit,
            docType: string.Empty);

    private string TabHref(TabItem item) =>
        BuildUrl(
            HasSelectedLaw ? Jurisdiction : null,
            HasSelectedLaw ? IdScheme : null,
            HasSelectedLaw ? LocalId : null,
            tab: item.Key,
            cat: SelectedCategoryValue,
            upDepth: ResolvedUpDepth,
            downDepth: ResolvedDownDepth,
            childLimit: ResolvedChildLimit,
            docType: DocType);

    private string BuildUrl(
        string? jurisdiction,
        string? idScheme,
        string? localId,
        string tab,
        string cat,
        int upDepth,
        int downDepth,
        int childLimit,
        string? docType)
    {
        var qs = new List<string>
        {
            $"cat={Uri.EscapeDataString(cat)}",
            $"tab={Uri.EscapeDataString(tab)}",
        };
        if (!string.IsNullOrWhiteSpace(jurisdiction) && !string.IsNullOrWhiteSpace(idScheme) && !string.IsNullOrWhiteSpace(localId))
        {
            qs.Add($"jurisdiction={Uri.EscapeDataString(jurisdiction)}");
            qs.Add($"idScheme={Uri.EscapeDataString(idScheme)}");
            qs.Add($"localId={Uri.EscapeDataString(localId)}");
        }

        if (!string.IsNullOrWhiteSpace(Query))
        {
            qs.Add($"q={Uri.EscapeDataString(Query)}");
        }

        if (!string.IsNullOrWhiteSpace(AsOf))
        {
            qs.Add($"asOf={Uri.EscapeDataString(AsOf)}");
        }

        if (upDepth != 2)
        {
            qs.Add($"upDepth={upDepth}");
        }

        if (downDepth != 2)
        {
            qs.Add($"downDepth={downDepth}");
        }

        if (childLimit != 20)
        {
            qs.Add($"childLimit={childLimit}");
        }

        if (!string.IsNullOrWhiteSpace(docType))
        {
            qs.Add($"docType={Uri.EscapeDataString(docType)}");
        }

        return $"{PortalPaths.Home(Tenant)}/laws?{string.Join("&", qs)}";
    }

    private string AskLawHref(string lawTitle) =>
        $"{PortalPaths.Home(Tenant)}?q={Uri.EscapeDataString(L["請說明「{0}」的主要規範重點。", lawTitle].Value)}";

    private static PortalLawGraphNode? FindFocusNode(PortalLawGraphResult graph) =>
        graph.Focus is null
            ? null
            : graph.Nodes.FirstOrDefault(n =>
                string.Equals(n.Key.Jurisdiction, graph.Focus.Jurisdiction, StringComparison.Ordinal)
                && string.Equals(n.Key.IdScheme, graph.Focus.IdScheme, StringComparison.Ordinal)
                && string.Equals(n.Key.LocalId, graph.Focus.LocalId, StringComparison.Ordinal));

    private static string NodeKey(string j, string s, string l) => $"{j}|{s}|{l}";

    private bool IsBranchOpen(string key) =>
        !string.IsNullOrWhiteSpace(Query) || _activeBranchKeys.Contains(key);

    private void CollectActiveBranch(string targetKey)
    {
        bool Walk(PortalLawTreeNode node)
        {
            var self = NodeKey(node.Jurisdiction, node.IdScheme, node.LocalId) == targetKey;
            var childHit = false;
            foreach (var c in node.Children)
            {
                if (Walk(c))
                {
                    childHit = true;
                }
            }

            if (self || childHit)
            {
                _activeBranchKeys.Add(NodeKey(node.Jurisdiction, node.IdScheme, node.LocalId));
                return true;
            }

            return false;
        }

        foreach (var root in _filteredRoots)
        {
            Walk(root);
        }
    }

    private string NormKindLabel(string normKind) => normKind switch
    {
        "constitution" => L["憲法"],
        "statute" or "law" => L["法律"],
        "local_ordinance" => L["自治條例"],
        "local_regulation" => L["自治規則"],
        "administrative_rule" => L["行政規則"],
        _ => L["命令"],
    };

    private string ViolationKindLabel(string kind) => kind switch
    {
        "duty" => L["違反行為義務"],
        "delegated_rule" => L["授權法規命令"],
        _ => kind,
    };

    private string VerdictLabel(string verdict) => verdict switch
    {
        "Violation" => L["違反"],
        "ConditionalViolation" => L["附條件違反"],
        "Compliant" => L["合規"],
        "ConditionalCompliant" => L["附條件合規"],
        "Undetermined" => L["事實不足"],
        "Excluded" => L["排除"],
        "NotApplicable" => L["不適用"],
        _ => verdict,
    };

    private static string VerdictTone(string verdict) => verdict switch
    {
        "Violation" => "error",
        "ConditionalViolation" => "warning",
        "Compliant" or "ConditionalCompliant" => "success",
        _ => "neutral",
    };
}