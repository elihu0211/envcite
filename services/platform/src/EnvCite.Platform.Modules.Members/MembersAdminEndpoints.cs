using System.Security.Claims;

using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace EnvCite.Platform.Modules.Members;

public sealed record CreateMemberRequest(string? Email, string? DisplayName, string? Role);

/// <summary>
/// <c>/admin</c> 之下，營運者管理租戶成員的端點。營運後台用的是同一個 <see cref="IMemberAdmin"/>，行為與稽核一致。
/// 有這組端點，第一位成員（一個租戶的第一位擁有者）不必靠營運後台的 Google 登入就能建立。
/// 建立與重設密碼的回應含一次性的初始密碼（只此一次，資料庫只存雜湊）：呼叫端要自己安全地處理，
/// 回應內容不會被記進日誌或稽核。
/// </summary>
public static class MembersAdminEndpoints
{
    public static RouteGroupBuilder MapMembersAdminEndpoints(this RouteGroupBuilder admin)
    {
        admin.MapGet("/tenants/{tenantId}/members", async (string tenantId, IMemberAdmin members, CancellationToken ct) =>
        {
            var list = await members.ListAsync(tenantId, ct);
            return list.IsOk ? Results.Ok(list.Value) : Problem(list);
        });

        admin.MapPost("/tenants/{tenantId}/members", async (
            string tenantId, CreateMemberRequest request, IMemberAdmin members, ClaimsPrincipal user, CancellationToken ct) =>
        {
            var created = await members.CreateAsync(user, tenantId, request.Email ?? string.Empty, request.DisplayName, request.Role ?? string.Empty, ct);
            return created.IsOk ? Results.Created($"/admin/tenants/{tenantId}/members", created.Value) : Problem(created);
        });

        admin.MapPost("/tenants/{tenantId}/members/{userId}/password-reset", async (
            string tenantId, string userId, IMemberAdmin members, ClaimsPrincipal user, CancellationToken ct) =>
        {
            var reset = await members.ResetPasswordAsync(user, tenantId, userId, ct);
            return reset.IsOk ? Results.Ok(reset.Value) : Problem(reset);
        });

        admin.MapPost("/tenants/{tenantId}/members/{userId}/two-factor-reset", async (
            string tenantId, string userId, IMemberAdmin members, ClaimsPrincipal user, CancellationToken ct) =>
        {
            var reset = await members.ResetTwoFactorAsync(user, tenantId, userId, ct);
            return reset.IsOk ? Results.Ok(reset.Value) : Problem(reset);
        });

        admin.MapPost("/tenants/{tenantId}/members/{userId}/disable", (string tenantId, string userId, IMemberAdmin members, ClaimsPrincipal user, CancellationToken ct) =>
            SetDisabledAsync(members, user, tenantId, userId, disabled: true, ct));

        admin.MapPost("/tenants/{tenantId}/members/{userId}/enable", (string tenantId, string userId, IMemberAdmin members, ClaimsPrincipal user, CancellationToken ct) =>
            SetDisabledAsync(members, user, tenantId, userId, disabled: false, ct));

        return admin;
    }

    private static async Task<IResult> SetDisabledAsync(
        IMemberAdmin members, ClaimsPrincipal user, string tenantId, string userId, bool disabled, CancellationToken ct)
    {
        var changed = await members.SetDisabledAsync(user, tenantId, userId, disabled, ct);
        return changed.IsOk ? Results.Ok(changed.Value) : Problem(changed);
    }

    private static IResult Problem<T>(AdminOutcome<T> outcome) => outcome.Status switch
    {
        AdminStatus.NotFound => Results.NotFound(),
        AdminStatus.Conflict => Results.Conflict(),
        AdminStatus.Invalid => Results.ValidationProblem(new Dictionary<string, string[]> { ["request"] = [outcome.Message ?? "Invalid request."] }),
        _ => Results.StatusCode(StatusCodes.Status500InternalServerError),
    };
}