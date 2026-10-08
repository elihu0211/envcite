using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvLaw.Platform.Web.Components.Portal;

public partial class Search
{
    [Parameter] public string Tenant { get; set; } = string.Empty;

    [Inject] private IPortalAccount Account { get; set; } = default!;

    [Inject] private ITenantDirectory Directory { get; set; } = default!;

    [Inject] private ILawSearch Laws { get; set; } = default!;

    [CascadingParameter] private HttpContext HttpContext { get; set; } = default!;

    [SupplyParameterFromForm] private SearchForm Input { get; set; } = default!;

    private bool _hasPlan;
    private string? _error;
    private string _asOfLabel = "現行";
    private IReadOnlyList<LawHit>? _hits;

    protected override async Task OnInitializedAsync()
    {
        Input ??= new();
        _hasPlan = await HasPlanAsync();
    }

    /// <summary>
    /// 完全付費制：沒有方案不能用資料功能。這裡再擋一次（不只是不顯示表單），因為表單可以被直接 POST。
    /// 租戶取自請求路徑解析出的租戶（授權已確認 cookie 的租戶就是它），不取自使用者輸入。
    /// </summary>
    private async Task<bool> HasPlanAsync() =>
        Account.CurrentTenant is { } tenant
        && (await Directory.FindAsync(tenant.Id, HttpContext.RequestAborted))?.Plan is not null;

    private async Task SearchAsync()
    {
        _error = null;
        if (!_hasPlan)
        {
            return;
        }

        var asOf = string.IsNullOrWhiteSpace(Input.AsOf) ? null : Input.AsOf.Trim();
        var result = await Laws.SearchAsync(Input.Query, asOf, limit: null, HttpContext.RequestAborted);
        switch (result.Status)
        {
            case AdminStatus.Ok:
                _hits = result.Value;
                _asOfLabel = asOf is null ? "現行" : $"{asOf} 有效";
                break;
            case AdminStatus.Invalid:
                _hits = null;
                _error = "查詢條件不正確：請輸入關鍵字；日期要是有效的日期。";
                break;
            default:
                _hits = null;
                _error = "搜尋服務暫時無法使用，請稍後再試。";
                break;
        }
    }

    public sealed class SearchForm
    {
        public string Query { get; set; } = string.Empty;

        public string AsOf { get; set; } = string.Empty;
    }
}