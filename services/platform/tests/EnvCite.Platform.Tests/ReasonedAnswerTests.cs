using System.Security.Claims;
using System.Text.Json;

using EnvCite.Platform.Modules.Tenancy;

using Grpc.Core;

using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 案件類問題的回答管線：抽事實、推論、結論區塊由系統填寫、模型只解釋。抽取與推論的任何失敗都退回沒有結論的回答，
/// 不造成拒答；環境設定或租戶模組沒開時，推理機一次都不碰。
/// </summary>
// 與 AskServiceTests 同組：兩邊都呼叫 consult 的 Ground，gRPC 用戶端的計量器是行程內全域的。
[Collection(PlatformTestGroup.Group1)]
public sealed class ReasonedAnswerTests(PlatformFactory factory)
{
    private static readonly string Explained = JsonReply(new ModelAnswerOutput(
        InsufficientEvidence: false,
        [new ProseBlock(null, "結論 1 依據的條文禁止在指定清除地區內從事燃燒行為；欠缺的事實須另行查明。", ["L1"])]));

    private static string JsonReply(ModelAnswerOutput output) => JsonSerializer.Serialize(output, AnswerJson.Options);

    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private (WebApplicationFactory<Program> Host, FakeChat Chat) Host(Func<IReadOnlyList<ChatMessage>, string> reply, bool reasoning = true)
    {
        var chat = new FakeChat(reply);
        var host = factory.WithWebHostBuilder(b =>
        {
            b.UseSetting("Platform:Answer:Enabled", "true")
                .UseSetting("Platform:Answer:Endpoint", "https://models.example.test/v1")
                .UseSetting("Platform:Answer:ApiKey", "test-key")
                .UseSetting("Platform:Answer:Reasoning", reasoning ? "true" : "false");
            b.ConfigureServices(s => s.Replace(ServiceDescriptor.Singleton<IChatClient>(chat)));
        });
        return (host, chat);
    }

