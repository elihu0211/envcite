using EnvLaw.Platform.SharedKernel;

namespace EnvLaw.Platform.Api;

/// <summary>主機層的設定（<c>Platform</c> 區段；與 <c>ApiAccessOptions</c> 共用區段，各取各的鍵）。</summary>
public sealed class PlatformHostOptions : IPlatformOptions
{
    public static string SectionName => "Platform";

    /// <summary>啟動時套用遷移。只給 Development 與測試；正式環境用遷移 bundle 在 Job 中套用。</summary>
    public bool ApplyMigrationsOnStartup { get; set; }
}