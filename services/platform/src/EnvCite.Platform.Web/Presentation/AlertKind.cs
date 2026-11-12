namespace EnvCite.Platform.Web.Presentation;

/// <summary>訊息的語氣（<c>FormAlert</c>）：決定樣式與無障礙角色（錯誤是 alert，其他是 status）。</summary>
public enum AlertKind
{
    Neutral,
    Info,
    Success,
    Warning,
    Error,
}