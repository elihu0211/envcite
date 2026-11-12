using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Auth;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Components.Portal;

public partial class Login(NavigationManager navigation)
{
    [Parameter] public string Tenant { get; set; } = string.Empty;

    [CascadingParameter] private HttpContext HttpContext { get; set; } = default!;

    [SupplyParameterFromQuery] private string? ReturnUrl { get; set; }

    [SupplyParameterFromQuery] private string? Activated { get; set; }

    [SupplyParameterFromForm] private LoginForm Input { get; set; } = default!;

    private string? _error;

    protected override void OnInitialized()
    {
        Input ??= new();

        // 路徑上的租戶不存在：這個頁面就不存在。
        if (Account.CurrentTenant is null)
        {
            navigation.NotFound();
        }
    }

    private async Task SignInAsync()
    {
        var tenant = Account.CurrentTenant!;
        var status = await Account.SignInAsync(Input.Email, Input.Password, HttpContext.RequestAborted);

        switch (status)
        {
            case PortalSignInStatus.Succeeded:
                navigation.NavigateTo(PortalEndpoints.SafeReturnUrl(ReturnUrl, tenant.Identifier));
                break;
            case PortalSignInStatus.MustChangePassword:
                navigation.NavigateTo(PortalPaths.Password(tenant.Identifier));
                break;
            case PortalSignInStatus.RequiresTwoFactor:
                // 還沒有登入 cookie，只有一張五分鐘的暫存憑證；returnUrl 帶到第二步，通過後才用它。
                var next = $"{PortalPaths.Login(tenant.Identifier)}/mfa";
                navigation.NavigateTo(string.IsNullOrEmpty(ReturnUrl) ? next : $"{next}?returnUrl={Uri.EscapeDataString(ReturnUrl)}");
                break;
            default:
                // 密碼不寫回表單：重新渲染的 HTML 會帶著輸入框的值，等於把密碼又傳回瀏覽器（與任何中間的快取）一次。
                Input.Password = string.Empty;
                // 帳號不存在、密碼錯、被鎖定都是同一句：說得出差別，就是在告訴攻擊者哪些 email 有帳號。
                _error = L["電子郵件或密碼不正確，或登入已被暫時停用。多次失敗會暫時鎖定帳號，請稍後再試。"];
                break;
        }
    }

    public sealed class LoginForm
    {
        public string Email { get; set; } = string.Empty;

        public string Password { get; set; } = string.Empty;
    }
}