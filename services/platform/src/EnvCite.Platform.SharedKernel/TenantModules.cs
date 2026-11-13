using System.Collections.Frozen;
using System.Security.Claims;

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// 租戶可啟用的領域功能模組識別碼。模組是編譯期定義（不做執行期載入組件），
/// 營運者可在執行期針對個別租戶動態覆寫開關。
/// </summary>
public static class ModuleKeys
{
    /// <summary>法規條文檢索（字面／語意／函釋帶出）。</summary>
    public const string LawSearch = "law-search";

    /// <summary>法規沿革與條文瀏覽（含時點版本與違憲失效）。</summary>
    public const string LawCorpus = "law-corpus";

    /// <summary>法源接地（產出封閉證據集，不經外部模型）。</summary>
    public const string EvidenceGrounding = "evidence-grounding";

    /// <summary>AI 法源問答（經外部模型產生有出處的回答；需環境設定與租戶同意）。</summary>
    public const string GroundedAnswer = "grounded-answer";

    /// <summary>規範推論評估（Clingo 確定性涵攝與授權鏈檢查）。</summary>
    public const string NormativeReasoning = "normative-reasoning";
}

/// <summary>單一功能模組的靜態規格：分類、前置依賴、環境前提與預設啟用策略。</summary>
public sealed record ModuleDescriptor(
    string Key,
    string Category,
    IReadOnlyList<string> Dependencies,
    bool RequiresGlobalAvailability = false,
    bool OptInByDefault = false);

/// <summary>編譯期註冊的所有租戶功能模組目錄。</summary>
public static class ModuleCatalog
{
    public static IReadOnlyList<ModuleDescriptor> All { get; } =
    [
        new(ModuleKeys.LawSearch, "ontology", []),
        new(ModuleKeys.LawCorpus, "ontology", []),
        new(ModuleKeys.EvidenceGrounding, "ontology", []),
        new(ModuleKeys.GroundedAnswer, "ontology", [ModuleKeys.EvidenceGrounding], RequiresGlobalAvailability: true, OptInByDefault: true),
        new(ModuleKeys.NormativeReasoning, "ontology", []),
    ];

    private static readonly FrozenDictionary<string, ModuleDescriptor> ByKey =
        All.ToFrozenDictionary(m => m.Key, StringComparer.Ordinal);

    public static ModuleDescriptor? Find(string key) => ByKey.GetValueOrDefault(key);

    public static bool IsKnown(string key) => ByKey.ContainsKey(key);
}

public enum TenantModuleSource
{
    PlanDefault,
    OperatorEnabled,
    OperatorDisabled,
}

/// <summary>單一租戶下某模組的解析後狀態。</summary>
public sealed record TenantModuleStatus(
    string Key,
    bool Enabled,
    TenantModuleSource Source,
    bool GloballyAvailable,
    IReadOnlyList<string> UnmetDependencies);

/// <summary>查詢租戶在目前環境與方案下實際啟用的模組（快速路徑，由記憶體快照讀取）。</summary>
public interface ITenantModules
{
    bool IsEnabled(string tenantIdentifier, string moduleKey);

    IReadOnlySet<string> EnabledModules(string tenantIdentifier);
}

/// <summary>營運者針對個別租戶動態開關功能模組（每次變更與稽核同交易）。</summary>
public interface IModuleAdmin
{
    Task<AdminOutcome<IReadOnlyList<TenantModuleStatus>>> ListAsync(string tenantId, CancellationToken cancellationToken);

    /// <param name="enabled"><c>true</c> 覆寫開啟、<c>false</c> 覆寫關閉、<c>null</c> 重設為方案預設。</param>
    Task<AdminOutcome<TenantModuleStatus>> SetAsync(
        ClaimsPrincipal actor, string tenantId, string moduleKey, bool? enabled, CancellationToken cancellationToken);
}