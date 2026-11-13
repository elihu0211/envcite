using System.Diagnostics;
using System.Diagnostics.Metrics;

namespace EnvCite.Platform.Modules.Consult;

/// <summary>
/// 入口每次作答得出結果（回答或拒答）就加一：<c>envcite.platform.answer.outcomes</c>。分母是全部結果，所以能算比率：
/// 主題詞閘門命中的有多少、其中有多少答出來、多少降級成列條文、多少被核驗丟棄。
/// 標籤只有兩個、值域固定（<c>outcome</c> 8 種 × <c>bare_topic</c> 2 種）：租戶、問句與命中的詞都不進指標。
/// 沒有結果的呼叫（模型或 consult 暫時不可用、輸入錯誤）不計入；它們看 <c>rpc.client.call.duration</c> 與 HTTP 指標。
/// 與 <c>consult</c> 的 <c>envcite.consult.ground.outcomes</c> 不同層：那個是接地的結果，這個是入口作答的結果
/// （含模型回「證據不足」與引用核驗），兩者對得上時，差額就是模型這一步的損耗。
/// </summary>
internal sealed class AnswerMetrics(IMeterFactory meterFactory)
{
    /// <summary>與模型呼叫的遙測共用一個 <c>Meter</c> 名稱（<c>Observability</c> 只登記這一個）。</summary>
    public const string MeterName = AnswerTelemetry.SourceName;

    public const string OutcomesMetric = "envcite.platform.answer.outcomes";

    public const string OutcomeTag = "outcome";

    public const string BareTopicTag = "bare_topic";

    /// <summary>通過核驗的回答。</summary>
    public const string Answered = "answered";

    private readonly Counter<long> _outcomes = meterFactory.Create(MeterName).CreateCounter<long>(
        OutcomesMetric, unit: "{outcome}",
        description: "入口作答的結果數；outcome 為回答或拒答的原因，bare_topic 為問句是否被判為只打了主題詞。");

    /// <param name="answer">一次作答的結果；拒答的原因（<see cref="GroundedAnswer.RefusalReason"/>）就是 outcome 的值。</param>
    /// <param name="bareTopic">使用者的原句是否被判為只打了主題詞（<see cref="BareTopic"/>）。</param>
    public void Record(GroundedAnswer answer, bool bareTopic) =>
        _outcomes.Add(1, new TagList
        {
            { OutcomeTag, answer.Refused ? answer.RefusalReason ?? throw new UnreachableException() : Answered },
            { BareTopicTag, bareTopic ? "true" : "false" },
        });
}