using System.Security.Claims;

namespace EnvCite.Platform.SharedKernel;

/// <summary>目錄同步（客戶的身分來源把成員推進租戶）與角色的關係。</summary>
public static class DirectoryRoles
{
    /// <summary><c>ApplicationUser.ManagedBy</c> 的值：這位成員的角色與停用狀態由目錄同步決定，介面上鎖住。</summary>
    public const string ManagedBy = "directory";

    /// <summary>
    /// 同步可授予的角色，最高到管理員。擁有者只由人指派：身分來源設定錯誤（例如整個群組對映到擁有者）
    /// 不能直接變成一批擁有者。
    /// </summary>
    public static IReadOnlyList<string> Assignable { get; } =
        [CustomerRoles.Admin, CustomerRoles.Developer, CustomerRoles.Billing, CustomerRoles.Viewer];
}

/// <param name="Id">成員的 Id（<c>ApplicationUser.Id</c>），也是目錄同步對外的資源 Id。</param>
/// <param name="ExternalId">身分來源給這個人的穩定 Id；還沒連結則 null。</param>
/// <param name="Active">沒有被停用。</param>
/// <param name="Invited">已邀請、還沒設定密碼。</param>
/// <param name="Managed">由目錄同步管理（角色與停用在介面上鎖住）。</param>
public sealed record DirectoryMember(
    string Id, string? ExternalId, string Email, string? DisplayName, string Role, bool Active, bool Invited, bool Managed, DateTimeOffset? SyncedAt);

/// <param name="Role">null = 用租戶設定的預設角色。</param>
/// <param name="Active">false = 建立後停用（不寄邀請信，重新啟用時才寄）。</param>
public sealed record DirectoryProvision(string Email, string? DisplayName = null, string? ExternalId = null, string? Role = null, bool Active = true);

/// <summary>部分更新：null 是「不動」；<paramref name="DisplayName"/> 給空字串是清掉。</summary>
public sealed record DirectoryPatch(
    string? DisplayName = null, bool? Active = null, string? Role = null, string? ExternalId = null, string? Email = null);

public sealed record DirectoryPage(IReadOnlyList<DirectoryMember> Items, int Total);

/// <param name="Role">這個群組對映的角色（只能是 <see cref="DirectoryRoles.Assignable"/>）；沒對映則 null，成員不因它改變角色。</param>
/// <param name="Members">成員的 Id（<see cref="DirectoryMember.Id"/>）。</param>
public sealed record DirectoryGroupDetail(Guid Id, string? ExternalId, string DisplayName, string? Role, IReadOnlyList<string> Members);

public sealed record DirectoryGroupPage(IReadOnlyList<DirectoryGroupDetail> Items, int Total);

public sealed record DirectoryGroupInput(string DisplayName, string? ExternalId = null, IReadOnlyList<string>? Members = null);

public enum GroupMemberOp
{
    /// <summary>加入這些成員。</summary>
    Add,

    /// <summary>移除這些成員（本來就不在的略過，重送因此安全）。</summary>
    Remove,

    /// <summary>成員整組換成這些。</summary>
    Replace,
}

public sealed record DirectoryGroupMemberChange(GroupMemberOp Kind, IReadOnlyList<string> Ids);

/// <summary>部分更新：null 是「不動」；成員的變更依序套用在目前的成員上。</summary>
public sealed record DirectoryGroupPatch(string? DisplayName = null, string? ExternalId = null, IReadOnlyList<DirectoryGroupMemberChange>? Members = null);

/// <summary>管理介面看的群組：不含成員清單，只有人數。</summary>
public sealed record DirectoryGroupInfo(Guid Id, string DisplayName, string? ExternalId, string? Role, int MemberCount);

