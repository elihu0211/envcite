using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Authorization;

namespace EnvLaw.Platform.Web.Auth;

/// <param name="AllowPasswordChangePending">允許還沒改掉營運者經手的初始密碼（改密碼頁用）。</param>
/// <param name="AllowEnrollmentPending">允許還沒註冊兩步驟驗證（註冊頁用）。</param>
public sealed record OperatorRequirement(bool AllowPasswordChangePending, bool AllowEnrollmentPending) : IAuthorizationRequirement;

/// <summary>
/// 每個請求都向資料庫重查這個 session 的營運者：帳號還在、沒被停用、session 的安全戳記仍是目前的，
/// 而且（依 policy）已改過初始密碼、已註冊兩步驟驗證。所以停用、刪除、重設密碼或兩步驟驗證，下一個請求就生效，
/// 不必等 cookie 過期。是否待改密碼、待註冊看資料庫，不看 cookie 裡的 claim（claim 只用來決定被拒時導去哪一頁）。
/// </summary>
public sealed class OperatorHandler(IOperatorSessions sessions) : AuthorizationHandler<OperatorRequirement>
{
    protected override async Task HandleRequirementAsync(AuthorizationHandlerContext context, OperatorRequirement requirement)
    {
        if (await sessions.FindAsync(context.User, CancellationToken.None) is { } session
            && (requirement.AllowPasswordChangePending || !session.MustChangePassword)
            && (requirement.AllowEnrollmentPending || session.TwoFactorEnabled))
        {
            context.Succeed(requirement);
        }
    }
}