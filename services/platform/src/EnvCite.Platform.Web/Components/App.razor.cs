using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Routing;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Components;

public sealed partial class App(IAppearancePreference preference)
{
    [CascadingParameter]
    private HttpContext HttpContext { get; set; } = default!;

    private Appearance _appearance;

    protected override void OnInitialized() => _appearance = preference.Read(HttpContext);

    /// <summary>手動選的外觀，要輸出到 <c>&lt;html data-theme&gt;</c>；跟隨系統是 null，屬性整個不輸出。</summary>
    private string? ChosenTheme => _appearance == Appearance.System ? null : _appearance.Name();

    private const string FollowSystem = "light dark";

    private string ColorScheme => _appearance == Appearance.System ? FollowSystem : _appearance.Name();

    /// <summary>跟隨系統時各自對應系統；手動選了，選的那一個對所有人生效、另一個關掉。</summary>
    private string ThemeColorMedia(Appearance scheme) => _appearance switch
    {
        Appearance.System => $"(prefers-color-scheme: {scheme.Name()})",
        _ => _appearance == scheme ? "all" : "not all",
    };

    /// <summary>
    /// 營運後台是互動的（Interactive Server）；標了 <c>[ExcludeFromInteractiveRouting]</c> 的頁面（客戶入口）是靜態伺服器端渲染：
    /// 每個請求自己解析租戶、自己驗證，不開 circuit，登入表單就是一個普通的 POST。
    /// </summary>
    internal const string RobotoLatin = "_content/EnvCite.Platform.Web/fonts/roboto/roboto-latin-wght-normal.woff2";

    private IComponentRenderMode? PageRenderMode => HttpContext.AcceptsInteractiveRouting() ? RenderMode.InteractiveServer : null;
}