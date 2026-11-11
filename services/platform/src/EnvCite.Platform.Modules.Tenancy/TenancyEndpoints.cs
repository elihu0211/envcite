using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.AspNetCore.Extensions;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace EnvCite.Platform.Modules.Tenancy;

public sealed record TenantResponse(string Id, string Identifier, string? Name)
{
    internal static TenantResponse From(TenantInfo tenant) => new(tenant.Id, tenant.Identifier, tenant.Name);
}

public static class TenancyEndpoints
{
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
}