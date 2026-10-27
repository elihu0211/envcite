using System.Text.Json;

using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;

namespace EnvCite.Platform.Web.Components.Pages;

public partial class Audit
{
    private static readonly JsonSerializerOptions Indented = new() { WriteIndented = true, Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping };

    [Inject] private IAuditQuery Query { get; set; } = default!;

    [Inject] private ITenantDirectory Directory { get; set; } = default!;

    [Inject] private NavigationManager Navigation { get; set; } = default!;

    // 條件的唯一來源是網址：套用只是改網址，參數一變就重新載入。
    [SupplyParameterFromQuery(Name = "tenant")] private string? TenantParam { get; set; }

    [SupplyParameterFromQuery(Name = "action")] private string? ActionParam { get; set; }

    [SupplyParameterFromQuery(Name = "actor")] private string? ActorParam { get; set; }

    private Dictionary<string, TenantSummary> _tenantsById = [];
    private readonly List<AuditRecord> _items = [];
    private readonly HashSet<Guid> _expanded = [];
    private Guid? _next;
    private bool _loading = true;
    private bool _loadingMore;
    private bool _dense;
    private string? _error;
    private string? _applied;

    // 表單欄位（尚未套用的輸入）。
    private string? _tenant;
    private string? _action;
    private string? _actor;

    private bool HasFilter => !string.IsNullOrEmpty(TenantParam) || !string.IsNullOrEmpty(ActionParam) || !string.IsNullOrEmpty(ActorParam);

    private string Status => _error is not null || _loading ? string.Empty
        : _items.Count == 0 ? L["沒有符合的事件"]
        : _next is null ? L["共 {0} 筆", _items.Count] : L["已載入 {0} 筆，還有更多", _items.Count];

    protected override async Task OnInitializedAsync()
    {
        try
        {
            _tenantsById = (await Directory.ListAsync(CancellationToken.None)).ToDictionary(t => t.Id, StringComparer.Ordinal);
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            _tenantsById = []; // 租戶名稱只是輔助顯示；清單載入失敗時事件仍可看，租戶欄退回 Id。
        }
    }

    protected override async Task OnParametersSetAsync()
    {
        var signature = $"{TenantParam}|{ActionParam}|{ActorParam}";
        if (signature == _applied)
        {
            return;
        }

        _applied = signature;
        (_tenant, _action, _actor) = (TenantParam, ActionParam, ActorParam);
        await LoadAsync(append: false);
    }

    private void Apply() => Navigation.NavigateTo(Navigation.GetUriWithQueryParameters(new Dictionary<string, object?>
    {
        ["tenant"] = Nullable(_tenant),
        ["action"] = Nullable(_action),
        ["actor"] = Nullable(_actor),
    }));

    private void Reset() => Navigation.NavigateTo(Navigation.GetUriWithQueryParameters(new Dictionary<string, object?>
    {
        ["tenant"] = null,
        ["action"] = null,
        ["actor"] = null,
    }));

    private Task LoadMoreAsync() => LoadAsync(append: true);

    private async Task LoadAsync(bool append)
    {
        _error = null;
        if (append)
        {
            _loadingMore = true;
        }
        else
        {
            _loading = true;
            _items.Clear();
            _expanded.Clear();
            _next = null;
        }

        try
        {
            var page = await Query.QueryAsync(
                new AuditFilter(Nullable(TenantParam), Nullable(ActionParam), Nullable(ActorParam), append ? _next : null),
                CancellationToken.None);
            _items.AddRange(page.Items);
            _next = page.NextCursor;
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            _error = exception.GetType().Name;
        }
        finally
        {
            _loading = false;
            _loadingMore = false;
        }
    }

    private void Toggle(Guid id)
    {
        if (!_expanded.Remove(id))
        {
            _expanded.Add(id);
        }
    }

    private string TenantLabel(string? tenantId) =>
        tenantId is null ? "—"
        : _tenantsById.TryGetValue(tenantId, out var tenant) ? tenant.Identifier
        : AuditLabels.Shorten(tenantId);

    private string Pretty(JsonElement detail) =>
        detail.ValueKind == JsonValueKind.Object && detail.EnumerateObject().Any()
            ? JsonSerializer.Serialize(detail, Indented)
            : L["（沒有額外內容）"];

    private static string? Nullable(string? value) => string.IsNullOrWhiteSpace(value) ? null : value.Trim();
}