/// <summary>
/// 身分來源推進來的變更，在<b>目前請求的租戶</b>內執行（租戶來自 token，不是參數）。<c>actor</c> 是帶
/// <see cref="PlatformClaimTypes.DirectoryTokenId"/> 的身分，稽核的「誰」由它推得。每個動作的規則見 <c>services/platform/AGENTS.md</c>
/// 「目錄同步」：冪等、停用不刪、不碰最後一位擁有者、角色最高到管理員、稽核寫不進去就不改成員。
/// 部署者沒開功能或租戶沒開連線時一律 <see cref="OutcomeCodes.DirectoryNotEnabled"/>（NotFound）。以 scoped 註冊。
/// </summary>
public interface IDirectoryProvisioning
{
    /// <summary>列出成員（含手動建立的，讓身分來源能先比對再決定建立或接管）。<paramref name="externalId"/>、<paramref name="email"/> 是精確比對。</summary>
    Task<AdminOutcome<DirectoryPage>> ListAsync(
        ClaimsPrincipal actor, int skip, int take, string? externalId, string? email, CancellationToken cancellationToken);

    Task<AdminOutcome<DirectoryMember>> FindAsync(ClaimsPrincipal actor, string userId, CancellationToken cancellationToken);

    /// <summary>建立（新成員走邀請信，需要寄信啟用）；同 email 的手動成員在租戶允許時接管；重複回 Conflict。</summary>
    Task<AdminOutcome<DirectoryMember>> ProvisionAsync(ClaimsPrincipal actor, DirectoryProvision member, CancellationToken cancellationToken);

    /// <summary>更新受管成員；沒有變化就不寫任何東西。不是受管的成員回 <see cref="OutcomeCodes.DirectoryAdoptRequired"/>。</summary>
    Task<AdminOutcome<DirectoryMember>> UpdateAsync(ClaimsPrincipal actor, string userId, DirectoryPatch patch, CancellationToken cancellationToken);

    /// <summary>停用（刪除也是停用：帳號與稽核保留）。已停用則照舊成功。</summary>
    Task<AdminOutcome<DirectoryMember>> DeactivateAsync(ClaimsPrincipal actor, string userId, CancellationToken cancellationToken);

    /// <summary>列出群組；<paramref name="externalId"/>、<paramref name="displayName"/> 是精確比對（名稱不分大小寫）。</summary>
    Task<AdminOutcome<DirectoryGroupPage>> ListGroupsAsync(
        ClaimsPrincipal actor, int skip, int take, string? externalId, string? displayName, CancellationToken cancellationToken);

    Task<AdminOutcome<DirectoryGroupDetail>> FindGroupAsync(ClaimsPrincipal actor, Guid groupId, CancellationToken cancellationToken);

    /// <summary>
    /// 建立群組。成員必須是這個租戶裡<b>由目錄同步管理</b>的成員，否則整個請求被拒（<see cref="OutcomeCodes.DirectoryGroupMemberInvalid"/>）。
    /// 群組一開始沒有對映角色（由客戶的擁有者設定），所以建立本身不會改任何人的角色。
    /// </summary>
    Task<AdminOutcome<DirectoryGroupDetail>> CreateGroupAsync(ClaimsPrincipal actor, DirectoryGroupInput group, CancellationToken cancellationToken);

    /// <summary>更新群組；成員變動後，受影響成員的角色依群組對映重算（見 README「目錄同步」）。沒有變化就不寫任何東西。</summary>
    Task<AdminOutcome<DirectoryGroupDetail>> UpdateGroupAsync(ClaimsPrincipal actor, Guid groupId, DirectoryGroupPatch patch, CancellationToken cancellationToken);

    /// <summary>刪除群組（群組本身可以刪，成員不受影響，只是少了這個群組給的角色）。</summary>
    Task<AdminOutcome<bool>> DeleteGroupAsync(ClaimsPrincipal actor, Guid groupId, CancellationToken cancellationToken);
}

public sealed record DirectoryTokenInfo(
    Guid Id, string Name, string Prefix, DateTimeOffset CreatedAt, DateTimeOffset? ExpiresAt, DateTimeOffset? RevokedAt, DateTimeOffset? LastUsedAt);

/// <summary>完整的 token 只在簽發時回傳一次；之後再也無法取回。</summary>
public sealed record IssuedDirectoryToken(string Token, DirectoryTokenInfo Info);

