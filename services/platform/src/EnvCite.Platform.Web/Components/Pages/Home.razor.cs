using System.Globalization;

using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;

namespace EnvCite.Platform.Web.Components.Pages;

public sealed partial class Home(ITenantDirectory directory, IOperatorAdmin operators, IAuditQuery auditQuery)
{
    private const int RecentCount = 8;

    private IReadOnlyList<TenantSummary> _tenants = [];
    private IReadOnlyList<OperatorInfo> _operators = [];
    private IReadOnlyList<TenantSummary> _withoutPlan = [];
    private IReadOnlyList<OperatorInfo> _pendingOperators = [];
    private IReadOnlyList<AuditRecord> _recent = [];

    public sealed record Overview(IReadOnlyList<TenantSummary> Tenants, IReadOnlyList<OperatorInfo> Operators, IReadOnlyList<AuditRecord> Recent);

    /// <summary>預先渲染查到的資料交給互動渲染，不再查一次。重新連線不還原：資料可能已經變了，要重查。</summary>
    [PersistentState(RestoreBehavior = RestoreBehavior.SkipLastSnapshot)]
    public Overview? Prerendered { get; set; }

    protected override Task OnInitializedAsync()
    {
        if (Prerendered is { } overview)
        {
            Prerendered = null;
            Show(overview);
            return Task.CompletedTask;
        }

        return LoadAsync();
    }

    private Task LoadAsync() => LoadingAsync(async () =>
    {
        var tenants = await directory.ListAsync(CancellationToken.None);
        var loadedOperators = await operators.ListAsync(CancellationToken.None);
        if (!loadedOperators.IsOk)
        {
            Error = loadedOperators.Localized() ?? L["載入失敗。"];
            return;
        }

        var recent = await auditQuery.QueryAsync(new AuditFilter(Limit: RecentCount), CancellationToken.None);

        var overview = new Overview(tenants, loadedOperators.Value!, recent.Items);
        if (!RendererInfo.IsInteractive)
        {
            Prerendered = overview;
        }

        Show(overview);
    });

    private IReadOnlyList<StatItem> Stats =>
    [
        new(L["租戶"], _tenants.Count.ToString(CultureInfo.CurrentCulture),
            _withoutPlan.Count == 0 ? L["都有方案"] : L["{0} 個沒有方案", _withoutPlan.Count], "/ui/tenants"),
        new(L["營運者"], _operators.Count(o => !o.Disabled).ToString(CultureInfo.CurrentCulture),
            _pendingOperators.Count == 0 ? L["都已完成設定"] : L["{0} 位需要處理", _pendingOperators.Count], "/ui/operators"),
        new(L["最近的登入失敗"], _recent.Count(r => AuditLabels.ActionTone(r.Action) == "error").ToString(CultureInfo.CurrentCulture),
            L["最近 {0} 筆事件中", _recent.Count], "/ui/audit"),
    ];

    private void Show(Overview overview)
    {
        Loading = false;
        _tenants = overview.Tenants;
        _operators = overview.Operators;
        _withoutPlan = [.. overview.Tenants.Where(t => t.Plan is null).OrderBy(t => t.Identifier, StringComparer.Ordinal)];
        _pendingOperators = [.. overview.Operators.Where(o => !o.Disabled && OperatorLabels.Tone(o) != "success")];
        _recent = overview.Recent;
    }
}