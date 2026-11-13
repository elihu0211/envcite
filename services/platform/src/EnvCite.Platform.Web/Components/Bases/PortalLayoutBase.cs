using System.Security.Claims;

using EnvCite.Platform.Web.Components.Layout;
using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;

namespace EnvCite.Platform.Web.Components.Bases;

/// <summary>
/// 客戶入口外殼的兩個版面（靜態的 <c>PortalLayout</c> 與互動式的 <c>WorkspaceLayout</c>）共用的部分：
/// 頭像的字、帳號選單、側欄的最近對話。兩個版面只在「身分與租戶從哪裡來」不同：靜態的靠請求解析出的環境租戶，
/// 互動式的（沒有環境租戶）取自已驗證的身分。
/// </summary>
public abstract class PortalLayoutBase : PlatformLayoutBase
{
    /// <summary>側欄最近對話的筆數：夠找到剛才問的，更早的在「對話紀錄」頁。</summary>
    private const int RecentLimit = 12;

    [Inject] protected IAskService Ask { get; set; } = default!;

    [Inject] protected TimeProvider Time { get; set; } = default!;

    /// <summary>頭像圓鈕上的一個字（名字或 email 的第一個字）。</summary>
    protected static string AvatarOf(ClaimsPrincipal? user)
    {
        var name = user?.FindFirst(ClaimTypes.GivenName)?.Value ?? user?.FindFirst(ClaimTypes.Email)?.Value;
        return string.IsNullOrEmpty(name) ? string.Empty : char.ConvertFromUtf32(char.ConvertToUtf32(name, 0)).ToUpperInvariant();
    }

    /// <summary>已登入、而且沒有待辦的強制步驟（改初始密碼、註冊驗證器）才有選單：那兩步只開一頁，其他連結點了也會被導回去。</summary>
    protected static bool CanUseMenu(ClaimsPrincipal? user) =>
        user?.Identity?.IsAuthenticated == true
        && user.FindFirst(PlatformClaimTypes.PasswordChangeRequired) is null
        && user.FindFirst(PlatformClaimTypes.MfaEnrollmentRequired) is null;

    protected IReadOnlyList<PortalShell.MenuItem> MenuItemsOf(string tenantIdentifier) =>
    [
        new($"{PortalPaths.Home(tenantIdentifier)}/settings", L["個人設定"]),
        new($"{PortalPaths.Home(tenantIdentifier)}/security", L["兩步驟驗證"]),
        new(PortalPaths.Password(tenantIdentifier), L["改密碼"]),
    ];

    /// <summary>
    /// 側欄的最近對話（已依日期分組，標題是使用者的資料、不翻譯）。問答歷史沒開啟時服務回空的（不開 scope、不查資料庫）；
    /// 只讀本人的對話標題。
    /// </summary>
    protected async Task<IReadOnlyList<PortalShell.RecentGroup>> RecentOfAsync(ClaimsPrincipal user, string tenantIdentifier) =>
    [
        .. ConversationGroups
            .Group(await Ask.RecentAsync(user, RecentLimit, CancellationToken.None), c => c.UpdatedAt, Time.GetUtcNow())
            .Select(g => new PortalShell.RecentGroup(
                AgeLabel(g.Age),
                [.. g.Items.Select(c => new PortalShell.RecentConversation(PortalPaths.Conversation(tenantIdentifier, c.Id), c.Title))])),
    ];

    private string AgeLabel(ConversationAge age) => age switch
    {
        ConversationAge.Today => L["今天"],
        ConversationAge.Yesterday => L["昨天"],
        ConversationAge.PastWeek => L["過去 7 天"],
        _ => L["更早"],
    };
}