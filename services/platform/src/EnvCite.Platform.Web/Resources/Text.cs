using System.Globalization;
using System.Resources;

[assembly: NeutralResourcesLanguage("zh-TW")]

namespace EnvCite.Platform.Web.Resources;

/// <summary>
/// 依目前請求的介面語言（<see cref="CultureInfo.CurrentUICulture"/>，由 request localization 設定）取標籤。
/// 標籤對照表是純函式，到處被呼叫，所以直接用 <see cref="ResourceManager"/>（<c>IStringLocalizer</c> 底下也是它），
/// 不經 DI；元件裡的文字則用注入的 <c>IStringLocalizer&lt;Labels&gt;</c>。
/// 找不到的鍵回傳 null，由呼叫端決定退路（例如未知的稽核事件直接顯示它的鍵，不隱藏）。
/// </summary>
public static class Text
{
    private static readonly ResourceManager Manager = new(typeof(Labels));

    public static string? Find(string key) => Manager.GetString(key, CultureInfo.CurrentUICulture);

    public static string Format(string key, params object[] args) =>
        string.Format(CultureInfo.CurrentCulture, Find(key) ?? key, args);
}