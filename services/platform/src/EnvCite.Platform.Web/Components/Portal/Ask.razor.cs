using System.Security.Claims;

using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Components.Portal;

public partial class Ask
{
    // 範例是臺灣法規裡會出現的詞（資料庫是中文），所以英文介面也維持中文：翻成英文就搜不到。
    private static readonly string[] Examples = ["噪音管制標準", "固定污染源", "廢棄物清理", "水污染防治"];

    [Parameter] public string Tenant { get; set; } = string.Empty;

    [Inject] private IPortalAccount Account { get; set; } = default!;

    [Inject] private ITenantDirectory Directory { get; set; } = default!;

    [Inject] private ILawSearch Laws { get; set; } = default!;

    [Inject] private IGroundedAnswerer Answerer { get; set; } = default!;

    [Inject] private IConversationHistory History { get; set; } = default!;

    [CascadingParameter] private HttpContext HttpContext { get; set; } = default!;

    [SupplyParameterFromForm] private SearchForm Input { get; set; } = default!;

    private bool _hasPlan;
    private bool _aiAnswer;
    private bool _asked;
    private string _question = string.Empty;
    private string? _error;
    private string _asOfLabel = string.Empty;
    private IReadOnlyList<LawHit>? _hits;
    private GroundedAnswer? _answer;
    private Guid? _conversationId;

    private string? Name => HttpContext.User.FindFirst(ClaimTypes.GivenName)?.Value ?? HttpContext.User.FindFirst(ClaimTypes.Email)?.Value;

    protected override async Task OnInitializedAsync()
    {
        _asOfLabel = L["現行"];
        Input ??= new();
        _hasPlan = await HasPlanAsync();
    }

    /// <summary>
    /// 完全付費制：沒有方案不能用資料功能。這裡再擋一次（不只是不顯示表單），因為表單可以被直接 POST。
    /// 租戶取自請求路徑解析出的租戶（授權已確認 cookie 的租戶就是它），不取自使用者輸入。
    /// </summary>
    private async Task<bool> HasPlanAsync()
    {
        if (Account.CurrentTenant is not { } tenant || await Directory.FindAsync(tenant.Id, HttpContext.RequestAborted) is not { } summary)
        {
            return false;
        }

        // AI 回答要兩個條件：部署者設定了模型端點，而且這個租戶同意（問題會送到外部的模型服務）。
        _aiAnswer = summary.Plan is not null && summary.AiAnswer && Answerer.Available;
        return summary.Plan is not null;
    }

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

        _asked = true;
        _question = query.Trim();
        _asOfLabel = asOf is null ? L["現行"] : L["{0} 有效", asOf];
        _hits = null;
        _answer = null;
        // 像聊天一樣：問完輸入框清空，接著問下一個（日期留著，常常是同一天的一串問題）。
        Input.Query = string.Empty;
        Input.Example = string.Empty;

        // 有設定模型端點就回答（只根據封閉證據、附出處，不然拒答）；沒有就只列條文。
        var answerText = _aiAnswer ? await AnswerAsync(query, asOf) : await ListAsync(query, asOf);
        if (answerText is not null)
        {
            _conversationId = await History.AppendTurnAsync(
                HttpContext.User, Guid.TryParse(Input.ConversationId, out var id) ? id : null, _question, answerText, HttpContext.RequestAborted);
        }
    }

    private async Task<string?> AnswerAsync(string query, string? asOf)
    {
        var result = await Answerer.AnswerAsync(query, asOf, HttpContext.RequestAborted);
        switch (result.Status)
        {
            case AdminStatus.Ok:
                _answer = result.Value;
                return _answer!.Refused
                    ? L["（拒答）{0}", RefusalLabel(_answer.RefusalReason)]
                    : $"{_answer.Text}\n{string.Join('\n', _answer.Sources.Select(s => $"[{s.Code}] {s.Citation}（{_asOfLabel}） {s.Uri}"))}";
            case AdminStatus.Invalid:
                _error = L["查詢條件不正確：請輸入問題；日期要是有效的日期。"];
                return null;
            default:
                _error = L["回答服務暫時無法使用，請稍後再試。"];
                return null;
        }
    }

    private async Task<string?> ListAsync(string query, string? asOf)
    {
        var result = await Laws.SearchAsync(query, asOf, limit: null, HttpContext.RequestAborted);
        switch (result.Status)
        {
            case AdminStatus.Ok:
                _hits = result.Value;
                return AnswerText(_hits!);
            case AdminStatus.Invalid:
                _error = L["查詢條件不正確：請輸入關鍵字；日期要是有效的日期。"];
                return null;
            default:
                _error = L["搜尋服務暫時無法使用，請稍後再試。"];
                return null;
        }
    }

    /// <summary>拒答對使用者說的話：說明為什麼沒有答案，而不是只說「不能回答」。</summary>
    internal string RefusalLabel(string? reason) => reason switch
    {
        "no_authority" => L["找不到足以回答這個問題的法條，所以沒有回答。試試換個說法，或換一天。"],
        "out_of_scope" => L["這個問題超出目前涵蓋的知識範圍，所以沒有回答。"],
        "missing_input" => L["問題不夠具體，請補充後再問。"],
        "unverified_citation" => L["系統產生的回答無法對應到法條出處，已捨棄，沒有顯示。請換個說法再問一次。"],
        _ => L["這次沒有產生可以對應到法條出處的回答。請換個說法再問一次。"],
    };

    /// <summary>
    /// 記進歷史的答案（只列條文時）：只有出處（法規名、條號、條文網址），不存條文全文——全文隨時可以由出處再查，
    /// 而且查的是哪一天的版本已經寫在問題的日期裡。
    /// </summary>
    private string AnswerText(IReadOnlyList<LawHit> hits) =>
        hits.Count == 0
            ? L["沒有符合的條文。"]
            : string.Join('\n', hits.Select(hit => L["《{0}》第 {1} 條（{2}） {3}", hit.LawTitle, hit.Number, _asOfLabel, hit.Uri]));

    public sealed class SearchForm
    {
        /// <summary>接續哪一串對話；空 = 另開一串。內容不可信，由歷史服務確認它屬於目前的成員。</summary>
        public string ConversationId { get; set; } = string.Empty;

        public string Query { get; set; } = string.Empty;

        public string Example { get; set; } = string.Empty;

        public string AsOf { get; set; } = string.Empty;
    }
}