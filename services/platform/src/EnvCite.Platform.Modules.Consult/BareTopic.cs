using System.Buffers;

namespace EnvCite.Platform.Modules.Consult;

/// <summary>
/// 使用者只打了一個主題詞（「露天燃燒」「噪音 罰則」），沒有提出問句。
/// <para>
/// 為什麼要有這道判準：模型拿到「問題：露天燃燒」這種沒有問句的輸入，會照系統提示「證據不足以回答問題時只回覆『證據不足』」
/// 拒答，即使證據集裡就有直接規範它的條文。這個約束放在提示詞裡不可靠，所以由程式判斷，命中時把送給模型的問題
/// 換成一道明確的任務句（<see cref="GroundedAnswerer"/>）。
/// </para>
/// <para>
/// 判準只看輸入的形態（長度、句末標點、疑問詞），不猜語意：它決定的是怎麼向模型表達任務，可逐字複驗，不隨模型版本漂移。
/// 判準取嚴（四項條件全中才算），因為命中時送給模型的問題會被換掉：把使用者真的提出的短問句誤當成主題詞，
/// 模型回答的就是另一個問題。漏認的代價小：模型回「證據不足」時，找到的條文仍會列給使用者（<c>insufficient_evidence</c>）。
/// </para>
/// </summary>
public static class BareTopic
{
    /// <summary>主題詞的長度上限（字元）。實際打進輸入框的行為詞多在 4–8 字，再長就開始出現成句的敘述。</summary>
    public const int MaxChars = 12;

    /// <summary>句末標點：出現任一個就代表使用者寫的是句子，不是主題詞。</summary>
    private static readonly SearchValues<char> SentencePunctuation = SearchValues.Create("。！？；.!?;");

    /// <summary>疑問詞：命中任一個就代表使用者提出了問句，不是主題詞。</summary>
    private static readonly string[] Interrogatives =
    [
        "嗎", "呢", "如何", "怎麼", "怎樣", "是否", "可否", "能否", "得否", "應否", "為何", "為什麼",
        "什麼", "哪", "多少", "幾條", "有無", "要不要", "該不該", "算不算",
    ];

    /// <summary>非空、不超過 <see cref="MaxChars"/>、沒有句末標點、沒有疑問詞，四項全中才是主題詞。</summary>
    public static bool Is(string? text)
    {
        var trimmed = text?.Trim();
        if (string.IsNullOrEmpty(trimmed) || trimmed.Length > MaxChars)
            return false;

        if (trimmed.AsSpan().IndexOfAny(SentencePunctuation) >= 0)
            return false;

        return !Interrogatives.Any(word => trimmed.Contains(word, StringComparison.Ordinal));
    }
}