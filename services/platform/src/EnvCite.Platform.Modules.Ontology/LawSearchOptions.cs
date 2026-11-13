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
/// 查詢依字串分三型，各用不同的鬆緊（分類只看查詢字串，見 <see cref="Classify"/>）：
/// 問句另走較低的語意下界（<see cref="QuestionSemanticFloor"/>）：問句與條文的餘弦相似度普遍較低，共用下界
/// （為 consult 的可答性校準）會把本來找得到的條文擋出列表，這條路由對原規則是超集，只會多列。
/// 關鍵字另走較高的覆蓋率門檻（<see cref="KeywordMinTermCoverage"/>）：關鍵字的整詞命中不靠門檻，
/// 門檻只決定片段命中放不放行，所以收緊它擋掉的是雜訊；這條路由比原規則嚴，不是超集，不少找到的證據是量測的，
/// 不是規則的性質。法規名稱維持原規則：名稱與不存在的名稱在語意臂上分不開，放寬與收緊都有代價。
/// 依據與量測見 <c>services/ontology/eval/query-routing-2026-11-13.md</c> 與
/// <c>services/ontology/eval/keyword-coverage-2026-11-13.md</c>。
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

    /// <summary>字面達標的命中，條文含有的查詢詞至少要有這個比例（0 至 1）；問句與法規名稱用這個值。</summary>
    [Range(0.0, 1.0)]
    public double MinTermCoverage { get; set; } = 0.5;

    /// <summary>
    /// 關鍵字型查詢的覆蓋率門檻（0 至 1），比 <see cref="MinTermCoverage"/> 嚴。收緊它只擋片段命中，
    /// 整詞命中（覆蓋率 1）不受影響；名稱類用 0.7 會少找到舊名，所以不套用。
    /// </summary>
    [Range(0.0, 1.0)]
    public double KeywordMinTermCoverage { get; set; } = 0.7;

    /// <summary>
    /// 問句型查詢的語意下界（0 至 1）。實際生效的是它與共用語意下界中較低的一個，所以不會比共用下界嚴。
    /// 開發集無答案問句的最高語意相似度是 0.649，下界要高過它至少 0.03 才不貼著開發集的邊緣。
    /// </summary>
    [Range(0.0, 1.0)]
    public double QuestionSemanticFloor { get; set; } = 0.68;

    /// <summary><see cref="QuestionMinLength"/> 的預設值；入口的主題詞判準（<c>BareTopic</c>）用同一個切點，不另訂。</summary>
    public const int DefaultQuestionMinLength = 10;

    /// <summary>沒有問號的查詢，字數達到這個數就當問句（關鍵字與法規用語多在這個長度以下）。</summary>
    [Range(1, 200)]
    public int QuestionMinLength { get; set; } = DefaultQuestionMinLength;

    /// <summary>
    /// 查詢型別：結尾是類型後綴的是名稱（即使有問號）；否則結尾有問號、或字數夠長的是問句；其餘是關鍵字。
    /// </summary>
    internal QueryKind Classify(string query) => Shape(query, QuestionMinLength);

    /// <summary>
    /// <see cref="Classify"/> 的判準本身，只看查詢字串。靜態公開，是為了讓入口的主題詞判準與搜尋端讀同一份：
    /// 同一個輸入不能在搜尋端是問句、在入口卻是主題詞。
    /// </summary>
    public static QueryKind Shape(string query, int questionMinLength = DefaultQuestionMinLength)
    {
        var text = query.Trim();
        var body = text.TrimEnd('？', '?');
        if (NameSuffixes.Any(s => body.EndsWith(s, StringComparison.Ordinal)))
            return QueryKind.Name;

        return body.Length < text.Length || body.Length >= questionMinLength ? QueryKind.Question : QueryKind.Keyword;
    }

    internal bool IsQuestion(string query) => Classify(query) == QueryKind.Question;

    internal bool Passes(
        RelevanceFloorsOptions floors, string query, float lexicalScore, float semanticSimilarity, float termCoverage, int guidanceDocuments)
    {
        var kind = Classify(query);
        var semanticFloor = kind == QueryKind.Question ? Math.Min(floors.SemanticFloor, QuestionSemanticFloor) : floors.SemanticFloor;
        var coverage = kind == QueryKind.Keyword ? KeywordMinTermCoverage : MinTermCoverage;
        return guidanceDocuments > 0
            || semanticSimilarity >= semanticFloor
            || (lexicalScore >= floors.LexicalFloor && termCoverage >= coverage)
            || (lexicalScore > 0 && termCoverage >= 1f);
    }
}

/// <summary>搜尋查詢的型別，決定用哪一套相關性規則。</summary>
public enum QueryKind
{
    Keyword,
    Question,
    Name,
}

[OptionsValidator]
public sealed partial class LawSearchOptionsValidator : IValidateOptions<LawSearchOptions>;