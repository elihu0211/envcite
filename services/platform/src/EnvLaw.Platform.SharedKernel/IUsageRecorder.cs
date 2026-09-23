namespace EnvLaw.Platform.SharedKernel;

/// <summary>Records billable usage for the current tenant (implemented by the Metering module).</summary>
public interface IUsageRecorder
{
    /// <param name="operation">Stable operation key, e.g. <c>ontology.get_law</c>.</param>
    /// <param name="units">Billable units consumed.</param>
    Task RecordAsync(string operation, int units, CancellationToken cancellationToken);
}