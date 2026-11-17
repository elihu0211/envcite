using System.Reflection;

namespace EnvCite.Platform.Api;

/// <summary>
/// 這個映像建自哪一個 commit。映像建置時以 <c>SourceRevisionId</c> 帶入（Dockerfile 的 <c>SOURCE_REVISION</c>），
/// SDK 把它接在資訊版本後面（<c>1.0.0+&lt;sha&gt;</c>）；沒帶（本機建置、測試）就沒有。
/// 部署的驗證靠它確認「執行中的版本就是清單裡的 tag」：滾動卡住時舊 pod 照常服務，探針看不出來。
/// </summary>
internal static class BuildRevision
{
    public static string? Current { get; } =
        Parse(typeof(BuildRevision).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion);

    /// <summary>取出資訊版本裡 <c>+</c> 之後的部分；沒有就是沒有。</summary>
    internal static string? Parse(string? informationalVersion)
    {
        var plus = informationalVersion?.IndexOf('+', StringComparison.Ordinal) ?? -1;
        return plus >= 0 && plus + 1 < informationalVersion!.Length ? informationalVersion[(plus + 1)..] : null;
    }
}