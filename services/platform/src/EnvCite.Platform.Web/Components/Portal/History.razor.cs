using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;

namespace EnvCite.Platform.Web.Components.Portal;

public partial class History(IConversationHistory store, IConversationHistoryAvailability historyFeature)
{
    private const int PageSize = 100;

    private const string ActiveView = "active";

    private const string ArchivedView = "archived";

    private IReadOnlyList<ConversationSummary> _items = [];

    private bool _archived;

    private IReadOnlyList<TabItem> _views = [];

    private IConversationHistoryAvailability HistoryFeature => historyFeature;

    /// <summary>標題搜尋（<c>?q=</c>）；空白是全部。</summary>
    [SupplyParameterFromQuery(Name = "q")]
    public string? Query { get; set; }

    /// <summary>檢視（<c>?view=</c>）：沒給是對話，<c>archived</c> 是已封存；其他值一律當對話。</summary>
    [SupplyParameterFromQuery(Name = "view")]
    public string? View { get; set; }

    protected override async Task OnInitializedAsync()
    {
        _archived = string.Equals(View, ArchivedView, StringComparison.Ordinal);
        _views = [new TabItem(ActiveView, L["對話"]), new TabItem(ArchivedView, L["已封存"])];
        _items = _archived
            ? await store.ListArchivedAsync(HttpContext.User, PageSize, Query, HttpContext.RequestAborted)
            : await store.ListAsync(HttpContext.User, PageSize, Query, HttpContext.RequestAborted);
    }
}