using Microsoft.Extensions.Hosting;

namespace EnvCite.Platform.Web.Auth;

/// <summary>
/// 哪些介面面向（後台、客戶入口、元件展示頁）在這個環境存在。三個中介層與路由對映都問這一處，
/// 「Development 一律開著、其他環境看設定」的規則才不會各自重寫一份而漸漸不一致。
/// </summary>
internal sealed class PlatformSurfaces(IOptions<UiOptions> ui, IOptions<PortalOptions> portal, IHostEnvironment environment)
{
    public bool OperatorUi => ui.Value.Enabled || environment.IsDevelopment();

    public bool Portal => portal.Value.Enabled || environment.IsDevelopment();

    /// <summary>元件展示頁只在 Development，其他環境不存在。</summary>
    public bool DesignGallery => environment.IsDevelopment();

    public bool Any => OperatorUi || Portal;
}