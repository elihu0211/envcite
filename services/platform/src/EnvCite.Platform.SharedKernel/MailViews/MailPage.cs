namespace EnvCite.Platform.SharedKernel.MailViews;

/// <summary>
/// 一封信要畫的內容，文案都已依信件語言解出（元件只管排版，不查資源檔也不編碼：編碼由 Razor 負責）。
/// 純文字版由同一組資料另外產生（<see cref="MailComposer"/>），所以兩版的連結、期限、租戶與提示不會各說各話。
/// </summary>
/// <param name="Language">文件的 <c>lang</c>（來自資源檔，<c>zh-Hant</c> 或 <c>en</c>）。</param>
/// <param name="Brand">系統名稱（<see cref="BrandOptions.Name"/>）：標頭與頁尾。</param>
/// <param name="Title">文件標題（<c>&lt;title&gt;</c>）。</param>
/// <param name="Preheader">收件匣預覽文字，不顯示在信裡。</param>
/// <param name="Heading">信的標題。</param>
/// <param name="Lead">一到兩句說明。</param>
/// <param name="Tenant">「租戶識別碼：…」整行；沒有租戶為 null。</param>
/// <param name="Action">按鈕與備用連結；沒有連結的信（測試信）為 null。</param>
/// <param name="Notes">分隔線下的說明，一行一則。</param>
/// <param name="Footer">頁尾。</param>
/// <param name="Theme">版型的長相（來自 <see cref="MailTemplateOptions"/>）。</param>
public sealed record MailPage(
    string Language,
    string Brand,
    string Title,
    string Preheader,
    string Heading,
    string Lead,
    string? Tenant,
    MailAction? Action,
    IReadOnlyList<string> Notes,
    string Footer,
    MailTheme Theme);

/// <summary>主要按鈕與它的備用說明。<see cref="Url"/> 是整封信唯一的外部網址。</summary>
public sealed record MailAction(string Url, string Label, string FallbackLabel);