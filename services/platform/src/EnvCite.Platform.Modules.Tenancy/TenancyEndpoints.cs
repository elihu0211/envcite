using System.Security.Claims;

using EnvCite.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.AspNetCore.Extensions;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace EnvCite.Platform.Modules.Tenancy;

public sealed record CreateTenantRequest(string Identifier, string Name);

public sealed record TenantResponse(string Id, string Identifier, string? Name)
{
    internal static TenantResponse From(TenantInfo tenant) => new(tenant.Id, tenant.Identifier, tenant.Name);
}

public static class TenancyEndpoints
{
    /// <summary><c>/admin</c> 之下的營運者端點；需要 admin key。</summary>
    public static RouteGroupBuilder MapTenancyAdminEndpoints(this RouteGroupBuilder admin)
    {
        admin.MapPost("/tenants", CreateTenantAsync);
        admin.MapGet("/tenants/{id}", async (string id, IMultiTenantStore<TenantInfo> store) =>
            await store.GetAsync(id) is { } tenant ? Results.Ok(TenantResponse.From(tenant)) : Results.NotFound());
        return admin;
    }

    /// <summary><c>/v1</c> 之下以租戶為範圍的端點。</summary>
    public static RouteGroupBuilder MapTenancyEndpoints(this RouteGroupBuilder v1)
    {
        v1.MapGet("/me", (HttpContext context) =>
            context.GetMultiTenantContext<TenantInfo>().TenantInfo is { } tenant
                ? Results.Ok(TenantResponse.From(tenant))
                : Results.Unauthorized())
            .WithName("GetCurrentTenant")
            .WithTags("租戶")
            .WithSummary("目前的租戶")
            .WithDescription("此 API key 所屬的租戶。")
            .Produces<TenantResponse>();
        return v1;
    }

    private static async Task<IResult> CreateTenantAsync(
        CreateTenantRequest request, ITenantProvisioner tenants, ClaimsPrincipal user, CancellationToken ct)
    {
        var created = await tenants.CreateAsync(user, request.Identifier, request.Name, ct);
        return created.Status switch
        {
            AdminStatus.Ok => Results.Created(
                $"/admin/tenants/{created.Value!.Id}", new TenantResponse(created.Value.Id, created.Value.Identifier, created.Value.Name)),
            AdminStatus.Conflict => Results.Conflict(),
            _ => Results.ValidationProblem(new Dictionary<string, string[]> { ["identifier"] = [created.Message ?? "Invalid request."] }),
        };
    }
}