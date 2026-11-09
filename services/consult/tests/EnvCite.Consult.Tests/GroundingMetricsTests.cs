using System.Diagnostics.Metrics;

using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Diagnostics.Metrics.Testing;

namespace EnvCite.Consult.Tests;

/// <summary>結果計數器本身：每個結果一筆、標籤只有兩個固定值域。端到端的接線在 <c>HostingTests</c>。</summary>
public sealed class GroundingMetricsTests
{
    private const string NoMarker = "空氣污染防制法第 20 條的罰則";
    private const string WithJurisdiction = "高雄市的工廠排放要符合什麼標準";
    private const string WithOrdinance = "自治條例有沒有規定";

    private static readonly GroundingOutcome[] Outcomes =
    [
        new Evidence([new Citation("L1", "iri", "label", "text", Grounding.Law)], Confidence.High),
        new Refusal(RefusalReason.NoEvidence, "detail"),
        new Refusal(RefusalReason.OutOfScope, "detail"),
        new Refusal(RefusalReason.MissingInput, "detail"),
        new Refusal(RefusalReason.LocalLawUncovered, "detail"),
    ];

    [Fact]
    public void EveryOutcomeAndMarkerCombinationIsCountedOnceUnderItsClosedLabels()
    {
        using var provider = new ServiceCollection().AddMetrics().BuildServiceProvider();
        var factory = provider.GetRequiredService<IMeterFactory>();
        using var collector = new MetricCollector<long>(factory, GroundingMetrics.MeterName, GroundingMetrics.OutcomesMetric);
        var metrics = new GroundingMetrics(factory);

        foreach (var outcome in Outcomes)
        {
            foreach (var question in new[] { NoMarker, WithJurisdiction, WithOrdinance })
            {
                metrics.Record(question, outcome);
            }
        }

        var points = collector.GetMeasurementSnapshot();
        string[] outcomeNames = ["grounded", "no_evidence", "out_of_scope", "missing_input", "local_law_uncovered"];
        string[] markerNames = ["none", "jurisdiction", "ordinance"];
        Assert.Equal(
            [.. outcomeNames.SelectMany(o => markerNames.Select(m => (o, m))).Order()],
            [.. points.Select(p => ((string)p.Tags["outcome"]!, (string)p.Tags["local_marker"]!)).Order()]);
        Assert.All(points, p =>
        {
            Assert.Equal(1, p.Value);
            // 標籤只有這兩個：租戶、問句與命中的詞不在其中。
            Assert.Equal(2, p.Tags.Count);
        });
    }
}