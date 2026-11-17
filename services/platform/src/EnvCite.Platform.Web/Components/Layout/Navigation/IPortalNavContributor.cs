using System.Security.Claims;

using Microsoft.Extensions.DependencyInjection.Extensions;

namespace EnvCite.Platform.Web.Components.Layout.Navigation;

/// <param name="Home">這個租戶的入口首頁路徑（<see cref="PortalPaths.Home"/>）；各功能的頁面都在它底下。</param>
/// <param name="User">目前的使用者；項目依角色出現與否由各來源自己決定（顯示與否不是授權，頁面與服務層另有檢查）。</param>
public sealed record PortalNavContext(string Home, ClaimsPrincipal User);

/// <summary>項目放在外殼的哪裡：側欄只放業務模塊；租戶的系統設定（成員、目錄同步、帳號與組織）收在頭像的帳號選單。</summary>
public enum PortalNavSlot
{
    Sidebar,
    AccountMenu,
}

/// <summary>
/// 入口導覽的一個來源。每個功能自己宣告它的項目放在側欄或帳號選單、在哪一群、排第幾；外殼（<c>PortalLayout</c>）不認識任何功能，
/// 新增功能只要實作這個介面並註冊，不必改外殼。以 <see cref="PortalNavigation.AddPortalNavContributor{T}"/> 註冊。
/// </summary>
public interface IPortalNavContributor
{
    /// <summary>項目放在哪裡：業務功能放側欄，要看角色才管得了的租戶設定放帳號選單。</summary>
    PortalNavSlot Slot { get; }

    /// <summary>
    /// 在同一個位置（<see cref="Slot"/>）裡的順序，小的在前。群組依第一個項目出現的順序排列，所以 Order 依群組分段：
    /// 側欄的服務群組 10–29、帳號選單的系統設定群組 30–59；新來源取同群組的空檔。
    /// </summary>
    int Order { get; }

    /// <summary>這個使用者在這個租戶看得到的項目；沒有就回空。</summary>
    IEnumerable<PortalShell.NavItem> Items(PortalNavContext context);
}

public static class PortalNavigation
{
    /// <summary>註冊一個導覽來源。scoped：有些來源要讀 scoped 的服務（例如問答歷史是否開啟）。</summary>
    public static IServiceCollection AddPortalNavContributor<T>(this IServiceCollection services)
        where T : class, IPortalNavContributor
    {
        services.TryAddEnumerable(ServiceDescriptor.Scoped<IPortalNavContributor, T>());
        return services;
    }

    /// <summary>把放在 <paramref name="slot"/> 的來源，依 <see cref="IPortalNavContributor.Order"/> 排成項目清單。外殼與測試都經這一條。</summary>
    public static IReadOnlyList<PortalShell.NavItem> Collect(IEnumerable<IPortalNavContributor> contributors, PortalNavContext context, PortalNavSlot slot) =>
        [.. contributors.Where(c => c.Slot == slot).OrderBy(c => c.Order).SelectMany(c => c.Items(context))];

    /// <summary>入口自帶的四個來源。</summary>
    internal static IServiceCollection AddPortalNavigation(this IServiceCollection services) => services
        .AddPortalNavContributor<LawWorkbenchNav>()
        .AddPortalNavContributor<MembersNav>()
        .AddPortalNavContributor<DirectoryNav>()
        .AddPortalNavContributor<AccountNav>();
}