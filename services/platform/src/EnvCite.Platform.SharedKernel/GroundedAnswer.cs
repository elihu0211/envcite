namespace EnvCite.Platform.SharedKernel;

/// <summary>一筆被引用的法源。<paramref name="Code"/> 是這次回答內的代號（<c>L1</c>…）。</summary>
public sealed record AnswerSource(string Code, string Uri, string Citation, string Text);

/// <summary>
/// 有出處的回答，或拒答。<see cref="Text"/> 為 null 就是拒答（<see cref="RefusalReason"/> 說明原因）。
/// 回答裡的 <c>[L1]</c> 標記一定對得上 <see cref="Sources"/>：對不上的回答不會送到這裡，而是變成拒答。
/// </summary>
public sealed record GroundedAnswer(string? Text, IReadOnlyList<AnswerSource> Sources, string? RefusalReason = null, string? RefusalDetail = null)
{
    public bool Refused => Text is null;
}

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
    Task<AdminOutcome<GroundedAnswer>> AnswerAsync(string? question, string? asOf, CancellationToken cancellationToken);
}