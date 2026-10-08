using System.Security.Claims;

using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvLaw.Platform.Web.Components.Portal;

public partial class Ask
{
    private static readonly string[] Examples = ["噪音管制標準", "固定污染源", "廢棄物清理", "水污染防治"];

    [Parameter] public string Tenant { get; set; } = string.Empty;

    [Inject] private IPortalAccount Account { get; set; } = default!;

    [Inject] private ITenantDirectory Directory { get; set; } = default!;

    [Inject] private ILawSearch Laws { get; set; } = default!;

    [CascadingParameter] private HttpContext HttpContext { get; set; } = default!;

    [SupplyParameterFromForm] private SearchForm Input { get; set; } = default!;

    private bool _hasPlan;
    private bool _asked;
    private string _question = string.Empty;
    private string? _error;
    private string _asOfLabel = "現行";
    private IReadOnlyList<LawHit>? _hits;

    private string? Name => HttpContext.User.FindFirst(ClaimTypes.GivenName)?.Value ?? HttpContext.User.FindFirst(ClaimTypes.Email)?.Value;

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

        // 範例鈕送的是 Example（它們不需要先填輸入框）；自己打的字優先。
        var query = string.IsNullOrWhiteSpace(Input.Query) ? Input.Example : Input.Query;
        var asOf = string.IsNullOrWhiteSpace(Input.AsOf) ? null : Input.AsOf.Trim();
        var result = await Laws.SearchAsync(query, asOf, limit: null, HttpContext.RequestAborted);

        _asked = true;
        _question = query.Trim();
        _asOfLabel = asOf is null ? "現行" : $"{asOf} 有效";
        // 像聊天一樣：問完輸入框清空，接著問下一個（日期留著，常常是同一天的一串問題）。
        Input.Query = string.Empty;
        Input.Example = string.Empty;

        switch (result.Status)
        {
            case AdminStatus.Ok:
                _hits = result.Value;
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

        public string Example { get; set; } = string.Empty;

        public string AsOf { get; set; } = string.Empty;
    }
}