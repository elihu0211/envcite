using Microsoft.AspNetCore.Components;

namespace EnvCite.Platform.Web.Components.Portal;

public partial class History
{
    private const int PageSize = 100;

    private IReadOnlyList<ConversationSummary> _items = [];

    /// <summary>標題搜尋（<c>?q=</c>）；空白是全部。</summary>
    [SupplyParameterFromQuery(Name = "q")]
    public string? Query { get; set; }

    protected override async Task OnInitializedAsync() =>
        _items = await Store.ListAsync(HttpContext.User, PageSize, Query, HttpContext.RequestAborted);
}