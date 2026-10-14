using System.Net;
using System.Net.Http.Json;
using System.Text.Json;

using EnvCite.Platform.Modules.Metering;
using EnvCite.Platform.Modules.Ontology;

namespace EnvCite.Platform.Tests;

[Collection(PlatformTestGroup.Name)]
public sealed class ReasoningGatewayTests(PlatformFactory factory)
{
    private const string Path = "/v2/reasoning/evaluate";

    private static EvaluateReasoningRequest Request(string conductOn = "2020-01-01", string decidedOn = "2020-06-01") =>
        new(conductOn, decidedOn,
            [new FactDto("effluent_cod", ["120"], null), new FactDto("discharging", null, [true])],
            new AdministrativePenaltyFactsDto(null, true, null, 15, null, null, null, null, null, null, null, null, null, null, null, null),
            "2020-01-05", null);

    [Fact]
    public async Task EvaluateMapsTheCaseToTheInternalContractAndTheOutcomeBackToTypedJson()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("reason");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(Path, Request());
        var result = await response.Content.ReadFromJsonAsync<ReasoningResultDto>();

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var sent = factory.Ontology.LastEvaluate!;
        Assert.Equal(("2020-01-01", "2020-06-01", "2020-01-05"), (sent.ConductOn, sent.DecidedOn, sent.ConductEndedOn));
        Assert.Equal("120", sent.Facts[0].Values[0].Number.Value);
        Assert.True(sent.Facts[1].Values[0].Flag);
        Assert.True(sent.AdministrativePenaltyFacts.HasNegligence);
        Assert.False(sent.AdministrativePenaltyFacts.HasIntent); // 省略 = 未知，不是「否」
        Assert.Equal(15, sent.AdministrativePenaltyFacts.Age);

        var outcome = result!.AtDecision;
        Assert.Equal([new StatementVerdictDto(7, VerdictDto.Violation)], outcome.Verdicts);
        Assert.Equal(new PenaltyCandidateDto(8, 7, LegalEffectDto.Fine, 60000, 1000000, true, false, false), outcome.Penalties[0]);
        Assert.Equal(LegalEffectDto.Order, outcome.Penalties[1].Effect);
        Assert.Null(outcome.Penalties[1].MinAmount); // 沒寫額度 = 省略，不是 0
        Assert.Equal(new FineIntervalDto(60000, 1000000), outcome.OneAct);
        Assert.Equal([new ProblemDto(ProblemKindDto.ContradictoryFact, "ph", null),
            new ProblemDto(ProblemKindDto.UnresolvedDelegation, null, 31)], outcome.Problems);
        Assert.Equal(OverrideBasisDto.LexSpecialis, outcome.Defeated[0].Basis);
        Assert.Equal(new TraceEntryDto(31, 7, "effluent_cod", "120", "100", ConditionRoleDto.Duty), outcome.Trace[0]);
        Assert.Equal([new LimitSourceDto(31, 52)], outcome.LimitSources);
        Assert.Equal(ExcuseDto.UnderFourteen, outcome.General.Unpunished[0].Excuse);
        Assert.Equal([8L], outcome.General.TimeBarred);
        Assert.Equal(["O0040001"], outcome.UncoveredLawIds);
        Assert.Equal("2020-01-01", result.Earlier[0].FirstDay);
        Assert.Contains(factory.Ontology.ReceivedMetadata, m =>
            m.GetValue("x-tenant-id") == tenant.Identifier
            && m.GetValue("authorization") == $"Bearer {FakeOntologyServer.InternalToken}");
    }

    [Fact]
    public async Task EnumsAreWrittenAsNamesNotNumbers()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("reason-enum");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(Path, Request());
        var json = await response.Content.ReadFromJsonAsync<JsonElement>();

        Assert.Equal("Violation", json.GetProperty("atDecision").GetProperty("verdicts")[0].GetProperty("verdict").GetString());
        Assert.Equal("Fine", json.GetProperty("atDecision").GetProperty("penalties")[0].GetProperty("effect").GetString());
    }

    [Fact]
    public async Task ASuccessfulEvaluationIsMeteredOnce()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("reason-usage");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(Path, Request());
        var usage = await client.GetFromJsonAsync<UsageSummary[]>("/v2/usage");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal([new UsageSummary(ReasoningEndpoints.EvaluateOperation, 1, 1)], usage!);
    }

    [Theory]
    [InlineData(null, "2020-06-01")]
    [InlineData("2020-01-01", " ")]
    public async Task BothDatesAreRequiredAndNothingIsMetered(string? conductOn, string? decidedOn)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("reason-required");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(Path, new EvaluateReasoningRequest(conductOn, decidedOn, null, null, null, null));
        var usage = await client.GetFromJsonAsync<UsageSummary[]>("/v2/usage");

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Empty(usage!);
    }

    [Fact]
    public async Task ABadDateFromTheInternalServiceIs400AndNotMetered()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("reason-date");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(Path, Request(conductOn: "yesterday"));
        var usage = await client.GetFromJsonAsync<UsageSummary[]>("/v2/usage");

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Empty(usage!);
    }

    [Theory]
    [InlineData("not-a-number")]
    [InlineData("")]
    public async Task ABadNumberIs400BeforeTheInternalServiceIsCalled(string number)
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("reason-number");
        using var client = factory.CreateTenantClient(key.Key);
        var request = Request() with { Facts = [new FactDto("effluent_cod", [number], null)] };

        using var response = await client.PostAsJsonAsync(Path, request);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task AFactWithoutValuesIs400()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("reason-novalue");
        using var client = factory.CreateTenantClient(key.Key);
        var request = Request() with { Facts = [new FactDto("effluent_cod", null, null)] };

        using var response = await client.PostAsJsonAsync(Path, request);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task ExhaustedReasoningCapacityIs503AndRetryableAndNotMetered()
    {
        var (_, key) = await factory.CreateTenantWithKeyAsync("reason-busy");
        using var client = factory.CreateTenantClient(key.Key);

        using var response = await client.PostAsJsonAsync(Path, Request(conductOn: FakeOntologyServer.BusyDate));
        var problem = await response.Content.ReadFromJsonAsync<JsonElement>();
        var usage = await client.GetFromJsonAsync<UsageSummary[]>("/v2/usage");

        Assert.Equal(HttpStatusCode.ServiceUnavailable, response.StatusCode);
        Assert.True(problem.GetProperty("retryable").GetBoolean());
        Assert.Empty(usage!);
    }
}