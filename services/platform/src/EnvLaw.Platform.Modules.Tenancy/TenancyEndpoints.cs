using System.Text.RegularExpressions;

using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.AspNetCore.Extensions;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;

namespace EnvLaw.Platform.Modules.Tenancy;

public sealed record CreateTenantRequest(string Identifier, string Name);

public sealed record TenantResponse(string Id, string Identifier, string? Name)
{
    internal static TenantResponse From(TenantInfo tenant) => new(tenant.Id, tenant.Identifier, tenant.Name);
}

public static partial class TenancyEndpoints
{
    [GeneratedRegex("^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$")]
    private static partial Regex IdentifierPattern();

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

    private static async Task<IResult> CreateTenantAsync(CreateTenantRequest request, IMultiTenantStore<TenantInfo> store)
    {
        if (!IdentifierPattern().IsMatch(request.Identifier) || string.IsNullOrWhiteSpace(request.Name))
        {
            return Results.ValidationProblem(new Dictionary<string, string[]>
            {
                ["identifier"] = ["3-63 chars: lowercase letters, digits, hyphens; must start/end alphanumeric."],
            });
        }

        if (await store.GetByIdentifierAsync(request.Identifier) is not null)
        {
            return Results.Conflict();
        }

        var tenant = new TenantInfo
        {
            Id = Guid.CreateVersion7().ToString(),
            Identifier = request.Identifier,
            Name = request.Name.Trim(),
        };

        if (!await store.AddAsync(tenant))
        {
            return Results.Conflict();
        }

        return Results.Created($"/admin/tenants/{tenant.Id}", TenantResponse.From(tenant));
    }
}