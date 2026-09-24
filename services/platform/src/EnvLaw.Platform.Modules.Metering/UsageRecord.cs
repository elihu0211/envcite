namespace EnvLaw.Platform.Modules.Metering;

/// <summary>一次被計量的呼叫。<c>TenantId</c> 是 Finbuckle 的 shadow property。</summary>
public sealed class UsageRecord
{
    public Guid Id { get; init; } = Guid.CreateVersion7();

    public required string Operation { get; init; }

    public int Units { get; init; }

    public DateTimeOffset OccurredAt { get; init; }
}