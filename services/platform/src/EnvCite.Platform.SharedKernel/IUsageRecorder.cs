namespace EnvCite.Platform.SharedKernel;

/// <summary>為目前租戶記錄計費用量（由 Metering 模組實作）。</summary>
public interface IUsageRecorder
{
    /// <param name="operation">穩定的 operation 鍵，例如 <c>ontology.get_law</c>。</param>
    /// <param name="units">消耗的計費單位數。</param>
    Task RecordAsync(string operation, int units, CancellationToken cancellationToken);
}