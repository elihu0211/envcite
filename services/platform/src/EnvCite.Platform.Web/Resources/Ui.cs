namespace EnvCite.Platform.Web.Resources;

/// <summary>
/// 頁面與對話框的文字資源（<c>Ui.en.resx</c> 是英文；<c>Ui.resx</c> 只是空的預設檔，讓資源管理員找得到清單）。
/// 以預設語言（繁體中文）的文字當鍵：<c>L["租戶"]</c>；英文檔把同一個鍵對到英文，沒有對到的鍵就顯示鍵本身（中文）。
/// 有變數的文字用 <c>{0}</c>：<c>L["撤銷 {0}", name]</c>。來源裡的每個鍵都要出現在英文檔，由測試守住。
/// 畫面標籤依領域鍵查的（稽核事件、角色…）在 <see cref="Labels"/>。
/// </summary>
public sealed class Ui;