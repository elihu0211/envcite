using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Components.Bases;
using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Components.Account;

public partial class Security(IOperatorTwoFactor twoFactor)
{
    [SupplyParameterFromForm(FormName = "ui-mfa-enable")] private CodeForm Input { get; set; } = default!;

    [SupplyParameterFromForm(FormName = "ui-mfa-regenerate")] private CodeForm Regenerate { get; set; } = default!;

    private bool _enabled;
    private string _codesTitle = string.Empty;
    private TwoFactorSetup? _setup;
    private string _qrSvg = string.Empty;
    private IReadOnlyList<string>? _recoveryCodes;

    protected override async Task OnInitializedAsync()
    {
        _codesTitle = L["兩步驟驗證已啟用"];
        Input ??= new();
        Regenerate ??= new();
        _enabled = await twoFactor.IsEnabledAsync(HttpContext.User, HttpContext.RequestAborted);
        if (!_enabled)
        {
            var setup = await twoFactor.BeginSetupAsync(HttpContext.User, HttpContext.RequestAborted);
            if (setup.IsOk)
            {
                _setup = setup.Value;
                _qrSvg = QrSvg.Render(_setup!.OtpauthUri);
            }
        }
    }

    private async Task EnableAsync()
    {
        var code = Input.Code;
        Input.Code = string.Empty;
        var enabled = await twoFactor.EnableAsync(HttpContext.User, code, HttpContext.RequestAborted);
        if (enabled.IsOk)
        {
            _recoveryCodes = enabled.Value;
            _enabled = true;
            return;
        }

        Error = enabled.Localized() ?? L["啟用失敗。"];
    }

    private async Task RegenerateAsync()
    {
        var code = Regenerate.Code;
        Regenerate.Code = string.Empty;
        var regenerated = await twoFactor.RegenerateRecoveryCodesAsync(HttpContext.User, code, HttpContext.RequestAborted);
        if (regenerated.IsOk)
        {
            _recoveryCodes = regenerated.Value;
            _codesTitle = L["新的復原碼"];
            return;
        }

        Error = regenerated.Localized() ?? L["重新產生失敗。"];
    }
}