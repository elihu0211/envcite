using EnvCite.Consult.V2;

using Grpc.Core;

namespace EnvCite.Consult.Tests;

/// <summary><c>Ground</c> 端到端：本服務經真實的 gRPC 用戶端呼叫假的本體。</summary>
[Collection(ConsultTestGroup.Name)]
public sealed class GroundServiceTests(ConsultFactory factory)
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private Task<GroundResponse> GroundAsync(GroundRequest request, Metadata? headers = null, DateTime? deadline = null) =>
        factory.CreateClient().GroundAsync(request, headers ?? ConsultFactory.CallMetadata, deadline, Ct).ResponseAsync;

    private int SearchesSince(int before) => factory.Ontology.Searches.Count - before;

    [Fact]
    public async Task EvidenceCarriesTheClosedSetWithText()
    {
        var response = await GroundAsync(new GroundRequest { Question = FakeOntologyServer.AnswerableQuery });

        Assert.Equal(GroundResponse.OutcomeOneofCase.Evidence, response.OutcomeCase);
        var citations = response.Evidence.Citations;
        Assert.Equal(["L1", "L2"], citations.Select(c => c.Code));
        Assert.Equal("空氣污染防制法 第 1 條", citations[0].Label);
        Assert.Equal("第 1 條條文內容。", citations[0].Text);
        Assert.All(citations, c => Assert.Equal("law", c.Ontology));
        Assert.Equal(V2.Confidence.High, response.Evidence.Confidence);
    }

    [Fact]
    public async Task DualTrackEvidenceCarriesBothCentralAndMunicipalIdentities()
    {
        var response = await GroundAsync(new GroundRequest { Question = FakeOntologyServer.DualTrackQuery });

        Assert.Equal(GroundResponse.OutcomeOneofCase.Evidence, response.OutcomeCase);
        var citations = response.Evidence.Citations;
        Assert.Equal(2, citations.Count);
        Assert.Equal(["L1", "L2"], citations.Select(c => c.Code));
        Assert.Equal("空氣污染防制法 第 20 條", citations[0].Label);
        Assert.NotNull(citations[0].LawIdentifier);
        Assert.Equal("TW-CENTRAL", citations[0].LawIdentifier.Jurisdiction);
        Assert.Equal("moj_pcode", citations[0].LawIdentifier.IdScheme);
        Assert.Equal("O0020001", citations[0].LawIdentifier.LocalId);

        Assert.Equal("臺北市宜居城市環境保護自治條例 第 5 條", citations[1].Label);
        Assert.NotNull(citations[1].LawIdentifier);
        Assert.Equal("TW-TPE", citations[1].LawIdentifier.Jurisdiction);
        Assert.Equal("local_glrs", citations[1].LawIdentifier.IdScheme);
        Assert.Equal("TPE-ENV-001", citations[1].LawIdentifier.LocalId);
    }

    [Fact]
    public async Task HitsAreLabelledAndKeptByEitherArm()
    {
        var response = await GroundAsync(new GroundRequest { Question = FakeOntologyServer.MappedQuery });

        // 第一筆字面 4.5 過下界，語意 0.85 達高信心；第二筆兩臂都不夠。
        var citation = Assert.Single(response.Evidence.Citations);
        Assert.Equal(("a", "空氣污染防制法 第 5 條之 1"), (citation.Iri, citation.Label));
        Assert.Equal(V2.Confidence.High, response.Evidence.Confidence);
    }

    [Fact]
    public async Task OneCallRetrievesOnceAtTheRequestedDate()
    {
        var before = factory.Ontology.Searches.Count;

        await GroundAsync(new GroundRequest { Question = FakeOntologyServer.AnswerableQuery, AsOf = "2020-01-01" });

        Assert.Equal(1, SearchesSince(before));
        var (request, _) = factory.Ontology.Searches.Last();
        Assert.Equal((FakeOntologyServer.AnswerableQuery, "2020-01-01", OntologySearch.Limit), (request.Query, request.AsOf, request.Limit));
        Assert.True(request.IncludeGuidance);
        Assert.False(request.Rerank); // 預設不精排
    }

    [Fact]
    public async Task TheRerankSettingReachesTheOntologyRequest()
    {
        await using var host = factory.WithWebHostBuilder(b => b.UseSetting("Ontology:Rerank", "true"));
        var before = factory.Ontology.Searches.Count;

        var response = await factory.CreateClient(host)
            .GroundAsync(new GroundRequest { Question = FakeOntologyServer.AnswerableQuery }, ConsultFactory.CallMetadata, cancellationToken: Ct)
            .ResponseAsync;

        Assert.Equal(1, SearchesSince(before));
        Assert.True(factory.Ontology.Searches.Last().Request.Rerank);
        Assert.Equal(GroundResponse.OutcomeOneofCase.Evidence, response.OutcomeCase); // 精排只加選項，接地照常
    }

    [Fact]
    public async Task AnArticleOnlyGuidanceExplainsIsGroundedButNotHighConfidence()
    {
        var response = await GroundAsync(new GroundRequest { Question = FakeOntologyServer.GuidanceOnlyQuery });

        var citation = Assert.Single(response.Evidence.Citations);
        Assert.Equal("空氣污染防制法 第 31 條", citation.Label);
        Assert.Equal(V2.Confidence.Low, response.Evidence.Confidence);
    }

    [Fact]
    public async Task TheLawOntologyMayBeRequestedExplicitly()
    {
        var response = await GroundAsync(new GroundRequest { Question = FakeOntologyServer.AnswerableQuery, Ontologies = { "law" } });

        Assert.Equal(GroundResponse.OutcomeOneofCase.Evidence, response.OutcomeCase);
    }

    [Fact]
    public async Task AnUnknownOntologyIsRejectedBeforeRetrieval()
    {
        var before = factory.Ontology.Searches.Count;

        var error = await Assert.ThrowsAsync<RpcException>(() =>
            GroundAsync(new GroundRequest { Question = "x", Ontologies = { "law", "case" } }));

        Assert.Equal(StatusCode.InvalidArgument, error.StatusCode);
        Assert.Contains("case", error.Status.Detail, StringComparison.Ordinal);
        Assert.Equal(0, SearchesSince(before));
    }

    [Fact]
    public async Task AMissingTenantIsRejected()
    {
        var error = await Assert.ThrowsAsync<RpcException>(() =>
            GroundAsync(new GroundRequest { Question = "x" }, headers: []));

        Assert.Equal(StatusCode.InvalidArgument, error.StatusCode);
    }

    [Theory]
    [InlineData("  ", Refused.Types.Reason.MissingInput)]
    [InlineData(FakeOntologyServer.EmptyQuery, Refused.Types.Reason.NoEvidence)]
    [InlineData(FakeOntologyServer.IrrelevantQuery, Refused.Types.Reason.OutOfScope)]
    [InlineData("臺北市有哪些空氣污染自治條例", Refused.Types.Reason.LocalLawUncovered)]
    public async Task RefusalIsAResultNotAnError(string question, Refused.Types.Reason reason)
    {
        var response = await GroundAsync(new GroundRequest { Question = question });

        Assert.Equal(GroundResponse.OutcomeOneofCase.Refused, response.OutcomeCase);
        Assert.Equal(reason, response.Refused.Reason);
    }

    [Fact]
    public async Task TheOntologyStatusPassesThroughWithItsDetails()
    {
        var error = await Assert.ThrowsAsync<RpcException>(() =>
            GroundAsync(new GroundRequest { Question = FakeOntologyServer.InvalidQuery }));

        Assert.Equal(StatusCode.InvalidArgument, error.StatusCode);
        Assert.Equal(FakeOntologyServer.InvalidDetail, error.Status.Detail);
        // google.rpc.Status（帶 ErrorInfo 的 reason）在這個 trailer：呼叫端依 reason 分流。
        Assert.Equal(FakeOntologyServer.InvalidStatusDetails, error.Trailers.GetValueBytes("grpc-status-details-bin"));
    }

    [Fact]
    public async Task AnUnavailableOntologyStaysRetryable()
    {
        var error = await Assert.ThrowsAsync<RpcException>(() =>
            GroundAsync(new GroundRequest { Question = FakeOntologyServer.UnavailableQuery }));

        Assert.Equal(StatusCode.Unavailable, error.StatusCode);
    }

    [Fact]
    public async Task TheCallersDeadlineIsPassedDownstreamUnderTheOwnLimit()
    {
        await GroundAsync(new GroundRequest { Question = FakeOntologyServer.AnswerableQuery }, deadline: DateTime.UtcNow.AddSeconds(3));
        var withDeadline = factory.Ontology.Searches.Last().Deadline - DateTime.UtcNow;
        await GroundAsync(new GroundRequest { Question = FakeOntologyServer.AnswerableQuery });
        var withoutDeadline = factory.Ontology.Searches.Last().Deadline - DateTime.UtcNow;

        // 期限由牆鐘換算，繁忙的 runner 上會差幾毫秒；要驗的是哪個上限生效，不是精確值。
        Assert.InRange(withDeadline, TimeSpan.Zero, TimeSpan.FromSeconds(3));
        Assert.InRange(withoutDeadline, TimeSpan.FromSeconds(3.5), TimeSpan.FromSeconds(5));
    }
}