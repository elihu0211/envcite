using System.Security.Claims;
using System.Text.RegularExpressions;

using EnvCite.Platform.Web.Resources;

using Microsoft.Extensions.AI;
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

    public string? AsOfLabel { get; internal set; } = asOfLabel;

    public TurnState State { get; internal set; } = TurnState.Pending;

    /// <summary>還在等結果時，回答進行到哪一步；只有回答模式才有，搜尋沒有步驟。</summary>
    public AnswerProgress? Progress { get; internal set; }

    public GroundedAnswer? Answer { get; internal set; }

    public IReadOnlyList<LawHit>? Hits { get; internal set; }

    public string? Error { get; internal set; }

    /// <summary>從歷史載入的回答：只有當時存下來的文字（正文與出處行），沒有條文卡片。</summary>
    public string? StoredAnswer { get; internal set; }

    /// <summary>
    /// 接續對話時當作前文的回答正文（<c>[L1]</c> 還在）與它的引用；沒有答案（拒答、只列條文、失敗）就是 null。
    /// 新答出來的回合與從歷史載入的回合都填這兩個，記憶只看這裡，不分回合從哪來。
    /// </summary>
    internal string? MemoryText { get; set; }

    internal IReadOnlyList<ConversationCitation> MemoryCitations { get; set; } = [];
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
        string? asOf = null;
        foreach (var message in detail.Messages)
        {
            if (message.Role == "user")
            {
                current = new ChatTurn(message.Content, asOfLabel: null);
                asOf = message.AsOf;
                _turns.Add(current);
            }
            else if (current is not null)
            {
                // 新格式的回答帶結果種類，所以問題的日期是明確的（null 是現行）；舊資料沒有，就不標。
                if (message.Outcome is not null)
                    current.AsOfLabel = AsOfLabelOf(asOf);

                current.StoredAnswer = StoredText(message, current.AsOfLabel);
                current.State = TurnState.Answered;
                if (message.Outcome == ConversationOutcome.Answered)
                {
                    current.MemoryText = message.Content;
                    current.MemoryCitations = message.Citations ?? [];
                }
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
        var turn = new ChatTurn(text, AsOfLabelOf(asOfValue));
        var history = HistoryOf();
        _turns.Add(turn);
        Busy = true;
        Changed?.Invoke();

        try
        {
            var result = await ask.AskAsync(user, text, asOfValue, history, new StageProgress(turn, this), cancellationToken);
            if (Present(turn, result) is { } recorded)
            {
                ConversationId = await ask.RecordAsync(
                    user, ConversationId, new ConversationTurn(text, recorded.Answer, recorded.Outcome, recorded.Citations, asOfValue), cancellationToken);
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

    /// <summary>
    /// 回答每進到下一步就更新回合並通知頁面。同步執行，不經 <see cref="Progress{T}"/> 的 SynchronizationContext：
    /// 回報來自 workflow 的執行緒，更新只是改回合的欄位，重畫由 <see cref="Changed"/> 的訂閱者回到畫面的執行緒做。
    /// </summary>
    private sealed class StageProgress(ChatTurn turn, ChatSession session) : IProgress<AnswerProgress>
    {
        public void Report(AnswerProgress value)
        {
            turn.Progress = value;
            session.Changed?.Invoke();
        }
    }

    /// <summary>拒答對使用者說的話：說明為什麼沒有答案，而不是只說「不能回答」。</summary>
    public string RefusalLabel(string? reason) => reason switch
    {
        "no_authority" => L["找不到足以回答這個問題的法條，所以沒有回答。試試換個說法，或換一天。"],
        "insufficient_evidence" => L["找到了相關的法條，但不足以直接回答這個問題。下列條文供參考；試著把問題問得更具體，例如指明行為或情形。"],
        "out_of_scope" => L["這個問題超出目前涵蓋的知識範圍，所以沒有回答。"],
        "local_law_uncovered" => L["這個問題問的是地方自治法規，而這裡只涵蓋中央法規，所以沒有回答。請向該地方政府查詢。"],
        "missing_input" => L["問題不夠具體，請補充後再問。"],
        "unverified_citation" => L["系統產生的回答無法對應到法條出處，已捨棄，沒有顯示。請換個說法再問一次。"],
        _ => L["這次沒有產生可以對應到法條出處的回答。請換個說法再問一次。"],
    };

    /// <summary>問題的日期標籤：「現行」或「2026-01-01 有效」；語言是當下的介面語言。</summary>
    private string AsOfLabelOf(string? asOf) => asOf is null ? L["現行"] : L["{0} 有效", asOf];

    /// <summary>
    /// 這串對話前面的幾輪，帶給模型當前文（.NET 通用的 <see cref="ChatMessage"/>）：每個有結果的回合是一則使用者的問題，
    /// 有答案的再接一則助理的回答，其中的 <c>[L1]</c> 換成法規名——代號只在那一輪的證據集裡有意義，換掉才不會和這一輪的代號混。
    /// 失敗的、只列條文的回合不算（沒有可接續的答案）；拒答的只留問題，使用者多半是換個說法再問。
    /// 裁成幾輪、每則多長由服務端決定，這裡全給。
    /// </summary>
    private List<ChatMessage> HistoryOf()
    {
        List<ChatMessage> messages = [];
        foreach (var turn in _turns)
        {
            if (turn.State is not (TurnState.Answered or TurnState.Refused))
                continue;

            messages.Add(new ChatMessage(ChatRole.User, turn.Question));
            if (turn.MemoryText is { } answer)
                messages.Add(new ChatMessage(ChatRole.Assistant, WithLabels(answer, turn.MemoryCitations)));
        }

        return messages;
    }

    /// <summary>把正文裡的代號換成法規名；對不上的代號直接拿掉。</summary>
    private static string WithLabels(string text, IReadOnlyList<ConversationCitation> citations) =>
        CodePattern().Replace(text, match =>
            citations.FirstOrDefault(c => c.Code == match.Groups[1].Value) is { } citation ? $"（{citation.Label}）" : string.Empty);

    /// <summary>從歷史載入的回答要顯示的文字：正文，接著每個出處一行（舊資料的出處行已經寫在正文裡）。</summary>
    private static string StoredText(ConversationMessageView message, string? asOfLabel) =>
        message.Citations is not { Count: > 0 } citations
            ? message.Content
            : $"{message.Content}\n{string.Join('\n', citations.Select(c => $"[{c.Code}] {c.Label}（{asOfLabel}） {c.Uri}"))}";

    [GeneratedRegex(@"\[(L\d+)\]")]
    private static partial Regex CodePattern();

    private sealed record Recorded(string Answer, string Outcome, IReadOnlyList<ConversationCitation> Citations);

    /// <summary>
    /// 把結果放進回合，並回傳要記進歷史的內容（沒有結果時 null）。歷史只存有出處的文字與引用（法規名、條號、條文網址），
    /// 不存條文全文：全文隨時可以由出處再查，而且查的是哪一天的版本已經寫在問題的日期裡。
    /// </summary>
    private Recorded? Present(ChatTurn turn, AskResult result)
    {
        var answering = result.Mode == AskMode.Answer;
        switch (result.Status)
        {
            case AskStatus.Ok when answering:
                turn.Answer = result.Answer;
                if (turn.Answer!.Refused)
                {
                    turn.State = TurnState.Refused;
                    // 多半沒有引用；找到法源但不足以回答時，條文照樣呈現，也一併記下出處（回頭看時才知道當時給了哪些條文）。
                    return new Recorded(
                        L["（拒答）{0}", RefusalLabel(turn.Answer.RefusalReason)],
                        ConversationOutcome.Refused,
                        [.. turn.Answer.Sources.Select(s => new ConversationCitation(s.Code, s.Citation, s.Uri))]);
                }

                turn.State = TurnState.Answered;
                IReadOnlyList<ConversationCitation> citations = [.. turn.Answer.Sources.Select(s => new ConversationCitation(s.Code, s.Citation, s.Uri))];
                turn.MemoryText = turn.Answer.Text;
                turn.MemoryCitations = citations;
                return new Recorded(turn.Answer.Text!, ConversationOutcome.Answered, citations);
            case AskStatus.Ok:
                turn.Hits = result.Hits;
                turn.State = TurnState.Listed;
                return new Recorded(
                    result.Hits!.Count == 0
                        ? L["沒有符合的條文。"]
                        : string.Join('\n', result.Hits.Select(hit => L["《{0}》第 {1} 條（{2}） {3}", hit.LawTitle, hit.Number, turn.AsOfLabel!, hit.Uri])),
                    ConversationOutcome.Listed,
                    []);
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