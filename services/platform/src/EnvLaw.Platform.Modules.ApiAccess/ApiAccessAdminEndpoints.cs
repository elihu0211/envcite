using System.Security.Claims;

using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace EnvLaw.Platform.Modules.ApiAccess;

public sealed record SetPlanRequest(string? Plan);

/// <summary>
/// <c>/admin</c> 之下，營運者管理 API key 與方案的端點。營運後台用的是同一組服務
/// （<see cref="IApiKeyAdmin"/>、<see cref="IPlanAdmin"/>），所以兩邊的行為與稽核一致。
/// </summary>
public static class ApiAccessAdminEndpoints
{
    public static RouteGroupBuilder MapApiAccessAdminEndpoints(this RouteGroupBuilder admin)
    {
        admin.MapPost("/tenants/{tenantId}/api-keys", async (
            string tenantId, CreateApiKeyRequest request, IApiKeyAdmin keys, ClaimsPrincipal user, CancellationToken ct) =>
        {
            var issued = await keys.IssueAsync(user, tenantId, request.Name, request.Scopes, request.ExpiresAt, ct);
            return issued.IsOk
                ? Results.Created($"/v2/api-keys/{issued.Value!.ApiKey.Id}", new CreatedApiKey(issued.Value.Key, Summary(issued.Value.ApiKey)))
                : Problem(issued);
        });

        admin.MapGet("/tenants/{tenantId}/api-keys", async (string tenantId, IApiKeyAdmin keys, CancellationToken ct) =>
        {
            var list = await keys.ListAsync(tenantId, ct);
            return list.IsOk ? Results.Ok(list.Value!.Select(Summary)) : Problem(list);
        });

        admin.MapDelete("/tenants/{tenantId}/api-keys/{id:guid}", async (
            string tenantId, Guid id, IApiKeyAdmin keys, ClaimsPrincipal user, CancellationToken ct) =>
        {
            var revoked = await keys.RevokeAsync(user, tenantId, id, ct);
            return revoked.IsOk ? Results.NoContent() : Problem(revoked);
        });

        admin.MapGet("/plans", (IPlanAdmin plans) => Results.Ok(plans.Plans));

        admin.MapPut("/tenants/{tenantId}/plan", async (
            string tenantId, SetPlanRequest request, IPlanAdmin plans, ClaimsPrincipal user, CancellationToken ct) =>
        {
            if (string.IsNullOrWhiteSpace(request.Plan))
            {
                return Results.ValidationProblem(new Dictionary<string, string[]> { ["plan"] = ["Plan is required; use DELETE to remove a plan."] });
            }

            var change = await plans.SetAsync(user, tenantId, request.Plan, ct);
            return change.IsOk ? Results.Ok(change.Value) : Problem(change);
        });

        admin.MapDelete("/tenants/{tenantId}/plan", async (
            string tenantId, IPlanAdmin plans, ClaimsPrincipal user, CancellationToken ct) =>
        {
            var change = await plans.SetAsync(user, tenantId, plan: null, ct);
            return change.IsOk ? Results.Ok(change.Value) : Problem(change);
        });

        return admin;
    }

    private static ApiKeySummary Summary(ApiKeyInfo k) => new(k.Id, k.Name, k.Prefix, k.Scopes, k.CreatedAt, k.ExpiresAt, k.RevokedAt);

    private static IResult Problem<T>(AdminOutcome<T> outcome) => outcome.Status switch
    {
        AdminStatus.NotFound => Results.NotFound(),
        AdminStatus.Invalid => Results.ValidationProblem(new Dictionary<string, string[]> { ["request"] = [outcome.Message ?? "Invalid request."] }),
        _ => Results.StatusCode(StatusCodes.Status500InternalServerError),
    };
}