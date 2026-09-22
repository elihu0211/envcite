namespace EnvLaw.Platform.Modules.Metering;

/// <summary>One metered call. <c>TenantId</c> is a Finbuckle shadow property.</summary>
public sealed class UsageRecord
{
    public Guid Id { get; init; } = Guid.CreateVersion7();

    public required string Operation { get; init; }

    public int Units { get; init; }

    public DateTimeOffset OccurredAt { get; init; }
}