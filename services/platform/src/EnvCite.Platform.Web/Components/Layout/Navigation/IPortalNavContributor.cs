using System.Security.Claims;

using Microsoft.Extensions.DependencyInjection.Extensions;

namespace EnvCite.Platform.Web.Components.Layout.Navigation;

/// <param name="Home">這個租戶的入口首頁路徑（<see cref="PortalPaths.Home"/>）；各功能的頁面都在它底下。</param>
/// <param name="User">目前的使用者；項目依角色出現與否由各來源自己決定（顯示與否不是授權，頁面與服務層另有檢查）。</param>
public sealed record PortalNavContext(string Home, ClaimsPrincipal User);

/// <summary>
/// 入口側欄的一個來源。每個功能自己宣告它在側欄裡有沒有項目、在哪一群、排第幾；外殼（<c>PortalLayout</c>）不認識任何功能，
/// 新增功能只要實作這個介面並註冊，不必改外殼。以 <see cref="PortalNavigation.AddPortalNavContributor{T}"/> 註冊。
/// </summary>
public interface IPortalNavContributor
{
    /// <summary>
    /// 在側欄的位置，小的在前。群組依第一個項目出現的順序排列，所以 Order 依群組分段：
    /// 服務群組 10–29、組織群組 30–59；新來源取同群組的空檔。
    /// </summary>
    int Order { get; }

    /// <summary>這個使用者在這個租戶看得到的項目；沒有就回空。</summary>
    IEnumerable<PortalShell.NavItem> Items(PortalNavContext context);
}

public static class PortalNavigation
{
    /// <summary>註冊一個側欄來源。scoped：有些來源要讀 scoped 的服務（例如問答歷史是否開啟）。</summary>
    public static IServiceCollection AddPortalNavContributor<T>(this IServiceCollection services)
        where T : class, IPortalNavContributor
    {
        services.TryAddEnumerable(ServiceDescriptor.Scoped<IPortalNavContributor, T>());
        return services;
    }

    /// <summary>把所有來源的項目依 <see cref="IPortalNavContributor.Order"/> 排成側欄。外殼與測試都經這一條。</summary>
    public static IReadOnlyList<PortalShell.NavItem> Collect(IEnumerable<IPortalNavContributor> contributors, PortalNavContext context) =>
        [.. contributors.OrderBy(c => c.Order).SelectMany(c => c.Items(context))];

    /// <summary>入口自帶的五個來源。</summary>
    internal static IServiceCollection AddPortalNavigation(this IServiceCollection services) => services
        .AddPortalNavContributor<QueryNav>()
        .AddPortalNavContributor<OntologyWorkbenchNav>()
        .AddPortalNavContributor<MembersNav>()
        .AddPortalNavContributor<DirectoryNav>()
        .AddPortalNavContributor<AccountNav>();
}