using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Auth;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Components.Account;

public partial class Login
{
    [Inject] private IOperatorAccount Account { get; set; } = default!;

    [Inject] private NavigationManager Navigation { get; set; } = default!;

    [CascadingParameter] private HttpContext HttpContext { get; set; } = default!;

    [SupplyParameterFromQuery] private string? ReturnUrl { get; set; }

    [SupplyParameterFromForm] private LoginForm Input { get; set; } = default!;

    private string? _error;

    protected override void OnInitialized() => Input ??= new();

    private async Task SignInAsync()
    {
        var status = await Account.SignInAsync(Input.Email, Input.Password, HttpContext.RequestAborted);

        switch (status)
        {
            case OperatorSignInStatus.Succeeded:
                Navigation.NavigateTo(UiEndpoints.SafeReturnUrl(ReturnUrl));
                break;
            case OperatorSignInStatus.MustChangePassword:
                Navigation.NavigateTo(UiEndpoints.PasswordPath);
                break;
            case OperatorSignInStatus.MustEnrollTwoFactor:
                Navigation.NavigateTo(UiEndpoints.SecurityPath);
                break;
            case OperatorSignInStatus.RequiresTwoFactor:
                // 還沒有 session，只有一張五分鐘的暫存憑證；returnUrl 帶到第二步，通過後才用它。
                Navigation.NavigateTo(string.IsNullOrEmpty(ReturnUrl) ? UiEndpoints.MfaPath : $"{UiEndpoints.MfaPath}?returnUrl={Uri.EscapeDataString(ReturnUrl)}");
                break;
            default:
                // 密碼不寫回表單：重新渲染的 HTML 會帶著輸入框的值，等於把密碼又傳回瀏覽器（與任何中間的快取）一次。
                Input.Password = string.Empty;
                // 帳號不存在、密碼錯、被鎖定、被停用都是同一句：說得出差別，就是在告訴攻擊者哪些 email 有帳號。
                _error = "電子郵件或密碼不正確，或登入已被暫時停用。多次失敗會暫時鎖定帳號，請稍後再試。";
                break;
        }
    }

    public sealed class LoginForm
    {
        public string Email { get; set; } = string.Empty;

        public string Password { get; set; } = string.Empty;
    }
}