using System.Net;
using System.Text.Json;

using EnvCite.Platform.Modules.Metering;
using EnvCite.Platform.Modules.Ontology;

namespace EnvCite.Platform.Tests;

[Collection(PlatformTestGroup.Group1)]
public sealed class ReasoningGatewayTests(PlatformFactory factory)
{
    private const string Path = "/v2/reasoning/evaluate";

    private static EvaluateReasoningRequest Request(string conductOn = "2020-01-01", string decidedOn = "2020-06-01") =>
        new(conductOn, decidedOn,
            [
                new FactDto("effluent_cod", ["120"], null, MeasurementMethod: "cems"), new FactDto("discharging", null, [true]),
                new FactDto("industry", null, null, ["factory"]),
            ],
            new AdministrativePenaltyFactsDto(null, true, null, 15, null, null, null, null, null, null, null, null, null, null, null, null,
                CriminalOffense: true, CriminalClearedOn: "2020-04-01", CriminalPayment: "30000",
                CommunityServiceHours: "60", GainedBenefit: "2500000"),
            "2020-01-05", null, [new PermitLimitDto("operating_permit", "effluent_cod", "100")],
            [LiabilityBasisDto.State, LiabilityBasisDto.Representative]);

    [Fact]
    public async Task EvaluateMapsTheCaseToTheInternalContractAndTheOutcomeBackToTypedJson()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("reason");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(Path, Request(), cancellationToken: TestContext.Current.CancellationToken);
        var result = await response.Content.ReadFromJsonAsync<ReasoningResultDto>(cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var sent = factory.Ontology.LastEvaluate!;
        Assert.Equal(("2020-01-01", "2020-06-01", "2020-01-05"), (sent.ConductOn, sent.DecidedOn, sent.ConductEndedOn));
        Assert.Equal("120", sent.Facts[0].Values[0].Number.Value);
        Assert.True(sent.Facts[1].Values[0].Flag);
        Assert.Equal("factory", sent.Facts[2].Values[0].Concept); // 概念值事實原樣對映
        Assert.Equal(("cems", ""), (sent.Facts[0].MeasurementMethod, sent.Facts[0].AveragingPeriod)); // 省略 = 未提供
        var permit = Assert.Single(sent.PermitLimits);
        Assert.Equal(("operating_permit", "effluent_cod", "100"), (permit.Permit, permit.Concept, permit.Value.Value));
        Assert.Equal([EnvCite.Ontology.V2.LiabilityBasis.State, EnvCite.Ontology.V2.LiabilityBasis.Representative], sent.ActorBases);
        Assert.True(sent.AdministrativePenaltyFacts.HasNegligence);
        Assert.False(sent.AdministrativePenaltyFacts.HasIntent); // 省略 = 未知，不是「否」
        Assert.Equal(15, sent.AdministrativePenaltyFacts.Age);
        var ap = sent.AdministrativePenaltyFacts;
        Assert.True(ap.CriminalOffense);
        Assert.False(ap.HasCourtConfiscated);
        Assert.Equal("2020-04-01", ap.CriminalClearedOn);
        Assert.Equal(("30000", "60", "2500000"), (ap.CriminalPayment.Value, ap.CommunityServiceHours.Value, ap.GainedBenefit.Value));
        Assert.Null(ap.MinimumHourlyWage); // 省略 = 未知，不是 0 元

        var outcome = result!.AtDecision;
        Assert.Equal([new StatementVerdictDto(7, VerdictDto.Violation)], outcome.Verdicts);
        var fine = outcome.Penalties[0];
        Assert.Empty(fine.TriggeredBy);
        Assert.Equal(new PenaltyCandidateDto(8, 7, LegalEffectDto.Fine, 60000, 1000000, true, false, false, fine.TriggeredBy, fine.SubjectBases), fine);
        Assert.Equal([LiabilityBasisDto.State], fine.SubjectBases);
        Assert.Equal(LegalEffectDto.Warning, outcome.Penalties[2].Effect);
        Assert.Equal([new SanctionTriggerDto(8, TriggerKindDto.SanctionImposed)], outcome.Penalties[2].TriggeredBy);
        Assert.Equal(LegalEffectDto.Disgorgement, outcome.Penalties[3].Effect);
        Assert.Equal([new PermitGapDto(7, "operating_permit", "effluent_cod")], outcome.MissingPermitLimits);
        Assert.Equal([9L], outcome.SubjectMismatch);
        Assert.Equal(LegalEffectDto.Order, outcome.Penalties[1].Effect);
        Assert.Null(outcome.Penalties[1].MinAmount); // 沒寫額度 = 省略，不是 0
        Assert.Equal(new FineIntervalDto(60000, 1000000), outcome.OneAct);
        Assert.Equal([new ProblemDto(ProblemKindDto.ContradictoryFact, "ph", null),
            new ProblemDto(ProblemKindDto.UnresolvedDelegation, null, 31),
            new ProblemDto(ProblemKindDto.MeasurementMismatch, null, 32),
            new ProblemDto(ProblemKindDto.InvalidatedDelegation, null, 33)], outcome.Problems);
        Assert.Equal(OverrideBasisDto.LexSpecialis, outcome.Defeated[0].Basis);
        Assert.Equal(new TraceEntryDto(31, 7, "effluent_cod", "120", "100", ConditionRoleDto.Duty), outcome.Trace[0]);
        Assert.Equal([new LimitSourceDto(31, 52)], outcome.LimitSources);
        Assert.Equal(ExcuseDto.UnderFourteen, outcome.General.Unpunished[0].Excuse);
        Assert.Equal([8L], outcome.General.TimeBarred);
        Assert.Equal(new PenaltyExcuseDto(13, ExcuseDto.CriminalPriority), outcome.General.Unpunished[1]);
        Assert.Equal([new ReducedFineDto(8, MitigationDto.Youth, ReductionShareDto.Half, 30000, 500000),
            new ReducedFineDto(14, MitigationDto.ExcessiveDefense, ReductionShareDto.Third, null, null)], outcome.General.Reduced); // 缺額度 = 省略
        Assert.Equal([new RaisedCapDto(8, "2500000")], outcome.General.RaisedCaps);
        Assert.Equal([new CriminalDeductionDto(8, "41760")], outcome.General.CriminalDeductions);
        Assert.Equal([new InvalidatedStatementDto(15, "https://envcite/tw/law/O0020001/2018-08-01/zho/art_40", "112年憲判字第1號", "2023-03-01", true)],
            outcome.Invalidated);
        var rooted = outcome.DelegationBases[0];
        Assert.True(rooted.Rooted);
        Assert.Equal(new DelegationLinkDto(new LawIdentifierDto("TW-CENTRAL", "moj_pcode", "O0020010"), "水污染防治法", "art_7/para_2",
            new LawIdentifierDto("TW-CENTRAL", "moj_pcode", "O0020001"),
            "https://envcite/tw/law/O0020001/2018-08-01/zho/art_7/para_2", null), Assert.Single(rooted.Links)); // 成立的環沒有缺口
        var unrooted = outcome.DelegationBases[1];
        Assert.False(unrooted.Rooted);
        Assert.Equal(new DelegationLinkDto(new LawIdentifierDto("TW-CENTRAL", "moj_pcode", "O0020011"), null, null, null, null, DelegationGapDto.NoCitation), Assert.Single(unrooted.Links));
        Assert.Equal([new LawIdentifierDto("TW-CENTRAL", "moj_pcode", "O0040001")], outcome.UncoveredLawIdentifiers);
        var used = Assert.Single(outcome.DefinitionsUsed);
        Assert.Equal((31L, 5L, "事業"), (used.ConditionId, used.DefinitionId, used.Term));
        Assert.EndsWith("/art_2/para_1/pt_1", used.Uri);
        Assert.Equal(["工廠", "礦場"], used.Members);
        var guidance = Assert.Single(outcome.Guidance);
        Assert.Equal(("環署水字第1090000001號", "2020-02-01", GuidanceKindDto.Interpretation),
            (guidance.DocumentNo, guidance.IssuedOn, guidance.Kind));
        Assert.Equal([7L], guidance.StatementIds);
        Assert.Equal("行政院公報第26卷", guidance.PublishedIn);
        Assert.Null(guidance.SourceUrl); // 空字串 = 沒有，不是空網址
        Assert.Equal("2020-01-01", result.Earlier[0].FirstDay);
        var earlier = result.Earlier[0].Outcome; // 空的結果：清單是空的，不是 null
        Assert.Empty(earlier.Invalidated);
        Assert.Empty(earlier.DelegationBases);
        Assert.Empty(earlier.General.Reduced);
        Assert.Contains(factory.Ontology.ReceivedMetadata, m =>
            m.GetValue("x-tenant-id") == tenant.Identifier);
    }

