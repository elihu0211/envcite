using System.Diagnostics;
using System.Diagnostics.Metrics;

using EnvCite.ServiceDefaults;

using Grpc.Core;
using Grpc.Core.Interceptors;

namespace EnvCite.Consult;

/// <summary>
/// gRPC 伺服器的 RED 指標（rate、errors、duration）：每個一元呼叫一筆 <c>rpc.server.call.duration</c>（秒）。
/// 次數是 rate、<c>rpc.response.status_code</c> 不是 <c>OK</c> 的是 errors、分布是 duration；名稱、邊界與標籤的約定見
/// <see cref="RpcMetrics"/>，與 platform 的用戶端同一組標籤，兩側能對照。只掛在本服務的端點上（見 Program.cs），
/// 探針（gRPC 健康檢查）不進指標，否則 rate 與延遲分布被它們稀釋。
/// </summary>
internal sealed class GrpcServerMetrics(IMeterFactory meterFactory) : Interceptor
{
    public const string MeterName = "EnvCite.Grpc.Server";

    public const string DurationMetric = "rpc.server.call.duration";

    private readonly Histogram<double> _duration = meterFactory.Create(MeterName).CreateHistogram<double>(
        DurationMetric, unit: "s",
        description: "gRPC 伺服器一元呼叫從收到到回應的耗時；rpc.response.status_code 為結果。",
        advice: RpcMetrics.DurationAdvice);

    public override async Task<TResponse> UnaryServerHandler<TRequest, TResponse>(
        TRequest request, ServerCallContext context, UnaryServerMethod<TRequest, TResponse> continuation)
    {
        var started = Stopwatch.GetTimestamp();
        var status = StatusCode.Unknown;
        try
        {
            var response = await continuation(request, context);
            status = context.Status.StatusCode;
            return response;
        }
        catch (RpcException ex)
        {
            status = ex.StatusCode;
            throw;
        }
        catch (OperationCanceledException) when (context.CancellationToken.IsCancellationRequested)
        {
            // 呼叫端取消或期限已到，連線上的請求已中止。
            status = context.Deadline <= DateTime.UtcNow ? StatusCode.DeadlineExceeded : StatusCode.Cancelled;
            throw;
        }
        finally
        {
            // 方法全名是 /<service>/<method>。
            var method = context.Method.AsSpan(1);
            var slash = method.IndexOf('/');
            _duration.Record(
                Stopwatch.GetElapsedTime(started).TotalSeconds,
                RpcMetrics.Tags(method[..slash].ToString(), method[(slash + 1)..].ToString(), status));
        }
    }
}