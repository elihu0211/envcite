using Microsoft.Extensions.Options;

namespace EnvCite.Consult.Tests;

/// <summary>管線的形狀：哪個階段決定什麼。檢索由測試控制，因為管線的工作正是決定拿檢索結果做什麼。</summary>
public sealed class GroundingTests
{
    private sealed class FakeOntology(params Article[] articles) : IOntologySearch
    {
        public List<(string Query, string AsOf)> Searches { get; } = [];

        public Task<IReadOnlyList<Article>> SearchAsync(string query, string asOf, CancellationToken cancellationToken)
        {
            Searches.Add((query, asOf));
            return Task.FromResult<IReadOnlyList<Article>>(articles);
        }
    }

    /// <summary>測試自己的下界：管線的形狀不取決於部署的值。</summary>
    private static readonly RelevanceOptions Relevance = new() { LexicalFloor = 4, SemanticFloor = 0.61, HighConfidenceSemantic = 0.75 };

    private static GroundingPipeline Pipeline(IOntologySearch ontology) => new(ontology, Options.Create(Relevance));

    private static Article Law(int n, double score, double? semantic = null) =>
        new($"https://envcite/tw/law/O0020001/2018-08-01/zho/art_{n}", $"空氣污染防制法 第 {n} 條", $"第 {n} 條條文內容。", score, semantic);

    [Fact]
    public void GroundAssignsCodesInRetrievalOrder()
    {
        var grounding = Grounding.Ground([Law(10, 9), Law(2, 8)], Relevance);

        Assert.Equal(["L1", "L2"], grounding.Select(c => c.Code));
        Assert.Equal(["空氣污染防制法 第 10 條", "空氣污染防制法 第 2 條"], grounding.Select(c => c.Label));
        Assert.All(grounding, c => Assert.Equal("law", c.Ontology));
    }

    [Fact]
    public void GroundDropsArticlesBelowTheRelevanceFloor()
    {
        var grounding = Grounding.Ground([Law(1, 9), Law(2, 0.1)], Relevance);

        Assert.Equal(["L1"], grounding.Select(c => c.Code));
    }

    [Fact]
    public void GroundKeepsAnArticleThatGuidanceExplainsEvenWhenBothScoresAreLow()
    {
        // 查詢用語只出現在函釋：條文本身字面與語意都不達標，函釋指向的那一條仍進證據集，沒有函釋的低分條不進。
        var guided = Law(31, 0.1) with { GuidanceDocuments = 1 };

        var grounding = Grounding.Ground([Law(77, 0.1), guided], Relevance);

        Assert.Equal(guided.Iri, Assert.Single(grounding).Iri);
    }

    [Fact]
    public void GuidanceAloneIsNotHighConfidence()
    {
        var guided = Law(31, 0.1) with { GuidanceDocuments = 3 };
        Article[] articles = [guided];

        var grounding = Grounding.Ground(articles, Relevance);

        Assert.Equal(Confidence.Low, Grounding.ConfidenceOf(grounding, articles, Relevance));
    }

    [Fact]
    public void GroundCapsTheClosedSet()
    {
        Article[] many = [.. Enumerable.Range(1, Grounding.MaxCitations + 4).Select(n => Law(n, 9))];

        Assert.Equal(Grounding.MaxCitations, Grounding.Ground(many, Relevance).Count);
    }

    [Fact]
    public void GroundKeepsArticlesThatQualifyBySemanticSimilarityAlone()
    {
        // 字面分數未達 4，但語意相似度達 0.80（>= 0.61）的條文保留；0.45 不夠。
        var grounding = Grounding.Ground([Law(5, 0.05, semantic: 0.80), Law(6, 0.10, semantic: 0.45)], Relevance);

        Assert.Equal(Law(5, 0).Iri, Assert.Single(grounding).Iri);
    }