    [Fact]
    public async Task OmittedCriminalAndBenefitFactsStayUnknown()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("reason-unknown");
        using var client = factory.CreateTenantClient(key.Key);
        var request = Request() with
        {
            AdministrativePenalty = new AdministrativePenaltyFactsDto(
                null, null, null, null, null, null, null, null, null, null, null, null, null, null, null, null),
        };

        using var response = await client.PostAsJsonAsync(Path, request, cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var ap = factory.Ontology.LastEvaluate!.AdministrativePenaltyFacts;
        Assert.False(ap.HasCriminalOffense);
        Assert.False(ap.HasCourtConfiscated);
        Assert.Equal("", ap.CriminalClearedOn);
        Assert.Null(ap.CriminalPayment);
        Assert.Null(ap.CommunityServiceHours);
        Assert.Null(ap.MinimumHourlyWage);
        Assert.Null(ap.GainedBenefit);
    }

    [Fact]
    public async Task EnumsAreWrittenAsNamesNotNumbers()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("reason-enum");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(Path, Request(), cancellationToken: TestContext.Current.CancellationToken);
        var json = await response.Content.ReadFromJsonAsync<JsonElement>(cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal("Violation", json.GetProperty("atDecision").GetProperty("verdicts")[0].GetProperty("verdict").GetString());
        Assert.Equal("Fine", json.GetProperty("atDecision").GetProperty("penalties")[0].GetProperty("effect").GetString());
        var atDecision = json.GetProperty("atDecision");
        Assert.Equal("Half", atDecision.GetProperty("general").GetProperty("reduced")[0].GetProperty("share").GetString());
        Assert.Equal("NoCitation", atDecision.GetProperty("delegationBases")[1].GetProperty("links")[0].GetProperty("gap").GetString());
    }

