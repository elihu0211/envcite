using System.Security.Claims;

using EnvCite.Platform.Web.Resources;

using Microsoft.Extensions.Localization;
using Microsoft.Extensions.Logging;

namespace EnvCite.Platform.Web.Components.Portal.Workspace;

public enum TurnState
{
    /// <summary>已送出，還在等結果。</summary>
    Pending,

    /// <summary>有出處的回答（或從歷史載入的回答）。</summary>
    Answered,

    /// <summary>拒答：說明為什麼沒有答案。拒答是結果，不是錯誤。</summary>
    Refused,

    /// <summary>只列條文（沒有 AI 回答）。</summary>
    Listed,

    /// <summary>輸入錯誤或服務暫時不可用；<see cref="ChatTurn.Error"/> 說明。</summary>
    Failed,
}

/// <summary>
/// 對話裡的一個回合：一個問題與它的結果。<paramref name="asOfLabel"/> 是問的哪一天的條文（「現行」或「2026-01-01 有效」）；
/// 從歷史載入的回合沒有。
/// </summary>
public sealed class ChatTurn(string question, string? asOfLabel)
{
    public string Question { get; } = question;

    public string? AsOfLabel { get; } = asOfLabel;

    public TurnState State { get; internal set; } = TurnState.Pending;

    public GroundedAnswer? Answer { get; internal set; }

    public IReadOnlyList<LawHit>? Hits { get; internal set; }

    public string? Error { get; internal set; }

    /// <summary>從歷史載入的回答：只有當時存下來的文字（正文與出處行），沒有條文卡片。</summary>
    public string? StoredAnswer { get; internal set; }
}

