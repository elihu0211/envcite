using System.Diagnostics;
using System.Diagnostics.Metrics;

namespace EnvCite.Consult;

/// <summary>
/// 每次 <c>Ground</c> 得出結果（證據或拒答）就加一：<c>envcite.consult.ground.outcomes</c>。分母是全部結果，分子是
/// <c>local_marker</c> 不是 <c>none</c> 的拒答，兩者的比率是上線後「問了地方自治法規但語料沒有」的真實需求。
/// 標籤只有兩個、值域固定（<c>outcome</c> 4 種 × <c>local_marker</c> 3 種）：租戶、問句與命中的詞都不進指標。
/// 失敗的呼叫（本體不可用、參數錯誤）沒有結果，不計入；它們看 <c>rpc.server.call.duration</c>。
/// </summary>
internal sealed class GroundingMetrics(IMeterFactory meterFactory)
{
    public const string MeterName = "EnvCite.Consult";

    public const string OutcomesMetric = "envcite.consult.ground.outcomes";

    public const string OutcomeTag = "outcome";

    public const string LocalMarkerTag = "local_marker";

    private readonly Counter<long> _outcomes = meterFactory.Create(MeterName).CreateCounter<long>(
        OutcomesMetric, unit: "{outcome}",
        description: "Ground 的結果數；outcome 為接地或拒答的原因，local_marker 為問句是否點名縣市或自治法規。");

    public void Record(string question, GroundingOutcome outcome) =>
        _outcomes.Add(1, new TagList
        {
            { OutcomeTag, OutcomeName(outcome) },
            { LocalMarkerTag, MarkerName(LocalMarkers.Classify(question)) },
        });

    private static string OutcomeName(GroundingOutcome outcome) => outcome switch
    {
        Evidence => "grounded",
        Refusal { Reason: RefusalReason.NoEvidence } => "no_evidence",
        Refusal { Reason: RefusalReason.OutOfScope } => "out_of_scope",
        Refusal { Reason: RefusalReason.MissingInput } => "missing_input",
        _ => throw new UnreachableException(),
    };

    private static string MarkerName(LocalMarker marker) => marker switch
    {
        LocalMarker.None => "none",
        LocalMarker.Jurisdiction => "jurisdiction",
        LocalMarker.Ordinance => "ordinance",
        _ => throw new UnreachableException(),
    };
}