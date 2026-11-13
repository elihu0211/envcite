using System.Globalization;
using System.Threading.RateLimiting;

using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.RateLimiting;

namespace EnvCite.Platform.Web.Auth;

/// <summary>
/// 客戶入口與營運者後台登入、第二步驗證與改密碼的速率限制：依來源位址分區，只限制 POST（看表單不限）。
/// 帳號鎖定只擋「同一個帳號被猜」，一個來源輪流試很多帳號（撞庫）要靠這一道。兩個面是各自的 policy、各自的額度
/// （<c>Platform:Portal:SignInAttemptsPerMinute</c>、<c>Platform:Ui:SignInAttemptsPerMinute</c>）。
/// </summary>
/// <remarks>
/// 來源位址是 <c>RemoteIpAddress</c>。在反向代理（Cloudflare、ingress）後面，必須讓轉送標頭生效
/// （<c>ASPNETCORE_FORWARDEDHEADERS_ENABLED=true</c>），否則所有客戶看起來都來自代理的位址，
/// 共用同一個額度：一個人試錯就讓所有人登不進去。狀態在各副本的記憶體裡，N 個副本的實際額度是 N 倍。
/// </remarks>
internal static class PortalRateLimit
{
    private static readonly TimeSpan Window = TimeSpan.FromMinutes(1);

    public static void Configure(RateLimiterOptions limiter)
    {
        limiter.AddPolicy(PlatformAuth.PortalSignInRateLimit, context =>
            Partition(context, context.RequestServices.GetRequiredService<IOptions<PortalOptions>>().Value.SignInAttemptsPerMinute));

        limiter.AddPolicy(PlatformAuth.UiSignInRateLimit, context =>
            Partition(context, context.RequestServices.GetRequiredService<IOptions<UiOptions>>().Value.SignInAttemptsPerMinute));

        // 被限制時告訴對方多久後再試（所有被限流的回應都加；/v2 的 429 本來就沒有這個標頭，加了不破壞）。
        // 入口是給人看的頁面，回一句人話；API 維持空的 429。
        limiter.OnRejected = async (context, cancellationToken) =>
        {
            // 滑動視窗的失敗 lease 不一定帶 RetryAfter；沒有就用視窗長度：一個視窗之後額度必定釋放，是保守但正確的上限。
            var wait = context.Lease.TryGetMetadata(MetadataName.RetryAfter, out var retryAfter) ? retryAfter : Window;
            context.HttpContext.Response.Headers.RetryAfter = ((int)Math.Ceiling(wait.TotalSeconds)).ToString(CultureInfo.InvariantCulture);

            var path = context.HttpContext.Request.Path;
            if (path.StartsWithSegments(PortalPaths.Prefix) || path.StartsWithSegments(UiEndpoints.HomePath))
            {
                context.HttpContext.Response.ContentType = "text/plain; charset=utf-8";
                await context.HttpContext.Response.WriteAsync("嘗試次數太多，請稍後再試。", cancellationToken);
            }
        };
    }

    private static RateLimitPartition<string> Partition(HttpContext context, int permits)
    {
        if (!HttpMethods.IsPost(context.Request.Method))
            return RateLimitPartition.GetNoLimiter("not-a-submission");

        return RateLimitPartition.GetSlidingWindowLimiter(
            context.Connection.RemoteIpAddress?.ToString() ?? "unknown",
            _ => new SlidingWindowRateLimiterOptions
            {
                PermitLimit = permits,
                Window = Window,
                SegmentsPerWindow = 6,
                QueueLimit = 0,
            });
    }
}