    private async Task<(TenantResponse Tenant, ClaimsPrincipal Member)> MemberOfAsync(string slug, bool reasoningModule = true)
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync(slug);
        var plans = factory.Services.GetRequiredService<IPlanAdmin>();
        Assert.True((await plans.SetAsync(OperatorSession.Principal(), tenant.Id, "standard", Ct)).IsOk);
        Assert.True((await plans.SetAiAnswerAsync(OperatorSession.Principal(), tenant.Id, true, Ct)).IsOk);
        var modules = factory.Services.GetRequiredService<IModuleAdmin>();
        Assert.True((await modules.SetAsync(OperatorSession.Principal(), tenant.Id, ModuleKeys.NormativeReasoning, reasoningModule, Ct)).IsOk);
        var (userId, _) = await AddMemberAsync(factory, tenant.Id, NewEmail(), CustomerRoles.Viewer);
        return (tenant, await MemberPrincipalAsync(factory, tenant.Id, userId));
    }

    private async Task<List<string>> UsageAsync(string tenantId) =>
        [.. (await factory.Services.GetRequiredService<IUsageAdmin>().GetTenantUsageAsync(tenantId, null, Ct)).Value!.Select(u => u.Operation).Order()];

    private static AnswerDocument DocumentOf(AskResult result) => Assert.IsType<AnswerDocument>(result.Answer!.Document);

    [Fact]
    public async Task ACaseQuestionGetsAConclusionBlockFirstAndTheModelOnlyExplainsIt()
    {
        var (host, chat) = Host(_ => Explained);
        using var h = host;
        var (tenant, member) = await MemberOfAsync("reasoned-a");

        var result = await host.Services.GetRequiredService<IAskService>().AskAsync(member, FakeConsultServer.AnswerableQuestion, null, null, Ct);

        Assert.Equal((AskMode.Answer, AskStatus.Ok), (result.Mode, result.Status));
        var document = DocumentOf(result);
        var conclusion = Assert.IsType<ConclusionBlock>(document.Blocks[0]);
        var item = Assert.Single(conclusion.Items);
        Assert.Equal((1, ConclusionVerdict.Satisfied), (item.No, item.Verdict));
        Assert.Equal(["L1"], item.CitationCodes);
        Assert.Equal(["排放水之化學需氧量"], item.MissingFacts);
        Assert.Equal(
            ["Disgorgement", "Fine", "Order", "Warning"],
            item.Penalties.Select(p => p.Effect).Order(StringComparer.Ordinal));
        Assert.Equal(new PenaltyRange("Fine", 60000, 1000000, Conditional: true, Discretionary: false), item.Penalties.Single(p => p.Effect == "Fine"));
        Assert.Equal(
            [new UsedFact("open_burning", "露天燃燒", true, null, "露天燃燒"), new UsedFact("burned_area", "燃燒面積", null, "12", "十二公頃")],
            conclusion.Facts);
        Assert.Equal(0, conclusion.OmittedCount);
        Assert.IsType<ProseBlock>(Assert.Single(document.Blocks.Skip(1)));
        Assert.Equal("L1", Assert.Single(result.Answer!.Sources).Code);

        // 問句原樣送去抽取；事實原樣送去推論；行為日與裁處日是同一天，沒有明說就是今日、標明是假設。
        Assert.Equal(FakeConsultServer.AnswerableQuestion, factory.Canon.LastExtract!.Question);
        var evaluated = factory.Canon.LastEvaluate!;
        Assert.Equal(evaluated.ConductOn, evaluated.DecidedOn);
        Assert.Equal((evaluated.ConductOn, true), (conclusion.AsOf, conclusion.AsOfAssumed));
        Assert.Equal(["open_burning", "burned_area"], evaluated.Facts.Select(f => f.Concept));
        Assert.True(evaluated.Facts[0].Values[0].Flag);
        Assert.Equal("12", evaluated.Facts[1].Values[0].Number.Value);

        // 模型看得到結論的編號、依據與欠缺的事實，看不到結論用語；系統提示多一條只准解釋的規則。
        var call = Assert.Single(chat.Calls);
        var user = Assert.Single(call, m => m.Role == ChatRole.User).Text;
        Assert.Contains("結論 1：依據 [L1]；欠缺事實 排放水之化學需氧量", user, StringComparison.Ordinal);
        Assert.All(ConclusionTerms.Forbidden, word => Assert.DoesNotContain(word, user.Split("問題：")[0], StringComparison.Ordinal));
        Assert.Contains(call, m => m.Role == ChatRole.System && m.Text.Contains("不得寫出結論本身", StringComparison.Ordinal));

        Assert.Equal(["consult.answer", "consult.ground", "reasoning.evaluate", "reasoning.extract_facts"], await UsageAsync(tenant.Id));
    }

    [Fact]
    public async Task AnAsOfDateTheUserChoseIsTheDateOfTheConductAndIsNotMarkedAssumed()
    {
        var (host, _) = Host(_ => Explained);
        using var h = host;
        var (_, member) = await MemberOfAsync("reasoned-asof");

        var result = await host.Services.GetRequiredService<IAskService>().AskAsync(member, FakeConsultServer.AnswerableQuestion, "2020-03-01", null, Ct);

        var conclusion = Assert.IsType<ConclusionBlock>(DocumentOf(result).Blocks[0]);
        Assert.Equal(("2020-03-01", false), (conclusion.AsOf, conclusion.AsOfAssumed));
        Assert.Equal(("2020-03-01", "2020-03-01"), (factory.Canon.LastEvaluate!.ConductOn, factory.Canon.LastEvaluate.DecidedOn));
    }

    // 說明重述結論就整段丟掉：使用者看到的是系統的結論加它的出處，沒有模型的話。
    [Fact]
    public async Task AnExplanationThatRestatesTheConclusionIsReplacedByTheConclusionAlone()
    {
        var (host, _) = Host(_ => JsonReply(new ModelAnswerOutput(false, [new ProseBlock(null, "本案該當，應處罰鍰。", ["L1"])])));
        using var h = host;
        var (_, member) = await MemberOfAsync("reasoned-overstep");

        var result = await host.Services.GetRequiredService<IAskService>().AskAsync(member, FakeConsultServer.AnswerableQuestion, null, null, Ct);

        Assert.False(result.Answer!.Refused);
        Assert.IsType<ConclusionBlock>(Assert.Single(DocumentOf(result).Blocks));
        Assert.Equal("L1", Assert.Single(result.Answer.Sources).Code);
    }

    [Fact]
    public async Task WithTheEnvironmentOptionOffTheReasonerIsNeverCalled()
    {
        var (host, chat) = Host(_ => Explained, reasoning: false);
        using var h = host;
        var (tenant, member) = await MemberOfAsync("reasoned-off");

        var result = await host.Services.GetRequiredService<IAskService>().AskAsync(member, FakeConsultServer.AnswerableQuestion, null, null, Ct);

        Assert.IsType<ProseBlock>(Assert.Single(DocumentOf(result).Blocks));
        Assert.DoesNotContain("結論 1", Assert.Single(chat.Calls)[^1].Text, StringComparison.Ordinal);
        Assert.Equal(["consult.answer", "consult.ground"], await UsageAsync(tenant.Id));
    }

    [Fact]
    public async Task WithTheTenantModuleOffTheReasonerIsNeverCalled()
    {
        var (host, _) = Host(_ => Explained);
        using var h = host;
        var (tenant, member) = await MemberOfAsync("reasoned-module-off", reasoningModule: false);

        var result = await host.Services.GetRequiredService<IAskService>().AskAsync(member, FakeConsultServer.AnswerableQuestion, null, null, Ct);

        Assert.IsType<ProseBlock>(Assert.Single(DocumentOf(result).Blocks));
        Assert.Equal(["consult.answer", "consult.ground"], await UsageAsync(tenant.Id));
    }

    // 平台先於 canon 部署（Unimplemented）、canon 沒設定抽取模型（FailedPrecondition）、容量用盡、暫時不可用：
    // 一律退回沒有結論的回答，不造成拒答，也不計抽取的量。
    [Theory]
    [InlineData(StatusCode.Unimplemented)]
    [InlineData(StatusCode.FailedPrecondition)]
    [InlineData(StatusCode.ResourceExhausted)]
    [InlineData(StatusCode.Unavailable)]
    public async Task AnExtractionFailureLeavesTheOrdinaryAnswer(StatusCode failure)
    {
        var (host, _) = Host(_ => Explained);
        using var h = host;
        var (tenant, member) = await MemberOfAsync($"reasoned-fail-{(int)failure}");

        AskResult result;
        using (factory.Canon.ExtractingAs(failure))
        {
            result = await host.Services.GetRequiredService<IAskService>().AskAsync(member, FakeConsultServer.AnswerableQuestion, null, null, Ct);
        }

        Assert.Equal((AskMode.Answer, AskStatus.Ok), (result.Mode, result.Status));
        Assert.IsType<ProseBlock>(Assert.Single(DocumentOf(result).Blocks));
        Assert.Equal(["consult.answer", "consult.ground"], await UsageAsync(tenant.Id));
    }

    // canon 回的事實沒有值是契約被破壞：整批不用，不拿其餘的事實去推論。
    [Fact]
    public async Task AFactWithoutAValueFromCanonLeavesTheOrdinaryAnswerAndIsNotEvaluated()
    {
        var (host, _) = Host(_ => Explained);
        using var h = host;
        var (tenant, member) = await MemberOfAsync("reasoned-malformed");

        AskResult result;
        using (factory.Canon.ExtractingAs(facts:
        [
            new EnvCite.Canon.V1.ExtractedFact { Concept = "open_burning", Name = "露天燃燒", Value = new EnvCite.Canon.V1.FactValue { Flag = true }, Quote = "露天燃燒" },
            new EnvCite.Canon.V1.ExtractedFact { Concept = "burned_area", Name = "燃燒面積", Quote = "十二公頃" },
        ]))
        {
            result = await host.Services.GetRequiredService<IAskService>().AskAsync(member, FakeConsultServer.AnswerableQuestion, null, null, Ct);
        }

        Assert.IsType<ProseBlock>(Assert.Single(DocumentOf(result).Blocks));
        Assert.Equal(["consult.answer", "consult.ground", "reasoning.extract_facts"], await UsageAsync(tenant.Id));
    }

    [Fact]
    public async Task AQuestionWithNoStatedFactsIsNotEvaluatedAndHasNoConclusion()
    {
        var (host, _) = Host(_ => Explained);
        using var h = host;
        var (tenant, member) = await MemberOfAsync("reasoned-nofacts");

        AskResult result;
        using (factory.Canon.ExtractingAs(facts: []))
        {
            result = await host.Services.GetRequiredService<IAskService>().AskAsync(member, FakeConsultServer.AnswerableQuestion, null, null, Ct);
        }

        Assert.IsType<ProseBlock>(Assert.Single(DocumentOf(result).Blocks));
        Assert.Equal(["consult.answer", "consult.ground", "reasoning.extract_facts"], await UsageAsync(tenant.Id));
    }

    [Fact]
    public async Task AnEvaluationFailureLeavesTheOrdinaryAnswer()
    {
        var (host, _) = Host(_ => Explained);
        using var h = host;
        var (tenant, member) = await MemberOfAsync("reasoned-busy");

        var result = await host.Services.GetRequiredService<IAskService>().AskAsync(
            member, FakeConsultServer.AnswerableQuestion, FakeCanonServer.BusyDate, null, Ct);

        Assert.Equal((AskMode.Answer, AskStatus.Ok), (result.Mode, result.Status));
        Assert.IsType<ProseBlock>(Assert.Single(DocumentOf(result).Blocks));
        Assert.Equal(["consult.answer", "consult.ground", "reasoning.extract_facts"], await UsageAsync(tenant.Id));
    }

    // 含結論區塊的文件寫版本 2、讀得回來；沒有結論區塊的仍寫 1，停用推論的環境不產生舊版程式讀不懂的資料。
    [Fact]
    public async Task ADocumentWithAConclusionBlockIsStoredAndReadBackIntact()
    {
        var (host, _) = Host(_ => Explained);
        using var h = host;
        var (_, member) = await MemberOfAsync("reasoned-history");
        var ask = host.Services.GetRequiredService<IAskService>();
        var answered = await ask.AskAsync(member, FakeConsultServer.AnswerableQuestion, null, null, Ct);

        var id = await ask.RecordAsync(
            member, null,
            new ConversationTurn(FakeConsultServer.AnswerableQuestion, ConversationOutcome.Answered, [], Document: answered.Answer!.Document),
            Ct);
        var detail = await ask.ConversationAsync(member, id!.Value, Ct);

        var stored = Assert.Single(detail!.Messages, m => m.Role == ConversationMessage.AssistantRole).Document;
        Assert.Equal(
            JsonSerializer.Serialize(answered.Answer.Document, AnswerJson.Options),
            JsonSerializer.Serialize(stored, AnswerJson.Options));
        Assert.IsType<ConclusionBlock>(stored!.Blocks[0]);
    }
}