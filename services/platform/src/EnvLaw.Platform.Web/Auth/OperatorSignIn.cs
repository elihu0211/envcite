using System.Security.Claims;

using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Authentication;
using Microsoft.Extensions.DependencyInjection;

namespace EnvLaw.Platform.Web.Auth;

/// <summary>
/// Google 登入完成、簽發 cookie 之前的檢查與稽核。不在白名單就不簽發 cookie，並記下被拒絕的嘗試。
/// 稽核寫入失敗會讓登入失敗（fail closed）：營運者只有少數幾位，寧可登不進去也不要有一次沒留紀錄的登入。
/// </summary>
internal static class OperatorSignIn
{
    public static async Task HandleAsync(TicketReceivedContext context)
    {
        var services = context.HttpContext.RequestServices;
        var allowlist = services.GetRequiredService<OperatorAllowlist>();
        var audit = services.GetRequiredService<IAuditLog>();
        var ct = context.HttpContext.RequestAborted;

        var principal = context.Principal;
        var email = principal?.FindFirst(ClaimTypes.Email)?.Value;

        if (principal is null || !allowlist.IsAllowed(principal))
        {
            // 被拒絕的人不是營運者：以匿名身分記錄，嘗試登入的 email 放在事件裡。
            await audit.AppendAsync(
                new ClaimsPrincipal(),
                new AuditEntry(AuditActions.OperatorSignInDenied, TargetType: "operator", TargetId: email, Detail: new Dictionary<string, object?>
                {
                    ["email"] = email,
                    ["emailVerified"] = principal?.FindFirst(OperatorAllowlist.EmailVerifiedClaim)?.Value,
                }),
                ct);
            context.HandleResponse();
            context.Response.Redirect(UiEndpoints.DeniedPath);
            return;
        }

        await audit.AppendAsync(principal, new AuditEntry(AuditActions.OperatorSignedIn, TargetType: "operator", TargetId: email), ct);
    }
}