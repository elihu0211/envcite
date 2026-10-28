using System.Diagnostics;
using System.Diagnostics.Metrics;

using Grpc.Core;
using Grpc.Core.Interceptors;

namespace EnvCite.ServiceDefaults;

/// <summary>
/// 內部 gRPC 用戶端的 RED 指標：每個一元呼叫一筆 <c>rpc.client.call.duration</c>（秒）。
/// 耗時含用戶端自己的重試（見 <c>InternalGrpcClient</c>），是呼叫端實際等待的時間。
/// 名稱、邊界與標籤的約定見 <see cref="RpcMetrics"/>。
/// 用戶端工廠的 HttpClient 指標看不出 gRPC 的失敗（gRPC 失敗仍是 HTTP 200），所以另有這個。
/// </summary>
public sealed class GrpcClientMetrics(IMeterFactory meterFactory) : Interceptor
{
    public const string MeterName = "EnvCite.Grpc.Client";

    public const string DurationMetric = "rpc.client.call.duration";

    // Meter 由主機的 IMeterFactory 建立並隨容器釋放：同一程序裡的多個主機（測試）各量各的。
    private readonly Histogram<double> _duration = meterFactory.Create(MeterName).CreateHistogram<double>(
        DurationMetric, unit: "s",
        description: "內部 gRPC 一元呼叫從送出到完成的耗時；rpc.response.status_code 為結果。",
        advice: RpcMetrics.DurationAdvice);

    public override AsyncUnaryCall<TResponse> AsyncUnaryCall<TRequest, TResponse>(
        TRequest request, ClientInterceptorContext<TRequest, TResponse> context, AsyncUnaryCallContinuation<TRequest, TResponse> continuation)
    {
        var started = Stopwatch.GetTimestamp();
        var call = continuation(request, context);
        return new AsyncUnaryCall<TResponse>(
            Observe(call.ResponseAsync, context.Method, started),
            call.ResponseHeadersAsync,
            call.GetStatus,
            call.GetTrailers,
            call.Dispose);
    }

    private async Task<TResponse> Observe<TRequest, TResponse>(
        Task<TResponse> response, Method<TRequest, TResponse> method, long started)
    {
        var status = StatusCode.OK;
        try
        {
            return await response.ConfigureAwait(false);
        }
        catch (RpcException ex)
        {
            status = ex.StatusCode;
            throw;
        }
        catch (OperationCanceledException)
        {
            status = StatusCode.Cancelled;
            throw;
        }
        catch
        {
            status = StatusCode.Unknown;
            throw;
        }
        finally
        {
            _duration.Record(Stopwatch.GetElapsedTime(started).TotalSeconds, RpcMetrics.Tags(method.ServiceName, method.Name, status));
        }
    }
}