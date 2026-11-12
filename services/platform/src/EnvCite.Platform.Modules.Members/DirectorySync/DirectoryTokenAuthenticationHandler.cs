using System.Security.Claims;
using System.Security.Cryptography;
using System.Text.Encodings.Web;

using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.Http;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Modules.Members.DirectorySync;

/// <summary>
/// 驗證 <c>Authorization: Bearer scim_…</c>，發出 tenant 與 token 的 claim。租戶<b>只</b>來自 token（查到它屬於哪個租戶），
/// 不來自路徑、查詢字串或本文。功能面沒開時什麼都不認（<c>NoResult</c>），所以關閉時 token 完全無效。
/// 失敗一律是同一個 401（不透露 token 是不存在、已撤銷還是已過期）。
/// </summary>
internal sealed partial class DirectoryTokenAuthenticationHandler(
    IOptionsMonitor<AuthenticationSchemeOptions> schemeOptions,
    ILoggerFactory logger,
    UrlEncoder encoder,
    IServiceScopeFactory scopeFactory,
    IOptions<DirectoryOptions> options,
    DirectoryMetrics metrics,
    TimeProvider time)
    : AuthenticationHandler<AuthenticationSchemeOptions>(schemeOptions, logger, encoder)
{
    private const string BearerPrefix = "Bearer ";

    /// <summary>租戶解析（早於授權）與授權各驗證一次；同一個請求只查一次資料庫。</summary>
    private const string CacheKey = "envcite.directory.authentication";

    private static readonly TimeSpan UsedThrottle = TimeSpan.FromMinutes(1);

    protected override async Task<AuthenticateResult> HandleAuthenticateAsync()
    {
        if (!options.Value.Enabled)
        {
            return AuthenticateResult.NoResult();
        }

        if (Context.Items.TryGetValue(CacheKey, out var cached) && cached is AuthenticateResult known)
        {
            return known;
        }

        var result = await AuthenticateTokenAsync();
        Context.Items[CacheKey] = result;
        return result;
    }

    protected override async Task HandleChallengeAsync(AuthenticationProperties properties)
    {
        metrics.Record("authenticate", "unauthorized");
        Response.StatusCode = StatusCodes.Status401Unauthorized;
        Response.Headers.WWWAuthenticate = "Bearer realm=\"scim\"";
        await ScimResponses.WriteErrorAsync(Context, StatusCodes.Status401Unauthorized, null, "A valid directory sync token is required.", null);
    }

    protected override async Task HandleForbiddenAsync(AuthenticationProperties properties)
    {
        Response.StatusCode = StatusCodes.Status403Forbidden;
        await ScimResponses.WriteErrorAsync(Context, StatusCodes.Status403Forbidden, null, "This token is not allowed to do that.", null);
    }

    private async Task<AuthenticateResult> AuthenticateTokenAsync()
    {
        string? header = Request.Headers.Authorization;
        if (header is null || !header.StartsWith(BearerPrefix, StringComparison.OrdinalIgnoreCase))
        {
            return AuthenticateResult.NoResult();
        }

        if (!DirectoryTokenFormat.TryParse(header[BearerPrefix.Length..].Trim(), out var prefix, out var secret))
        {
            return AuthenticateResult.Fail("Malformed token.");
        }

        // 另開 scope：request-scoped 的 PlatformDbContext 只能在租戶解析之後建立（這裡正是在解析租戶）。
        await using var scope = scopeFactory.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<PlatformDbContext>();

        // 跨租戶的查詢，只此一處：以前綴查找 token，只略過租戶過濾（同 API key）。
        var match = await db.DirectoryTokens
            .IgnoreQueryFilters([Constants.TenantToken])
            .AsNoTracking()
            .Where(t => t.Prefix == prefix)
            .Select(t => new { Token = t, TenantId = EF.Property<string>(t, "TenantId") })
            .SingleOrDefaultAsync(Context.RequestAborted);

        var now = time.GetUtcNow();
        if (match is null
            || !CryptographicOperations.FixedTimeEquals(match.Token.SecretHash, DirectoryTokenFormat.Hash(secret))
            || !match.Token.IsActive(now))
        {
            return AuthenticateResult.Fail("Invalid token.");
        }

        var store = scope.ServiceProvider.GetRequiredService<IMultiTenantStore<TenantInfo>>();
        if (await store.GetAsync(match.TenantId) is not { } tenant)
        {
            return AuthenticateResult.Fail("Invalid token.");
        }

        await TouchAsync(db, match.Token, now);

        var principal = new ClaimsPrincipal(new ClaimsIdentity(
            [
                new Claim(PlatformClaimTypes.TenantIdentifier, tenant.Identifier!),
                new Claim(PlatformClaimTypes.DirectoryTokenId, match.Token.Id.ToString()),
            ],
            Scheme.Name));
        return AuthenticateResult.Success(new AuthenticationTicket(principal, Scheme.Name));
    }

    /// <summary>最後使用時間給介面看（這把 token 還在用嗎）；一分鐘最多寫一次，寫不進去不使驗證失敗。</summary>
    private async Task TouchAsync(PlatformDbContext db, DirectoryToken token, DateTimeOffset now)
    {
        if (token.LastUsedAt is { } last && now - last < UsedThrottle)
        {
            return;
        }

        try
        {
            var stale = now - UsedThrottle;
            await db.DirectoryTokens
                .IgnoreQueryFilters([Constants.TenantToken])
                .Where(t => t.Id == token.Id && (t.LastUsedAt == null || t.LastUsedAt < stale))
                .ExecuteUpdateAsync(set => set.SetProperty(t => t.LastUsedAt, now), Context.RequestAborted);
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            TouchFailed(Logger, exception);
        }
    }

    [LoggerMessage(Level = LogLevel.Warning, Message = "Could not record directory token use")]
    private static partial void TouchFailed(ILogger logger, Exception exception);
}