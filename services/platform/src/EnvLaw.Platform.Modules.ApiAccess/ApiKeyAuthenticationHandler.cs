using System.Security.Claims;
using System.Security.Cryptography;
using System.Text.Encodings.Web;

using EnvLaw.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Authentication;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace EnvLaw.Platform.Modules.ApiAccess;

/// <summary>Authenticates <c>Authorization: Bearer sk_live_…</c> and emits tenant, key and scope claims.</summary>
public sealed class ApiKeyAuthenticationHandler(
    IOptionsMonitor<AuthenticationSchemeOptions> options,
    ILoggerFactory logger,
    UrlEncoder encoder,
    IServiceScopeFactory scopeFactory,
    TimeProvider timeProvider)
    : AuthenticationHandler<AuthenticationSchemeOptions>(options, logger, encoder)
{
    private const string BearerPrefix = "Bearer ";

    protected override async Task<AuthenticateResult> HandleAuthenticateAsync()
    {
        string? header = Request.Headers.Authorization;
        if (header is null || !header.StartsWith(BearerPrefix, StringComparison.OrdinalIgnoreCase))
        {
            return AuthenticateResult.NoResult();
        }

        if (!ApiKeyFormat.TryParse(header[BearerPrefix.Length..].Trim(), out var prefix, out var secret))
        {
            return AuthenticateResult.Fail("Malformed API key.");
        }

        // Separate scope: the request-scoped ApiAccessDbContext must be created only after tenant resolution.
        await using var scope = scopeFactory.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<ApiAccessDbContext>();

        // The only cross-tenant query: look up by prefix, bypassing just the tenant filter.
        var match = await db.ApiKeys
            .IgnoreQueryFilters([Finbuckle.MultiTenant.Abstractions.Constants.TenantToken])
            .AsNoTracking()
            .Where(k => k.Prefix == prefix)
            .Select(k => new { Key = k, TenantId = EF.Property<string>(k, "TenantId") })
            .SingleOrDefaultAsync(Context.RequestAborted);

        if (match is null || !CryptographicOperations.FixedTimeEquals(match.Key.SecretHash, ApiKeyFormat.Hash(secret)))
        {
            return AuthenticateResult.Fail("Invalid API key.");
        }

        if (!match.Key.IsActive(timeProvider.GetUtcNow()))
        {
            return AuthenticateResult.Fail("API key revoked or expired.");
        }

        var store = scope.ServiceProvider.GetRequiredService<IMultiTenantStore<TenantInfo>>();
        if (await store.GetAsync(match.TenantId) is not { } tenant)
        {
            return AuthenticateResult.Fail("Tenant not found.");
        }

        List<Claim> claims =
        [
            new(PlatformClaimTypes.TenantIdentifier, tenant.Identifier),
            new(PlatformClaimTypes.ApiKeyId, match.Key.Id.ToString()),
            .. match.Key.Scopes.Select(s => new Claim(PlatformClaimTypes.Scope, s)),
        ];

        var principal = new ClaimsPrincipal(new ClaimsIdentity(claims, Scheme.Name));
        return AuthenticateResult.Success(new AuthenticationTicket(principal, Scheme.Name));
    }
}