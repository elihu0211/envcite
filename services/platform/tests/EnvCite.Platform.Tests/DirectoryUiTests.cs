using System.Net;
using System.Text.RegularExpressions;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 目錄同步的介面：客戶入口（擁有者設定、簽發與撤銷 token、解除管理，一次性的 token 只顯示一次）與營運者的租戶頁
/// （看狀態、緊急關閉）。服務層的規則在 <see cref="DirectorySyncTests"/>，端點在 <see cref="ScimEndpointTests"/>；
/// 這裡驗頁面把規則如實呈現，而且只給該看的人。
/// </summary>
[Collection(PlatformTestGroup.Group4)]
public sealed partial class DirectoryUiTests(PlatformFactory factory) : DirectoryTestBase(factory)
{
    private const string SettingsForm = "portal-directory-settings";

    private const string TokenForm = "portal-directory-token";

    private const string ActionForm = "portal-directory-action";

    private static string DirectoryPage(string tenant) => $"/portal/{tenant}/directory";

    private static string MembersPage(string tenant) => $"/portal/{tenant}/members";

    [GeneratedRegex("""id="issued-token">([^<]+)</code>""", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    private static partial Regex IssuedToken();

    private async Task<(Org Org, PortalBrowser Owner)> OwnerSessionAsync(string prefix, bool enabled = true)
    {
        var org = await OrgAsync(prefix, enabled: enabled);
        var owner = new PortalBrowser(Factory);
        await SignInFullyAsync(owner, org.Tenant.Identifier, org.OwnerEmail, org.OwnerInitial);
        return (org, owner);
    }

    private async Task<PortalBrowser> SignedInAsync(Org org, string role)
    {
        var email = NewEmail();
        var (_, initial) = await AddMemberAsync(Factory, org.Tenant.Id, email, role);
        var browser = new PortalBrowser(Factory);
        await SignInFullyAsync(browser, org.Tenant.Identifier, email, initial);
        return browser;
    }

    private Task<AdminOutcome<DirectoryMember>> ProvisionAsync(Org org, DirectoryProvision member) =>
        InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().ProvisionAsync(org.Idp, member, Ct));

    private Task<AdminOutcome<DirectoryStatus>> StatusAsync(Org org) =>
        InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().GetStatusAsync(org.Owner, Ct));

    private async Task<HttpStatusCode> ScimStatusAsync(string token)
    {
        using var client = Factory.CreateClient();
        using var request = new HttpRequestMessage(HttpMethod.Get, "/scim/v2/Users");
        request.Headers.Authorization = new System.Net.Http.Headers.AuthenticationHeaderValue("Bearer", token);
        using var response = await client.SendAsync(request, Ct);
        return response.StatusCode;
    }

    [Fact]
    public async Task TheOwnerSeesTheScimUrlTheStatusTheTokensAndTheNavigationEntry()
    {
        var (org, owner) = await OwnerSessionAsync("dui-a");
        using var _ = owner;

        var page = await owner.GetAsync(DirectoryPage(org.Tenant.Identifier));
        var home = await owner.GetAsync(Home(org.Tenant.Identifier));

        Assert.Equal(HttpStatusCode.OK, page.Status);
        Assert.Contains("目錄同步", page.Html, StringComparison.Ordinal);
        Assert.Contains("https://localhost/scim/v2", page.Html, StringComparison.Ordinal); // 身分來源要填的網址
        Assert.Contains("等待身分來源連線", page.Html, StringComparison.Ordinal);
        Assert.True(DirectoryTokenFormatPrefix(org.Token, out var prefix));
        Assert.Contains(prefix, page.Html, StringComparison.Ordinal); // 清單只有前綴
        Assert.DoesNotContain(org.Token, page.Html, StringComparison.Ordinal);
        Assert.Contains($"value=\"revoke:{org.TokenId}\"", page.Html, StringComparison.Ordinal);
        Assert.Contains(DirectoryPage(org.Tenant.Identifier), home.Html, StringComparison.Ordinal); // 導覽列
    }

    private static bool DirectoryTokenFormatPrefix(string token, out string prefix) =>
        EnvCite.Platform.Modules.Members.DirectorySync.DirectoryTokenFormat.TryParse(token, out prefix, out _);

