namespace EnvLaw.Platform.SharedKernel;

/// <summary>
/// design-time factory（EF Core 工具、遷移 bundle）的連線字串：與應用程式同一個設定鍵
/// （<c>ConnectionStrings__Platform</c>），未設定時給一個不會被連線的佔位值。
/// </summary>
public static class DesignTimeConnection
{
    public static string Get() =>
        Environment.GetEnvironmentVariable("ConnectionStrings__Platform") ?? "Host=localhost;Database=platform";
}