namespace EnvCite.Platform.SharedKernel;

// 三個驗證器都只在 Platform:Mail:Enabled 為 true 時檢查（未啟用時其餘鍵被忽略，部署庫可以先放著不完整的值）。
// 錯誤只指名鍵，不帶值：帳號與密碼都不進訊息。

internal sealed class MailOptionsValidator : IValidateOptions<MailOptions>
{
    public ValidateOptionsResult Validate(string? name, MailOptions options)
    {
        if (!options.Enabled)
            return ValidateOptionsResult.Success;

        if (options.BaseUri is null)
            return ValidateOptionsResult.Fail("Platform:Mail:BaseUrl must be an https origin (http only for localhost) without a path when Platform:Mail:Enabled is true.");

        return options.LinkLifetime < TimeSpan.FromHours(1) || options.LinkLifetime > TimeSpan.FromDays(14)
            ? ValidateOptionsResult.Fail("Platform:Mail:LinkLifetime must be between 1 hour and 14 days.")
            : ValidateOptionsResult.Success;
    }
}

internal sealed class MailTransportOptionsValidator(IOptions<MailOptions> mail) : IValidateOptions<MailTransportOptions>
{
    public ValidateOptionsResult Validate(string? name, MailTransportOptions options)
    {
        if (!mail.Value.Enabled)
            return ValidateOptionsResult.Success;

        if (string.IsNullOrWhiteSpace(options.Host))
            return ValidateOptionsResult.Fail("Platform:Mail:Host is required when Platform:Mail:Enabled is true.");

        if (options.Port is < 1 or > 65535)
            return ValidateOptionsResult.Fail("Platform:Mail:Port must be between 1 and 65535.");

        if (!Enum.IsDefined(options.Security))
            return ValidateOptionsResult.Fail($"Platform:Mail:Security must be one of: {string.Join(", ", Enum.GetNames<MailSecurity>())}.");

        if (string.IsNullOrWhiteSpace(options.Username) != string.IsNullOrEmpty(options.Password))
        {
            return ValidateOptionsResult.Fail(string.IsNullOrWhiteSpace(options.Username)
                ? "Platform:Mail:Username is required when Platform:Mail:Password is set (set both or neither)."
                : "Platform:Mail:Password is required when Platform:Mail:Username is set (set both or neither).");
        }

        return options.TimeoutSeconds is < 1 or > 120
            ? ValidateOptionsResult.Fail("Platform:Mail:TimeoutSeconds must be between 1 and 120.")
            : ValidateOptionsResult.Success;
    }
}

internal sealed class MailIdentityOptionsValidator(IOptions<MailOptions> mail) : IValidateOptions<MailIdentityOptions>
{
    public ValidateOptionsResult Validate(string? name, MailIdentityOptions options)
    {
        if (!mail.Value.Enabled)
            return ValidateOptionsResult.Success;

        // From 的預設（帳號是 email 時用帳號）已在驗證之前套用。
        if (!MailAddresses.IsValid(options.From))
            return ValidateOptionsResult.Fail("Platform:Mail:From must be a valid mail address when Platform:Mail:Enabled is true (or set Platform:Mail:Username to a mail address).");

        return !string.IsNullOrWhiteSpace(options.ReplyTo) && !MailAddresses.IsValid(options.ReplyTo)
            ? ValidateOptionsResult.Fail("Platform:Mail:ReplyTo must be a valid mail address when set.")
            : ValidateOptionsResult.Success;
    }
}