    [Fact]
    public async Task SavingTheSettingsChangesThemAndAnUncheckedBoxMeansOff()
    {
        var (org, owner) = await OwnerSessionAsync("dui-b");
        using var _ = owner;
        var path = DirectoryPage(org.Tenant.Identifier);

        var saved = await owner.SubmitAsync(path, new()
        {
            ["_handler"] = SettingsForm,
            ["Settings.Enabled"] = "true",
            ["Settings.DefaultRole"] = CustomerRoles.Developer,
            ["Settings.Adopt"] = "true",
        });

        Assert.Contains("已儲存設定。", saved.Html, StringComparison.Ordinal);
        var status = (await StatusAsync(org)).Value!.Settings;
        Assert.Equal(new DirectorySettings(true, CustomerRoles.Developer, true), status);
        // 重新載入：表單帶出目前的值，勾選框是勾著的。
        var again = await owner.GetAsync(path);
        Assert.Matches("""<input[^>]*id="settings-adopt"[^>]*checked""", again.Html);
        Assert.Matches("""<input[^>]*id="settings-enabled"[^>]*checked""", again.Html);

        // 瀏覽器不送沒勾的勾選框：全部關掉。
        await owner.SubmitAsync(path, new() { ["_handler"] = SettingsForm, ["Settings.DefaultRole"] = CustomerRoles.Viewer });
        Assert.Equal(new DirectorySettings(false, CustomerRoles.Viewer, false), (await StatusAsync(org)).Value!.Settings);
    }

    [Fact]
    public async Task ADefaultRoleAboveAdminIsRefusedWithAReadableReason()
    {
        var (org, owner) = await OwnerSessionAsync("dui-c");
        using var _ = owner;

        var refused = await owner.SubmitAsync(DirectoryPage(org.Tenant.Identifier),
            new() { ["_handler"] = SettingsForm, ["Settings.Enabled"] = "true", ["Settings.DefaultRole"] = CustomerRoles.Owner });

        Assert.Contains("目錄同步不能指派這個角色", refused.Html, StringComparison.Ordinal);
        Assert.Equal(CustomerRoles.Viewer, (await StatusAsync(org)).Value!.Settings.DefaultRole);
    }

    [Fact]
    public async Task AnIssuedTokenIsShownOnceWorksAgainstTheEndpointAndNeverAppearsAgain()
    {
        var (org, owner) = await OwnerSessionAsync("dui-d");
        using var _ = owner;
        var path = DirectoryPage(org.Tenant.Identifier);

        var issued = await owner.SubmitAsync(path, new() { ["_handler"] = TokenForm, ["Token.Name"] = "Okta", ["Token.ExpiresInDays"] = "30" });

        var token = IssuedToken().Match(issued.Html).Groups[1].Value;
        Assert.StartsWith("scim_", token, StringComparison.Ordinal);
        Assert.Contains("只會顯示這一次", issued.Html, StringComparison.Ordinal);
        Assert.Contains("data-copy-target=\"issued-token\"", issued.Html, StringComparison.Ordinal);
        Assert.Equal(HttpStatusCode.OK, await ScimStatusAsync(token)); // 真的能用

        var again = await owner.GetAsync(path);
        Assert.DoesNotContain(token, again.Html, StringComparison.Ordinal);
        Assert.Contains("Okta", again.Html, StringComparison.Ordinal);

        var tokens = (await StatusAsync(org)).Value!.Tokens;
        Assert.Equal(2, tokens.Count);
        Assert.NotNull(tokens.Single(t => t.Name == "Okta").ExpiresAt);
        var audit = System.Text.Json.JsonSerializer.Serialize(await AuditAsync(org, AuditActions.DirectoryTokenIssued));
        Assert.DoesNotContain(token, audit, StringComparison.Ordinal);
    }

