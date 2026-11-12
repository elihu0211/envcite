using System.Resources;

[assembly: NeutralResourcesLanguage("zh-TW")]

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// 信件文案的資源檔（<c>MailTexts.resx</c> 是預設的繁體中文，<c>MailTexts.en.resx</c> 是英文）。這個類別只是資源的名稱標記，
/// 讓 <c>IStringLocalizer&lt;MailTexts&gt;</c> 找得到同一組檔案。鍵是領域鍵（<c>Mail.Heading.Invitation</c>），不是文案本身；
/// 有變數的文字用 <c>{0}</c>。兩個語言的鍵與參數個數一致，由測試守住。信件的語言由 <see cref="MailTemplateOptions.Culture"/> 決定，不隨收到請求的語言。
/// </summary>
public sealed class MailTexts;