using System.Net;
using System.Net.Http.Json;
using System.Text.Json;

using EnvCite.Platform.Modules.Members;
using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Hosting;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

using static EnvCite.Platform.Tests.OperatorTestSupport;
using static EnvCite.Platform.Tests.PortalTestSupport;

namespace EnvCite.Platform.Tests;

/// <summary>
/// <c>/admin/operators</c>：營運者帳號只能由持營運者金鑰的人管理。與 <c>AdminMembersTests</c> 對應：
/// 驗證端點本身——驗證、狀態碼、金鑰保護、稽核、一次性密碼不外流。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed class AdminOperatorsTests(PlatformFactory factory)
{
    private const string Operators = "/admin/operators";

    private static async Task<(string Id, string Password)> CreateAsync(HttpClient admin, string email)
    {
        using var response = await admin.PostAsJsonAsync(Operators, new CreateOperatorRequest(email, "王小明"));
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        var issued = await response.Content.ReadFromJsonAsync<JsonElement>();
        return (issued.GetProperty("operator").GetProperty("id").GetString()!, issued.GetProperty("password").GetString()!);
    }

    private static async Task<HttpResponseMessage> PostAsync(HttpClient admin, string id, string action) =>
        await admin.PostAsync(new Uri($"{Operators}/{id}/{action}", UriKind.Relative), content: null);

    private async Task<List<AuditRecord>> EventsAsync(string action, string id) =>
        [.. (await factory.Services.GetRequiredService<IAuditQuery>().QueryAsync(new AuditFilter(Action: action, Limit: 200), CancellationToken.None))
            .Items.Where(e => e.TargetId == id)];

    [Fact]
    public async Task AnOperatorIsCreatedWithJustTheAdminKeyAndSignsInWithTheOneTimePassword()
    {
        using var admin = factory.CreateAdminClient();
        var email = NewOperatorEmail();

        var (id, password) = await CreateAsync(admin, email);

        Assert.True(password.Length >= 16);
        using var browser = new PortalBrowser(factory);
        var signedIn = await browser.SubmitAsync(UiLogin, Credentials(email, password));
        Assert.Equal(UiPassword, signedIn.Location); // 第一次必須改密碼

        var list = await admin.GetFromJsonAsync<JsonElement>(Operators, cancellationToken: TestContext.Current.CancellationToken);
        var created = list.EnumerateArray().Single(o => o.GetProperty("id").GetString() == id);
        Assert.Equal(email, created.GetProperty("email").GetString());
        Assert.True(created.GetProperty("mustChangePassword").GetBoolean());
        Assert.False(created.GetProperty("twoFactorEnabled").GetBoolean());
        Assert.False(created.GetProperty("disabled").GetBoolean());
        Assert.False(created.TryGetProperty("password", out _)); // 清單裡沒有任何密碼
    }

    [Theory]
    [InlineData("not-an-email")]
    [InlineData("")]
    [InlineData(null)]
    public async Task BadInputIs400AndCreatesNothing(string? email)
    {
        using var admin = factory.CreateAdminClient();
        var before = (await admin.GetFromJsonAsync<JsonElement>(Operators, cancellationToken: TestContext.Current.CancellationToken)).GetArrayLength();

        using var response = await admin.PostAsJsonAsync(Operators, new CreateOperatorRequest(email, null), cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Equal(before, (await admin.GetFromJsonAsync<JsonElement>(Operators, cancellationToken: TestContext.Current.CancellationToken)).GetArrayLength());
    }

    [Fact]
    public async Task ADuplicateEmailInAnyCaseIs409AndAnUnknownIdIs404()
    {
        using var admin = factory.CreateAdminClient();
        var email = NewOperatorEmail();
        _ = await CreateAsync(admin, email);

        using var duplicate = await admin.PostAsJsonAsync(Operators, new CreateOperatorRequest(email.ToUpperInvariant(), null), cancellationToken: TestContext.Current.CancellationToken);
        var missing = Guid.NewGuid().ToString();

        Assert.Equal(HttpStatusCode.Conflict, duplicate.StatusCode);
        foreach (var action in new[] { "password-reset", "two-factor-reset", "disable", "enable" })
        {
            using var response = await PostAsync(admin, missing, action);
            Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
        }

        using var notAnId = await PostAsync(admin, "not-a-guid", "disable");
        Assert.Equal(HttpStatusCode.NotFound, notAnId.StatusCode);
    }

    [Fact]
    public async Task EveryEndpointNeedsTheAdminKey()
    {
        using var anonymous = factory.CreateClient();
        var id = Guid.NewGuid().ToString();

        var responses = new List<HttpResponseMessage>
        {
            await anonymous.GetAsync(new Uri(Operators, UriKind.Relative), TestContext.Current.CancellationToken),
            await anonymous.PostAsJsonAsync(Operators, new CreateOperatorRequest(NewOperatorEmail(), null), cancellationToken: TestContext.Current.CancellationToken),
        };
        foreach (var action in new[] { "password-reset", "two-factor-reset", "disable", "enable" })
        {
            responses.Add(await PostAsync(anonymous, id, action));
        }

        Assert.All(responses, r => Assert.Equal(HttpStatusCode.Unauthorized, r.StatusCode));
        responses.ForEach(r => r.Dispose());
    }

    [Fact]
    public async Task ResetDisableAndEnableWorkThroughTheApiAndTheOperatorKeyIsTheActor()
    {
        using var admin = factory.CreateAdminClient();
        var email = NewOperatorEmail();
        var (id, initial) = await CreateAsync(admin, email);

        // 重設密碼：新的一次性密碼，舊的不能用，要求再改密碼。
        using var reset = await PostAsync(admin, id, "password-reset");
        Assert.Equal(HttpStatusCode.OK, reset.StatusCode);
        var second = (await reset.Content.ReadFromJsonAsync<JsonElement>(cancellationToken: TestContext.Current.CancellationToken)).GetProperty("password").GetString()!;
        Assert.NotEqual(initial, second);
        using var browser = new PortalBrowser(factory);
        Assert.Contains("電子郵件或密碼不正確", (await browser.SubmitAsync(UiLogin, Credentials(email, initial))).Html, StringComparison.Ordinal);
        Assert.Equal(UiPassword, (await browser.SubmitAsync(UiLogin, Credentials(email, second))).Location);

        // 停用：不能登入；再停用一次是冪等的，不重複記錄。
        using var disabled = await PostAsync(admin, id, "disable");
        Assert.True((await disabled.Content.ReadFromJsonAsync<JsonElement>(cancellationToken: TestContext.Current.CancellationToken)).GetProperty("disabled").GetBoolean());
        using var again = await PostAsync(admin, id, "disable");
        Assert.Equal(HttpStatusCode.OK, again.StatusCode);
        using var blocked = new PortalBrowser(factory);
        Assert.Contains("電子郵件或密碼不正確", (await blocked.SubmitAsync(UiLogin, Credentials(email, second))).Html, StringComparison.Ordinal);
        Assert.Single(await EventsAsync(AuditActions.OperatorDisabled, id));

        // 重新啟用後可以再登入。
        using var enabled = await PostAsync(admin, id, "enable");
        Assert.False((await enabled.Content.ReadFromJsonAsync<JsonElement>(cancellationToken: TestContext.Current.CancellationToken)).GetProperty("disabled").GetBoolean());
        using var back = new PortalBrowser(factory);
        Assert.Equal(UiPassword, (await back.SubmitAsync(UiLogin, Credentials(email, second))).Location);

        foreach (var action in new[] { AuditActions.OperatorCreated, AuditActions.OperatorPasswordReset, AuditActions.OperatorDisabled, AuditActions.OperatorEnabled })
        {
            var evt = Assert.Single(await EventsAsync(action, id));
            Assert.Equal((AuditActors.OperatorKey, "admin-key", (string?)null), (evt.ActorType, evt.ActorId, evt.TenantId));
            Assert.Equal(email, evt.Detail.GetProperty("email").GetString());
        }
    }

    [Fact]
    public async Task ATwoFactorResetThroughTheApiIsAuditedAndAsksForANewAuthenticator()
    {
        var op = await factory.EnsureOperatorAsync(NewOperatorEmail());
        using var admin = factory.CreateAdminClient();

        using var reset = await PostAsync(admin, op.Id.ToString(), "two-factor-reset");

        Assert.Equal(HttpStatusCode.OK, reset.StatusCode);
        Assert.False((await reset.Content.ReadFromJsonAsync<JsonElement>(cancellationToken: TestContext.Current.CancellationToken)).GetProperty("twoFactorEnabled").GetBoolean());
        var evt = Assert.Single(await EventsAsync(AuditActions.OperatorMfaReset, op.Id.ToString()));
        Assert.Equal(AuditActors.OperatorKey, evt.ActorType);
    }

    [Fact]
    public async Task TheIssuedPasswordNeverReachesTheLogsOrTheAuditTrail()
    {
        var logs = new CapturingLoggerProvider();
        await using var host = factory.WithWebHostBuilder(b => b.ConfigureLogging(l => l.AddProvider(logs)));
        using var admin = host.CreateClient();
        admin.DefaultRequestHeaders.Add(Modules.ApiAccess.AdminKeyAuthenticationHandler.HeaderName, PlatformFactory.AdminKey);

        var (id, password) = await CreateAsync(admin, NewOperatorEmail());
        using var reset = await PostAsync(admin, id, "password-reset");
        var second = (await reset.Content.ReadFromJsonAsync<JsonElement>(cancellationToken: TestContext.Current.CancellationToken)).GetProperty("password").GetString()!;

        var events = new List<AuditRecord>();
        foreach (var action in AuditActions.All)
        {
            events.AddRange((await host.Services.GetRequiredService<IAuditQuery>()
                .QueryAsync(new AuditFilter(Action: action, Limit: 200), CancellationToken.None)).Items);
        }

        var dump = JsonSerializer.Serialize(events) + string.Join('\n', logs.Messages);
        Assert.NotEmpty(logs.Messages);
        Assert.Contains(AuditActions.OperatorCreated, dump, StringComparison.Ordinal);
        Assert.DoesNotContain(password, dump, StringComparison.Ordinal);
        Assert.DoesNotContain(second, dump, StringComparison.Ordinal);
    }

    [Fact]
    public void TheOperatorTableIsNotTheMemberTable()
    {
        // 營運者是另一個 user store：不在成員（隨租戶）的 Identity 表裡，也沒有租戶篩選。
        using var scope = factory.Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<Data.PlatformDbContext>();
        var table = db.Model.FindEntityType(typeof(Data.OperatorUser))!;
        var member = db.Model.FindEntityType(typeof(Data.ApplicationUser))!;

        Assert.Equal("Operators", table.GetTableName());
        Assert.NotEqual(member.GetTableName(), table.GetTableName());
        Assert.Empty(table.GetDeclaredQueryFilters());
        Assert.Null(table.FindProperty("TenantId"));
    }
}