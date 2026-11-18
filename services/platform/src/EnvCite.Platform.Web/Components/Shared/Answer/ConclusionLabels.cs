using EnvCite.Platform.Web.Resources;

using Microsoft.Extensions.Localization;

namespace EnvCite.Platform.Web.Components.Shared.Answer;

/// <summary>
/// 結論區塊上的文字與語氣。中文的結論用語與 <see cref="ConclusionTerms"/> 是同一組（測試守住），英文是另一套說法，
/// 但仍然不說「違法」或「應罰」。這裡放在 <c>Components/</c> 之下，讓本地化的鍵與英文資源檔一起被掃描。
/// </summary>
internal static class ConclusionLabels
{
    /// <summary>
    /// 徽章語氣：該當用警示色，不該當與但書排除用中性的資訊色，事實不明用灰。不用紅色與綠色：
    /// 紅色讀起來像「違法」、綠色讀起來像「合法」，而結論兩者都不是。
    /// </summary>
    public static string Tone(ConclusionVerdict verdict) => verdict switch
    {
        ConclusionVerdict.Satisfied or ConclusionVerdict.ConditionallySatisfied => "warning",
        ConclusionVerdict.NotSatisfied or ConclusionVerdict.ConditionallyNotSatisfied or ConclusionVerdict.Excluded => "info",
        _ => "neutral",
    };

    public static string Verdict(ConclusionVerdict verdict, IStringLocalizer<Ui> L) => verdict switch
    {
        ConclusionVerdict.Satisfied => L["該當"],
        ConclusionVerdict.NotSatisfied => L["不該當"],
        ConclusionVerdict.ConditionallySatisfied => L["附條件該當"],
        ConclusionVerdict.ConditionallyNotSatisfied => L["附條件不該當"],
        ConclusionVerdict.Undetermined => L["事實不明"],
        ConclusionVerdict.Excluded => L["但書排除"],
        ConclusionVerdict.NotApplicable => L["不適用"],
        _ => throw new ArgumentOutOfRangeException(nameof(verdict), verdict, null),
    };

    /// <summary>候選罰則的一行：效果、法定額度範圍，以及附條件與得裁量。認不得的效果名稱原樣顯示，不吞掉。</summary>
    public static string Penalty(PenaltyRange penalty, IStringLocalizer<Ui> L)
    {
        var effect = penalty.Effect switch
        {
            "Fine" => L["罰鍰"],
            "Confiscation" => L["沒入"],
            "Restriction" => L["限制或禁止行為"],
            "Deprivation" => L["剝奪或消滅資格、權利"],
            "Reputation" => L["影響名譽"],
            "Warning" => L["警告"],
            "Order" => L["命令（非裁罰性）"],
            "Disgorgement" => L["追繳不法利得"],
            var other => other,
        };
        List<string> parts = [effect];
        switch (penalty.MinAmount, penalty.MaxAmount)
        {
            case (long min, long max):
                parts.Add(L["新臺幣 {0:N0} 元至 {1:N0} 元", min, max]);
                break;
            case (long min, null):
                parts.Add(L["新臺幣 {0:N0} 元以上", min]);
                break;
            case (null, long max):
                parts.Add(L["新臺幣 {0:N0} 元以下", max]);
                break;
        }

        if (penalty.Conditional)
            parts.Add(L["附條件"]);
        if (penalty.Discretionary)
            parts.Add(L["得裁量"]);
        return string.Join(" · ", parts);
    }

    /// <summary>一項事實的值：旗標是「是」或「否」，數值原樣。</summary>
    public static string Value(UsedFact fact, IStringLocalizer<Ui> L) =>
        fact.Flag is { } flag ? (flag ? L["是"] : L["否"]) : fact.Number ?? string.Empty;
}