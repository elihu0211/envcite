namespace EnvCite.Platform.Web.Components.Portal;

/// <summary>
/// 法規體系頁（<c>/portal/{租戶}/laws</c>）的查詢字串狀態：把網址上的參數正規化成頁面實際使用的值（領域、分頁、深度），
/// 並組出頁內連結。連結一律帶著目前的篩選（領域、關鍵字、基準日、深度），所以換分頁或換焦點不會丟掉脈絡。
/// 只有純函式：不碰元件、本地化與服務，所以不經瀏覽器就能測。
/// </summary>
internal sealed record LawCorpusRoute(
    string Tenant,
    string? Jurisdiction,
    string? IdScheme,
    string? LocalId,
    string? AsOf,
    string? Query,
    string? Category,
    int? UpDepth,
    int? DownDepth,
    int? ChildLimit,
    string? DocType,
    string? ActiveTab)
{
    public const string AllCategories = "ALL";
    public const string DefaultCategory = "O";
    public const int DefaultDepth = 2;
    public const int DefaultChildLimit = 20;

    public const string GraphTab = "graph";
    public const string StructureTab = "structure";
    public const string ReasoningTab = "reasoning";

    public bool HasSelectedLaw =>
        !string.IsNullOrWhiteSpace(Jurisdiction)
        && !string.IsNullOrWhiteSpace(IdScheme)
        && !string.IsNullOrWhiteSpace(LocalId);

    public int ResolvedUpDepth => Math.Clamp(UpDepth ?? DefaultDepth, 1, 4);

    public int ResolvedDownDepth => Math.Clamp(DownDepth ?? DefaultDepth, 1, 4);

    public int ResolvedChildLimit => Math.Clamp(ChildLimit ?? DefaultChildLimit, 1, 60);

    /// <summary>
    /// 領域代碼：明確指定（<c>ALL</c> 或單一大寫字母）者優先；沒指定但在搜尋或已選定某部法規時看全部領域
    /// （否則會被預設領域擋掉），其餘預設環境保護。
    /// </summary>
    public string SelectedCategory
    {
        get
        {
            if (string.Equals(Category, AllCategories, StringComparison.OrdinalIgnoreCase))
                return AllCategories;

            if (!string.IsNullOrWhiteSpace(Category) && Category.Trim().Length == 1 && char.IsAsciiLetterUpper(Category.Trim()[0]))
                return Category.Trim();

            return !string.IsNullOrWhiteSpace(Query) || HasSelectedLaw ? AllCategories : DefaultCategory;
        }
    }

    /// <summary>查詢法規樹用的領域前綴；全部領域不加前綴。</summary>
    public string? CategoryPrefix => SelectedCategory == AllCategories ? null : SelectedCategory;

    /// <summary>
    /// 目前的分頁。推論剛評估過或出了錯時回到推論頁（結果在那裡）；推論頁只在租戶開啟模組時才存在，其餘不認得的值都回圖譜。
    /// </summary>
    /// <param name="hasReasoning">租戶有沒有開啟推論模組。</param>
    /// <param name="reasoningActive">推論剛評估過或剛出錯。</param>
    public string ResolveTab(bool hasReasoning, bool reasoningActive)
    {
        if (reasoningActive)
            return hasReasoning ? ReasoningTab : GraphTab;

        if (string.Equals(ActiveTab, StructureTab, StringComparison.OrdinalIgnoreCase))
            return StructureTab;

        return string.Equals(ActiveTab, ReasoningTab, StringComparison.OrdinalIgnoreCase) && hasReasoning ? ReasoningTab : GraphTab;
    }

    /// <summary>法規樹節點的連結：以它為焦點，推論頁退回圖譜（推論頁沒有焦點的概念）。</summary>
    public string NodeHref(PortalLawTreeNode node, string resolvedTab) =>
        BuildUrl(
            node.Jurisdiction,
            node.IdScheme,
            node.LocalId,
            tab: resolvedTab == ReasoningTab ? GraphTab : resolvedTab,
            cat: SelectedCategory,
            upDepth: ResolvedUpDepth,
            downDepth: ResolvedDownDepth,
            childLimit: DefaultChildLimit,
            docType: string.Empty);

    /// <summary>以某部法規為焦點的連結；沒指定的參數沿用目前的值（分頁預設圖譜）。</summary>
    public string FocusHref(
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
            tab: tab ?? GraphTab,
            cat: SelectedCategory,
            upDepth: upDepth ?? ResolvedUpDepth,
            downDepth: downDepth ?? ResolvedDownDepth,
            childLimit: childLimit ?? ResolvedChildLimit,
            docType: docType ?? DocType);

    /// <summary>換領域：焦點與子法分類清掉（舊焦點不一定屬於新領域），結構頁沒有領域總覽，退回圖譜。</summary>
    public string CategoryHref(string code, string resolvedTab) =>
        BuildUrl(
            jurisdiction: null,
            idScheme: null,
            localId: null,
            tab: resolvedTab == StructureTab ? GraphTab : resolvedTab,
            cat: code,
            upDepth: ResolvedUpDepth,
            downDepth: ResolvedDownDepth,
            childLimit: ResolvedChildLimit,
            docType: string.Empty);

    /// <summary>換分頁：保留焦點與其餘篩選。</summary>
    public string TabHref(string tab) =>
        BuildUrl(
            HasSelectedLaw ? Jurisdiction : null,
            HasSelectedLaw ? IdScheme : null,
            HasSelectedLaw ? LocalId : null,
            tab: tab,
            cat: SelectedCategory,
            upDepth: ResolvedUpDepth,
            downDepth: ResolvedDownDepth,
            childLimit: ResolvedChildLimit,
            docType: DocType);

    /// <summary>帶著問題前往問答首頁。</summary>
    public string AskHref(string question) => $"{PortalPaths.Home(Tenant)}?q={Uri.EscapeDataString(question)}";

    // 與預設相同的深度與上限不寫進網址：連結短，也讓「沒設」與「設成預設」是同一個網址。
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
            qs.Add($"q={Uri.EscapeDataString(Query)}");

        if (!string.IsNullOrWhiteSpace(AsOf))
            qs.Add($"asOf={Uri.EscapeDataString(AsOf)}");

        if (upDepth != DefaultDepth)
            qs.Add($"upDepth={upDepth}");

        if (downDepth != DefaultDepth)
            qs.Add($"downDepth={downDepth}");

        if (childLimit != DefaultChildLimit)
            qs.Add($"childLimit={childLimit}");

        if (!string.IsNullOrWhiteSpace(docType))
            qs.Add($"docType={Uri.EscapeDataString(docType)}");

        return $"{PortalPaths.Home(Tenant)}/laws?{string.Join("&", qs)}";
    }
}