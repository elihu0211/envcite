using Microsoft.Extensions.Diagnostics.HealthChecks;

namespace EnvCite.Consult;

/// <summary>
/// 收到 SIGTERM 時的排空：先在 gRPC 健康檢查回報 NOT_SERVING，等 <see cref="Delay"/> 讓探針與 DNS 把這個 pod
/// 從 headless Service 摘掉，主機之後才停止 Kestrel（新的呼叫才不會打到正在關的伺服器）；進行中的呼叫在
/// <c>HostOptions.ShutdownTimeout</c> 內完成。主機在停止任何 <see cref="IHostedService"/> 之前先呼叫
/// <see cref="StoppingAsync"/>，所以這段時間 Kestrel 仍在接受呼叫。
/// </summary>
internal sealed class Draining(TimeProvider time) : IHealthCheck, IHostedLifecycleService
{
    public static readonly TimeSpan Delay = TimeSpan.FromSeconds(3);

    private int _stopping;

    public Task<HealthCheckResult> CheckHealthAsync(HealthCheckContext context, CancellationToken cancellationToken = default) =>
        Task.FromResult(Volatile.Read(ref _stopping) == 1 ? HealthCheckResult.Unhealthy("draining") : HealthCheckResult.Healthy());

    /// <summary>只排空一次：主機可能被停止不只一次（訊號之後又被釋放），已經摘掉的 pod 不必再等。</summary>
    public Task StoppingAsync(CancellationToken cancellationToken) =>
        Interlocked.Exchange(ref _stopping, 1) == 1 ? Task.CompletedTask : Task.Delay(Delay, time, cancellationToken);

    public Task StartingAsync(CancellationToken cancellationToken) => Task.CompletedTask;

    public Task StartAsync(CancellationToken cancellationToken) => Task.CompletedTask;

    public Task StartedAsync(CancellationToken cancellationToken) => Task.CompletedTask;

    public Task StopAsync(CancellationToken cancellationToken) => Task.CompletedTask;

    public Task StoppedAsync(CancellationToken cancellationToken) => Task.CompletedTask;
}