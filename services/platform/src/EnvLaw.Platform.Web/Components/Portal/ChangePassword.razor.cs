using System.Security.Claims;

using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvLaw.Platform.Web.Components.Portal;

public partial class ChangePassword
{
    [Parameter] public string Tenant { get; set; } = string.Empty;

    [Inject] private IPortalAccount Account { get; set; } = default!;

    [Inject] private NavigationManager Navigation { get; set; } = default!;

    [CascadingParameter] private HttpContext HttpContext { get; set; } = default!;

    [SupplyParameterFromForm] private PasswordForm Input { get; set; } = default!;

    private string? _error;

    protected override void OnInitialized() => Input ??= new();

    /// <summary>這個 session 是否還在等使用者改掉營運者經手的密碼。</summary>
    private bool Pending => HttpContext.User.FindFirst(PlatformClaimTypes.PasswordChangeRequired) is not null;

    private async Task ChangeAsync()
    {
        // 不論成敗，三個密碼欄都不寫回表單（理由見 Login）：失敗時使用者重打，比把密碼回傳安全。
        var (current, next, confirm) = (Input.Current, Input.New, Input.Confirm);
        Input.Current = Input.New = Input.Confirm = string.Empty;

        if (!string.Equals(next, confirm, StringComparison.Ordinal))
        {
            _error = "兩次輸入的新密碼不一樣。";
            return;
        }

        var changed = await Account.ChangePasswordAsync(HttpContext.User, current, next, HttpContext.RequestAborted);
        if (changed.IsOk)
        {
            Navigation.NavigateTo(PortalPaths.Home(Tenant));
            return;
        }

        _error = changed.Message ?? "改密碼失敗。";
    }

    public sealed class PasswordForm
    {
        public string Current { get; set; } = string.Empty;

        public string New { get; set; } = string.Empty;

        public string Confirm { get; set; } = string.Empty;
    }
}