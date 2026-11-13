namespace EnvCite.Platform.Modules.Consult;

/// <summary>
/// 有依據的回答 workflow 的節點之間傳遞的一個回合。不可變：每個節點回傳加了自己那一份的新回合。
/// 問題文字是客戶案情，所以這個型別不實作任何會被框架序列化、記錄或轉成字串輸出的成員（只在程序內傳遞）。
/// </summary>
/// <param name="Question">使用者的原句（已去頭尾空白）；作答與核驗永遠用原句，改寫只用在取證據。</param>
/// <param name="AsOf">依哪一天施行的條文；省略表示現行。</param>
internal sealed record AnswerTurn(string Question, string? AsOf)
{
    /// <summary>整理過的前文（User／Assistant 交替、由使用者的問題開頭、代號已剝掉）；第一次提問是空的。</summary>
    public IReadOnlyList<Microsoft.Extensions.AI.ChatMessage> History { get; init; } = [];

    /// <summary>追問改寫成的獨立問句，只用來取證據；沒改寫（第一次提問、改寫失敗）是 null，取證據用 <see cref="Question"/>。</summary>
    public string? Standalone { get; init; }

    /// <summary>consult 給的封閉證據集；代號由程式指派。</summary>
    public IReadOnlyList<AnswerSource> Sources { get; init; } = [];

    /// <summary>模型寫的散文，尚未核驗。</summary>
    public string? ModelText { get; init; }

    /// <summary>已有結論（拒答或失敗）時設定；後面的節點見到就原樣放行，不再做事。</summary>
    public AdminOutcome<GroundedAnswer>? Done { get; init; }

    // 記錄型別預設的 ToString 會印出所有屬性，包括問題文字；這裡明確擋掉。
    public override string ToString() => nameof(AnswerTurn);
}