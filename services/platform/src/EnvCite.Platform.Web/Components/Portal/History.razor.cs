using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Components.Portal;

public partial class History
{
    private const int PageSize = 100;

    private IReadOnlyList<ConversationSummary> _items = [];
    private string? _message;

    protected override async Task OnInitializedAsync()
    {
        await LoadAsync();
    }

    private async Task LoadAsync() =>
        _items = await Store.ListAsync(HttpContext.User, PageSize, HttpContext.RequestAborted);

    private async Task DeleteAsync()
    {
        // 不是自己的（或不存在、已刪過）都一樣：只說沒找到，不洩漏它存不存在。
        var deleted = Guid.TryParse(Input.Delete, out var id) && await Store.DeleteAsync(HttpContext.User, id, HttpContext.RequestAborted);
        _message = deleted ? L["已刪除。"] : L["找不到這個對話。"];
        await LoadAsync();
    }
}

public sealed class DeleteForm
{
    public string Delete { get; set; } = string.Empty;
}