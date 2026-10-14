using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Components.Portal;

public partial class History
{
    private const int PageSize = 100;

    [Parameter] public string Tenant { get; set; } = string.Empty;

    [Inject] private IConversationHistory Store { get; set; } = default!;

    [CascadingParameter] private HttpContext HttpContext { get; set; } = default!;

    [SupplyParameterFromForm] private DeleteForm Input { get; set; } = default!;

    private IReadOnlyList<ConversationSummary> _items = [];
    private string? _message;

    protected override async Task OnInitializedAsync()
    {
        Input ??= new();
        await LoadAsync();
    }

    private async Task LoadAsync() =>
        _items = await Store.ListAsync(HttpContext.User, PageSize, HttpContext.RequestAborted);

    private async Task DeleteAsync()
    {
        // 不是自己的（或不存在、已刪過）都一樣：只說沒找到，不洩漏它存不存在。
        var deleted = Guid.TryParse(Input.Delete, out var id) && await Store.DeleteAsync(HttpContext.User, id, HttpContext.RequestAborted);
        _message = deleted ? "已刪除。" : "找不到這個對話。";
        await LoadAsync();
    }

    public sealed class DeleteForm
    {
        public string Delete { get; set; } = string.Empty;
    }
}