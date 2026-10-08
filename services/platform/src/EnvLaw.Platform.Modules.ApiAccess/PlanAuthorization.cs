using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Authorization.Policy;
using Microsoft.AspNetCore.Http;

namespace EnvLaw.Platform.Modules.ApiAccess;

/// <summary>租戶要有方案，才能呼叫資料端點（本體、接地）。</summary>
public sealed class PlanRequirement : IAuthorizationRequirement;

public sealed class PlanHandler(PlanCatalog plans) : AuthorizationHandler<PlanRequirement>
{
    protected override Task HandleRequirementAsync(AuthorizationHandlerContext context, PlanRequirement requirement)
    {
        var tenant = context.User.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value;
        if (tenant is not null && plans.For(tenant) is not null)
        {
            context.Succeed(requirement);
        }
        else
        {
            // 記下失敗的原因，讓結果處理器能分辨「沒有方案」與一般的「禁止」。
            context.Fail(new AuthorizationFailureReason(this, "The tenant has no active plan."));
        }

        return Task.CompletedTask;
    }
}

/// <summary>
/// 沒有方案是 402 Payment Required，不是 403：呼叫端的身分沒問題，是缺少付費的方案。
/// 授權在限流之前執行，所以沒有方案的租戶不會消耗任何租戶限流額度。
/// </summary>
internal sealed class PaymentRequiredResultHandler : IAuthorizationMiddlewareResultHandler
{
    private readonly AuthorizationMiddlewareResultHandler _default = new();

    public async Task HandleAsync(
        RequestDelegate next, HttpContext context, AuthorizationPolicy policy, PolicyAuthorizationResult authorizeResult)
    {
        if (authorizeResult.Forbidden
            && authorizeResult.AuthorizationFailure?.FailureReasons.Any(r => r.Handler is PlanHandler) == true)
        {
            await Results.Problem(
                statusCode: StatusCodes.Status402PaymentRequired,
                title: "Payment required",
                detail: "This tenant has no active plan. Contact your account manager to activate one.")
                .ExecuteAsync(context);
            return;
        }

        await _default.HandleAsync(next, context, policy, authorizeResult);
    }
}