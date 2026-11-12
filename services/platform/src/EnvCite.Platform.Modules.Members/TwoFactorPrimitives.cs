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
/// 驗證 TOTP（RFC 6238）：允許前後各一個時間步的時鐘誤差，且只接受比上一次成功更新的時間步，
/// 所以同一組碼不能用第二次。更新是原子的（<c>UPDATE … WHERE LastTotpStep &lt; @step</c>），
/// 同時送出同一組碼的兩個請求只有一個會成功。僅後台的營運者使用；客戶入口用 Identity 原生的驗證器 provider。
/// </summary>
internal sealed class TotpVerifier(PlatformDbContext db, TimeProvider time)
{
    public async Task<bool> VerifyAsync(UserManager<OperatorUser> users, OperatorUser user, string? code)
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

        var advanced = await db.Operators
            .Where(u => u.Id == user.Id && u.LastTotpStep < step)
            .ExecuteUpdateAsync(s => s.SetProperty(u => u.LastTotpStep, step));
        return advanced == 1;
    }

    /// <summary>六位數字；容許使用者從驗證器複製時帶的空白與連字號。</summary>
    internal static string? Normalize(string? code)
    {
        var digits = new string((code ?? string.Empty).Where(c => c is not (' ' or '-')).ToArray());
        return digits.Length == 6 && digits.All(char.IsAsciiDigit) ? digits : null;
    }
}