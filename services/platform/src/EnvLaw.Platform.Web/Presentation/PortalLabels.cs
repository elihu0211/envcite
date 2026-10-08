using EnvLaw.Platform.SharedKernel;

namespace EnvLaw.Platform.Web.Presentation;

/// <summary>客戶入口與後台的成員角色，在畫面上的名稱。角色的鍵（claim 的值）永遠是英文常數，這裡只管顯示。</summary>
public static class PortalLabels
{
    public static string Role(string? role) => role switch
    {
        CustomerRoles.Owner => "擁有者",
        CustomerRoles.Admin => "管理員",
        CustomerRoles.Developer => "開發者",
        CustomerRoles.Billing => "帳務",
        CustomerRoles.Viewer => "唯讀",
        null or "" => "—",
        _ => role,
    };

    /// <summary>選角色時，名稱加上這個角色能做什麼（07-01 的角色表是草案，未與客戶確認）。</summary>
    public static string RoleWithAbilities(string role) => role switch
    {
        CustomerRoles.Owner => "擁有者（管理成員與帳務）",
        CustomerRoles.Admin => "管理員（管理成員與 API key）",
        CustomerRoles.Developer => "開發者（管理 API key、看用量）",
        CustomerRoles.Billing => "帳務（看發票）",
        CustomerRoles.Viewer => "唯讀",
        _ => role,
    };
}