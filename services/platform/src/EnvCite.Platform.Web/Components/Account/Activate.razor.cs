using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Auth;
using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Components.Account;

public partial class Activate(IOperatorAccount account, NavigationManager navigation)
{
    [CascadingParameter] private HttpContext HttpContext { get; set; } = default!;

    [SupplyParameterFromQuery(Name = "userId")] private string? UserId { get; set; }

    [SupplyParameterFromQuery(Name = "code")] private string? Code { get; set; }

    [SupplyParameterFromForm] private ActivateForm Input { get; set; } = default!;

    private ActivationState _state;
    private string? _error;

    private string Heading => _state switch
    {
        ActivationState.SetPassword => L["設定密碼"],
        ActivationState.ConfirmEmail => L["驗證信箱"],
        _ => L["連結無效"],
    };

    protected override async Task OnInitializedAsync()
    {
        Input ??= new();

        // 網址帶著權杖：不讓它經 Referer 流到別的網站（OWASP）。
        HttpContext.Response.Headers["Referrer-Policy"] = "no-referrer";

        // 只是檢視，不用掉連結：信件掃描器預先開啟網址不會讓它失效。
        _state = await account.InspectActivationAsync(UserId, Code, HttpContext.RequestAborted);
    }

    private async Task SubmitAsync()
    {
        var (next, confirm) = (Input.New, Input.Confirm);
        Input.New = Input.Confirm = string.Empty; // 密碼不寫回表單

        if (_state == ActivationState.SetPassword && !string.Equals(next, confirm, StringComparison.Ordinal))
        {
            _error = L["兩次輸入的新密碼不一樣。"];
            return;
        }

        var activated = await account.ActivateAsync(UserId, Code, next, HttpContext.RequestAborted);
        if (activated.IsOk)
        {
            navigation.NavigateTo($"{UiEndpoints.LoginPath}?activated=1");
            return;
        }

        _state = await account.InspectActivationAsync(UserId, Code, HttpContext.RequestAborted);

        // 連結已無效：頁面本身就說明了，不再重複一條錯誤。
        _error = _state == ActivationState.Invalid ? null : activated.Localized() ?? L["操作失敗。"];
    }

    public sealed class ActivateForm
    {
        public string New { get; set; } = string.Empty;

        public string Confirm { get; set; } = string.Empty;
    }
}