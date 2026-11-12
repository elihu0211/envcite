using System.Globalization;

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// 在信件的語言（<see cref="MailTemplateOptions.Culture"/>）下取文案。只包住同步的取字串，不跨 await，所以暫時換掉目前執行緒的文化不會影響別的工作。
/// 信與 <c>mail test</c> 的說明共用它，語言都不取自請求或操作者的介面。
/// </summary>
internal static class MailCulture
{
    public static T Run<T>(string cultureName, Func<T> work)
    {
        var culture = CultureInfo.GetCultureInfo(cultureName);
        var (current, currentUi) = (CultureInfo.CurrentCulture, CultureInfo.CurrentUICulture);
        (CultureInfo.CurrentCulture, CultureInfo.CurrentUICulture) = (culture, culture);
        try
        {
            return work();
        }
        finally
        {
            (CultureInfo.CurrentCulture, CultureInfo.CurrentUICulture) = (current, currentUi);
        }
    }
}