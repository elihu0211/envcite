using System.Diagnostics.Metrics;

namespace EnvCite.Platform.Modules.Members.DirectorySync;

/// <summary>
/// 目錄同步請求的計數。標籤只用有限集合（<c>operation</c>、<c>outcome</c>）：租戶、成員與請求內容都不進指標
/// （租戶數量不受控，會讓標籤爆量；租戶只標在請求追蹤的 <c>tenant.id</c>）。HTTP 的速率與延遲由 ASP.NET Core 標準指標量。
/// </summary>
public sealed class DirectoryMetrics : IDisposable
{
    public const string MeterName = "EnvCite.Platform.Directory";

    private readonly Meter _meter = new(MeterName);
    private readonly Counter<long> _requests;

    public DirectoryMetrics() =>
        _requests = _meter.CreateCounter<long>("envcite.platform.directory.requests", description: "Directory sync (SCIM) requests by operation and outcome.");

    /// <param name="operation"><c>list</c>、<c>get</c>、<c>create</c>、<c>update</c>、<c>delete</c>、<c>discovery</c>、<c>authenticate</c>、<c>other</c>。</param>
    /// <param name="outcome"><c>ok</c>、<c>not_found</c>、<c>conflict</c>、<c>invalid</c>、<c>forbidden</c>、<c>unavailable</c>、<c>unauthorized</c>、<c>rate_limited</c>。</param>
    public void Record(string operation, string outcome) =>
        _requests.Add(1, new KeyValuePair<string, object?>("operation", operation), new KeyValuePair<string, object?>("outcome", outcome));

    public void Dispose() => _meter.Dispose();
}