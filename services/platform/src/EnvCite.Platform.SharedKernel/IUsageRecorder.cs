namespace EnvCite.Platform.SharedKernel;

/// <summary>為目前租戶記錄計費用量（由 Metering 模組實作）。</summary>
public interface IUsageRecorder
{
    /// <param name="operation">穩定的 operation 鍵，例如 <c>ontology.get_law</c>。</param>
    /// <param name="units">消耗的計費單位數。</param>
    Task RecordAsync(string operation, int units, CancellationToken cancellationToken);

    /// <summary>加入用量但不儲存：呼叫端自己儲存，與同一個 context 的其他變更在同一個交易裡。</summary>
    void Stage(string operation, int units);
}