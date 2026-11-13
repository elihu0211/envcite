using Microsoft.Extensions.AI;

namespace EnvCite.Platform.Modules.Consult;

/// <summary>
/// 模型呼叫的遙測。來源名稱固定，部署者在 <c>OTEL_*</c> 之外不必知道框架的實驗名稱；
/// 提示詞與回應內容永不擷取（問題文字是客戶案情），只留模型、token 數、延遲與結束原因。
/// </summary>
public static class AnswerTelemetry
{
    /// <summary>追蹤的 <c>ActivitySource</c> 與指標的 <c>Meter</c> 共用的名稱。</summary>
    public const string SourceName = "EnvCite.Platform.Answer";

    /// <summary>
    /// 把模型用戶端接上遙測。<c>EnableSensitiveData</c> 明確設為 false：它的預設會讀
    /// <c>OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT</c>，環境變數一開，提示詞就進了追蹤。
    /// </summary>
    public static IChatClient Wrap(IChatClient inner) =>
        inner.AsBuilder()
            .UseOpenTelemetry(sourceName: SourceName, configure: client => client.EnableSensitiveData = false)
            .Build();
}