using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Components.Account;

public partial class Security
{
    [Inject] private IOperatorTwoFactor TwoFactor { get; set; } = default!;

    [CascadingParameter] private HttpContext HttpContext { get; set; } = default!;

    [SupplyParameterFromForm(FormName = "ui-mfa-enable")] private CodeForm Input { get; set; } = default!;

    [SupplyParameterFromForm(FormName = "ui-mfa-regenerate")] private CodeForm Regenerate { get; set; } = default!;

    private bool _enabled;
    private string _codesTitle = "兩步驟驗證已啟用";
    private TwoFactorSetup? _setup;
    private string _qrSvg = string.Empty;
    private string? _error;
    private IReadOnlyList<string>? _recoveryCodes;

    protected override async Task OnInitializedAsync()
    {
        Input ??= new();
        Regenerate ??= new();
        _enabled = await TwoFactor.IsEnabledAsync(HttpContext.User, HttpContext.RequestAborted);
        if (!_enabled)
        {
            var setup = await TwoFactor.BeginSetupAsync(HttpContext.User, HttpContext.RequestAborted);
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
        var enabled = await TwoFactor.EnableAsync(HttpContext.User, code, HttpContext.RequestAborted);
        if (enabled.IsOk)
        {
            _recoveryCodes = enabled.Value;
            _enabled = true;
            return;
        }

        _error = enabled.Message ?? "啟用失敗。";
    }

    private async Task RegenerateAsync()
    {
        var code = Regenerate.Code;
        Regenerate.Code = string.Empty;
        var regenerated = await TwoFactor.RegenerateRecoveryCodesAsync(HttpContext.User, code, HttpContext.RequestAborted);
        if (regenerated.IsOk)
        {
            _recoveryCodes = regenerated.Value;
            _codesTitle = "新的復原碼";
            return;
        }

        _error = regenerated.Message ?? "重新產生失敗。";
    }

    public sealed class CodeForm
    {
        public string Code { get; set; } = string.Empty;
    }
}