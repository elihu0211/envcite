using EnvCite.Platform.Web.Resources;

using Microsoft.AspNetCore.Components;
using Microsoft.Extensions.Localization;

namespace EnvCite.Platform.Web.Components.Bases;

/// <summary>
/// 所有元件的基底：本地化（<see cref="L"/>）與系統名稱（<see cref="Brand"/>）。<c>Components/_Imports.razor</c> 以 <c>@inherits</c> 套給每個元件，
/// 元件不再各自 <c>@inject</c>，也不各自決定怎麼取文字。需要導覽或頁面、對話框、版面能力的元件用 <c>@inherits</c> 換成它的衍生類別。
/// </summary>
public abstract class PlatformComponentBase : ComponentBase
{
    [Inject] protected IStringLocalizer<Ui> L { get; set; } = default!;

    [Inject] protected BrandText Brand { get; set; } = default!;
}

/// <summary>需要導覽的元件基底：共用 <see cref="Navigation"/>（頁面、分頁、語言切換與入口外殼共用）。</summary>
public abstract class NavigableComponentBase : PlatformComponentBase
{
    [Inject] protected NavigationManager Navigation { get; set; } = default!;
}

/// <summary>版面元件的基底：同樣的 <see cref="L"/>、<see cref="Brand"/> 與 <see cref="Navigation"/>。</summary>
public abstract class PlatformLayoutBase : LayoutComponentBase
{
    [Inject] protected IStringLocalizer<Ui> L { get; set; } = default!;

    [Inject] protected BrandText Brand { get; set; } = default!;

    [Inject] protected NavigationManager Navigation { get; set; } = default!;
}