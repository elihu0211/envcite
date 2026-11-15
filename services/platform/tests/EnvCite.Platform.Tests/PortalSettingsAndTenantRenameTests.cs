using System.Net;

namespace EnvCite.Platform.Tests;

[Collection(PlatformTestGroup.Group1)]
public sealed class PortalSettingsAndTenantRenameTests(PlatformFactory factory)
{
    private const string ProfileForm = "portal-profile-name";
    private const string RenameForm = "portal-tenant-rename";

    private static string SettingsPath(string tenant) => $"/portal/{tenant}/settings";

    private static string AccountPath(string tenant) => $"/portal/{tenant}/account";

    [Fact]
    public async Task SettingsPageRendersGoogleStyleGroupedSections()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "set-ui", CustomerRoles.Viewer, "Alice Lin");
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var page = await browser.GetAsync(SettingsPath(tenant.Identifier));

        Assert.Equal(HttpStatusCode.OK, page.Status);
        Assert.Contains("id=\"profile-title\"", page.Html, StringComparison.Ordinal);
        Assert.Contains("id=\"preferences-title\"", page.Html, StringComparison.Ordinal);
        Assert.Contains("id=\"security-title\"", page.Html, StringComparison.Ordinal);
        Assert.Contains("Alice Lin", page.Html, StringComparison.Ordinal);
        Assert.Contains(email, page.Html, StringComparison.Ordinal);
        Assert.Contains($"{Home(tenant.Identifier)}/language", page.Html, StringComparison.Ordinal);
        Assert.Contains($"{Home(tenant.Identifier)}/security", page.Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task MemberCanUpdateAndClearOwnDisplayNameAndCookieRefreshesImmediately()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "set-name", CustomerRoles.Developer, "Old Name");
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var settings = SettingsPath(tenant.Identifier);
        var saved = await browser.SubmitAsync(
            settings,
            new() { ["_handler"] = ProfileForm, ["ProfileInput.DisplayName"] = "  New Display Name  " });

        Assert.Equal(HttpStatusCode.Redirect, saved.Status);
        Assert.Equal($"{settings}?saved=1", saved.Location);

        var after = await browser.GetAsync(saved.Location!);
        Assert.Contains("已更新顯示名稱。", after.Html, StringComparison.Ordinal);
        Assert.Contains("New Display Name", after.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("Old Name", after.Html, StringComparison.Ordinal);

        // 清除顯示名稱（留空送出）後退回「尚未設定」。
        var cleared = await browser.SubmitAsync(
            settings,
            new() { ["_handler"] = ProfileForm, ["ProfileInput.DisplayName"] = "   " });

        Assert.Equal(HttpStatusCode.Redirect, cleared.Status);
        var afterClear = await browser.GetAsync(cleared.Location!);
        Assert.Contains("尚未設定", afterClear.Html, StringComparison.Ordinal);

        // 超過 200 字元會被擋下並顯示錯誤。
        var tooLong = await browser.SubmitAsync(
            settings,
            new() { ["_handler"] = ProfileForm, ["ProfileInput.DisplayName"] = new string('a', 201) });
        Assert.Equal(HttpStatusCode.OK, tooLong.Status);
        Assert.Contains("role=\"alert\"", tooLong.Html, StringComparison.Ordinal);

        // 確認稽核有寫入 portal.profile_updated。
        var audit = factory.Services.GetRequiredService<IAuditQuery>();
        var events = await audit.QueryAsync(
            new AuditFilter(Tenant: tenant.Id, Action: AuditActions.PortalProfileUpdated),
            TestContext.Current.CancellationToken);
        Assert.NotEmpty(events.Items);
    }

    [Fact]
    public async Task ManagedMemberCannotEditDisplayNameInSettings()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "set-scim", CustomerRoles.Developer, "Synced User");
        var scopes = factory.Services.GetRequiredService<TenantScopes>();
        string userId = string.Empty;

        await scopes.InAsync(tenant.Id, async scope =>
        {
            await using var db = scope.CreateDb();
            var user = await db.Users.SingleAsync(u => u.Email == email, TestContext.Current.CancellationToken);
            user.ManagedBy = DirectoryRoles.ManagedBy;
            userId = user.Id;
            await db.SaveChangesAsync(TestContext.Current.CancellationToken);
            return AdminOutcome.Ok(true);
        });

        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var page = await browser.GetAsync(SettingsPath(tenant.Identifier));
        Assert.Contains("由組織目錄同步管理，無法在此修改。", page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain(ProfileForm, page.Html, StringComparison.Ordinal);

        // 直接從服務層呼叫也會被拒（Conflict / directory.member_managed）。
        var principal = await PortalTestSupport.MemberPrincipalAsync(factory, tenant.Id, userId);
        var outcome = await scopes.InAsync(tenant.Id, scope =>
            scope.Get<IPortalProfile>().UpdateDisplayNameAsync(principal, "Hacked", TestContext.Current.CancellationToken));
        Assert.Equal(AdminStatus.Conflict, outcome.Status);
        Assert.Equal(OutcomeCodes.DirectoryMemberManaged, outcome.Code);
    }

    [Fact]
    public async Task OwnerCanRenameOrganizationInAccountOverviewAndNonOwnerIsForbidden()
    {
        var (tenant, ownerEmail, ownerInitial) = await MemberAsync(factory, "org-ren", CustomerRoles.Owner, "Owner");
        var adminEmail = NewEmail();
        var (adminId, adminInitial) = await AddMemberAsync(factory, tenant.Id, adminEmail, CustomerRoles.Admin, "Admin");

        // Owner 可以從帳號與組織頁重新命名，頂欄與頁面立即更新並寫稽核。
        using (var owner = new PortalBrowser(factory))
        {
            await SignInFullyAsync(owner, tenant.Identifier, ownerEmail, ownerInitial);
            var accountPath = AccountPath(tenant.Identifier);
            var page = await owner.GetAsync(accountPath);
            Assert.Contains("重新命名", page.Html, StringComparison.Ordinal);

            var renamed = await owner.SubmitAsync(
                accountPath,
                new() { ["_handler"] = RenameForm, ["RenameInput.Name"] = "  Acme Legal Corp  " });
            Assert.Equal(HttpStatusCode.Redirect, renamed.Status);
            Assert.Equal($"{accountPath}?renamed=1", renamed.Location);

            var after = await owner.GetAsync(renamed.Location!);
            Assert.Contains("已更新組織名稱。", after.Html, StringComparison.Ordinal);
            Assert.Contains("Acme Legal Corp", after.Html, StringComparison.Ordinal);

            // 空白名稱會被擋下。
            var blank = await owner.SubmitAsync(
                accountPath,
                new() { ["_handler"] = RenameForm, ["RenameInput.Name"] = "   " });
            Assert.Equal(HttpStatusCode.OK, blank.Status);
            Assert.Contains("名稱不可空白。", blank.Html, StringComparison.Ordinal);
        }

        // 非 Owner（例如 Admin）看不到重新命名按鈕，服務層也拒絕。
        using (var admin = new PortalBrowser(factory))
        {
            await SignInFullyAsync(admin, tenant.Identifier, adminEmail, adminInitial);
            var page = await admin.GetAsync(AccountPath(tenant.Identifier));
            Assert.DoesNotContain(RenameForm, page.Html, StringComparison.Ordinal);
        }

        var scopes = factory.Services.GetRequiredService<TenantScopes>();
        var adminPrincipal = await PortalTestSupport.MemberPrincipalAsync(factory, tenant.Id, adminId);
        var forbidden = await scopes.InAsync(tenant.Id, scope =>
            scope.Get<ITenantSelfService>().RenameAsync(adminPrincipal, "Should Fail", TestContext.Current.CancellationToken));
        Assert.Equal(AdminStatus.Forbidden, forbidden.Status);
        Assert.Equal(OutcomeCodes.TenantRenameForbidden, forbidden.Code);

        var audit = factory.Services.GetRequiredService<IAuditQuery>();
        var events = await audit.QueryAsync(
            new AuditFilter(Tenant: tenant.Id, Action: AuditActions.TenantRenamed),
            TestContext.Current.CancellationToken);
        Assert.Single(events.Items);
    }
}