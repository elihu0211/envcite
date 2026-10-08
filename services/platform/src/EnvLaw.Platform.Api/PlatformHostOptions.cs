using EnvLaw.Platform.SharedKernel;

namespace EnvLaw.Platform.Api;

/// <summary>主機層的設定（<c>Platform</c> 區段；與 <c>ApiAccessOptions</c> 共用區段，各取各的鍵）。</summary>
public sealed class PlatformHostOptions : IPlatformOptions
{
    public static string SectionName => "Platform";

    /// <summary>啟動時套用遷移。只給 Development 與測試；正式環境用遷移 bundle 在 Job 中套用。</summary>
    public bool ApplyMigrationsOnStartup { get; set; }

    /// <summary>
    /// 啟動時種一個 <c>dev</c> 租戶與它的第一位擁有者（<see cref="DevelopmentSeed"/>）。只在 Development 環境有效：
    /// 其他環境即使打開也不會執行，正式環境的第一位擁有者由營運者經 <c>/admin</c> 建立。
    /// </summary>
    public bool SeedDevelopmentData { get; set; }
}