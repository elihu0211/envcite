using System.Net;

using Microsoft.AspNetCore.Components.Authorization;
using Microsoft.AspNetCore.Identity;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 授權每個請求向資料庫重查的每一項，各自單獨改資料庫、<b>不動</b>安全戳記（停用與重設會順手換戳記，把別的檢查蓋住——
/// 拿掉某一項檢查，端對端的停用測試照樣通過）。每個測試用自己的營運者，不影響別的測試。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed class OperatorSessionChecksTests(PlatformFactory factory)
{
    private async Task<(HttpClient Client, string Id)> SessionAsync()
    {
        var user = await factory.EnsureOperatorAsync(OperatorTestSupport.NewOperatorEmail());
        return (factory.ClientFor(factory.SessionOf(user)), user.Id.ToString());
    }

    private async Task EditAsync(string id, Action<OperatorUser> change)
    {
        await using var scope = factory.Services.CreateAsyncScope();
        var users = scope.ServiceProvider.GetRequiredService<UserManager<OperatorUser>>();
        var user = (await users.FindByIdAsync(id))!;
        var stamp = user.SecurityStamp;
        change(user);
        Assert.True((await users.UpdateAsync(user)).Succeeded);
        Assert.Equal(stamp, (await users.FindByIdAsync(id))!.SecurityStamp); // 戳記沒動
    }

    private static async Task<HttpStatusCode> StatusAsync(HttpClient client, string path)
    {
        using var response = await client.GetAsync(new Uri(path, UriKind.Relative));
        return response.StatusCode;
    }

    [Fact]
    public async Task ASessionOfAnEnabledEnrolledOperatorWithAMatchingStampIsLetIn()
    {
        var (client, _) = await SessionAsync();
        using var _ = client;

        Assert.Equal(HttpStatusCode.OK, await StatusAsync(client, "/ui"));
    }

    [Fact]
    public async Task TheDisabledFlagAloneEndsTheSession()
    {
        var (client, id) = await SessionAsync();
        using var _ = client;

        await EditAsync(id, u => u.Disabled = true);

        Assert.Equal(HttpStatusCode.Redirect, await StatusAsync(client, "/ui"));
    }

    [Fact]
    public async Task AnAuthenticatorThatIsGoneAloneEndsTheSession()
    {
        var (client, id) = await SessionAsync();
        using var _ = client;

        await EditAsync(id, u => u.TwoFactorEnabled = false);

        Assert.Equal(HttpStatusCode.Redirect, await StatusAsync(client, "/ui"));
        Assert.Equal(HttpStatusCode.Redirect, await StatusAsync(client, "/ui/tenants"));
    }

    [Fact]
    public async Task AnIssuedPasswordThatIsBackAloneEndsTheSession()
    {
        var (client, id) = await SessionAsync();
        using var _ = client;

        await EditAsync(id, u => u.MustChangePassword = true);

        Assert.Equal(HttpStatusCode.Redirect, await StatusAsync(client, "/ui"));
        Assert.Equal(HttpStatusCode.OK, await StatusAsync(client, "/ui/password")); // 改密碼頁本來就開
    }

    [Fact]
    public async Task ADifferentSecurityStampAloneEndsTheSession()
    {
        var (client, id) = await SessionAsync();
        using var _ = client;
        await using var scope = factory.Services.CreateAsyncScope();
        var users = scope.ServiceProvider.GetRequiredService<UserManager<OperatorUser>>();

        Assert.True((await users.UpdateSecurityStampAsync((await users.FindByIdAsync(id))!)).Succeeded);

        Assert.Equal(HttpStatusCode.Redirect, await StatusAsync(client, "/ui"));
    }

    [Fact]
    public async Task ASessionWithNoStampAtAllIsNotAccepted()
    {
        var user = await factory.EnsureOperatorAsync(OperatorTestSupport.NewOperatorEmail());
        var session = factory.SessionOf(user);
        var identity = (System.Security.Claims.ClaimsIdentity)session.Identity!;
        identity.RemoveClaim(identity.FindFirst("AspNet.Identity.SecurityStamp"));
        using var client = factory.ClientFor(session);

        Assert.Equal(HttpStatusCode.Redirect, await StatusAsync(client, "/ui"));
    }

    // Blazor 的 circuit 是長壽命連線，點擊不是 HTTP 請求：由重驗的 AuthenticationStateProvider 把失效的帳號換成匿名。
    [Fact]
    public async Task AnOpenCircuitIsRevalidatedAndEndsWhenTheAccountIsDisabled()
    {
        var user = await factory.EnsureOperatorAsync(OperatorTestSupport.NewOperatorEmail());
        var session = factory.SessionOf(user);
        await using var scope = factory.Services.CreateAsyncScope();
        var provider = Assert.IsType<Web.Auth.OperatorAuthenticationStateProvider>(scope.ServiceProvider.GetRequiredService<AuthenticationStateProvider>());

        Assert.True(await provider.IsStillValidAsync(session, CancellationToken.None));
        Assert.True(await provider.IsStillValidAsync(new System.Security.Claims.ClaimsPrincipal(new System.Security.Claims.ClaimsIdentity()), CancellationToken.None)); // 匿名沒有東西可重驗

        await EditAsync(user.Id.ToString(), u => u.Disabled = true);

        Assert.False(await provider.IsStillValidAsync(session, CancellationToken.None));
    }

    // 不是 /ui 的 cookie 簽發的 principal（沒有營運者 Id），就算 email 對得上一位營運者也不行。
    [Fact]
    public async Task AnEmailAloneIdentifiesNobody()
    {
        var user = await factory.EnsureOperatorAsync(OperatorTestSupport.NewOperatorEmail());
        using var client = factory.ClientFor(new System.Security.Claims.ClaimsPrincipal(new System.Security.Claims.ClaimsIdentity(
            [new System.Security.Claims.Claim(System.Security.Claims.ClaimTypes.Email, user.Email!)], "legacy")));

        Assert.Equal(HttpStatusCode.Redirect, await StatusAsync(client, "/ui"));
    }
}