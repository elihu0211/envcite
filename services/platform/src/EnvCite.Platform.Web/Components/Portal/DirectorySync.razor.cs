using System.Globalization;

using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Components.Portal;

public partial class DirectorySync(IDirectorySelfService directory, IDirectoryAvailability availability, TimeProvider time)
{
    [SupplyParameterFromForm(FormName = "portal-directory-settings")] private SettingsForm Settings { get; set; } = default!;

    [SupplyParameterFromForm(FormName = "portal-directory-token")] private TokenForm Token { get; set; } = default!;

    [SupplyParameterFromForm(FormName = "portal-directory-action")] private ActionForm Input { get; set; } = default!;

    private bool _available;
    private DirectoryStatus? _status;
    private string? _issued;
    private string? _message;

    private string BaseUrl => new Uri(new Uri(Navigation.BaseUri), ScimPaths.Prefix).AbsoluteUri;

    private static IEnumerable<SelectOption> RoleOptions =>
        DirectoryRoles.Assignable.Select(role => new SelectOption(role, PortalLabels.RoleWithAbilities(role)));

    private IEnumerable<SelectOption> ExpiryOptions =>
    [
        new("0", L["不會到期"]),
        new("30", L["30 天"]),
        new("90", L["90 天"]),
        new("365", L["1 年"]),
    ];

    protected override async Task OnInitializedAsync()
    {
        Settings ??= new();
        Token ??= new();
        Input ??= new();
        _available = availability.Available;
        if (!_available)
        {
            return;
        }

        await LoadAsync();
        if (_status is not null && !HttpContext.Request.HasFormContentType)
        {
            Settings = new() { Enabled = _status.Settings.Enabled, DefaultRole = _status.Settings.DefaultRole, Adopt = _status.Settings.AdoptExistingMembers };
        }
    }

    /// <summary>不是擁有者（或功能面沒開）時 <c>_status</c> 是 null，頁面只說明，不露出任何租戶的設定。</summary>
    private async Task LoadAsync()
    {
        var read = await directory.GetStatusAsync(HttpContext.User, HttpContext.RequestAborted);
        _status = read.IsOk ? read.Value : null;
    }

    private async Task SaveSettingsAsync()
    {
        var saved = await directory.ConfigureAsync(
            HttpContext.User, new DirectorySettings(Settings.Enabled, Settings.DefaultRole, Settings.Adopt), HttpContext.RequestAborted);
        Report(saved, L["已儲存設定。"]);
        await LoadAsync();
    }

    private async Task IssueAsync()
    {
        var days = int.TryParse(Token.ExpiresInDays, NumberStyles.None, CultureInfo.InvariantCulture, out var parsed) ? parsed : 0;
        var issued = await directory.IssueTokenAsync(
            HttpContext.User, Token.Name, days > 0 ? time.GetUtcNow().AddDays(days) : null, HttpContext.RequestAborted);
        if (issued.IsOk)
        {
            _issued = issued.Value!.Token;
            Token = new();
        }
        else
        {
            Error = Describe(issued);
        }

        await LoadAsync();
    }

    private async Task ActAsync()
    {
        var action = Input.Action ?? string.Empty;
        var split = action.IndexOf(':', StringComparison.Ordinal);
        var (verb, id) = split < 0 ? (action, string.Empty) : (action[..split], action[(split + 1)..]);
        if (verb == "revoke" && Guid.TryParse(id, out var tokenId))
        {
            Report(await directory.RevokeTokenAsync(HttpContext.User, tokenId, HttpContext.RequestAborted), L["已撤銷這把 token，用它的身分來源立刻被拒絕。"]);
        }
        else
        {
            Error = L["不認得這個動作。"];
        }

        await LoadAsync();
    }

    private void Report<T>(AdminOutcome<T> outcome, string success)
    {
        if (outcome.IsOk)
        {
            _message = success;
        }
        else
        {
            Error = Describe(outcome);
        }
    }

    private string Describe<T>(AdminOutcome<T> outcome) => outcome.Localized() ?? L["操作失敗。"];

    public sealed class SettingsForm
    {
        public bool Enabled { get; set; }

        public string DefaultRole { get; set; } = CustomerRoles.Viewer;

        public bool Adopt { get; set; }
    }

    public sealed class TokenForm
    {
        public string Name { get; set; } = string.Empty;

        public string ExpiresInDays { get; set; } = "0";
    }

    public sealed class ActionForm
    {
        public string? Action { get; set; }
    }
}