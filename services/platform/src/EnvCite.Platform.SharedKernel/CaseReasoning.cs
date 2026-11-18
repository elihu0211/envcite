using System.Text.Json.Serialization;

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// 推論的結論，用語採台灣法學術語（<see cref="ConclusionTerms"/>）。「該當」只指構成要件該當：不稱違法、不稱應罰，
/// 違法性與有責性不在結論的判斷範圍。
/// </summary>
[JsonConverter(typeof(JsonStringEnumConverter<ConclusionVerdict>))]
public enum ConclusionVerdict
{
    /// <summary>構成要件該當：要件事實均已涵攝成立。</summary>
    Satisfied,

    /// <summary>構成要件不該當。</summary>
    NotSatisfied,

    /// <summary>但書或例外的要件尚待查明；該要件不成立時該當。</summary>
    ConditionallySatisfied,

    /// <summary>但書或例外的要件尚待查明；該要件成立時不該當。</summary>
    ConditionallyNotSatisfied,

    /// <summary>欠缺要件事實，無從涵攝。</summary>
    Undetermined,

    /// <summary>本文之適用被成立的但書排除。</summary>
    Excluded,

    /// <summary>該規定不及於本案，例如主體不符。</summary>
    NotApplicable,
}

/// <summary>結論的用語：由系統固定輸出，不經模型。</summary>
public static class ConclusionTerms
{
    public const string Heading = "候選結論（依已確認的規範陳述與問句明說的事實推得）";

    public const string Disclaimer = "以上為候選結論，不是行政處分；違法性與有責性不在判斷範圍。";

    public static string Of(ConclusionVerdict verdict) => verdict switch
    {
        ConclusionVerdict.Satisfied => "該當",
        ConclusionVerdict.NotSatisfied => "不該當",
        ConclusionVerdict.ConditionallySatisfied => "附條件該當",
        ConclusionVerdict.ConditionallyNotSatisfied => "附條件不該當",
        ConclusionVerdict.Undetermined => "事實不明",
        ConclusionVerdict.Excluded => "但書排除",
        ConclusionVerdict.NotApplicable => "不適用",
        _ => throw new ArgumentOutOfRangeException(nameof(verdict), verdict, null),
    };

    /// <summary>
    /// 說明文字不得出現的結論詞：結論只由系統用固定格式輸出，模型的說明不能重述或改寫它。
    /// 「違反」「違法」「應罰」連同六個結論用語，一律視為越界。
    /// </summary>
    public static IReadOnlyList<string> Forbidden { get; } =
    [
        "該當", "不該當", "附條件該當", "附條件不該當", "事實不明", "但書排除", "違反", "違法", "應罰",
    ];
}

/// <summary>候選罰則的法定額度範圍；系統只列範圍與是否裁量，不選額。</summary>
public sealed record PenaltyRange(string Effect, long? MinAmount, long? MaxAmount, bool Conditional, bool Discretionary);

/// <summary>結論區塊裡的一則結論。<paramref name="No"/> 是這個回答內的編號，說明以它引用。</summary>
public sealed record Conclusion(
    int No,
    ConclusionVerdict Verdict,
    IReadOnlyList<string> CitationCodes,
    IReadOnlyList<string> MissingFacts,
    IReadOnlyList<string> NeedsAssessment,
    IReadOnlyList<PenaltyRange> Penalties);

/// <summary>推論所採用的一項事實與問句裡的原文片段：只是揭露，沒有確認步驟。</summary>
public sealed record UsedFact(string Concept, string Name, bool? Flag, string? Number, string Quote);

/// <summary>
/// 結論區塊：由推論結果用固定格式填寫，不經模型。<paramref name="AsOf"/> 是推論用的日期（行為時與裁處時同為這一天）；
/// <paramref name="AsOfAssumed"/> 為真表示問句沒有明說日期、以今日為準。<paramref name="OmittedCount"/> 是所依條文不在
/// 本次證據內、因而沒有列出的結論數。
/// </summary>
public sealed record ConclusionBlock(
    IReadOnlyList<Conclusion> Items,
    IReadOnlyList<UsedFact> Facts,
    string AsOf,
    bool AsOfAssumed,
    int OmittedCount) : AnswerBlock;

/// <summary>問句抽出、通過檢查的一項事實。</summary>
public sealed record CaseFact(string Concept, string Name, bool? Flag, string? Number, string Quote);

/// <summary>某則陳述的推論結果，連同它所讀條文版本的網址（供對到證據集）。</summary>
public sealed record StatementConclusion(
    string SourceUri,
    ConclusionVerdict Verdict,
    IReadOnlyList<string> MissingConcepts,
    IReadOnlyList<string> NeedsAssessmentConcepts,
    IReadOnlyList<PenaltyRange> Penalties);

public sealed record CaseConclusions(IReadOnlyList<StatementConclusion> Items);

/// <summary>
/// 問句的案件事實與推論：回答管線在取得封閉證據集之後呼叫。兩個呼叫都可能失敗（沒設定、逾時、容量用盡），
/// 呼叫端一律退回沒有推論的做法，不讓推論失敗造成拒答。以 scoped 註冊（計量需要目前請求的租戶）。
/// </summary>
public interface ICaseReasoner
{
    /// <summary>問句裡明說的事實；沒有明說的事實時是空的清單。</summary>
    Task<Outcome<IReadOnlyList<CaseFact>>> ExtractFactsAsync(string question, CancellationToken cancellationToken);

    /// <summary>以事實推論；<paramref name="asOf"/>（<c>YYYY-MM-DD</c>）同時是行為日與裁處日。</summary>
    Task<Outcome<CaseConclusions>> EvaluateAsync(IReadOnlyList<CaseFact> facts, string asOf, CancellationToken cancellationToken);
}