    [Fact]
    public async Task ASuccessfulEvaluationIsMeteredOnce()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("reason-usage");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(Path, Request(), cancellationToken: TestContext.Current.CancellationToken);
        var usage = await client.GetFromJsonAsync<UsageSummary[]>("/v2/usage", cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal([new UsageSummary(ReasoningEndpoints.EvaluateOperation, 1, 1)], usage!);
    }

    [Fact]
    public async Task TheCaseIsForwardedAsGivenAndJudgedByTheInternalService()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("reason-forward");
        using var client = factory.CreateTenantClient(key.Key);
        var request = Request() with
        {
            Facts = [new FactDto("effluent_cod", ["lots"], null)],
            PermitLimits = [new PermitLimitDto("operating_permit", "effluent_cod", "")],
        };

        using var response = await client.PostAsJsonAsync(Path, request, cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode); // 假服務不驗證；檢查只在 ontology 一處
        var sent = factory.Ontology.LastEvaluate!;
        Assert.Equal("lots", sent.Facts[0].Values[0].Number.Value);
        Assert.Equal("", sent.PermitLimits[0].Value.Value);
    }

    [Fact]
    public async Task ABadDateFromTheInternalServiceIs400AndNotMetered()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("reason-date");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(Path, Request(conductOn: "yesterday"), cancellationToken: TestContext.Current.CancellationToken);
        var usage = await client.GetFromJsonAsync<UsageSummary[]>("/v2/usage", cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Empty(usage!);
    }

    [Fact]
    public async Task ExhaustedReasoningCapacityIs503AndRetryableAndNotMetered()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("reason-busy");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(Path, Request(conductOn: FakeOntologyServer.BusyDate), cancellationToken: TestContext.Current.CancellationToken);
        var problem = await response.Content.ReadFromJsonAsync<JsonElement>(cancellationToken: TestContext.Current.CancellationToken);
        var usage = await client.GetFromJsonAsync<UsageSummary[]>("/v2/usage", cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
        Assert.True(problem.GetProperty("retryable").GetBoolean());
        Assert.Empty(usage!);
    }
}