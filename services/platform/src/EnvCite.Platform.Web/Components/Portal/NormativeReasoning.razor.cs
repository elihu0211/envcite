using System.Globalization;

using EnvCite.Platform.Web.Presentation;

namespace EnvCite.Platform.Web.Components.Portal;

public sealed partial class NormativeReasoning(ITenantModules modules, INormativeReasoningService reasoningService)
{
    private PortalReasoningOutcome? _outcome;

    private ITenantModules Modules => modules;

    private IReadOnlyList<SelectOption> TriStateOptions =>
    [
        new(string.Empty, L["未知（省略）"]),
        new("true", L["是"]),
        new("false", L["否"]),
    ];

    private IReadOnlyList<SelectOption> UnitOptions =>
    [
        new(string.Empty, L["不指定單位"]),
        new("mg/L", "mg/L"),
        new("ug/L", "μg/L"),
        new("mg/Nm3", "mg/Nm³"),
        new("ppm", "ppm"),
        new("dB(A)", "dB(A)"),
        new("kg", "kg"),
        new("ton", "ton"),
    ];

    private IReadOnlyList<SelectOption> MatrixOptions =>
    [
        new(string.Empty, L["不試算矩陣"]),
        new("water_effluent", L["水污法罰鍰裁罰準則（點數×基數）"]),
        new("air_stationary", L["空污法固定污染源裁罰準則（A×B×C×D×(1+E)）"]),
        new("waste_disposal", L["廢清法罰鍰裁罰準則"]),
        new("construction_demerit", L["營建工程空污設施缺失記點（10點門檻）"]),
    ];

    private IReadOnlyList<StatItem> SummaryStats(PortalReasoningOutcome r) =>
    [
        new(L["裁處時結論"], r.Verdicts.Count.ToString(CultureInfo.CurrentCulture)),
        new(L["期間法律變更組"], r.EarlierCount.ToString(CultureInfo.CurrentCulture)),
        new(L["違憲失效排除"], r.InvalidatedCount.ToString(CultureInfo.CurrentCulture)),
        new(L["授權鏈缺口"], r.DelegationGapCount.ToString(CultureInfo.CurrentCulture)),
        new(L["語料未涵蓋法規"], r.UncoveredLawCount.ToString(CultureInfo.CurrentCulture)),
    ];

    protected override void OnInitialized()
    {
        base.OnInitialized();
        var today = DateOnly.FromDateTime(DateTime.UtcNow.AddHours(8)).ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        if (string.IsNullOrEmpty(Input.ConductOn))
        {
            Input.ConductOn = today;
        }

        if (string.IsNullOrEmpty(Input.DecidedOn))
        {
            Input.DecidedOn = today;
        }
    }

    private async Task EvaluateAsync()
    {
        if (!modules.IsEnabled(Tenant, ModuleKeys.NormativeReasoning))
        {
            return;
        }

        var evaluated = await reasoningService.EvaluateAsync(
            User,
            Input.ToInput(),
            CancellationToken.None);

        if (!evaluated.IsOk)
        {
            Error = evaluated.Localized() ?? L["推論評估失敗，請稍後再試。"];
            return;
        }

        _outcome = evaluated.Value;
    }

    private string VerdictLabel(string verdict) => verdict switch
    {
        "Violation" => L["違反"],
        "ConditionalViolation" => L["附條件違反"],
        "Compliant" => L["合規"],
        "ConditionalCompliant" => L["附條件合規"],
        "Undetermined" => L["事實不足"],
        "Excluded" => L["排除"],
        "NotApplicable" => L["不適用"],
        _ => verdict,
    };

    private static string VerdictTone(string verdict) => verdict switch
    {
        "Violation" => "error",
        "ConditionalViolation" => "warning",
        "Compliant" or "ConditionalCompliant" => "success",
        _ => "neutral",
    };
}