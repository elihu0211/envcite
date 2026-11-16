using System.Reflection;

namespace EnvCite.Platform.Api;

/// <summary>這個行程是以什麼身分在執行。</summary>
internal static class PlatformHost
{
    /// <summary>
    /// 建置時產生 OpenAPI 規格的模擬伺服器（進入組件為 <c>GetDocument.Insider</c>）只建出端點、不連線，
    /// 所以啟動時的資料庫、遷移與種子資料都要略過，設定檢查用占位值通過。
    /// </summary>
    public static bool IsGeneratingOpenApi { get; } = Assembly.GetEntryAssembly()?.GetName().Name == "GetDocument.Insider";

    /// <summary>占位的連線字串：只為通過啟動時的設定檢查，不會真的連線。</summary>
    public static IReadOnlyDictionary<string, string?> OpenApiPlaceholders { get; } = new Dictionary<string, string?>
    {
        ["ConnectionStrings:Platform"] = "Host=build-time",
    };
}