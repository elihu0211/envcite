using EnvCite.Platform.Web.Components.Dialogs;

using Microsoft.AspNetCore.Components;

using MudBlazor;

namespace EnvCite.Platform.Web.Components.Pages;

public sealed partial class Tenants(ITenantDirectory directory)
{
    private IReadOnlyList<TenantSummary>? _tenants;
    private string? _search;

    /// <summary>預先渲染查到的清單交給互動渲染，不再查一次。重新連線不還原：資料可能已經變了，要重查。</summary>
    [PersistentState(RestoreBehavior = RestoreBehavior.SkipLastSnapshot)]
    public IReadOnlyList<TenantSummary>? Prerendered { get; set; }

    protected override Task OnInitializedAsync()
    {
        if (Prerendered is not null)
        {
            (_tenants, Prerendered) = (Prerendered, null);
            return Task.CompletedTask;
        }

        return LoadAsync();
    }

    private async Task LoadAsync()
    {
        Error = null;
        _tenants = null;
        try
        {
            _tenants = await directory.ListAsync(CancellationToken.None);
            if (!RendererInfo.IsInteractive)
            {
                Prerendered = _tenants;
            }
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            Error = exception.GetType().Name;
        }
    }

    private async Task CreateAsync()
    {
        var dialog = await Dialogs.ShowAsync<CreateTenantDialog>(
            L["新增租戶"], new DialogOptions { MaxWidth = MaxWidth.Small, FullWidth = true, CloseButton = true });
        var result = await dialog.Result;
        if (result is { Canceled: false, Data: CreatedTenant created })
        {
            Navigation.NavigateTo($"/ui/tenants/{Uri.EscapeDataString(created.Id)}");
        }
    }

    private bool Matches(TenantSummary tenant) =>
        string.IsNullOrWhiteSpace(_search)
        || tenant.Identifier.Contains(_search, StringComparison.OrdinalIgnoreCase)
        || (tenant.Name?.Contains(_search, StringComparison.OrdinalIgnoreCase) ?? false);
}