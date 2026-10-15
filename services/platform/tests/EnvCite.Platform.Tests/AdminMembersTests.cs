using System.Net;
using System.Net.Http.Json;
using System.Text.Json;

using EnvCite.Platform.Modules.ApiAccess;
using EnvCite.Platform.Modules.Members;
using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

using static EnvCite.Platform.Tests.PortalTestSupport;

namespace EnvCite.Platform.Tests;

/// <summary>
/// <c>/admin</c> 的成員端點：不靠營運後台（Google 登入）就能建立租戶的第一位成員。
/// 與營運後台、客戶自己的管理員共用同一個核心，所以這裡只驗證端點本身：驗證、狀態碼、金鑰保護、不洩漏。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed class AdminMembersTests(PlatformFactory factory)
{
    private static string Url(string tenantId, string tail = "") => $"/admin/tenants/{tenantId}/members{tail}";

    private static async Task<(string Id, string Password)> CreateAsync(HttpClient admin, string tenantId, string email, string role)
    {
        using var response = await admin.PostAsJsonAsync(Url(tenantId), new CreateMemberRequest(email, "王小明", role));
        Assert.Equal(HttpStatusCode.Created, response.StatusCode);
        var issued = await response.Content.ReadFromJsonAsync<JsonElement>();
        return (issued.GetProperty("member").GetProperty("id").GetString()!, issued.GetProperty("password").GetString()!);
    }

    [Fact]
    public async Task TheFirstOwnerOfATenantCanBeCreatedWithJustTheAdminKeyAndThenSignsIn()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("am-a");
        using var admin = factory.CreateAdminClient();
        var email = NewEmail();

        var (id, password) = await CreateAsync(admin, tenant.Id, email, CustomerRoles.Owner);

        Assert.True(password.Length >= 16);
        using var browser = new PortalBrowser(factory);
        var signedIn = await browser.SubmitAsync(Login(tenant.Identifier), Credentials(email, password));
        Assert.Equal(PasswordPage(tenant.Identifier), signedIn.Location); // 與營運後台建立的一樣：第一次必須改密碼

        var list = await admin.GetFromJsonAsync<JsonElement>(Url(tenant.Id), cancellationToken: TestContext.Current.CancellationToken);
        var member = Assert.Single(list.EnumerateArray());
        Assert.Equal((id, email, CustomerRoles.Owner), (member.GetProperty("id").GetString(), member.GetProperty("email").GetString(), member.GetProperty("role").GetString()));
        Assert.False(member.TryGetProperty("password", out _)); // 清單裡沒有任何密碼
    }

    [Theory]
    [InlineData("not-an-email", CustomerRoles.Owner)]
    [InlineData("someone@example.com", "SuperAdmin")]
    [InlineData(null, CustomerRoles.Owner)]
    [InlineData("someone@example.com", null)]
    public async Task BadInputIs400AndCreatesNothing(string? email, string? role)
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("am-b");
        using var admin = factory.CreateAdminClient();

        using var response = await admin.PostAsJsonAsync(Url(tenant.Id), new CreateMemberRequest(email, null, role), cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Empty((await admin.GetFromJsonAsync<JsonElement>(Url(tenant.Id), cancellationToken: TestContext.Current.CancellationToken)).EnumerateArray());
    }

    [Fact]
    public async Task ADuplicateIs409AndAnUnknownTenantOrMemberIs404()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("am-c");
        using var admin = factory.CreateAdminClient();
        var email = NewEmail();
        var (id, _) = await CreateAsync(admin, tenant.Id, email, CustomerRoles.Viewer);

        using var duplicate = await admin.PostAsJsonAsync(Url(tenant.Id), new CreateMemberRequest(email, null, CustomerRoles.Viewer), cancellationToken: TestContext.Current.CancellationToken);
        using var noTenant = await admin.PostAsJsonAsync(Url("no-such-tenant"), new CreateMemberRequest(NewEmail(), null, CustomerRoles.Viewer), cancellationToken: TestContext.Current.CancellationToken);
        using var noMember = await admin.PostAsync(new Uri(Url(tenant.Id, "/no-such-member/password-reset"), UriKind.Relative), content: null, cancellationToken: TestContext.Current.CancellationToken);
        using var noList = await admin.GetAsync(new Uri(Url("no-such-tenant"), UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.Conflict, duplicate.StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, noTenant.StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, noMember.StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, noList.StatusCode);
        Assert.NotEmpty(id);
    }

    [Fact]
    public async Task ResetDisableAndEnableWorkThroughTheApiAndTheOperatorIsTheActor()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("am-d");
        using var admin = factory.CreateAdminClient();
        var email = NewEmail();
        var (id, initial) = await CreateAsync(admin, tenant.Id, email, CustomerRoles.Developer);
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        // 第二個 session：停用與重新啟用之間它完全沒有發過請求，所以它的 cookie 還在瀏覽器裡（第一個在停用時被擋下、cookie 已被清掉）。
        using var untouched = new PortalBrowser(factory);
        Assert.Equal(Home(tenant.Identifier), (await untouched.SubmitAsync(Login(tenant.Identifier), Credentials(email, NewPassword))).Location);

        // 停用：既有 session 立刻失效，不能登入。
        using var disabled = await admin.PostAsync(new Uri(Url(tenant.Id, $"/{id}/disable"), UriKind.Relative), content: null, cancellationToken: TestContext.Current.CancellationToken);
        Assert.Equal(HttpStatusCode.OK, disabled.StatusCode);
        Assert.True((await disabled.Content.ReadFromJsonAsync<JsonElement>(cancellationToken: TestContext.Current.CancellationToken)).GetProperty("disabled").GetBoolean());
        Assert.Equal(HttpStatusCode.Redirect, (await browser.GetAsync(Home(tenant.Identifier))).Status);

        // 再停用一次是冪等的：不重複記錄。
        (await admin.PostAsync(new Uri(Url(tenant.Id, $"/{id}/disable"), UriKind.Relative), content: null, cancellationToken: TestContext.Current.CancellationToken)).Dispose();

        using var enabled = await admin.PostAsync(new Uri(Url(tenant.Id, $"/{id}/enable"), UriKind.Relative), content: null, cancellationToken: TestContext.Current.CancellationToken);
        Assert.False((await enabled.Content.ReadFromJsonAsync<JsonElement>(cancellationToken: TestContext.Current.CancellationToken)).GetProperty("disabled").GetBoolean());

        // 重新啟用不能讓停用之前發出的 cookie 復活：停用期間它被旗標擋住，旗標一拿掉，只有換過的安全戳記還擋得住它。
        Assert.Equal(HttpStatusCode.Redirect, (await untouched.GetAsync(Home(tenant.Identifier))).Status);

        // 重設密碼：新的一次性密碼可用，舊的不行。
        using var reset = await admin.PostAsync(new Uri(Url(tenant.Id, $"/{id}/password-reset"), UriKind.Relative), content: null, cancellationToken: TestContext.Current.CancellationToken);
        var newPassword = (await reset.Content.ReadFromJsonAsync<JsonElement>(cancellationToken: TestContext.Current.CancellationToken)).GetProperty("password").GetString()!;
        using var fresh = new PortalBrowser(factory);
        Assert.Contains("電子郵件或密碼不正確", (await fresh.SubmitAsync(Login(tenant.Identifier), Credentials(email, NewPassword))).Html, StringComparison.Ordinal);
        Assert.Equal(PasswordPage(tenant.Identifier), (await fresh.SubmitAsync(Login(tenant.Identifier), Credentials(email, newPassword))).Location);

        var audit = factory.Services.GetRequiredService<IAuditQuery>();
        foreach (var action in new[] { AuditActions.MemberCreated, AuditActions.MemberDisabled, AuditActions.MemberEnabled, AuditActions.MemberPasswordReset })
        {
            var events = (await audit.QueryAsync(new AuditFilter(Tenant: tenant.Id, Action: action), CancellationToken.None)).Items;
            var evt = Assert.Single(events);
            Assert.Equal(AuditActors.OperatorKey, evt.ActorType); // 用共用的 admin key 做的，記的就是 admin key
        }
    }

    [Fact]
    public async Task OnlyTheAdminKeyReachesThem()
    {
        var (tenant, key) = await factory.CreateTenantWithKeyAsync("am-e");
        using var anonymous = factory.CreateClient();
        using var tenantClient = factory.CreateTenantClient(key.Key);

        using var noKey = await anonymous.GetAsync(new Uri(Url(tenant.Id), UriKind.Relative), TestContext.Current.CancellationToken);
        using var asTenant = await tenantClient.PostAsJsonAsync(Url(tenant.Id), new CreateMemberRequest(NewEmail(), null, CustomerRoles.Owner), cancellationToken: TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.Unauthorized, noKey.StatusCode);
        Assert.True(asTenant.StatusCode is HttpStatusCode.Unauthorized or HttpStatusCode.Forbidden);
    }

    // 回應含一次性密碼，但請求與回應的內容都不能出現在日誌或稽核裡。
    [Fact]
    public async Task TheIssuedPasswordNeverReachesTheLogsOrTheAuditTrail()
    {
        var logs = new CapturingLoggerProvider();
        await using var host = factory.WithWebHostBuilder(b => b.ConfigureLogging(l => l.AddProvider(logs)));
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("am-f");
        using var admin = host.CreateClient();
        admin.DefaultRequestHeaders.Add(AdminKeyAuthenticationHandler.HeaderName, PlatformFactory.AdminKey);

        var (id, password) = await CreateAsync(admin, tenant.Id, NewEmail(), CustomerRoles.Owner);
        using var reset = await admin.PostAsync(new Uri(Url(tenant.Id, $"/{id}/password-reset"), UriKind.Relative), content: null, cancellationToken: TestContext.Current.CancellationToken);
        var second = (await reset.Content.ReadFromJsonAsync<JsonElement>(cancellationToken: TestContext.Current.CancellationToken)).GetProperty("password").GetString()!;

        var events = new List<AuditRecord>();
        foreach (var action in AuditActions.All)
        {
            events.AddRange((await factory.Services.GetRequiredService<IAuditQuery>()
                .QueryAsync(new AuditFilter(Tenant: tenant.Id, Action: action), CancellationToken.None)).Items);
        }

        var dump = JsonSerializer.Serialize(events) + string.Join('\n', logs.Messages);
        Assert.NotEmpty(logs.Messages);
        Assert.DoesNotContain(password, dump, StringComparison.Ordinal);
        Assert.DoesNotContain(second, dump, StringComparison.Ordinal);
    }
}