    [Fact]
    public async Task RevokingATokenFromThePageStopsItImmediately()
    {
        var (org, owner) = await OwnerSessionAsync("dui-e");
        using var _ = owner;
        Assert.Equal(HttpStatusCode.OK, await ScimStatusAsync(org.Token));

        var revoked = await owner.SubmitAsync(DirectoryPage(org.Tenant.Identifier),
            new() { ["_handler"] = ActionForm, ["Input.Action"] = $"revoke:{org.TokenId}" });

        Assert.Contains("已撤銷這把 token", revoked.Html, StringComparison.Ordinal);
        Assert.Equal(HttpStatusCode.Unauthorized, await ScimStatusAsync(org.Token));
        Assert.DoesNotContain($"value=\"revoke:{org.TokenId}\"", revoked.Html, StringComparison.Ordinal); // 已撤銷的沒有撤銷鈕

        var bogus = await owner.SubmitAsync(DirectoryPage(org.Tenant.Identifier), new() { ["_handler"] = ActionForm, ["Input.Action"] = "revoke:not-a-guid" });
        Assert.Contains("不認得這個動作", bogus.Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task OnlyTheOwnerGetsThePageTheNavigationAndTheForms()
    {
        var (org, owner) = await OwnerSessionAsync("dui-f");
        using var _ = owner;
        using var admin = await SignedInAsync(org, CustomerRoles.Admin);
        var path = DirectoryPage(org.Tenant.Identifier);

        var page = await admin.GetAsync(path);
        var home = await admin.GetAsync(Home(org.Tenant.Identifier));
        var attempt = await admin.SubmitAsync(path, new() { ["_handler"] = TokenForm, ["Token.Name"] = "sneaky", ["Token.ExpiresInDays"] = "0" });

        Assert.Contains("只有擁有者能管理目錄同步", page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain(SettingsForm, page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain(path, home.Html, StringComparison.Ordinal); // 導覽列也沒有
        Assert.DoesNotContain("id=\"issued-token\"", attempt.Html, StringComparison.Ordinal);
        Assert.Single((await StatusAsync(org)).Value!.Tokens); // 沒有多出 token
    }

    [Fact]
    public async Task ManagedMembersAreLockedOnTheMembersPageAndOnlyTheOwnerCanRelease()
    {
        await Factory.WithMail(async () =>
        {
            var (org, owner) = await OwnerSessionAsync("dui-g");
            using var _ = owner;
            using var admin = await SignedInAsync(org, CustomerRoles.Admin);
            var member = (await ProvisionAsync(org, Person(role: CustomerRoles.Developer))).Value!;
            var path = MembersPage(org.Tenant.Identifier);

            var ownerView = await owner.GetAsync(path);
            var adminView = await admin.GetAsync(path);

            Assert.Contains("由目錄同步管理", ownerView.Html, StringComparison.Ordinal);
            Assert.Contains($"value=\"release:{member.Id}\"", ownerView.Html, StringComparison.Ordinal);
            Assert.DoesNotContain($"value=\"disable:{member.Id}\"", ownerView.Html, StringComparison.Ordinal);
            Assert.DoesNotContain($"value=\"role:{member.Id}\"", ownerView.Html, StringComparison.Ordinal);
            Assert.DoesNotContain($"name=\"role-{member.Id}\"", ownerView.Html, StringComparison.Ordinal);
            Assert.Contains("由目錄同步管理", adminView.Html, StringComparison.Ordinal);
            Assert.DoesNotContain($"value=\"release:{member.Id}\"", adminView.Html, StringComparison.Ordinal);

            // 管理員直接送出解除也不行（服務層擋）。
            var forged = await admin.SubmitAsync(path, new() { ["_handler"] = "portal-member-action", ["Input.Action"] = $"release:{member.Id}" });
            Assert.Contains("只有擁有者能管理目錄同步", forged.Html, StringComparison.Ordinal);
            Assert.True((await UserAsync(org, member.Id)).ManagedBy == DirectoryRoles.ManagedBy);

            var released = await owner.SubmitAsync(path, new() { ["_handler"] = "portal-member-action", ["Input.Action"] = $"release:{member.Id}" });
            Assert.Contains("已解除目錄同步的管理", released.Html, StringComparison.Ordinal);
            Assert.DoesNotContain($"value=\"release:{member.Id}\"", released.Html, StringComparison.Ordinal);
            Assert.Contains($"value=\"disable:{member.Id}\"", released.Html, StringComparison.Ordinal); // 回到人手上
            Assert.Contains($"name=\"role-{member.Id}\"", released.Html, StringComparison.Ordinal);
        });
    }

    [Fact]
    public async Task TheEntryPointsAreAbsentWhenTheDeploymentHasNotEnabledDirectorySync()
    {
        var (org, owner) = await OwnerSessionAsync("dui-h");
        using var _ = owner;
        using var off = Factory.WithWebHostBuilder(builder => builder.UseSetting("Platform:Directory:Enabled", "false"));
        using var browser = new PortalBrowser(off);
        var signedIn = await browser.SubmitAsync(Login(org.Tenant.Identifier), Credentials(org.OwnerEmail, NewPassword));
        Assert.Equal(Home(org.Tenant.Identifier), signedIn.Location);

        var page = await browser.GetAsync(DirectoryPage(org.Tenant.Identifier));
        var home = await browser.GetAsync(Home(org.Tenant.Identifier));

        Assert.Contains("這個環境沒有開啟目錄同步", page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain(SettingsForm, page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain(DirectoryPage(org.Tenant.Identifier), home.Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task TheOperatorsTenantPageShowsTheStatusTheTokensAndManagedMembersWithoutEverShowingASecret()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("dui-i");
            var member = (await ProvisionAsync(org, Person())).Value!;
            using var client = Factory.SignedInClient();

            var html = await client.PageAsync($"/ui/tenants/{org.Tenant.Id}");

            Assert.Contains("目錄同步", html, StringComparison.Ordinal);
            Assert.Contains("受管成員", html, StringComparison.Ordinal);
            Assert.Contains("/scim/v2", html, StringComparison.Ordinal);
            Assert.True(DirectoryTokenFormatPrefix(org.Token, out var prefix));
            Assert.Contains(prefix, html, StringComparison.Ordinal);
            Assert.DoesNotContain(org.Token, html, StringComparison.Ordinal);
            Assert.Contains("關閉目錄同步", html, StringComparison.Ordinal); // 緊急關閉
            Assert.Contains("由目錄同步管理", html, StringComparison.Ordinal);
            Assert.Contains($"aria-label=\"解除 {member.Email} 的目錄同步管理\"", html, StringComparison.Ordinal);
            Assert.DoesNotContain($"aria-label=\"停用 {member.Email}\"", html, StringComparison.Ordinal); // 停用由身分來源決定
        });
    }

    [Fact]
    public async Task TheOperatorsTenantPageHasNoDirectoryCardWhenTheDeploymentHasNotEnabledIt()
    {
        var org = await OrgAsync("dui-j");
        using var off = Factory.WithWebHostBuilder(builder => builder.UseSetting("Platform:Directory:Enabled", "false"));
        using var client = off.SignedInClient();

        var html = await client.PageAsync($"/ui/tenants/{org.Tenant.Id}");

        Assert.DoesNotContain("受管成員", html, StringComparison.Ordinal);
        Assert.DoesNotContain("/scim/v2", html, StringComparison.Ordinal);
        Assert.DoesNotContain("關閉目錄同步", html, StringComparison.Ordinal);
        Assert.Contains(org.Tenant.Identifier, html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task TheOwnerMapsAGroupToARoleOnThePageAndItsMembersFollow()
    {
        await Factory.WithMail(async () =>
        {
            var (org, owner) = await OwnerSessionAsync("dui-k");
            using var _ = owner;
            var member = (await ProvisionAsync(org, Person())).Value!;
            var group = (await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>()
                .CreateGroupAsync(org.Idp, new DirectoryGroupInput("Engineering", "g-eng", [member.Id]), Ct))).Value!;
            var path = DirectoryPage(org.Tenant.Identifier);

            var page = await owner.GetAsync(path);
            Assert.Contains("Engineering", page.Html, StringComparison.Ordinal);
            Assert.Contains($"name=\"group-role-{group.Id}\"", page.Html, StringComparison.Ordinal);
            Assert.Contains($"value=\"group:{group.Id}\"", page.Html, StringComparison.Ordinal);

            var mapped = await owner.SubmitAsync(path, new()
            {
                ["_handler"] = ActionForm,
                ["Input.Action"] = $"group:{group.Id}",
                [$"group-role-{group.Id}"] = CustomerRoles.Admin,
            });
            Assert.Contains("已儲存群組對映的角色", mapped.Html, StringComparison.Ordinal);
            Assert.Equal(CustomerRoles.Admin, (await UserAsync(org, member.Id)).Role);

            // 選「不對映」（空值）：成員回到預設角色。
            await owner.SubmitAsync(path, new() { ["_handler"] = ActionForm, ["Input.Action"] = $"group:{group.Id}", [$"group-role-{group.Id}"] = string.Empty });
            Assert.Equal(CustomerRoles.Viewer, (await UserAsync(org, member.Id)).Role);

            // 擁有者不是能對映的角色：被拒絕，原因看得懂。
            var refused = await owner.SubmitAsync(path, new()
            {
                ["_handler"] = ActionForm,
                ["Input.Action"] = $"group:{group.Id}",
                [$"group-role-{group.Id}"] = CustomerRoles.Owner,
            });
            Assert.Contains("目錄同步不能指派這個角色", refused.Html, StringComparison.Ordinal);
        });
    }

    [Fact]
    public async Task TheOperatorSeesTheGroupsAndTheirRolesButCannotChangeThem()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("dui-l");
            var group = (await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>()
                .CreateGroupAsync(org.Idp, new DirectoryGroupInput("Engineering", "g-eng"), Ct))).Value!;
            Assert.True((await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().SetGroupRoleAsync(org.Owner, group.Id, CustomerRoles.Developer, Ct))).IsOk);
            using var client = Factory.SignedInClient();

            var html = await client.PageAsync($"/ui/tenants/{org.Tenant.Id}");

            Assert.Contains("Engineering", html, StringComparison.Ordinal);
            Assert.Contains("開發者", html, StringComparison.Ordinal);
            Assert.DoesNotContain("group-role-", html, StringComparison.Ordinal); // 營運者只看，對映由客戶的擁有者決定
        });
    }
}