using System.Security.Claims;

using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Identity;
using Microsoft.EntityFrameworkCore;

using OtpNet;

namespace EnvCite.Platform.Modules.Members;

/// <summary>
/// 「密碼已通過、等第二步」的暫存憑證：一張只存使用者 id 與租戶、五分鐘內有效的 cookie，
/// <b>不是</b>登入 cookie（不同 scheme，入口的授權不認它），所以拿到它什麼頁面都進不去，只能到輸入驗證碼的那一頁。
/// </summary>
internal sealed class MfaPendingCookie(IHttpContextAccessor http, IMultiTenantContextAccessor<TenantInfo> tenant)
{
    public const string Scheme = "PortalMfaPending";

    public static readonly TimeSpan Lifetime = TimeSpan.FromMinutes(5);

    public Task IssueAsync(ApplicationUser user)
    {
        var principal = new ClaimsPrincipal(new ClaimsIdentity(
            [
                new Claim(PlatformClaimTypes.PortalUserId, user.Id),
                new Claim(PlatformClaimTypes.TenantIdentifier, CurrentTenant()),
            ],
            Scheme));
        return Context().SignInAsync(Scheme, principal, new AuthenticationProperties { IsPersistent = false });
    }

    /// <summary>暫存憑證裡的使用者 id；沒有、過期，或是別的租戶簽發的則 null。</summary>
    public async Task<string?> ReadAsync()
    {
        var result = await Context().AuthenticateAsync(Scheme);
        if (!result.Succeeded
            || !string.Equals(result.Principal.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value, tenant.MultiTenantContext?.TenantInfo?.Identifier, StringComparison.Ordinal))
        {
            return null;
        }

        return result.Principal.FindFirst(PlatformClaimTypes.PortalUserId)?.Value;
    }

    public Task ClearAsync() => Context().SignOutAsync(Scheme);

    private HttpContext Context() => http.HttpContext ?? throw new InvalidOperationException("No HTTP request.");

    private string CurrentTenant() =>
        tenant.MultiTenantContext?.TenantInfo?.Identifier ?? throw new InvalidOperationException("No tenant resolved for the portal request.");
}

/// <summary>
/// 驗證 TOTP（RFC 6238）：允許前後各一個時間步的時鐘誤差，且只接受比上一次成功更新的時間步，
/// 所以同一組碼不能用第二次。更新是原子的（<c>UPDATE … WHERE LastTotpStep &lt; @step</c>），
/// 同時送出同一組碼的兩個請求只有一個會成功。客戶入口的成員與後台的營運者共用，各有自己的使用者表與更新語句。
/// </summary>
internal sealed class TotpVerifier(PlatformDbContext db, TimeProvider time)
{
    public Task<bool> VerifyAsync(UserManager<ApplicationUser> users, ApplicationUser user, string? code) =>
        VerifyAsync(users, user, code, step => db.Users
            .Where(u => u.Id == user.Id && u.LastTotpStep < step)
            .ExecuteUpdateAsync(s => s.SetProperty(u => u.LastTotpStep, step)));

    public Task<bool> VerifyAsync(UserManager<OperatorUser> users, OperatorUser user, string? code) =>
        VerifyAsync(users, user, code, step => db.Operators
            .Where(u => u.Id == user.Id && u.LastTotpStep < step)
            .ExecuteUpdateAsync(s => s.SetProperty(u => u.LastTotpStep, step)));

    private async Task<bool> VerifyAsync<TUser>(UserManager<TUser> users, TUser user, string? code, Func<long, Task<int>> advanceStep)
        where TUser : class
    {
        var digits = Normalize(code);
        if (digits is null || await users.GetAuthenticatorKeyAsync(user) is not { } key)
        {
            return false;
        }

        var totp = new Totp(Base32Encoding.ToBytes(key));
        // 時間來自 TimeProvider（測試可以撥動它，才測得到「下一個時間步」）。
        if (!totp.VerifyTotp(time.GetUtcNow().UtcDateTime, digits, out var step, VerificationWindow.RfcSpecifiedNetworkDelay))
        {
            return false;
        }

        return await advanceStep(step) == 1;
    }

    /// <summary>六位數字；容許使用者從驗證器複製時帶的空白與連字號。</summary>
    internal static string? Normalize(string? code)
    {
        var digits = new string((code ?? string.Empty).Where(c => c is not (' ' or '-')).ToArray());
        return digits.Length == 6 && digits.All(char.IsAsciiDigit) ? digits : null;
    }
}