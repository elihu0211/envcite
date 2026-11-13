using EnvCite.Platform.Web.Resources;

using Microsoft.Extensions.Localization;

using MudBlazor;

namespace EnvCite.Platform.Web.Components.Layout.Navigation;

/// <summary>入口自帶的側欄來源的共同基底：本地化與兩個群組的標題。項目文字一律經 <c>L</c>，資源檔的測試才掃得到。</summary>
internal abstract class PortalNavContributorBase(IStringLocalizer<Ui> localizer) : IPortalNavContributor
{
    protected IStringLocalizer<Ui> L { get; } = localizer;

    protected string ServicesGroup => L["服務"];

    protected string OrganizationGroup => L["組織"];

    public abstract int Order { get; }

    public abstract IEnumerable<PortalShell.NavItem> Items(PortalNavContext context);
}

internal sealed class QueryNav(IStringLocalizer<Ui> localizer) : PortalNavContributorBase(localizer)
{
    public override int Order => 10;

    // 首頁只在首頁算「目前頁」，否則其他頁都在它底下，它會一直亮著。
    public override IEnumerable<PortalShell.NavItem> Items(PortalNavContext context) =>
        [new(context.Home, L["AI 法規問答"], ServicesGroup, Exact: true, Icon: Icons.Material.Outlined.QuestionAnswer)];
}

internal sealed class OntologyWorkbenchNav(IStringLocalizer<Ui> localizer, ITenantModules modules) : PortalNavContributorBase(localizer)
{
    public override int Order => 15;

    public override IEnumerable<PortalShell.NavItem> Items(PortalNavContext context) =>
        context.User.FindFirst(PlatformClaimTypes.TenantIdentifier)?.Value is { } tenant
        && (modules.IsEnabled(tenant, ModuleKeys.LawCorpus) || modules.IsEnabled(tenant, ModuleKeys.NormativeReasoning))
            ? [new($"{context.Home}/laws", L["法規本體工作台"], ServicesGroup, Icon: Icons.Material.Outlined.AccountTree)]
            : [];
}

internal sealed class HistoryNav(IStringLocalizer<Ui> localizer, IConversationHistoryAvailability history) : PortalNavContributorBase(localizer)
{
    public override int Order => 20;

    public override IEnumerable<PortalShell.NavItem> Items(PortalNavContext context) =>
        history.Enabled ? [new($"{context.Home}/history", L["對話紀錄"], ServicesGroup, Icon: Icons.Material.Outlined.Forum)] : [];
}

internal sealed class MembersNav(IStringLocalizer<Ui> localizer) : PortalNavContributorBase(localizer)
{
    public override int Order => 30;

    public override IEnumerable<PortalShell.NavItem> Items(PortalNavContext context) =>
        context.User.IsInRole(CustomerRoles.Owner) || context.User.IsInRole(CustomerRoles.Admin)
            ? [new($"{context.Home}/members", L["成員"], OrganizationGroup, Icon: Icons.Material.Outlined.Group)]
            : [];
}

internal sealed class DirectoryNav(IStringLocalizer<Ui> localizer, IDirectoryAvailability availability) : PortalNavContributorBase(localizer)
{
    public override int Order => 40;

    // 設定與 token 只有擁有者能管（服務層也擋）；功能面沒開就不列。
    public override IEnumerable<PortalShell.NavItem> Items(PortalNavContext context) =>
        availability.Available && context.User.IsInRole(CustomerRoles.Owner)
            ? [new($"{context.Home}/directory", L["目錄同步"], OrganizationGroup, Icon: Icons.Material.Outlined.SyncAlt)]
            : [];
}

internal sealed class AccountNav(IStringLocalizer<Ui> localizer) : PortalNavContributorBase(localizer)
{
    public override int Order => 50;

    public override IEnumerable<PortalShell.NavItem> Items(PortalNavContext context) =>
        [new($"{context.Home}/account", L["帳號與組織"], OrganizationGroup, Icon: Icons.Material.Outlined.ManageAccounts)];
}