/// <param name="Enabled">租戶是否接受目錄同步（另有部署者的總開關）。</param>
/// <param name="DefaultRole">推送沒指定角色時的角色，只能是同步可授予的角色。</param>
/// <param name="AdoptExistingMembers">同 email 的手動成員是否讓同步接管（連結並鎖住角色與停用）。</param>
public sealed record DirectorySettings(bool Enabled, string DefaultRole, bool AdoptExistingMembers);

public sealed record DirectoryStatus(
    DirectorySettings Settings, DateTimeOffset? LastRequestAt, string? LastFailureCode,
    long Created, long Updated, long Deactivated, long Rejected, int ManagedMembers, IReadOnlyList<DirectoryTokenInfo> Tokens,
    IReadOnlyList<DirectoryGroupInfo> Groups);

/// <summary>
/// 營運者看目錄同步的狀態，並在出事時關掉或解除管理。設定與 token 由客戶的擁有者管理（<see cref="IDirectorySelfService"/>）：
/// token 能讓身分來源建立管理員，營運者不該代為持有。每個動作都留稽核。
/// </summary>
public interface IDirectoryAdmin
{
    Task<AdminOutcome<DirectoryStatus>> GetStatusAsync(string tenantId, CancellationToken cancellationToken);

    /// <summary>緊急關閉這個租戶的目錄同步（冪等）。既有成員與 token 不動，推送立刻被拒。</summary>
    Task<AdminOutcome<DirectoryStatus>> DisableAsync(ClaimsPrincipal actor, string tenantId, CancellationToken cancellationToken);

    /// <summary>解除一位成員的管理：之後角色與停用回到人手上，身分來源要重新接管才會再動它。</summary>
    Task<AdminOutcome<MemberInfo>> ReleaseManagedAsync(ClaimsPrincipal actor, string tenantId, string userId, CancellationToken cancellationToken);
}

/// <summary>
/// 客戶自己的擁有者管理目錄同步：開關與設定、簽發與撤銷 token、解除管理。租戶是目前請求的租戶，操作的人以資料庫裡的角色為準；
/// <b>只有擁有者</b>：token 能讓身分來源建立管理員，管理員不能建立管理員，所以也不能持有它。以 scoped 註冊。
/// </summary>
public interface IDirectorySelfService
{
    Task<AdminOutcome<DirectoryStatus>> GetStatusAsync(ClaimsPrincipal actor, CancellationToken cancellationToken);

    Task<AdminOutcome<DirectoryStatus>> ConfigureAsync(ClaimsPrincipal actor, DirectorySettings settings, CancellationToken cancellationToken);

    Task<AdminOutcome<IssuedDirectoryToken>> IssueTokenAsync(
        ClaimsPrincipal actor, string name, DateTimeOffset? expiresAt, CancellationToken cancellationToken);

    Task<AdminOutcome<DirectoryTokenInfo>> RevokeTokenAsync(ClaimsPrincipal actor, Guid tokenId, CancellationToken cancellationToken);

    Task<AdminOutcome<MemberInfo>> ReleaseManagedAsync(ClaimsPrincipal actor, string userId, CancellationToken cancellationToken);

    /// <summary>
    /// 設定一個群組對映的角色（<c>null</c> = 不對映）。只能是 <see cref="DirectoryRoles.Assignable"/>；群組的成員立刻依新的對映重算角色，
    /// 並留稽核。群組由身分來源推送，對映只有客戶的擁有者能決定。
    /// </summary>
    Task<AdminOutcome<DirectoryGroupInfo>> SetGroupRoleAsync(ClaimsPrincipal actor, Guid groupId, string? role, CancellationToken cancellationToken);
}

/// <summary>
/// 目錄同步的功能面有沒有開（<c>Platform:Directory:Enabled</c>），以及連線多久沒收到請求就該警示（<c>StaleAfter</c>）。singleton，只讀設定。
/// 介面據此決定要不要顯示入口與區塊（同 <see cref="IAnswerAvailability"/> 的做法）；真正的存取控制在服務層，不看這裡。
/// </summary>
public interface IDirectoryAvailability
{
    bool Available { get; }

    TimeSpan StaleAfter { get; }
}