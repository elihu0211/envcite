using System.Security.Claims;

using EnvCite.Platform.Modules.Tenancy;

using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 入口問答的應用服務：在呼叫者的租戶裡檢查方案與同意、回答或搜尋、記進歷史。不經頁面也不經瀏覽器——
/// 靜態頁與互動式 circuit（沒有 ambient tenant）呼叫的就是這一層，所以租戶隔離、計量與 fail closed 在這裡直接證明。
/// 頁面怎麼呈現由 <c>PortalAnswerTests</c> 等守住。
/// </summary>
// 放在 Group1：這裡會呼叫 consult 的 Ground，而 gRPC 用戶端的計量器是行程內全域的；RedMetricsTests（也在 Group1，同組循序）
// 只數自己發出的 Ground，放在別的測試組並行跑，它的指標點就會多出這裡的呼叫。
[Collection(PlatformTestGroup.Group1)]
public sealed class AskServiceTests(PlatformFactory factory)
{
    private const string Reply = "在指定清除地區內不得燃燒 [L1]。";

    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    // 測試主機預設給每個租戶一個預設方案（讓剛建立的租戶能用）；沒有方案的情形要把它清掉。
    private (WebApplicationFactory<Program> Host, FakeChat Chat) Host(
        Func<IReadOnlyList<ChatMessage>, string> reply, bool history = false, bool defaultPlan = true)
    {
        var chat = new FakeChat(reply);
        var host = factory.WithWebHostBuilder(b =>
        {
            b.UseSetting("Platform:Answer:Enabled", "true")
                .UseSetting("Platform:Answer:Endpoint", "https://models.example.test/v1")
                .UseSetting("Platform:Answer:ApiKey", "test-key");
            if (history)
                b.UseSetting("Platform:ConversationHistory:Enabled", "true");

            if (!defaultPlan)
                b.UseSetting("Platform:DefaultPlan", string.Empty);

            b.ConfigureServices(s => s.Replace(ServiceDescriptor.Singleton<IChatClient>(chat)));
        });
        return (host, chat);
    }

    /// <summary>租戶、（可選）方案與同意，與一位成員登入後的 principal。</summary>
    private async Task<(TenantResponse Tenant, ClaimsPrincipal Member)> MemberOfAsync(
        string slug, bool plan = true, bool consent = true, string role = CustomerRoles.Viewer)
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync(slug);
        var plans = factory.Services.GetRequiredService<IPlanAdmin>();
        if (plan)
            Assert.True((await plans.SetAsync(OperatorSession.Principal(), tenant.Id, "standard", Ct)).IsOk);

        if (plan && consent)
            Assert.True((await plans.SetAiAnswerAsync(OperatorSession.Principal(), tenant.Id, true, Ct)).IsOk);

