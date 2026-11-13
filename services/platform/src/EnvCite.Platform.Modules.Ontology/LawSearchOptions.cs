using System.ComponentModel.DataAnnotations;

using EnvCite.ServiceDefaults;

namespace EnvCite.Platform.Modules.Ontology;

/// <summary>
/// 條文搜尋的相關性規則（<c>Platform:Search</c> 區段）：一條檢索結果的語意相似度達標，或字面分數達標而且
/// 條文含有足夠比例的查詢詞，或條文含有全部查詢詞且字面臂有命中，才回傳；都沒達標的不列，不是排在後面。檢索永遠會回「最像的幾條」，沒有下界時
/// 查不到答案的詞會得到一串不相干的條文；字面臂是各詞的 OR，只沾到查詢一個詞的條文字面分數與含全部詞的
/// 相同，所以字面達標還要看覆蓋率。字面下界是為自然語句問題校準的，關鍵字查詢的整詞命中字面分數常常很低
/// （同一部法規的多個條文平分一個詞的權重），條文既然含全部查詢詞，就不再被字面下界擋下。函釋提到查詢詞並解釋的條文也列出（函釋只用來找到條文，不是依據），不看分數。
/// 字面與語意兩個共用下界不在這裡：它們與 consult 共用同一組（<see cref="RelevanceFloorsOptions"/>，值只寫在
/// ServiceDefaults 一處），這裡只有覆蓋率門檻與問句的語意下界，它們只有搜尋端用。
/// <para>
/// 問句另走較低的語意下界。同一個共用語意下界對關鍵字與整句問句的鬆緊不同：問句與條文的餘弦相似度
/// 普遍較低，共用下界（為 consult 的可答性校準）會把本來找得到的條文擋出列表。問句型查詢改用
/// <see cref="QuestionSemanticFloor"/>；其餘查詢不變，所以這條路由對原規則是超集，只會多列、不會少列。
/// 分類只看查詢字串，分錯的代價是少了增益或多列幾條，不會少找到。依據與量測見
/// <c>services/ontology/eval/query-routing-2026-11-13.md</c>。
/// </para>
/// </summary>
public sealed class LawSearchOptions : ISectionOptions
{
    public static string SectionName => "Platform:Search";

    /// <summary>
    /// 法規名稱的類型後綴（法、條例、標準…）。結尾是其中之一的查詢是法規名稱，不是問句：名稱與不存在的名稱
    /// 在語意臂上分不開（最高相似度貼著問句的下界），所以名稱維持共用下界。由題庫涉及的法規名稱統計而來
    /// （<c>tools/query_routing.py prepare</c>），已去掉結尾是較短後綴的冗餘項（辦法、理法、師法都以「法」結尾）。
    /// </summary>
    private static readonly string[] NameSuffixes = ["法", "條例", "標準", "規則", "準則", "細則"];

    /// <summary>字面達標的命中，條文含有的查詢詞至少要有這個比例（0 至 1）。</summary>
    [Range(0.0, 1.0)]
    public double MinTermCoverage { get; set; } = 0.5;

    /// <summary>
    /// 問句型查詢的語意下界（0 至 1）。實際生效的是它與共用語意下界中較低的一個，所以不會比共用下界嚴。
    /// 開發集無答案問句的最高語意相似度是 0.649，下界要高過它至少 0.03 才不貼著開發集的邊緣。
    /// </summary>
    [Range(0.0, 1.0)]
    public double QuestionSemanticFloor { get; set; } = 0.68;

    /// <summary>沒有問號的查詢，字數達到這個數就當問句（關鍵字與法規用語多在這個長度以下）。</summary>
    [Range(1, 200)]
    public int QuestionMinLength { get; set; } = 10;

    /// <summary>問句：結尾是問號，或不是法規名稱而且夠長。</summary>
    internal bool IsQuestion(string query)
    {
        var text = query.Trim();
        var body = text.TrimEnd('？', '?');
        if (NameSuffixes.Any(s => body.EndsWith(s, StringComparison.Ordinal)))
            return false;

        return body.Length < text.Length || body.Length >= QuestionMinLength;
    }

    internal bool Passes(
        RelevanceFloorsOptions floors, string query, float lexicalScore, float semanticSimilarity, float termCoverage, int guidanceDocuments)
    {
        var semanticFloor = IsQuestion(query) ? Math.Min(floors.SemanticFloor, QuestionSemanticFloor) : floors.SemanticFloor;
        return guidanceDocuments > 0
            || semanticSimilarity >= semanticFloor
            || (lexicalScore >= floors.LexicalFloor && termCoverage >= MinTermCoverage)
            || (lexicalScore > 0 && termCoverage >= 1f);
    }
}

[OptionsValidator]
public sealed partial class LawSearchOptionsValidator : IValidateOptions<LawSearchOptions>;