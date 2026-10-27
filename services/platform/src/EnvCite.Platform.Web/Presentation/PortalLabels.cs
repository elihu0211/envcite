using EnvCite.Platform.Web.Resources;

namespace EnvCite.Platform.Web.Presentation;

/// <summary>客戶入口與後台的成員角色，在畫面上的名稱（依介面語言）。角色的鍵（claim 的值）永遠是英文常數，這裡只管顯示。</summary>
public static class PortalLabels
{
    public static string Role(string? role) =>
        string.IsNullOrEmpty(role) ? "—" : Text.Find($"Role.{role}") ?? role;

    /// <summary>選角色時，名稱加上這個角色能做什麼（07-01 的角色表是草案，未與客戶確認）。</summary>
    public static string RoleWithAbilities(string role) => Text.Find($"Role.{role}.abilities") ?? role;
}