        var (userId, _) = await AddMemberAsync(factory, tenant.Id, NewEmail(), role);
        return (tenant, await MemberPrincipalAsync(factory, tenant.Id, userId));
    }

    private async Task<List<string>> UsageAsync(string tenantId) =>
        [.. (await factory.Services.GetRequiredService<IUsageAdmin>().GetTenantUsageAsync(tenantId, null, Ct)).Value!.Select(u => u.Operation).Order()];

    [Fact]
    public async Task WithoutAPlanNothingIsAnsweredSearchedOrMetered()
    {
        var (host, chat) = Host(_ => "不該被呼叫 [L1]", defaultPlan: false);
        using var h = host;
        var (tenant, member) = await MemberOfAsync("ask-a", plan: false);
        var ask = host.Services.GetRequiredService<IAskService>();

        Assert.Equal(new AskCapabilities(HasPlan: false, AiAnswer: false), await ask.CapabilitiesAsync(member, Ct));
        var result = await ask.AskAsync(member, FakeConsultServer.AnswerableQuestion, null, Ct);

        Assert.Equal(AskStatus.NoPlan, result.Status);
        Assert.Empty(chat.Calls);
        Assert.Empty(await UsageAsync(tenant.Id));
    }

    // 租戶沒有同意：問題與證據不會離開我們的系統，所以退回只列條文，模型一次都不呼叫。
    [Fact]
    public async Task WithAPlanButNoConsentTheResultIsAListOfArticlesAndNoModelIsCalled()
    {
        var (host, chat) = Host(_ => "不該被呼叫 [L1]");
        using var h = host;
        var (tenant, member) = await MemberOfAsync("ask-b", consent: false);
        var ask = host.Services.GetRequiredService<IAskService>();

        var capabilities = await ask.CapabilitiesAsync(member, Ct);
        var result = await ask.AskAsync(member, "噪音管制標準", null, Ct);

        Assert.Equal(new AskCapabilities(HasPlan: true, AiAnswer: false), capabilities);
        Assert.Equal((AskMode.Search, AskStatus.Ok), (result.Mode, result.Status));
        Assert.NotEmpty(result.Hits!);
        Assert.Empty(chat.Calls);
        Assert.Equal(["ontology.search"], await UsageAsync(tenant.Id));
    }

    [Fact]
    public async Task WithPlanAndConsentTheQuestionIsAnsweredFromTheEvidenceAndMetered()
    {
        var (host, chat) = Host(_ => Reply);
        using var h = host;
        var (tenant, member) = await MemberOfAsync("ask-c");
        var ask = host.Services.GetRequiredService<IAskService>();

        var capabilities = await ask.CapabilitiesAsync(member, Ct);
        var result = await ask.AskAsync(member, FakeConsultServer.AnswerableQuestion, null, Ct);

        Assert.Equal(new AskCapabilities(HasPlan: true, AiAnswer: true), capabilities);
        Assert.Equal((AskMode.Answer, AskStatus.Ok), (result.Mode, result.Status));
        Assert.Equal(Reply, result.Answer!.Text);
        Assert.Equal("L1", Assert.Single(result.Answer.Sources).Code);
        Assert.Single(chat.Calls);
        Assert.Equal(["consult.answer", "consult.ground"], await UsageAsync(tenant.Id));
    }

    // 拒答是成功的結果，不是錯誤；consult 拒答就不呼叫模型。
    [Fact]
    public async Task ARefusalIsAResultNotAnErrorAndTheModelIsNeverCalled()
    {
        var (host, chat) = Host(_ => "不該被呼叫 [L1]");
        using var h = host;
        var (_, member) = await MemberOfAsync("ask-d");
        var ask = host.Services.GetRequiredService<IAskService>();

        var result = await ask.AskAsync(member, FakeConsultServer.UnrelatedQuestion, null, Ct);

        Assert.Equal(AskStatus.Ok, result.Status);
        Assert.True(result.Answer!.Refused);
        Assert.Empty(chat.Calls);
    }

    [Fact]
    public async Task BadInputIsInvalidInBothModes()
    {
        var (host, _) = Host(_ => Reply);
        using var h = host;
        var (_, answering) = await MemberOfAsync("ask-e");
        var (_, searching) = await MemberOfAsync("ask-f", consent: false);
        var ask = host.Services.GetRequiredService<IAskService>();

        Assert.Equal((AskMode.Answer, AskStatus.Invalid), Pair(await ask.AskAsync(answering, "  ", null, Ct)));
        Assert.Equal((AskMode.Search, AskStatus.Invalid), Pair(await ask.AskAsync(searching, "  ", null, Ct)));
        Assert.Equal((AskMode.Search, AskStatus.Invalid), Pair(await ask.AskAsync(searching, "噪音管制標準", "yesterday", Ct)));
    }

    [Fact]
    public async Task AModelOutageIsUnavailable()
    {
        var (host, _) = Host(_ => throw new HttpRequestException("down"));
        using var h = host;
        var (_, member) = await MemberOfAsync("ask-g");

        var result = await host.Services.GetRequiredService<IAskService>().AskAsync(member, FakeConsultServer.AnswerableQuestion, null, Ct);

        Assert.Equal((AskMode.Answer, AskStatus.Unavailable), Pair(result));
    }

    // 用量記在呼叫者的租戶：互動式沒有 ambient tenant，記錯租戶就是別人的帳單。
    [Fact]
    public async Task UsageIsRecordedOnlyForTheCallersTenant()
    {
        var (host, _) = Host(_ => Reply);
        using var h = host;
        var (a, memberOfA) = await MemberOfAsync("ask-h");
        var (b, _) = await MemberOfAsync("ask-i");

        await host.Services.GetRequiredService<IAskService>().AskAsync(memberOfA, FakeConsultServer.AnswerableQuestion, null, Ct);

        Assert.NotEmpty(await UsageAsync(a.Id));
        Assert.Empty(await UsageAsync(b.Id));
    }

    // 不是入口成員的身分（匿名、營運者、沒有租戶 claim 的成員）一律當作沒有方案：不問、不記、不呼叫任何服務。
    [Fact]
    public async Task APrincipalThatIsNotAPortalMemberGetsNothing()
    {
        var (host, chat) = Host(_ => "不該被呼叫 [L1]");
        using var h = host;
        var (tenant, member) = await MemberOfAsync("ask-j");
        var withoutTenant = new ClaimsPrincipal(new ClaimsIdentity(
            member.Claims.Where(c => c.Type != PlatformClaimTypes.TenantIdentifier), "portal"));
        // 有租戶 claim、但不是入口成員（例如 API key 的身分）：不能拿來用入口的問答。
        var notAMember = new ClaimsPrincipal(new ClaimsIdentity(
            member.Claims.Where(c => c.Type != PlatformClaimTypes.PortalUserId), "api-key"));
        var ask = host.Services.GetRequiredService<IAskService>();

        foreach (var who in new[] { new ClaimsPrincipal(new ClaimsIdentity()), OperatorSession.Principal(), withoutTenant, notAMember })
        {
            Assert.Equal(new AskCapabilities(false, false), await ask.CapabilitiesAsync(who, Ct));
            Assert.Equal(AskStatus.NoPlan, (await ask.AskAsync(who, FakeConsultServer.AnswerableQuestion, null, Ct)).Status);
            Assert.Null(await ask.RecordAsync(who, null, "問題", "答案", Ct));
        }

        Assert.Empty(chat.Calls);
        Assert.Empty(await UsageAsync(tenant.Id));
    }

    // 歷史只有作者看得到：同組織的 Owner 也不行（規則在 IConversationHistory；這裡證明服務把作者與租戶都交對了）。
    [Fact]
    public async Task ARecordedTurnBelongsToItsAuthorInTheirTenantOnly()
    {
        var (host, _) = Host(_ => Reply, history: true);
        using var h = host;
        var (tenant, author) = await MemberOfAsync("ask-k");
        var (userId, _) = await AddMemberAsync(factory, tenant.Id, NewEmail(), CustomerRoles.Owner);
        var boss = await MemberPrincipalAsync(factory, tenant.Id, userId);
        var ask = host.Services.GetRequiredService<IAskService>();

        var id = await ask.RecordAsync(author, null, "噪音管制標準", "《噪音管制法》第 9 條", Ct);
        var followUp = await ask.RecordAsync(author, id, "固定污染源", "《空氣污染防制法》第 5-1 條", Ct);

        Assert.NotNull(id);
        Assert.Equal(id, followUp); // 接在同一串後面
        Assert.Equal([id!.Value], await ListAsync(host, tenant.Id, author));
        Assert.Empty(await ListAsync(host, tenant.Id, boss));
    }

    [Fact]
    public async Task WhenHistoryIsOffNothingIsRecorded()
    {
        var (_, member) = await MemberOfAsync("ask-l");

        Assert.Null(await factory.Services.GetRequiredService<IAskService>().RecordAsync(member, null, "問題", "答案", Ct));
    }

    private static (AskMode, AskStatus) Pair(AskResult result) => (result.Mode, result.Status);

    private static async Task<List<Guid>> ListAsync(WebApplicationFactory<Program> host, string tenantId, ClaimsPrincipal user)
    {
        var listed = await host.Services.GetRequiredService<TenantScopes>().InAsync<List<Guid>>(tenantId, async scope =>
            AdminOutcome.Ok((await scope.Get<IConversationHistory>().ListAsync(user, 10, Ct)).Select(c => c.Id).ToList()));
        return listed.Value!;
    }
}