using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Auth;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Components.Portal;

public partial class LoginMfa
{
    [Parameter] public string Tenant { get; set; } = string.Empty;

    [Inject] private IPortalAccount Account { get; set; } = default!;

    [Inject] private IPortalTwoFactor TwoFactor { get; set; } = default!;

    [Inject] private NavigationManager Navigation { get; set; } = default!;

    [CascadingParameter] private HttpContext HttpContext { get; set; } = default!;

    [SupplyParameterFromQuery] private string? ReturnUrl { get; set; }

    [SupplyParameterFromQuery(Name = "recovery")] private string? RecoveryParam { get; set; }

    [SupplyParameterFromForm] private CodeForm Input { get; set; } = default!;

    private string? _error;

    private bool Recovery => RecoveryParam == "1";

    protected override async Task OnInitializedAsync()
    {
        Input ??= new();
        if (Account.CurrentTenant is null)
        {
            Navigation.NotFound();
            return;
        }

        // 沒有（或過期的）暫存憑證：這一頁沒有東西可驗，回到輸入密碼。
        if (!await TwoFactor.HasPendingSignInAsync())
        {
            Navigation.NavigateTo(PortalPaths.Login(Tenant));
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

        return $"{PortalPaths.Login(Tenant)}/mfa" + (parameters.Count == 0 ? string.Empty : "?" + string.Join('&', parameters));
    }

    private async Task VerifyAsync()
    {
        var code = Input.Code;
        Input.Code = string.Empty; // 碼不寫回表單（理由同密碼）。
        var status = await TwoFactor.CompleteSignInAsync(code, Recovery, HttpContext.RequestAborted);

        switch (status)
        {
            case PortalSignInStatus.Succeeded:
                Navigation.NavigateTo(PortalEndpoints.SafeReturnUrl(ReturnUrl, Tenant));
                break;
            case PortalSignInStatus.MustChangePassword:
                Navigation.NavigateTo(PortalPaths.Password(Tenant));
                break;
            default:
                // 碼錯、過期、重複使用、被鎖定都是同一句話；暫存憑證在鎖定時會被收回，下一頁會回到輸入密碼。
                _error = Recovery
                    ? "復原碼不正確，或已經用過。多次失敗會暫時鎖定帳號。"
                    : "驗證碼不正確，或已經用過。請輸入驗證器上現在顯示的六位數；多次失敗會暫時鎖定帳號。";
                break;
        }
    }

    public sealed class CodeForm
    {
        public string Code { get; set; } = string.Empty;
    }
}