using Microsoft.Extensions.AI;

namespace EnvCite.Platform.SharedKernel;

/// <summary>一筆被引用的法源。<paramref name="Code"/> 是這次回答內的代號（<c>L1</c>…）。</summary>
public sealed record AnswerSource(string Code, string Uri, string Citation, string Text);

/// <summary>
/// 有出處的回答，或拒答。<see cref="Text"/> 為 null 就是拒答（<see cref="RefusalReason"/> 說明原因）。
/// 回答裡的 <c>[L1]</c> 標記一定對得上 <see cref="Sources"/>：對不上的回答不會送到這裡，而是變成拒答。
/// 拒答多半沒有法源；只有 <c>insufficient_evidence</c>（找到了法源、模型判定不足以回答）會帶著 <see cref="Sources"/>：
/// 它們是 consult 給的封閉證據集，有出處、可核對，呈現給使用者比空白的拒答有用。
/// </summary>
public sealed record GroundedAnswer(string? Text, IReadOnlyList<AnswerSource> Sources, string? RefusalReason = null, string? RefusalDetail = null)
{
    public bool Refused => Text is null;
}

/// <summary>回答進行到哪一步：（有前文時）先把追問改寫成獨立的問句，再向 consult 取證據，讓模型寫，最後核對引用。</summary>
public enum AnswerStage
{
    /// <summary>只有帶著前文的追問才有這一步。</summary>
    Rewriting,
    Grounding,
    Writing,
    Verifying,
}

/// <param name="Stage">進行到哪一步。</param>
/// <param name="Sources"><see cref="AnswerStage.Writing"/> 起才有意義：找到幾條法源。</param>
public readonly record struct AnswerProgress(AnswerStage Stage, int Sources = 0);

/// <summary>
/// 模型端點有沒有設定（<c>Platform:Answer:Enabled</c>）。singleton，只讀設定。
/// 與 <see cref="IGroundedAnswerer"/> 分開：後者是 scoped（計量要目前請求的租戶與 <c>DbContext</c>），
/// Blazor circuit 若為了這個旗標解析它，會把那個 context 抓住直到斷線。
/// </summary>
public interface IAnswerAvailability
{
    bool Available { get; }
}

/// <summary>
/// 以封閉證據集回答法規問題：先向 consult 取得證據（沒有就拒答，不呼叫模型），再讓模型<b>只根據這些證據</b>作答，
/// 並檢查回答引用的每個代號都在證據裡；沒有引用或引用不存在的代號，整個回答作廢、改為拒答。
/// 以 scoped 註冊（計量需要目前請求的租戶）。呼叫端負責確認租戶有方案（完全付費制）。
/// 端點有沒有設定看 <see cref="IAnswerAvailability"/>，不看這裡。
/// </summary>
public interface IGroundedAnswerer
{
    /// <returns>
    /// <see cref="AdminStatus.Invalid"/>：輸入錯誤；<see cref="AdminStatus.Unavailable"/>：consult 或模型暫時不可用
    /// （模型的錯誤內容不回傳，因為它可能夾帶問題文字）。拒答是成功的結果，不是錯誤。
    /// </returns>
    /// <param name="history">
    /// 同一串對話前面的幾輪（<see cref="ChatMessage"/>：User 是問題、Assistant 是答案，依時間由舊到新）；第一次提問給空的。
    /// 它只用來理解追問（把代名詞與省略補成獨立的問句）與接續語氣，<b>不是證據</b>：回答仍只能引用這一輪證據集的代號，
    /// 前文裡的代號會被剝掉。實際帶進模型的長度由 <see cref="IChatReducer"/> 與設定決定，呼叫端不必先裁。
    /// </param>
    /// <param name="progress">每進到下一步就回報一次（可為 null）；已有結論的回合（拒答、失敗）不會回報後面的步驟。回報不帶問題或證據的內容，只有步驟與條數。</param>
    Task<AdminOutcome<GroundedAnswer>> AnswerAsync(
        string? question, string? asOf, IReadOnlyList<ChatMessage> history, IProgress<AnswerProgress>? progress, CancellationToken cancellationToken);
}