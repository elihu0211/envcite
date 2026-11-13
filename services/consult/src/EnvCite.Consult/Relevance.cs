using EnvCite.ServiceDefaults;

using Microsoft.Extensions.Options;

namespace EnvCite.Consult;

/// <summary>
/// 相關性下界（<c>Relevance</c> 區段），本服務唯一的下界來源。低於下界的檢索資料視為與問題無關。下界都是受模型制約的值，
/// 不是領域常數：只在產生它的評分方式（嵌入模型、分詞器、檢索範圍）下才可比較，評分方式一旦改變就必須重新量測後重訂。
/// 字面分數是未正規化的 <c>ts_rank_cd</c>，隨語料大小與檢索範圍而變。值由離線風險—覆蓋掃描選出
/// （<c>services/ontology/tools/floor_risk_coverage.py</c>）：可答問句至少 97% 通過，在其下讓最少的無答案問句通過。
/// </summary>
internal sealed class RelevanceOptions : ISectionOptions
{
    public static string SectionName => "Relevance";

    /// <summary>
    /// 高信心的字面線是下界的這個倍數：高信心要比「相關」更強，而兩者之間沒有獨立的量測準則可定，只保留固定比例。
    /// </summary>
    public const double HighConfidenceLexicalRatio = 1.5;

    /// <summary>字面臂分數的下界。</summary>
    public double LexicalFloor { get; init; }

    /// <summary>語意臂餘弦相似度的下界。</summary>
    public double SemanticFloor { get; init; }

    /// <summary>語意臂達到此強度表示高度語意對齊。</summary>
    public double HighConfidenceSemantic { get; init; }

    public double HighConfidenceLexical => LexicalFloor * HighConfidenceLexicalRatio;

    /// <summary>單一條文是否通過下界：字面臂或語意臂任一臂達標。</summary>
    public bool IsRelevant(Article article) =>
        article.SemanticSimilarity >= SemanticFloor || article.LexicalScore >= LexicalFloor;

    /// <summary>至少有一個來源達到此強度，即表示答案建立在直接的依據上。</summary>
    public bool IsHighConfidence(Article article) =>
        article.SemanticSimilarity >= HighConfidenceSemantic || article.LexicalScore >= HighConfidenceLexical;
}

/// <summary>
/// 字面下界為正；語意下界在 (0, 1) 且低於高信心線，高信心線不超過 1。缺鍵即為 0，啟動失敗，不靜默退回某個預設值。
/// </summary>
internal sealed class RelevanceOptionsValidator : IValidateOptions<RelevanceOptions>
{
    public ValidateOptionsResult Validate(string? name, RelevanceOptions options)
    {
        var failures = new List<string>();
        var section = RelevanceOptions.SectionName;
        if (!(options.LexicalFloor > 0))
            failures.Add($"{section}:LexicalFloor must be positive.");

        if (!(options.SemanticFloor > 0 && options.SemanticFloor < 1))
            failures.Add($"{section}:SemanticFloor must be between 0 and 1.");

        if (!(options.HighConfidenceSemantic > options.SemanticFloor && options.HighConfidenceSemantic <= 1))
            failures.Add($"{section}:HighConfidenceSemantic must be above SemanticFloor and at most 1.");

        return failures.Count == 0 ? ValidateOptionsResult.Success : ValidateOptionsResult.Fail(failures);
    }
}