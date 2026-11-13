using System.Net;
using System.Text.Json;

using Npgsql;

namespace EnvCite.Platform.Tests;

/// <summary>營運者建立成員與重設密碼：一次性密碼、驗證、衝突、跨租戶、稽核與儲存。</summary>
[Collection(PlatformTestGroup.Name)]
public sealed class MemberAdminTests(PlatformFactory factory)
{
    private IMemberAdmin Admin => factory.Services.GetRequiredService<IMemberAdmin>();

    [Fact]
    public async Task ACreatedMemberGetsAOneTimeStrongPasswordThatIsStoredOnlyAsAHash()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("adm-a");
        var email = NewEmail();

        var created = await Admin.CreateAsync(OperatorSession.Principal(), tenant.Id, $"  {email}  ", "Ann", CustomerRoles.Admin, CancellationToken.None);

        Assert.True(created.IsOk, created.Message);
        var password = created.Value!.Password;
        Assert.True(password.Length >= 16);
        Assert.True(created.Value.Member.MustChangePassword);
        Assert.Equal(email, created.Value.Member.Email); // 去除前後空白

        // 資料庫裡只有雜湊（Identity 的 v3 格式），沒有密碼本身。
        await using var connection = new NpgsqlConnection(factory.Services.GetRequiredService<IOptions<DatabaseOptions>>().Value.Platform);
        await connection.OpenAsync(TestContext.Current.CancellationToken);
        await using var command = new NpgsqlCommand("""select "PasswordHash" from platform."AspNetUsers" where "Id" = @id""", connection);
        command.Parameters.AddWithValue("id", created.Value.Member.Id);
        var hash = (string)(await command.ExecuteScalarAsync(TestContext.Current.CancellationToken))!;
        Assert.StartsWith("AQAAAA", hash, StringComparison.Ordinal);
        Assert.DoesNotContain(password, hash, StringComparison.Ordinal);

