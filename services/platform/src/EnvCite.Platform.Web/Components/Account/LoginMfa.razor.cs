using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Auth;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Components.Account;

public partial class LoginMfa(IOperatorTwoFactor twoFactor)
{
    [SupplyParameterFromQuery] private string? ReturnUrl { get; set; }

    [SupplyParameterFromQuery(Name = "recovery")] private string? RecoveryParam { get; set; }

    private bool Recovery => RecoveryParam == "1";

    protected override async Task OnInitializedAsync()
    {
        // 沒有（或過期的）暫存憑證：這一頁沒有東西可驗，回到輸入密碼。
        if (!await twoFactor.HasPendingSignInAsync())
        {
            Navigation.NavigateTo(UiEndpoints.LoginPath);
        }
    }

    private string Link(bool recovery)
    {
        var parameters = new List<string>();
        if (recovery)
        {
            parameters.Add("recovery=1");
        }

        if (!string.IsNullOrEmpty(ReturnUrl))
        {
            parameters.Add($"returnUrl={Uri.EscapeDataString(ReturnUrl)}");
        }

        return UiEndpoints.MfaPath + (parameters.Count == 0 ? string.Empty : "?" + string.Join('&', parameters));
    }

    private async Task VerifyAsync()
    {
        var code = Input.Code;
        Input.Code = string.Empty; // 碼不寫回表單（理由同密碼）。
        var status = await twoFactor.CompleteSignInAsync(code, Recovery, HttpContext.RequestAborted);

        switch (status)
        {
            case OperatorSignInStatus.Succeeded:
                Navigation.NavigateTo(UiEndpoints.SafeReturnUrl(ReturnUrl));
                break;
            case OperatorSignInStatus.MustChangePassword:
                Navigation.NavigateTo(UiEndpoints.PasswordPath);
                break;
            default:
                // 碼錯、過期、重複使用、被鎖定都是同一句話；暫存憑證在鎖定時會被收回，下一頁會回到輸入密碼。
                Error = Recovery
                    ? L["復原碼不正確，或已經用過。多次失敗會暫時鎖定帳號。"]
                    : L["驗證碼不正確，或已經用過。請輸入驗證器上現在顯示的六位數；多次失敗會暫時鎖定帳號。"];
                break;
        }
    }
}