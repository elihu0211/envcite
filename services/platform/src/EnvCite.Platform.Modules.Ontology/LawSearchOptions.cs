using System.ComponentModel.DataAnnotations;

using EnvCite.ServiceDefaults;

namespace EnvCite.Platform.Modules.Ontology;

/// <summary>
/// 條文搜尋的相關性規則（<c>Platform:Search</c> 區段）：一條檢索結果的語意相似度達標，或字面分數達標而且
/// 條文含有足夠比例的查詢詞，或條文含有全部查詢詞且字面臂有命中，才回傳；都沒達標的不列，不是排在後面。檢索永遠會回「最像的幾條」，沒有下界時
/// 查不到答案的詞會得到一串不相干的條文；字面臂是各詞的 OR，只沾到查詢一個詞的條文字面分數與含全部詞的
/// 相同，所以字面達標還要看覆蓋率。字面下界是為自然語句問題校準的，關鍵字查詢的整詞命中字面分數常常很低
/// （同一部法規的多個條文平分一個詞的權重），條文既然含全部查詢詞，就不再被字面下界擋下。函釋提到查詢詞並解釋的條文也列出（函釋只用來找到條文，不是依據），不看分數。
/// 字面與語意兩個下界不在這裡：它們與 consult 共用同一組（<see cref="RelevanceFloorsOptions"/>，值只寫在
/// ServiceDefaults 一處），這裡只有覆蓋率門檻，它只有搜尋端用。
/// </summary>
public sealed class LawSearchOptions : ISectionOptions
{
    public static string SectionName => "Platform:Search";

    /// <summary>字面達標的命中，條文含有的查詢詞至少要有這個比例（0 至 1）。</summary>
    [Range(0.0, 1.0)]
    public double MinTermCoverage { get; set; } = 0.5;

    internal bool Passes(
        RelevanceFloorsOptions floors, float lexicalScore, float semanticSimilarity, float termCoverage, int guidanceDocuments) =>
        guidanceDocuments > 0
        || semanticSimilarity >= floors.SemanticFloor
        || (lexicalScore >= floors.LexicalFloor && termCoverage >= MinTermCoverage)
        || (lexicalScore > 0 && termCoverage >= 1f);
}

[OptionsValidator]
public sealed partial class LawSearchOptionsValidator : IValidateOptions<LawSearchOptions>;