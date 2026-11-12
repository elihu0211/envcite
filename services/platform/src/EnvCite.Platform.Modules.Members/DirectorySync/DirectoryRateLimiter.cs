using System.Threading.RateLimiting;

using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Modules.Members.DirectorySync;

/// <summary>
/// 每把 token 每分鐘的請求上限（<c>Platform:Directory:RequestsPerMinute</c>）。依 token 分區：身分來源可能從同一個出口位址
/// 為很多租戶送請求（依來源位址會互相拖累），租戶也可能有多把 token（依租戶會讓一把失控的 token 餓死其他）。
/// 在授權之後、端點之前套用（<see cref="ScimEndpoints"/> 的端點過濾器），所以沒有有效 token 的請求不耗用任何額度。
/// 狀態在各副本的記憶體裡，N 個副本的實際額度是 N 倍。
/// </summary>
internal sealed class DirectoryRateLimiter(IOptions<DirectoryOptions> options) : IDisposable
{
    private static readonly TimeSpan Window = TimeSpan.FromMinutes(1);

    private readonly PartitionedRateLimiter<string> _limiter = PartitionedRateLimiter.Create<string, string>(token =>
        RateLimitPartition.GetFixedWindowLimiter(token, _ => new FixedWindowRateLimiterOptions
        {
            PermitLimit = options.Value.RequestsPerMinute,
            Window = Window,
            QueueLimit = 0,
        }));

    /// <summary>取一個額度；沒有就回要等多久（秒）。</summary>
    public bool TryAcquire(string tokenId, out int retryAfterSeconds)
    {
        using var lease = _limiter.AttemptAcquire(tokenId);
        retryAfterSeconds = lease.TryGetMetadata(MetadataName.RetryAfter, out var wait)
            ? (int)Math.Ceiling(wait.TotalSeconds)
            : (int)Window.TotalSeconds; // 固定視窗最遲一個視窗之後釋放
        return lease.IsAcquired;
    }

    public void Dispose() => _limiter.Dispose();
}