using System.Buffers;

using EnvCite.Platform.Modules.Ontology;

namespace EnvCite.Platform.Modules.Consult;

/// <summary>
/// 使用者只打了一個主題詞（「露天燃燒」「噪音 罰則」），沒有提出問句。
/// <para>
/// 為什麼要有這道判準：模型拿到「問題：露天燃燒」這種沒有問句的輸入，會照系統提示「證據不足以回答問題時只回覆『證據不足』」
/// 拒答，即使證據集裡就有直接規範它的條文。這個約束放在提示詞裡不可靠，所以由程式判斷，命中時把送給模型的問題
/// 換成一道明確的任務句（<see cref="GroundedAnswerer"/>）。
/// </para>
/// <para>
/// 判準只看輸入的形態，不猜語意：它決定的是怎麼向模型表達任務，可逐字複驗，不隨模型版本漂移。
/// 「是不是問句」讀搜尋端的同一份判準（<see cref="LawSearchOptions.Shape"/>：結尾是問號，或不是法規名稱而且字數夠長），
/// 在這之上再加兩道只有入口需要的條件（沒有句末標點、沒有疑問詞），所以主題詞一定不是搜尋端的問句，反過來不一定。
/// 判準取嚴，因為命中時送給模型的問題會被換掉：把使用者真的提出的短問句誤當成主題詞，模型回答的就是另一個問題。
/// 漏認的代價小：模型回「證據不足」時，找到的條文仍會列給使用者（<c>insufficient_evidence</c>）。
/// </para>
/// </summary>
public static class BareTopic
{
    /// <summary>句末標點：出現任一個就代表使用者寫的是句子，不是主題詞。</summary>
    private static readonly SearchValues<char> SentencePunctuation = SearchValues.Create("。！？；.!?;");

    /// <summary>疑問詞：命中任一個就代表使用者提出了問句，不是主題詞。</summary>
    private static readonly string[] Interrogatives =
    [
        "嗎", "呢", "如何", "怎麼", "怎樣", "是否", "可否", "能否", "得否", "應否", "為何", "為什麼",
        "什麼", "哪", "多少", "幾條", "有無", "要不要", "該不該", "算不算",
    ];

    /// <summary>非空、搜尋端不當問句、沒有句末標點、沒有疑問詞，四項全中才是主題詞。</summary>
    public static bool Is(string? text)
    {
        var trimmed = text?.Trim();
        if (string.IsNullOrEmpty(trimmed) || LawSearchOptions.Shape(trimmed) == QueryKind.Question)
            return false;

        if (trimmed.AsSpan().IndexOfAny(SentencePunctuation) >= 0)
            return false;

        return !Interrogatives.Any(word => trimmed.Contains(word, StringComparison.Ordinal));
    }
}