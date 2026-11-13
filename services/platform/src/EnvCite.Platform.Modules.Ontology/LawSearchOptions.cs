using System.ComponentModel.DataAnnotations;

using EnvCite.ServiceDefaults;

namespace EnvCite.Platform.Modules.Ontology;

/// <summary>
/// 條文搜尋的相關性下界（<c>Platform:Search</c> 區段）：一條檢索結果的語意相似度達標，或字面分數達標而且
/// 條文含有足夠比例的查詢詞，才回傳；都沒達標的不列，不是排在後面。檢索永遠會回「最像的幾條」，沒有下界時
/// 查不到答案的詞會得到一串不相干的條文；字面臂是各詞的 OR，只沾到查詢一個詞的條文字面分數與含全部詞的
/// 相同，所以字面達標還要看覆蓋率。
/// 前兩個數字與 consult 的 <c>Relevance:LexicalFloor</c>、<c>Relevance:SemanticFloor</c> 是同一組：
/// 它們量的是同一個檢索，兩處對「夠不夠相關」要有同一個答案，改的時候兩處一起改。覆蓋率只有這裡用。
/// </summary>
public sealed class LawSearchOptions : ISectionOptions
{
    public static string SectionName => "Platform:Search";

    [Range(0.0001, 100)]
    public double LexicalFloor { get; set; } = 0.5;

    [Range(0.0001, 0.9999)]
    public double SemanticFloor { get; set; } = 0.71;

    /// <summary>字面達標的命中，條文含有的查詢詞至少要有這個比例（0 至 1）。</summary>
    [Range(0.0, 1.0)]
    public double MinTermCoverage { get; set; } = 0.5;

    internal bool Passes(float lexicalScore, float semanticSimilarity, float termCoverage) =>
        semanticSimilarity >= SemanticFloor || (lexicalScore >= LexicalFloor && termCoverage >= MinTermCoverage);
}

[OptionsValidator]
public sealed partial class LawSearchOptionsValidator : IValidateOptions<LawSearchOptions>;