using System.Globalization;

using Microsoft.Extensions.Localization;

using MudBlazor;

namespace EnvCite.Platform.Web.Presentation;

/// <summary>
/// MudBlazor 自己內建的文字（分頁按鈕的名稱、關閉、清除、必填提示…）的繁體中文。
/// 英文用 MudBlazor 內建的英文：這裡對英文回「找不到」，它就退回預設，所以不必另外維護英文。
/// 只列這個介面用得到的鍵（鍵名是 MudBlazor 的）；沒列到的鍵維持英文。
/// </summary>
public sealed class PlatformMudLocalizer : MudLocalizer
{
    internal static IReadOnlyDictionary<string, string> TraditionalChinese { get; } = new Dictionary<string, string>(StringComparer.Ordinal)
    {
        ["MudTablePager_FirstPage"] = "第一頁",
        ["MudTablePager_PreviousPage"] = "上一頁",
        ["MudTablePager_NextPage"] = "下一頁",
        ["MudTablePager_LastPage"] = "最後一頁",
        ["MudTable_Loading"] = "載入中…",
        ["MudTable_SelectAllRows"] = "選取全部列",
        ["MudTable_SelectRow"] = "選取這一列",
        ["MudDialog_Close"] = "關閉",
        ["MudAlert_Close"] = "關閉",
        ["MudSnackbar_Close"] = "關閉",
        ["MudChip_Close"] = "移除",
        ["MudInput_Clear"] = "清除",
        ["MudSelect_SelectAll"] = "全選",
        ["MudFormComponent_Required"] = "必填",
        ["MudNavGroup_ToggleExpand"] = "展開或收合",
    };

    public override LocalizedString this[string key] =>
        IsTraditionalChinese && TraditionalChinese.TryGetValue(key, out var text)
            ? new LocalizedString(key, text, resourceNotFound: false)
            : new LocalizedString(key, key, resourceNotFound: true);

    private static bool IsTraditionalChinese =>
        CultureInfo.CurrentUICulture.TwoLetterISOLanguageName == "zh";
}