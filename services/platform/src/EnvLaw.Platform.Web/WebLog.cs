using Microsoft.Extensions.Logging;

namespace EnvLaw.Platform.Web;

internal static partial class WebLog
{
    [LoggerMessage(
        Level = LogLevel.Information,
        Message = "The operator UI is disabled: Platform:Ui:Enabled is false. /ui is not served; the API is unaffected.")]
    public static partial void UiDisabled(ILogger logger);
}