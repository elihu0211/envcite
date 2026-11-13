using EnvCite.Platform.Modules.Tenancy;

using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection.Extensions;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 入口的工作區是互動式的：頁面載入後要連上 SignalR、circuit 裡的身分要是登入的成員、之後的互動不經頁面請求。
/// 這些只有真的瀏覽器加真的伺服器（Kestrel，不是 TestServer）才驗得到——circuit 的身分來自連線，
/// 連線沒有租戶路徑，曾經因此把入口成員當成匿名、整頁被導去後台登入。其餘邏輯在 <c>ChatSessionTests</c> 與 <c>AskServiceTests</c>。
/// 這裡只負責在行程內起伺服器（真的 PostgreSQL、假的 consult 與模型）、建租戶與成員；瀏覽器的情境在 <c>tests/e2e/workspace.mjs</c>。
/// </summary>
// 放在 Group1：問答會呼叫 consult 的 Ground，gRPC 計量器是行程內全域的（同 AskServiceTests）。
[Collection(PlatformTestGroup.Group1)]
public sealed class WorkspaceE2ETests(PlatformFactory factory)
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private async Task<FakeChat> RunAsync(string scenario, bool consent, bool otherTenant = false)
    {
        Assert.SkipWhen(E2EToolchain.Skipped, "ENVCITE_SKIP_E2E=1");
        await E2EToolchain.EnsureAsync(Ct);

        var chat = new FakeChat(_ => "在指定清除地區內不得燃燒 [L1]。");
        using var host = factory.WithWebHostBuilder(b =>
        {
            b.UseSetting("Platform:Answer:Enabled", consent ? "true" : "false")
                .UseSetting("Platform:Answer:Endpoint", "https://models.example.test/v1")
                .UseSetting("Platform:Answer:ApiKey", "test-key")
                .UseSetting("Platform:ConversationHistory:Enabled", "true");
            b.ConfigureServices(s => s.Replace(ServiceDescriptor.Singleton<IChatClient>(chat)));
        });
        host.UseKestrel(0);
        host.StartServer();

        // Cookie 是 Secure：Chromium 只在 localhost 把 http 當成安全的來源。
        var address = host.Services.GetRequiredService<IServer>().Features.Get<IServerAddressesFeature>()!.Addresses.First()
            .Replace("[::]", "localhost", StringComparison.Ordinal).Replace("127.0.0.1", "localhost", StringComparison.Ordinal);

        var (tenant, email, initial) = await MemberAsync($"e2e-{scenario}", consent);
        var environment = new Dictionary<string, string>
        {
            ["E2E_BASE"] = address,
            ["E2E_TENANT"] = tenant.Identifier,
            ["E2E_EMAIL"] = email,
            ["E2E_INITIAL"] = initial,
            ["E2E_NEW_PASSWORD"] = NewPassword,
            ["E2E_QUESTION"] = FakeConsultServer.AnswerableQuestion,
            ["E2E_SHOTS"] = Directory.CreateDirectory(Path.Combine(AppContext.BaseDirectory, "e2e-shots")).FullName,
        };
        if (E2EToolchain.Chromium is { Length: > 0 } chromium)
            environment["E2E_CHROMIUM"] = chromium;

        if (otherTenant)
            environment["E2E_OTHER_TENANT"] = (await MemberAsync($"e2e-{scenario}-other", consent)).Tenant.Identifier;

        var output = await E2EToolchain.RunAsync("node", ["workspace.mjs", scenario], environment, TimeSpan.FromMinutes(2), Ct);
        Assert.DoesNotContain("FAIL", output, StringComparison.Ordinal);
        return chat;
    }

    private async Task<(TenantResponse Tenant, string Email, string Initial)> MemberAsync(string slug, bool consent)
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync(slug);
        var plans = factory.Services.GetRequiredService<IPlanAdmin>();
        Assert.True((await plans.SetAsync(OperatorSession.Principal(), tenant.Id, "standard", Ct)).IsOk);
        if (consent)
            Assert.True((await plans.SetAiAnswerAsync(OperatorSession.Principal(), tenant.Id, true, Ct)).IsOk);

        var email = NewEmail();
        var (_, initial) = await AddMemberAsync(factory, tenant.Id, email, CustomerRoles.Owner);
        return (tenant, email, initial);
    }

    [Fact]
    public async Task AMemberSignsInAsksFollowsUpReloadsAndReopensTheirConversation()
    {
        var chat = await RunAsync("flow", consent: true);

        Assert.Equal(2, chat.Calls.Count); // 兩題各問一次模型
    }

    [Fact]
    public async Task ARefusalLeavesTheCircuitUsableAndNeverCallsTheModel()
    {
        var chat = await RunAsync("refusal", consent: true);

        Assert.Single(chat.Calls); // 被拒答的那題沒有呼叫模型，之後的那題才有
    }

    [Fact]
    public async Task WithoutTheAiConsentTheWorkspaceListsTheArticlesAndNeverCallsAModel()
    {
        var chat = await RunAsync("search", consent: false);

        Assert.Empty(chat.Calls);
    }

    [Fact]
    public async Task TheHomeComposerGrowsSubmitsOnEnterAndKeepsItsHelpInANativeSheet()
    {
        var chat = await RunAsync("composer", consent: true);

        Assert.Single(chat.Calls); // 只有 Enter 送出的那題有證據、呼叫了模型；範例被假的 consult 拒答；Shift+Enter、組字中、空白都沒有送出
    }

    [Fact]
    public async Task TheShellDoesNotMoveBetweenTheInteractiveWorkspaceAndTheStaticPages()
    {
        var chat = await RunAsync("shell", consent: true);

        Assert.Empty(chat.Calls); // 只換頁，不問問題
    }

    [Fact]
    public async Task AMemberCannotReachAnotherTenantsWorkspaceFromInsideTheCircuit()
    {
        var chat = await RunAsync("cross-tenant", consent: true, otherTenant: true);

        Assert.Empty(chat.Calls);
    }
}