/// <summary>
/// 一個 circuit 裡一串對話的狀態與流程（scoped）：問一題、等結果、記進歷史、開啟舊的對話。純 C# 類別，
/// 頁面只訂閱 <see cref="Changed"/> 並呈現，所以不需要 Blazor 就能測。所有領域動作經 <see cref="IAskService"/>
/// 在呼叫者的租戶裡執行；使用者看到的文字與記進歷史的文字在這裡組（語言是提問當下的介面語言）。
/// 問題文字是客戶案情：不進日誌，只留在這個 circuit 的記憶體裡（有開歷史才另存）。
/// </summary>
public sealed partial class ChatSession(
    IAskService ask, IStringLocalizer<Ui> localizer, WorkspaceState workspace, ILogger<ChatSession> logger)
{
    private readonly List<ChatTurn> _turns = [];

    private IStringLocalizer<Ui> L { get; } = localizer;

    public IReadOnlyList<ChatTurn> Turns => _turns;

    /// <summary>已經問了、還在等結果。期間不接受下一題（輸入框也鎖住）。</summary>
    public bool Busy { get; private set; }

    /// <summary>這串對話在歷史裡的 Id；沒開歷史、或還沒問過就是 null。</summary>
    public Guid? ConversationId { get; private set; }

    /// <summary>已經查過這位成員能用什麼；沒查過之前頁面不該下「沒有方案」的結論。</summary>
    public bool Ready { get; private set; }

    public AskCapabilities Capabilities { get; private set; } = new(HasPlan: false, AiAnswer: false);

    /// <summary>狀態變了，頁面該重畫。</summary>
    public event Action? Changed;

    public async Task InitializeAsync(ClaimsPrincipal user, CancellationToken cancellationToken)
    {
        Capabilities = await ask.CapabilitiesAsync(user, cancellationToken);
        Ready = true;
        Changed?.Invoke();
    }

    /// <summary>開啟自己的一串對話，回傳有沒有找到（不是自己的、已刪除、已過期、沒開啟：都是沒有）。找不到時是空白的新對話。</summary>
    public async Task<bool> OpenAsync(ClaimsPrincipal user, Guid id, CancellationToken cancellationToken)
    {
        var detail = await ask.ConversationAsync(user, id, cancellationToken);
        StartNew();
        if (detail is null)
            return false;

        ConversationId = detail.Id;
        ChatTurn? current = null;
        foreach (var message in detail.Messages)
        {
            if (message.Role == "user")
            {
                current = new ChatTurn(message.Content, asOfLabel: null);
                _turns.Add(current);
            }
            else if (current is not null)
            {
                current.StoredAnswer = message.Content;
                current.State = TurnState.Answered;
            }
        }

        Changed?.Invoke();
        return true;
    }

    /// <summary>空白的新對話（保留這位成員能用什麼）。</summary>
    public void StartNew()
    {
        _turns.Clear();
        ConversationId = null;
        Busy = false;
        Changed?.Invoke();
    }

    /// <summary>問一題。一次只處理一題；空白、沒有方案（頁面也不該讓人送出）都直接忽略。</summary>
    public async Task AskAsync(ClaimsPrincipal user, string? question, string? asOf, CancellationToken cancellationToken)
    {
        var text = question?.Trim();
        if (Busy || string.IsNullOrEmpty(text) || !Capabilities.HasPlan)
            return;

        var asOfValue = string.IsNullOrWhiteSpace(asOf) ? null : asOf.Trim();
        var turn = new ChatTurn(text, asOfValue is null ? L["現行"] : L["{0} 有效", asOfValue]);
        _turns.Add(turn);
        Busy = true;
        Changed?.Invoke();

        try
        {
            var result = await ask.AskAsync(user, text, asOfValue, cancellationToken);
            if (Present(turn, result) is { } historyText)
            {
                ConversationId = await ask.RecordAsync(user, ConversationId, text, historyText, cancellationToken);
                if (ConversationId is not null)
                    workspace.NotifyRecentChanged();
            }
            else if (turn.State == TurnState.Pending)
            {
                _turns.Remove(turn); // 沒有方案：沒有回合可畫
            }
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            // 沒預期到的錯誤：電路不能因此斷線。只記型別（訊息可能回顯問題），使用者看到的是通用說明。
            Failed(logger, exception.GetType().Name);
            turn.State = TurnState.Failed;
            turn.Error = L["回答服務暫時無法使用，請稍後再試。"];
        }
        finally
        {
            Busy = false;
            Changed?.Invoke();
        }
    }

    /// <summary>拒答對使用者說的話：說明為什麼沒有答案，而不是只說「不能回答」。</summary>
    public string RefusalLabel(string? reason) => reason switch
    {
        "no_authority" => L["找不到足以回答這個問題的法條，所以沒有回答。試試換個說法，或換一天。"],
        "out_of_scope" => L["這個問題超出目前涵蓋的知識範圍，所以沒有回答。"],
        "local_law_uncovered" => L["這個問題問的是地方自治法規，而這裡只涵蓋中央法規，所以沒有回答。請向該地方政府查詢。"],
        "missing_input" => L["問題不夠具體，請補充後再問。"],
        "unverified_citation" => L["系統產生的回答無法對應到法條出處，已捨棄，沒有顯示。請換個說法再問一次。"],
        _ => L["這次沒有產生可以對應到法條出處的回答。請換個說法再問一次。"],
    };

    /// <summary>
    /// 把結果放進回合，並回傳要記進歷史的文字（沒有結果時 null）。歷史只存有出處的文字（法規名、條號、條文網址），
    /// 不存條文全文：全文隨時可以由出處再查，而且查的是哪一天的版本已經寫在問題的日期裡。
    /// </summary>
    private string? Present(ChatTurn turn, AskResult result)
    {
        var answering = result.Mode == AskMode.Answer;
        switch (result.Status)
        {
            case AskStatus.Ok when answering:
                turn.Answer = result.Answer;
                if (turn.Answer!.Refused)
                {
                    turn.State = TurnState.Refused;
                    return L["（拒答）{0}", RefusalLabel(turn.Answer.RefusalReason)];
                }

                turn.State = TurnState.Answered;
                return $"{turn.Answer.Text}\n{string.Join('\n', turn.Answer.Sources.Select(s => $"[{s.Code}] {s.Citation}（{turn.AsOfLabel}） {s.Uri}"))}";
            case AskStatus.Ok:
                turn.Hits = result.Hits;
                turn.State = TurnState.Listed;
                return result.Hits!.Count == 0
                    ? L["沒有符合的條文。"]
                    : string.Join('\n', result.Hits.Select(hit => L["《{0}》第 {1} 條（{2}） {3}", hit.LawTitle, hit.Number, turn.AsOfLabel!, hit.Uri]));
            case AskStatus.Invalid:
                turn.State = TurnState.Failed;
                turn.Error = answering
                    ? L["查詢條件不正確：請輸入問題；日期要是有效的日期。"]
                    : L["查詢條件不正確：請輸入關鍵字；日期要是有效的日期。"];
                return null;
            case AskStatus.NoPlan:
                return null;
            default:
                turn.State = TurnState.Failed;
                turn.Error = answering
                    ? L["回答服務暫時無法使用，請稍後再試。"]
                    : L["搜尋服務暫時無法使用，請稍後再試。"];
                return null;
        }
    }

    [LoggerMessage(Level = LogLevel.Warning, Message = "Ask turn failed unexpectedly: {ExceptionType}")]
    private static partial void Failed(ILogger logger, string exceptionType);
}