using Microsoft.Extensions.Logging;

namespace EnvLaw.Platform.Web;

internal static partial class WebLog
{
    [LoggerMessage(
        Level = LogLevel.Warning,
        Message = "The operator UI is disabled: Platform:Ui:Google:ClientId and Platform:Ui:Google:ClientSecret are not set. /ui is not served; the API is unaffected.")]
    public static partial void UiDisabled(ILogger logger);
}