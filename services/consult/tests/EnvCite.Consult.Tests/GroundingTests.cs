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

    private static Article Law(int n, double score, double? semantic = null) =>
        new($"https://envcite/tw/law/O0020001/2018-08-01/zho/art_{n}", $"空氣污染防制法 第 {n} 條", $"第 {n} 條條文內容。", score, semantic);

    [Fact]
    public void GroundAssignsCodesInRetrievalOrder()
    {
        var grounding = Grounding.Ground([Law(10, 0.9), Law(2, 0.8)]);

        Assert.Equal(["L1", "L2"], grounding.Select(c => c.Code));
        Assert.Equal(["空氣污染防制法 第 10 條", "空氣污染防制法 第 2 條"], grounding.Select(c => c.Label));
        Assert.All(grounding, c => Assert.Equal("law", c.Ontology));
    }

    [Fact]
    public void GroundDropsArticlesBelowTheRelevanceFloor()
    {
        var grounding = Grounding.Ground([Law(1, 0.9), Law(2, 0.1)]);

        Assert.Equal(["L1"], grounding.Select(c => c.Code));
    }

    [Fact]
    public void GroundCapsTheClosedSet()
    {
        Article[] many = [.. Enumerable.Range(1, Grounding.MaxCitations + 4).Select(n => Law(n, 0.9))];

        Assert.Equal(Grounding.MaxCitations, Grounding.Ground(many).Count);
    }

    [Fact]
    public void GroundKeepsArticlesThatQualifyBySemanticSimilarityAlone()
    {
        // 字面分數未達 0.30，但語意相似度達 0.80（>= 0.65）的條文保留；0.45 不夠。
        var grounding = Grounding.Ground([Law(5, 0.05, semantic: 0.80), Law(6, 0.10, semantic: 0.45)]);

        Assert.Equal(Law(5, 0).Iri, Assert.Single(grounding).Iri);
    }

    [Fact]
    public void NothingRetrievedAndNothingRelevantAreDifferentRefusals()
    {
        Article[] irrelevant = [Law(1, 0.1)];

        Assert.Equal(RefusalReason.NoEvidence, Grounding.Assess([], [])?.Reason);
        Assert.Equal(RefusalReason.OutOfScope, Grounding.Assess(Grounding.Ground(irrelevant), irrelevant)?.Reason);
    }

    [Fact]
    public void ConfidenceFollowsTheStrongestGroundedSource()
    {
        Article[] weak = [Law(1, 0.35)];
        Article[] strong = [Law(1, 0.9)];
        Article[] semantic = [Law(1, 0.35, semantic: 0.78)];

        Assert.Equal(Confidence.Low, Grounding.ConfidenceOf(Grounding.Ground(weak), weak));
        Assert.Equal(Confidence.High, Grounding.ConfidenceOf(Grounding.Ground(strong), strong));
        Assert.Equal(Confidence.High, Grounding.ConfidenceOf(Grounding.Ground(semantic), semantic));
    }

    [Fact]
    public async Task ABlankQuestionIsRefusedBeforeRetrieval()
    {
        var ontology = new FakeOntology(Law(1, 0.9));

        var outcome = await new GroundingPipeline(ontology).RunAsync("　 ", "", TestContext.Current.CancellationToken);

        Assert.Equal(RefusalReason.MissingInput, Assert.IsType<Refusal>(outcome).Reason);
        Assert.Empty(ontology.Searches);
    }

    [Fact]
    public async Task EvidenceIsGradedByItsOwnRetrieval()
    {
        var ontology = new FakeOntology(Law(1, 0.35), Law(2, 0.1));

        var outcome = await new GroundingPipeline(ontology).RunAsync("問句", "2020-01-01", TestContext.Current.CancellationToken);

        var evidence = Assert.IsType<Evidence>(outcome);
        Assert.Equal(["L1"], evidence.Citations.Select(c => c.Code));
        Assert.Equal(Confidence.Low, evidence.Confidence);
        Assert.Equal([("問句", "2020-01-01")], ontology.Searches);
    }

    [Fact]
    public async Task NothingRetrievedIsRefused()
    {
        var outcome = await new GroundingPipeline(new FakeOntology()).RunAsync("問句", "", TestContext.Current.CancellationToken);

        Assert.Equal(RefusalReason.NoEvidence, Assert.IsType<Refusal>(outcome).Reason);
    }

    [Theory]
    [InlineData("20", "第 20 條")]
    [InlineData("5-1", "第 5 條之 1")]
    public void ArticleNumbersBecomeLabels(string number, string label)
    {
        Assert.Equal(label, OntologySearch.ArticleLabel(number));
    }
}