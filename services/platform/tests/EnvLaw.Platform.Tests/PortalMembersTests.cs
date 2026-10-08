using System.Net;
using System.Text.Json;
using System.Text.RegularExpressions;

using EnvLaw.Platform.Modules.Tenancy;
using EnvLaw.Platform.SharedKernel;

using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

using Npgsql;

using static EnvLaw.Platform.Tests.PortalTestSupport;

namespace EnvLaw.Platform.Tests;

/// <summary>
/// 客戶自己的管理員管理成員（不經營運者）：階層、不能動自己、停用立即生效、一次性密碼只顯示一次、租戶隔離。
/// 共用主機不強制兩步驟，所以這裡的擁有者與管理員登入後就能用。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed partial class PortalMembersTests(PlatformFactory factory)
{
    private const string Action = "portal-member-action";

    private static string MembersPage(string tenant) => $"/portal/{tenant}/members";

    private static Dictionary<string, string> Add(string email, string role, string name = "") =>
        new() { ["_handler"] = "portal-member-add", ["Add.Email"] = email, ["Add.DisplayName"] = name, ["Add.Role"] = role };

    private static Dictionary<string, string> Act(string action, string? role = null, string? roleFor = null)
    {
        var fields = new Dictionary<string, string> { ["_handler"] = Action, ["Input.Action"] = action };
        if (role is not null && roleFor is not null)
        {
            fields[$"role-{roleFor}"] = role;
        }

        return fields;
    }

    [GeneratedRegex("""id="issued-password">([^<]+)</code>""")]
    private static partial Regex IssuedPassword();

    private sealed record Team(TenantResponse Tenant, PortalBrowser Owner, string OwnerId, string OwnerEmail);

    private IMemberAdmin Admin => factory.Services.GetRequiredService<IMemberAdmin>();

    private async Task<string> IdOfAsync(string tenantId, string email) =>
        (await Admin.ListAsync(tenantId, CancellationToken.None)).Value!.Single(m => m.Email == email).Id;

    /// <summary>一個租戶，一位已登入的擁有者。</summary>
    private async Task<Team> TeamAsync(string prefix)
    {
        var (tenant, email, initial) = await MemberAsync(factory, prefix, CustomerRoles.Owner);
        var owner = new PortalBrowser(factory);
        await SignInFullyAsync(owner, tenant.Identifier, email, initial);
        return new Team(tenant, owner, await IdOfAsync(tenant.Id, email), email);
    }

    private async Task<(PortalBrowser Browser, string Id, string Email)> SignedInMemberAsync(TenantResponse tenant, string role)
    {
        var email = NewEmail();
        var (id, initial) = await AddMemberAsync(factory, tenant.Id, email, role);
        var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);
        return (browser, id, email);
    }

    private async Task<List<AuditRecord>> EventsAsync(string tenantId, string action) =>
        [.. (await factory.Services.GetRequiredService<IAuditQuery>()
            .QueryAsync(new AuditFilter(Tenant: tenantId, Action: action), CancellationToken.None)).Items];

    [Fact]
    public async Task AnOwnerAddsAMemberWithoutAnOperatorAndTheOneTimePasswordWorksOnce()
    {
        var team = await TeamAsync("mem-a");
        using var owner = team.Owner;
        var email = NewEmail();

        var created = await owner.SubmitAsync(MembersPage(team.Tenant.Identifier), Add(email, CustomerRoles.Viewer, "新同事"));

        var password = IssuedPassword().Match(created.Html).Groups[1].Value;
        Assert.True(password.Length >= 16);
        Assert.Contains("只會顯示這一次", created.Html, StringComparison.Ordinal);
        Assert.Contains(email, created.Html, StringComparison.Ordinal);

        // 重新整理就沒有了。
        var again = await owner.GetAsync(MembersPage(team.Tenant.Identifier));
        Assert.DoesNotContain(password, again.Html, StringComparison.Ordinal);

        // 新成員用它登入，第一次必須改；改完是他自己的密碼。
        using var member = new PortalBrowser(factory);
        var signedIn = await member.SubmitAsync(Login(team.Tenant.Identifier), Credentials(email, password));
        Assert.Equal(PasswordPage(team.Tenant.Identifier), signedIn.Location);

        // 稽核的操作者是這位擁有者（租戶成員），不是營運者；密碼不在裡面。
        var evt = Assert.Single(await EventsAsync(team.Tenant.Id, AuditActions.MemberCreated), e => e.Detail.GetProperty("email").GetString() == email);
        Assert.Equal((AuditActors.TenantUser, team.OwnerId), (evt.ActorType, evt.ActorId));
        Assert.DoesNotContain(password, JsonSerializer.Serialize(evt), StringComparison.Ordinal);
    }

    [Fact]
    public async Task AnAdminCanAddLowerRolesButNeverAnAdminOrAnOwner()
    {
        var team = await TeamAsync("mem-b");
        using var owner = team.Owner;
        var (adminBrowser, _, _) = await SignedInMemberAsync(team.Tenant, CustomerRoles.Admin);
        using var admin = adminBrowser;

        var page = await admin.GetAsync(MembersPage(team.Tenant.Identifier));
        Assert.DoesNotContain("擁有者（管理成員與帳務）", page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("管理員（管理成員與 API key）", page.Html, StringComparison.Ordinal);
        Assert.Contains("開發者（管理 API key、看用量）", page.Html, StringComparison.Ordinal);

        var ok = await admin.SubmitAsync(MembersPage(team.Tenant.Identifier), Add(NewEmail(), CustomerRoles.Developer));
        Assert.Contains("新成員已建立", ok.Html, StringComparison.Ordinal);

        // 表單沒有這些選項，但可以被直接送：服務端要擋。
        var before = (await Admin.ListAsync(team.Tenant.Id, CancellationToken.None)).Value!.Count;
        foreach (var role in new[] { CustomerRoles.Owner, CustomerRoles.Admin })
        {
            var refused = await admin.SubmitAsync(MembersPage(team.Tenant.Identifier), Add(NewEmail(), role));
            Assert.Contains("不能新增這個角色", refused.Html, StringComparison.Ordinal);
        }

        Assert.Equal(before, (await Admin.ListAsync(team.Tenant.Id, CancellationToken.None)).Value!.Count);
    }

    [Fact]
    public async Task AnAdminCannotTouchAnOwnerOrAnotherAdminEvenByPostingTheActionDirectly()
    {
        var team = await TeamAsync("mem-c");
        using var owner = team.Owner;
        var (adminBrowser, _, _) = await SignedInMemberAsync(team.Tenant, CustomerRoles.Admin);
        using var admin = adminBrowser;
        var (otherAdminBrowser, otherAdminId, _) = await SignedInMemberAsync(team.Tenant, CustomerRoles.Admin);
        using var otherAdmin = otherAdminBrowser;

        foreach (var target in new[] { team.OwnerId, otherAdminId })
        {
            foreach (var action in new[] { $"disable:{target}", $"reset:{target}" })
            {
                var refused = await admin.SubmitAsync(MembersPage(team.Tenant.Identifier), Act(action));
                Assert.Contains("不能管理這位成員", refused.Html, StringComparison.Ordinal);
            }

            var promote = await admin.SubmitAsync(MembersPage(team.Tenant.Identifier), Act($"role:{target}", CustomerRoles.Viewer, target));
            Assert.Contains("不能管理這位成員", promote.Html, StringComparison.Ordinal);
        }

        var members = (await Admin.ListAsync(team.Tenant.Id, CancellationToken.None)).Value!;
        Assert.All(members, m => Assert.False(m.Disabled));
        Assert.Contains(members, m => m.Id == team.OwnerId && m.Role == CustomerRoles.Owner);

        // 它們看到的清單裡，這兩列也沒有操作按鈕。
        var page = await admin.GetAsync(MembersPage(team.Tenant.Identifier));
        Assert.DoesNotContain($"disable:{team.OwnerId}", page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain($"disable:{otherAdminId}", page.Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task NobodyCanDisableDemoteOrResetThemselves()
    {
        var team = await TeamAsync("mem-d");
        using var owner = team.Owner;

        foreach (var action in new[] { $"disable:{team.OwnerId}", $"reset:{team.OwnerId}" })
        {
            var refused = await owner.SubmitAsync(MembersPage(team.Tenant.Identifier), Act(action));
            Assert.Contains("不能對自己做這件事", refused.Html, StringComparison.Ordinal);
        }

        var demote = await owner.SubmitAsync(MembersPage(team.Tenant.Identifier), Act($"role:{team.OwnerId}", CustomerRoles.Viewer, team.OwnerId));
        Assert.Contains("不能對自己做這件事", demote.Html, StringComparison.Ordinal);

        // 構造上，組織一定留著一位啟用中的擁有者：只有啟用中的擁有者能管理擁有者，而且沒有人能動自己。
        var me = (await Admin.ListAsync(team.Tenant.Id, CancellationToken.None)).Value!.Single();
        Assert.Equal((CustomerRoles.Owner, false), (me.Role, me.Disabled));
    }

    [Fact]
    public async Task DisablingEndsTheMembersSessionAtOnceAndBlocksSignInUntilReEnabled()
    {
        var team = await TeamAsync("mem-e");
        using var owner = team.Owner;
        var (memberBrowser, memberId, memberEmail) = await SignedInMemberAsync(team.Tenant, CustomerRoles.Developer);
        using var member = memberBrowser;
        Assert.Equal(HttpStatusCode.OK, (await member.GetAsync(Home(team.Tenant.Identifier))).Status);

        var disabled = await owner.SubmitAsync(MembersPage(team.Tenant.Identifier), Act($"disable:{memberId}"));
        Assert.Contains("已停用", disabled.Html, StringComparison.Ordinal);

        // 已登入的 session 馬上失效；新的登入失敗，而且訊息與密碼錯一樣（不說「這個帳號被停用了」）。
        Assert.Equal(HttpStatusCode.Redirect, (await member.GetAsync(Home(team.Tenant.Identifier))).Status);
        using var fresh = new PortalBrowser(factory);
        var attempt = await fresh.SubmitAsync(Login(team.Tenant.Identifier), Credentials(memberEmail, NewPassword));
        Assert.Contains("電子郵件或密碼不正確", attempt.Html, StringComparison.Ordinal);
        Assert.Equal(HttpStatusCode.OK, attempt.Status);

        _ = await owner.SubmitAsync(MembersPage(team.Tenant.Identifier), Act($"enable:{memberId}"));
        var back = await fresh.SubmitAsync(Login(team.Tenant.Identifier), Credentials(memberEmail, NewPassword));
        Assert.Equal(Home(team.Tenant.Identifier), back.Location);

        Assert.Single(await EventsAsync(team.Tenant.Id, AuditActions.MemberDisabled));
        Assert.Single(await EventsAsync(team.Tenant.Id, AuditActions.MemberEnabled));
    }

    // 停用除了換安全戳記，session 驗證也直接看旗標：只改旗標（不換戳記）也要擋。
    [Fact]
    public async Task TheSessionCheckLooksAtTheDisabledFlagNotOnlyAtTheSecurityStamp()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "mem-f", CustomerRoles.Viewer);
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);
        Assert.Equal(HttpStatusCode.OK, (await browser.GetAsync(Home(tenant.Identifier))).Status);

        await using var connection = new NpgsqlConnection(factory.Services.GetRequiredService<IOptions<DatabaseOptions>>().Value.Platform);
        await connection.OpenAsync();
        await using var command = new NpgsqlCommand("""update platform."AspNetUsers" set "Disabled" = true where "Email" = @email""", connection);
        command.Parameters.AddWithValue("email", email);
        Assert.Equal(1, await command.ExecuteNonQueryAsync());

        Assert.Equal(HttpStatusCode.Redirect, (await browser.GetAsync(Home(tenant.Identifier))).Status);
    }

    [Fact]
    public async Task ChangingARoleEndsTheOldSessionAndTheNewRoleAppliesOnNextSignIn()
    {
        var team = await TeamAsync("mem-g");
        using var owner = team.Owner;
        var (memberBrowser, memberId, memberEmail) = await SignedInMemberAsync(team.Tenant, CustomerRoles.Viewer);
        using var member = memberBrowser;

        var changed = await owner.SubmitAsync(MembersPage(team.Tenant.Identifier), Act($"role:{memberId}", CustomerRoles.Developer, memberId));
        Assert.Contains("已變更角色", changed.Html, StringComparison.Ordinal);

        Assert.Equal(HttpStatusCode.Redirect, (await member.GetAsync(Home(team.Tenant.Identifier))).Status);
        using var fresh = new PortalBrowser(factory);
        _ = await fresh.SubmitAsync(Login(team.Tenant.Identifier), Credentials(memberEmail, NewPassword));
        Assert.Contains("開發者", (await fresh.GetAsync(Home(team.Tenant.Identifier))).Html, StringComparison.Ordinal);

        var evt = Assert.Single(await EventsAsync(team.Tenant.Id, AuditActions.MemberRoleChanged));
        Assert.Equal((CustomerRoles.Viewer, CustomerRoles.Developer),
            (evt.Detail.GetProperty("previous").GetString(), evt.Detail.GetProperty("current").GetString()));
    }

    [Fact]
    public async Task ResettingAPasswordShowsANewOneOnceEndsTheirSessionsAndForcesAChange()
    {
        var team = await TeamAsync("mem-h");
        using var owner = team.Owner;
        var (memberBrowser, memberId, memberEmail) = await SignedInMemberAsync(team.Tenant, CustomerRoles.Billing);
        using var member = memberBrowser;

        var reset = await owner.SubmitAsync(MembersPage(team.Tenant.Identifier), Act($"reset:{memberId}"));
        var newPassword = IssuedPassword().Match(reset.Html).Groups[1].Value;
        Assert.True(newPassword.Length >= 16);

        Assert.Equal(HttpStatusCode.Redirect, (await member.GetAsync(Home(team.Tenant.Identifier))).Status);
        using var fresh = new PortalBrowser(factory);
        var old = await fresh.SubmitAsync(Login(team.Tenant.Identifier), Credentials(memberEmail, NewPassword));
        Assert.Contains("電子郵件或密碼不正確", old.Html, StringComparison.Ordinal);
        var viaNew = await fresh.SubmitAsync(Login(team.Tenant.Identifier), Credentials(memberEmail, newPassword));
        Assert.Equal(PasswordPage(team.Tenant.Identifier), viaNew.Location);
        Assert.DoesNotContain(newPassword, (await owner.GetAsync(MembersPage(team.Tenant.Identifier))).Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task AnOwnerOfOneTenantCannotReachAnotherTenantsMembers()
    {
        var mine = await TeamAsync("mem-i1");
        using var owner = mine.Owner;
        var (otherTenant, _) = await factory.CreateTenantWithKeyAsync("mem-i2");
        var (otherId, _) = await AddMemberAsync(factory, otherTenant.Id, NewEmail(), CustomerRoles.Viewer);

        foreach (var action in new[] { $"disable:{otherId}", $"reset:{otherId}" })
        {
            var refused = await owner.SubmitAsync(MembersPage(mine.Tenant.Identifier), Act(action));
            Assert.Contains("找不到這位成員", refused.Html, StringComparison.Ordinal);
        }

        Assert.False((await Admin.ListAsync(otherTenant.Id, CancellationToken.None)).Value!.Single().Disabled);
    }

    [Theory]
    [InlineData(CustomerRoles.Developer)]
    [InlineData(CustomerRoles.Billing)]
    [InlineData(CustomerRoles.Viewer)]
    public async Task OtherRolesGetNoFormAndCannotAddByPosting(string role)
    {
        var team = await TeamAsync("mem-j");
        using var owner = team.Owner;
        var (browser, _, _) = await SignedInMemberAsync(team.Tenant, role);
        using var member = browser;

        var page = await member.GetAsync(MembersPage(team.Tenant.Identifier));
        Assert.Contains("只有擁有者與管理員能管理成員", page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("name=\"Add.Email\"", page.Html, StringComparison.Ordinal);

        var before = (await Admin.ListAsync(team.Tenant.Id, CancellationToken.None)).Value!.Count;
        _ = await member.SubmitAsync(MembersPage(team.Tenant.Identifier), Add(NewEmail(), CustomerRoles.Viewer));
        Assert.Equal(before, (await Admin.ListAsync(team.Tenant.Id, CancellationToken.None)).Value!.Count);
    }

    [Fact]
    public async Task ADuplicateEmailIsRefusedAndTheOperatorListShowsTheDisabledMember()
    {
        var team = await TeamAsync("mem-k");
        using var owner = team.Owner;
        var (_, memberId, memberEmail) = await SignedInMemberAsync(team.Tenant, CustomerRoles.Viewer);

        var duplicate = await owner.SubmitAsync(MembersPage(team.Tenant.Identifier), Add(memberEmail, CustomerRoles.Viewer));
        Assert.Contains("已經有這個 email 的成員", duplicate.Html, StringComparison.Ordinal);

        _ = await owner.SubmitAsync(MembersPage(team.Tenant.Identifier), Act($"disable:{memberId}"));
        using var operatorClient = factory.SignedInClient();
        var detail = await operatorClient.PageAsync($"/ui/tenants/{team.Tenant.Id}");
        Assert.Contains("已停用", detail, StringComparison.Ordinal);
    }

    // 管理員能管理開發者、帳務、唯讀，但不能把他們升到管理員或擁有者（否則管理員能自己造出另一位管理員，繞過階層）。
    [Theory]
    [InlineData(CustomerRoles.Admin)]
    [InlineData(CustomerRoles.Owner)]
    public async Task AnAdminCannotPromoteAnyoneToAnAdminOrOwner(string target)
    {
        var team = await TeamAsync("mem-l");
        using var owner = team.Owner;
        var (adminBrowser, _, _) = await SignedInMemberAsync(team.Tenant, CustomerRoles.Admin);
        using var admin = adminBrowser;
        var (_, viewerId, _) = await SignedInMemberAsync(team.Tenant, CustomerRoles.Viewer);

        var refused = await admin.SubmitAsync(MembersPage(team.Tenant.Identifier), Act($"role:{viewerId}", target, viewerId));

        Assert.Contains("不能指派這個角色", refused.Html, StringComparison.Ordinal);
        Assert.Equal(CustomerRoles.Viewer, (await Admin.ListAsync(team.Tenant.Id, CancellationToken.None)).Value!.Single(m => m.Id == viewerId).Role);
    }

    [Fact]
    public async Task ARoleThatDoesNotExistIsRefused()
    {
        var team = await TeamAsync("mem-m");
        using var owner = team.Owner;
        var (_, viewerId, _) = await SignedInMemberAsync(team.Tenant, CustomerRoles.Viewer);

        var refused = await owner.SubmitAsync(MembersPage(team.Tenant.Identifier), Act($"role:{viewerId}", "SuperAdmin", viewerId));

        Assert.Contains("角色不在允許的範圍內", refused.Html, StringComparison.Ordinal);
    }
}