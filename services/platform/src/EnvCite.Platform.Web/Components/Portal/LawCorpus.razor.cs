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
    private LawCorpusRoute Route => new(
        Tenant, Jurisdiction, IdScheme, LocalId, AsOf, Query, Category, UpDepth, DownDepth, ChildLimit, DocType, ActiveTabQuery);

    private bool HasSelectedLaw => Route.HasSelectedLaw;

    private int ResolvedUpDepth => Route.ResolvedUpDepth;

    private int ResolvedDownDepth => Route.ResolvedDownDepth;

    private int ResolvedChildLimit => Route.ResolvedChildLimit;

    private string SelectedCategoryValue => Route.SelectedCategory;

    private string? EffectiveCategoryPrefix => Route.CategoryPrefix;

    private string ResolvedTab => Route.ResolveTab(HasReasoning, _reasoningOutcome is not null || !string.IsNullOrEmpty(Error));

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
        new(L["行政法院裁判"], g.CourtDecisions.Count.ToString(CultureInfo.CurrentCulture), L["行政訴訟法第 216 條"]),
    ];

    private IReadOnlyList<StatItem> StructureStats(PortalLawCorpusResult law, PortalLawGraphNode? focus) =>
    [
        new(L["條文總數"], law.Articles.Count.ToString(CultureInfo.CurrentCulture)),
        new(L["結構章節"], law.Structure.Count.ToString(CultureInfo.CurrentCulture)),
        new(L["直接授權子法"], (focus?.TotalChildren ?? 0).ToString(CultureInfo.CurrentCulture)),
        new(L["有效行政函釋"], (focus?.GuidanceCount ?? 0).ToString(CultureInfo.CurrentCulture)),
        new(L["行政法院裁判"], (_graphResult?.CourtDecisions.Count ?? 0).ToString(CultureInfo.CurrentCulture), L["行政訴訟法第 216 條"]),
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

            _activeBranchKeys.UnionWith(LawTree.ActiveBranch(_filteredRoots, LawTree.Key(Jurisdiction!, IdScheme!, LocalId!)));
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

    private void ApplyTreeOutcome(Outcome<PortalLawTreeResult> treeOutcome)
    {
        if (treeOutcome.IsOk && treeOutcome.Value is { } tree)
        {
            _treeLoaded = tree.CatalogAvailable;
            _treeRoots = tree.Roots;
            _filteredRoots = LawTree.Filter(_treeRoots, Query);
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
            otherDocType = L["其他授權子法"].Value,
            expandGroupSuffix = L["點擊展開"].Value,
            collapseGroupSuffix = L["收合"].Value,
            inspectAction = L["檢視法學詳情"].Value,
            copyNameAction = L["複製法規名稱"].Value,
            toggleGroupAction = L["展開／收合授權子法"].Value,
            expandAllGroupsAction = L["展開全部子法分組 (+{n})"].Value,
            collapseAllGroupsAction = L["收合子法分組"].Value,
            guideTitle = L["授權圖譜導讀"].Value,
            guideStep1 = L["① 追查授權明確性：沿著連線確認下位辦法／標準是否具母法具體授權條文。"].Value,
            guideStep2 = L["② 檢視法規範位階：由左至右依「憲法 → 法律 → 法規命令 → 再授權」分層呈現。"].Value,
            guideStep3 = L["③ 點擊節點查細節：單擊顯示授權母法與子法清單，雙擊以其為中心展開。"].Value,
            quickJumpHeading = L["重點母法快速定位"].Value,
            legendTiers = L["層級（點選顯示或隱藏）"].Value,
            legendEdges = L["連線"].Value,
            legendShowAll = L["全部顯示"].Value,
            legendLastTier = L["至少要保留一個層級"].Value,
        };

        return JsonSerializer.Serialize(new { nodes, edges, labels });
    }

    private string BuildNodeHref(PortalLawTreeNode node) => Route.NodeHref(node, ResolvedTab);

    private string BuildFocusUrl(
        PortalLawKey key,
        string? tab = null,
        int? upDepth = null,
        int? downDepth = null,
        int? childLimit = null,
        string? docType = null) =>
        Route.FocusHref(key, tab, upDepth, downDepth, childLimit, docType);

    private string CategoryHref(string code) => Route.CategoryHref(code, ResolvedTab);

    private string TabHref(TabItem item) => Route.TabHref(item.Key);

    private string AskLawHref(string lawTitle) => Route.AskHref(L["請說明「{0}」的主要規範重點。", lawTitle].Value);

    private static PortalLawGraphNode? FindFocusNode(PortalLawGraphResult graph) => LawTree.FindFocus(graph);

    private static string NodeKey(string j, string s, string l) => LawTree.Key(j, s, l);

    private bool IsBranchOpen(string key) =>
        !string.IsNullOrWhiteSpace(Query) || _activeBranchKeys.Contains(key);

    private string NormKindLabel(string normKind) => normKind switch
    {
        "constitution" => L["憲法"],
        "statute" or "law" => L["法律"],
        "local_ordinance" => L["自治條例"],
        "local_regulation" => L["自治規則"],
        "administrative_rule" => L["行政規則"],
        _ => L["命令"],
    };

    private string CourtInstanceLabel(string instance) => instance switch
    {
        "supreme" => L["最高行政法院"],
        "high" => L["高等行政法院"],
        "district" => L["地方行政法院"],
        _ => instance,
    };

    private string CourtKindLabel(string kind) => kind switch
    {
        "grand_chamber" => L["大法庭"],
        _ => L["通常"],
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