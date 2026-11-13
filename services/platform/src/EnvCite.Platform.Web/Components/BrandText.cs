using EnvCite.Platform.Web.Resources;

using Microsoft.Extensions.Localization;

namespace EnvCite.Platform.Web.Components;

/// <summary>
/// 介面上的系統名稱：<c>Platform:Brand:Name</c> 是唯一來源，標頭與頁面標題都經這裡取，不在元件裡寫死名稱。
/// 預設名稱有英文對照（<c>Ui.en.resx</c> 的「環境知識」→ EnvCite），所以預設值下兩種語言的畫面與改名前相同；
/// 設定了別的名稱就兩種語言都原樣顯示它，不會留下一半舊名稱。
/// 帶名稱的頁面標題用格式鍵：<c>L["登入 · {0}", Brand.Name]</c>。
/// </summary>
public sealed class BrandText(IOptions<BrandOptions> options, IStringLocalizer<Ui> L)
{
    public string Name => options.Value.Name == BrandOptions.DefaultName ? L["環境知識"] : options.Value.Name;
}