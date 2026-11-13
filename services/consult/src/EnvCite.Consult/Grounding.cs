using System.Collections.Frozen;

using EnvCite.Ontology.V2;

using Microsoft.Extensions.Options;

namespace EnvCite.Consult;

internal enum Confidence
{
    Low,
    High,
}

internal enum RefusalReason
{
    NoEvidence,
    OutOfScope,
    MissingInput,
    LocalLawUncovered,
}

internal sealed record Citation(
    string Code,
    string Iri,
    string Label,
    string Text,
    string Ontology,
    LawIdentifier? LawIdentifier = null);

/// <summary>一次管線執行的結果：<see cref="Evidence"/> 或 <see cref="Refusal"/>，恰有其一。</summary>
internal abstract record GroundingOutcome;

/// <summary>呼叫端的模型可引用的封閉集合，以及其中最強依據的信心度。</summary>
internal sealed record Evidence(IReadOnlyList<Citation> Citations, Confidence Confidence) : GroundingOutcome;

/// <summary>拒答是一種結果，不是錯誤：呼叫端應將其呈現出來，而非重試。</summary>
internal sealed record Refusal(RefusalReason Reason, string Detail) : GroundingOutcome;

/// <summary>
/// 接地管線的各階段、其順序，以及由誰決定什麼。本服務不執行語言模型；完整的問答有五個階段，本服務擁有前三個，
/// 後兩個在呼叫端，由呼叫端自己的模型執行：
/// <list type="number">
/// <item><b>retrieve</b>：由本體決定存在什麼</item>
/// <item><b>ground</b>：由程式碼指派封閉的引用集合</item>
/// <item><b>assess</b>：由規則決定這個問題到底能不能回答</item>
/// <item><b>generate</b>（呼叫端）：呼叫端的模型撰寫文字，只能以代碼引用</item>
/// <item><b>verify</b>（呼叫端）：呼叫端刪除模型自行捏造的任何代碼</item>
/// </list>
/// 模型因此只決定一件事——如何以交給它的證據措辭成答案。可引用什麼、能不能答，都在它執行前就已決定，
/// 並隨證據一起交給呼叫端。
/// </summary>
internal static class Grounding
{
    /// <summary>封閉引用集合設有上限，讓證據區塊維持在模型能可靠關注的範圍內。超出的來源直接捨棄，而非加以摘要。</summary>
    public const int MaxCitations = 8;

    public const string Law = "law";

    /// <summary>本體代號 → 引用代號的字母前綴。一個本體一個字母，新增本體時在此登記，跨本體的代號才不會相撞。</summary>
    public static FrozenDictionary<string, string> CodePrefix { get; } =
        new Dictionary<string, string> { [Law] = "L" }.ToFrozenDictionary(StringComparer.Ordinal);

    /// <summary>
    /// 指派封閉的引用集合：依檢索順序編為 L1、L2、…。代碼由程式碼指派，絕不由模型指派，而這個對應關係正是
    /// 引用得以查核的原因：<c>L3</c> 要嘛在這份清單裡，要嘛不在。
    /// </summary>
    public static IReadOnlyList<Citation> Ground(IReadOnlyList<Article> articles, RelevanceOptions relevance) =>
        [.. articles.Where(relevance.IsRelevant).Take(MaxCitations)
            .Select((a, i) => new Citation($"{CodePrefix[Law]}{i + 1}", a.Iri, a.Label, a.Text, Law, a.LawIdentifier))];

    /// <summary>
    /// 依檢索結果決定是否作答：回傳拒答，或回傳 null 表示繼續。沒有達標的條文時，問句明白在問地方自治法規
    /// （<see cref="LocalMarker.Ordinance"/>）的拒答原因是範圍，不是檢索結果：語料只涵蓋中央法規，
    /// 「查無」會被讀成地方沒有規定。有達標的條文就照常作答，中央條文可能正是答案。
    /// </summary>
    public static Refusal? Assess(IReadOnlyList<Citation> grounding, IReadOnlyList<Article> articles, LocalMarker marker)
    {
        if (grounding.Count > 0)
            return null;

        if (marker == LocalMarker.Ordinance)
            return new Refusal(RefusalReason.LocalLawUncovered, "問句指向地方自治法規，語料只涵蓋中央法規");

        // 什麼都沒檢索到，與有檢索到東西但沒有任何一筆超過下界，是不同的失敗，呼叫端應能加以區分：
        // 前者表示語料缺漏，後者表示問題談的是別的事。
        return articles.Count == 0
            ? new Refusal(RefusalReason.NoEvidence, "檢索無任何結果")
            : new Refusal(RefusalReason.OutOfScope, "檢索結果皆未達相關性下界");
    }

    public static Confidence ConfidenceOf(IReadOnlyList<Citation> grounding, IReadOnlyList<Article> articles, RelevanceOptions relevance)
    {
        var cited = grounding.Select(c => c.Iri).ToHashSet(StringComparer.Ordinal);
        return articles.Any(a => cited.Contains(a.Iri) && relevance.IsHighConfidence(a)) ? Confidence.High : Confidence.Low;
    }
}

/// <summary>為一個問題備妥證據，或拒答。只檢索一次：信心度與 grounding 出自同一次結果。</summary>
internal sealed class GroundingPipeline(IOntologySearch ontology, IOptions<RelevanceOptions> relevance)
{
    public async Task<GroundingOutcome> RunAsync(string question, string asOf, CancellationToken cancellationToken)
    {
        // 空白問句在檢索之前就拒答：本體對空查詢回 INVALID_ARGUMENT，而這是使用者少給了輸入，不是呼叫端的錯誤。
        if (string.IsNullOrWhiteSpace(question))
            return new Refusal(RefusalReason.MissingInput, "問句為空");

        var articles = await ontology.SearchAsync(question, asOf, cancellationToken);
        var grounding = Grounding.Ground(articles, relevance.Value);
        return Grounding.Assess(grounding, articles, LocalMarkers.Classify(question))
            ?? (GroundingOutcome)new Evidence(grounding, Grounding.ConfidenceOf(grounding, articles, relevance.Value));
    }
}