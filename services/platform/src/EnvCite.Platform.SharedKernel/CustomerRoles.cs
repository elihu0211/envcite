namespace EnvCite.Platform.SharedKernel;

/// <summary>客戶入口的角色。授權只讀 role claim，不依賴 Identity 的內部類別（見 07-01 §7）。</summary>
public static class CustomerRoles
{
    public const string Owner = "TenantOwner";
    public const string Admin = "TenantAdmin";
    public const string Developer = "Developer";
    public const string Billing = "Billing";
    public const string Viewer = "Viewer";

    public static IReadOnlyList<string> All { get; } = [Owner, Admin, Developer, Billing, Viewer];
}