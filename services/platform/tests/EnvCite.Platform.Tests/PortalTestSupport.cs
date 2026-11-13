using EnvCite.Platform.Modules.Tenancy;

namespace EnvCite.Platform.Tests;

/// <summary>客戶入口測試共用：路徑、表單欄位、建立成員。</summary>
internal static class PortalTestSupport
{
    public const string NewPassword = "correct horse battery staple";

    public static string Login(string tenant) => $"/portal/{tenant}/login";

    public static string Home(string tenant) => $"/portal/{tenant}";

    public static string PasswordPage(string tenant) => $"/portal/{tenant}/password";

    public static string AccountPage(string tenant) => $"/portal/{tenant}/account";

    public static Dictionary<string, string> Credentials(string email, string password) =>
        new() { ["Input.Email"] = email, ["Input.Password"] = password };

    public static Dictionary<string, string> PasswordChange(string current, string next, string? confirm = null) =>
        new() { ["Input.Current"] = current, ["Input.New"] = next, ["Input.Confirm"] = confirm ?? next };

    public static string NewEmail() => $"member-{Guid.NewGuid():N}"[..20] + "@example.com";

    /// <summary>在既有租戶建立成員，回傳營運者經手的初始密碼。</summary>
    public static async Task<(string Id, string Password)> AddMemberAsync(
        PlatformFactory factory, string tenantId, string email, string role = CustomerRoles.Owner, string? name = "王小明")
    {
        var created = await factory.Services.GetRequiredService<IMemberAdmin>()
            .CreateAsync(OperatorSession.Principal(), tenantId, email, name, role, CancellationToken.None);
        Assert.True(created.IsOk, created.Message);
        return (created.Value!.Member.Id, created.Value.Password);
    }

    /// <summary>建立租戶與一位成員。</summary>
    public static async Task<(TenantResponse Tenant, string Email, string Initial)> MemberAsync(
        PlatformFactory factory, string prefix, string role = CustomerRoles.Owner, string? name = "王小明")
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync(prefix);
        var email = NewEmail();
        var (_, password) = await AddMemberAsync(factory, tenant.Id, email, role, name);
        return (tenant, email, password);
    }

    /// <summary>登入並改掉初始密碼，回到「可以正常使用」的狀態；回傳新密碼。</summary>
    public static async Task<string> SignInFullyAsync(PortalBrowser browser, string tenantIdentifier, string email, string initial)
    {
        var signedIn = await browser.SubmitAsync(Login(tenantIdentifier), Credentials(email, initial));
        Assert.Equal(PasswordPage(tenantIdentifier), signedIn.Location);
        var changed = await browser.SubmitAsync(PasswordPage(tenantIdentifier), PasswordChange(initial, NewPassword));
        Assert.Equal(Home(tenantIdentifier), changed.Location);
        return NewPassword;
    }
}