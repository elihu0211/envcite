using Microsoft.AspNetCore.Components;

using MudBlazor;

namespace EnvCite.Platform.Web.Components.Shared;

public sealed partial class AppearanceSetting(IAppearancePreference preference)
{
    private sealed record Choice(Appearance Value, string Label, string Hint, string Icon);

    /// <summary>換外觀的端點（營運者後台與客戶入口各一個）。</summary>
    [Parameter, EditorRequired]
    public string Action { get; set; } = string.Empty;

    private Appearance _current;

    private string ReturnUrl => "/" + Navigation.ToBaseRelativePath(Navigation.Uri);

    protected override void OnInitialized() => _current = preference.Read(HttpContext);

    private static string HintId(Appearance appearance) => $"ep-appearance-hint-{appearance.Name()}";

    private Choice[] Options =>
    [
        new(Appearance.System, L["系統"], L["跟隨裝置的外觀"], Icons.Material.Outlined.SettingsBrightness),
        new(Appearance.Light, L["亮色"], L["一律使用亮色外觀"], Icons.Material.Outlined.LightMode),
        new(Appearance.Dark, L["暗色"], L["一律使用暗色外觀"], Icons.Material.Outlined.DarkMode),
    ];
}