        // 兩次產生的密碼不同（不是固定值或可預測的值）。
        var second = await Admin.CreateAsync(OperatorSession.Principal(), tenant.Id, NewEmail(), null, CustomerRoles.Viewer, CancellationToken.None);
        Assert.NotEqual(password, second.Value!.Password);
    }

    [Theory]
    [InlineData("not-an-email", CustomerRoles.Viewer)]
    [InlineData("", CustomerRoles.Viewer)]
    [InlineData("Name <someone@example.com>", CustomerRoles.Viewer)] // 只接受單純的位址
    [InlineData("someone@example.com", "SuperAdmin")] // 不在角色清單
    [InlineData("someone@example.com", "")]
    public async Task BadInputIsRejectedWithAReasonAndCreatesNothing(string email, string role)
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("adm-b");

        var created = await Admin.CreateAsync(OperatorSession.Principal(), tenant.Id, email, null, role, CancellationToken.None);

        Assert.Equal(AdminStatus.Invalid, created.Status);
        Assert.Empty((await Admin.ListAsync(tenant.Id, CancellationToken.None)).Value!);
    }

    [Fact]
    public async Task TheSameEmailTwiceInOneTenantIsAConflictButFineInAnother()
    {
        var (a, _) = await factory.CreateTenantWithKeyAsync("adm-c1");
        var (b, _) = await factory.CreateTenantWithKeyAsync("adm-c2");
        var email = NewEmail();

        Assert.True((await Admin.CreateAsync(OperatorSession.Principal(), a.Id, email, null, CustomerRoles.Owner, CancellationToken.None)).IsOk);
        var again = await Admin.CreateAsync(OperatorSession.Principal(), a.Id, email.ToUpperInvariant(), null, CustomerRoles.Owner, CancellationToken.None);
        var otherTenant = await Admin.CreateAsync(OperatorSession.Principal(), b.Id, email, null, CustomerRoles.Owner, CancellationToken.None);

        Assert.Equal(AdminStatus.Conflict, again.Status); // email 不分大小寫
        Assert.True(otherTenant.IsOk);
    }

    [Fact]
    public async Task EachTenantOnlySeesItsOwnMembers()
    {
        var (a, _) = await factory.CreateTenantWithKeyAsync("adm-d1");
        var (b, _) = await factory.CreateTenantWithKeyAsync("adm-d2");
        var (_, _) = await AddMemberAsync(factory, a.Id, NewEmail());

        Assert.Single((await Admin.ListAsync(a.Id, CancellationToken.None)).Value!);
        Assert.Empty((await Admin.ListAsync(b.Id, CancellationToken.None)).Value!);
        Assert.Equal(AdminStatus.NotFound, (await Admin.ListAsync("no-such-tenant", CancellationToken.None)).Status);
    }

    [Fact]
    public async Task APasswordCannotBeResetThroughAnotherTenantsId()
    {
        var (a, _) = await factory.CreateTenantWithKeyAsync("adm-e1");
        var (b, _) = await factory.CreateTenantWithKeyAsync("adm-e2");
        var (memberOfA, _) = await AddMemberAsync(factory, a.Id, NewEmail());

        var reset = await Admin.ResetPasswordAsync(OperatorSession.Principal(), b.Id, memberOfA, CancellationToken.None);

        Assert.Equal(AdminStatus.NotFound, reset.Status);
    }

    [Fact]
    public async Task AResetEndsEveryOpenSessionAndForcesANewPassword()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "adm-f");
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);
        Assert.Equal(HttpStatusCode.OK, (await browser.GetAsync(Home(tenant.Identifier))).Status);
        var id = (await Admin.ListAsync(tenant.Id, CancellationToken.None)).Value!.Single().Id;

        var reset = await Admin.ResetPasswordAsync(OperatorSession.Principal(), tenant.Id, id, CancellationToken.None);

        Assert.True(reset.IsOk);
        Assert.Equal(HttpStatusCode.Redirect, (await browser.GetAsync(Home(tenant.Identifier))).Status); // 原本的 session 失效
        using var again = new PortalBrowser(factory);
        var oldPassword = await again.SubmitAsync(Login(tenant.Identifier), Credentials(email, NewPassword));
        Assert.Equal(HttpStatusCode.OK, oldPassword.Status); // 舊密碼不能用
        var fresh = await again.SubmitAsync(Login(tenant.Identifier), Credentials(email, reset.Value!.Password));
        Assert.Equal(PasswordPage(tenant.Identifier), fresh.Location); // 新的一次性密碼，第一次要改
    }

    [Fact]
    public async Task EveryActionIsAuditedWithTheOperatorAndNeverTheSecret()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("adm-g");
        var operatorUser = OperatorSession.Principal("auditor@example.com");
        var created = await Admin.CreateAsync(operatorUser, tenant.Id, NewEmail(), "Bo", CustomerRoles.Billing, CancellationToken.None);
        var reset = await Admin.ResetPasswordAsync(operatorUser, tenant.Id, created.Value!.Member.Id, CancellationToken.None);

        var audit = factory.Services.GetRequiredService<IAuditQuery>();
        var createdEvent = Assert.Single((await audit.QueryAsync(new AuditFilter(Tenant: tenant.Id, Action: AuditActions.MemberCreated), CancellationToken.None)).Items);
        var resetEvent = Assert.Single((await audit.QueryAsync(new AuditFilter(Tenant: tenant.Id, Action: AuditActions.MemberPasswordReset), CancellationToken.None)).Items);

        foreach (var evt in new[] { createdEvent, resetEvent })
        {
            Assert.Equal(AuditActors.OperatorUser, evt.ActorType);
            Assert.Equal("auditor@example.com", evt.ActorId);
            Assert.Equal(created.Value.Member.Id, evt.TargetId);
            var dump = JsonSerializer.Serialize(evt);
            Assert.DoesNotContain(created.Value.Password, dump, StringComparison.Ordinal);
            Assert.DoesNotContain(reset.Value!.Password, dump, StringComparison.Ordinal);
        }
    }
}