    [Fact]
    public void NothingRetrievedAndNothingRelevantAreDifferentRefusals()
    {
        Article[] irrelevant = [Law(1, 0.1)];

        Assert.Equal(RefusalReason.NoEvidence, Grounding.Assess([], [], LocalMarker.None)?.Reason);
        Assert.Equal(RefusalReason.OutOfScope, Grounding.Assess(Grounding.Ground(irrelevant, Relevance), irrelevant, LocalMarker.None)?.Reason);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void NoEvidenceForAnOrdinanceQuestionIsRefusedAsLocalLawNotAsNotFound(bool retrievedSomething)
    {
        Article[] articles = retrievedSomething ? [Law(1, 0.1)] : [];

        var refusal = Grounding.Assess(Grounding.Ground(articles, Relevance), articles, LocalMarker.Ordinance);

        Assert.Equal(RefusalReason.LocalLawUncovered, refusal?.Reason);
    }

    [Fact]
    public void AJurisdictionNameAloneKeepsTheRetrievalReason()
    {
        // 縣市可能只是提問者所在地：只有自治法規字樣才改變拒答原因。
        Assert.Equal(RefusalReason.NoEvidence, Grounding.Assess([], [], LocalMarker.Jurisdiction)?.Reason);
    }

    [Fact]
    public void RelevantEvidenceIsNeverReplacedByTheLocalLawRefusal()
    {
        Article[] articles = [Law(1, 9)];

        Assert.Null(Grounding.Assess(Grounding.Ground(articles, Relevance), articles, LocalMarker.Ordinance));
    }

    [Fact]
    public void ConfidenceFollowsTheStrongestGroundedSource()
    {
        Article[] weak = [Law(1, 4.5)];
        Article[] strong = [Law(1, 9)];
        Article[] semantic = [Law(1, 4.5, semantic: 0.78)];

        Assert.Equal(Confidence.Low, Grounding.ConfidenceOf(Grounding.Ground(weak, Relevance), weak, Relevance));
        Assert.Equal(Confidence.High, Grounding.ConfidenceOf(Grounding.Ground(strong, Relevance), strong, Relevance));
        Assert.Equal(Confidence.High, Grounding.ConfidenceOf(Grounding.Ground(semantic, Relevance), semantic, Relevance));
    }

    [Fact]
    public async Task ABlankQuestionIsRefusedBeforeRetrieval()
    {
        var ontology = new FakeOntology(Law(1, 9));

        var outcome = await Pipeline(ontology).RunAsync("　 ", "", TestContext.Current.CancellationToken);

        Assert.Equal(RefusalReason.MissingInput, Assert.IsType<Refusal>(outcome).Reason);
        Assert.Empty(ontology.Searches);
    }

    [Fact]
    public async Task AnOrdinanceQuestionWithNothingRelevantIsRefusedAsLocalLawAfterRetrieval()
    {
        var ontology = new FakeOntology();

        var outcome = await Pipeline(ontology).RunAsync("臺北市有哪些空氣污染自治條例", "", TestContext.Current.CancellationToken);

        Assert.Equal(RefusalReason.LocalLawUncovered, Assert.IsType<Refusal>(outcome).Reason);
        Assert.Single(ontology.Searches);
    }

    [Fact]
    public async Task EvidenceIsGradedByItsOwnRetrieval()
    {
        var ontology = new FakeOntology(Law(1, 4.5), Law(2, 0.1));

        var outcome = await Pipeline(ontology).RunAsync("問句", "2020-01-01", TestContext.Current.CancellationToken);

        var evidence = Assert.IsType<Evidence>(outcome);
        Assert.Equal(["L1"], evidence.Citations.Select(c => c.Code));
        Assert.Equal(Confidence.Low, evidence.Confidence);
        Assert.Equal([("問句", "2020-01-01")], ontology.Searches);
    }

    [Fact]
    public async Task NothingRetrievedIsRefused()
    {
        var outcome = await Pipeline(new FakeOntology()).RunAsync("問句", "", TestContext.Current.CancellationToken);

        Assert.Equal(RefusalReason.NoEvidence, Assert.IsType<Refusal>(outcome).Reason);
    }

    [Fact]
    public void HighConfidenceLexicalIsAFixedMultipleOfTheFloor()
    {
        Assert.Equal(6, Relevance.HighConfidenceLexical);
    }

    [Theory]
    [InlineData(0, 0.61, 0.75)]
    [InlineData(4, 0, 0.75)]
    [InlineData(4, 1, 1)]
    [InlineData(4, 0.8, 0.75)]
    [InlineData(4, 0.61, 1.2)]
    public void FloorsOutOfRangeAreRejected(double lexical, double semantic, double highSemantic)
    {
        var options = new RelevanceOptions { LexicalFloor = lexical, SemanticFloor = semantic, HighConfidenceSemantic = highSemantic };

        Assert.True(new RelevanceOptionsValidator().Validate(null, options).Failed);
    }

    [Fact]
    public void TheTestFloorsAreValid()
    {
        Assert.True(new RelevanceOptionsValidator().Validate(null, Relevance).Succeeded);
    }

    [Theory]
    [InlineData("20", "第 20 條")]
    [InlineData("5-1", "第 5 條之 1")]
    public void ArticleNumbersBecomeLabels(string number, string label)
    {
        Assert.Equal(label, OntologySearch.ArticleLabel(number));
    }
}