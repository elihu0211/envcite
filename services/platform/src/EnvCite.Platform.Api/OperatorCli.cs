using System.Security.Claims;

using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Auth;

using Microsoft.Extensions.DependencyInjection;

namespace EnvCite.Platform.Api;

/// <summary>
/// 營運者的命令列入口：建立第一位營運者與救援（重設密碼、重設驗證器），以及寄信設定的冒煙測試（<c>mail test</c>）。在服務自己的環境裡執行
/// （資料庫連線與設定都來自該環境），不啟動 HTTP 伺服器，跑完就結束，所以沒有常駐的管理端點可被攻擊。
/// 走的是後台建立營運者的同一條路（<see cref="IOperatorAdmin"/>），稽核的操作者記為 <c>system</c>。
/// 一次性密碼只印在標準輸出，不進日誌、不進稽核。
/// </summary>
public static class OperatorCli
{
    public const string Usage = """
        用法：
          operator create <email> [顯示名稱]
          operator resend-invite <email>
          operator reset-password <email>
          operator reset-two-factor <email>
          mail test <email>
          mail preview <輸出目錄>

        mail test 用目前的 Platform:Mail 設定寄一封沒有連結、沒有密碼的測試信，確認中繼接受（要在服務自己的環境執行，
        來源位址才是中繼認得的那個）。
        mail preview 不寄信也不連資料庫：把信件版型（邀請、驗證、測試信）寫成 HTML 與純文字檔到輸出目錄，連結是占位值。

        已啟用寄信（Platform:Mail）時，create 寄邀請信，對方開啟連結自己設定密碼並驗證信箱，不產生密碼；
        沒啟用時，create 印出一次性初始密碼。reset-password 一律印出新的一次性密碼（寄信失敗或收不到信時的退路）。
        """;

    /// <summary>第一個引數是 <c>operator</c> 或 <c>mail</c> 才是這個入口；其餘照常啟動伺服器。</summary>
    public static bool IsCommand(string[] args) => args is ["operator", ..] or ["mail", ..];

    /// <returns>結束碼：0 成功，1 操作失敗（帳號已存在、找不到等），2 用法錯誤。</returns>
    public static async Task<int> RunAsync(
        IServiceProvider services, string[] args, TextWriter output, TextWriter error, CancellationToken cancellationToken)
    {
        var admin = services.GetRequiredService<IOperatorAdmin>();
        var mail = services.GetRequiredService<IMailSender>();
        var actor = new ClaimsPrincipal(new ClaimsIdentity([new Claim(ClaimTypes.Role, AuditActors.System)], "operator-cli"));

        switch (args)
        {
            case ["mail", "test", var recipient]:
                return await TestMailAsync(services, mail, recipient, output, error, cancellationToken);
            case ["operator", "create", var email]:
                return await CreateAsync(admin, mail, actor, email, null, output, error, cancellationToken);
            case ["operator", "create", var email, var displayName]:
                return await CreateAsync(admin, mail, actor, email, displayName, output, error, cancellationToken);
            case ["operator", "resend-invite", var email]:
                return await ResendInviteAsync(admin, actor, email, output, error, cancellationToken);
            case ["operator", "reset-password", var email]:
                return await ResetPasswordAsync(admin, actor, email, output, error, cancellationToken);
            case ["operator", "reset-two-factor", var email]:
                return await ResetTwoFactorAsync(admin, actor, email, output, error, cancellationToken);
            default:
                await error.WriteLineAsync(Usage);
                return 2;
        }
    }

    /// <summary>
    /// 寄信冒煙測試。印出實際使用的設定（主機、埠、寄件者、連結來源，都不是機密），寄一封只有說明文字的信。
    /// <see cref="MailDelivery.Sent"/> 只代表中繼接受了信，不代表投遞成功：要到收件匣確認。
    /// 失敗的細節在日誌（例外型別與 SMTP 狀態碼，不含位址與內文）。
    /// </summary>
    private static async Task<int> TestMailAsync(
        IServiceProvider services, IMailSender mail, string recipient,
        TextWriter output, TextWriter error, CancellationToken cancellationToken)
    {
        if (!System.Net.Mail.MailAddress.TryCreate(recipient, out var parsed) || parsed.Address != recipient.Trim())
        {
            await error.WriteLineAsync($"收件地址不合法：{recipient}");
            return 1;
        }

        if (!mail.Enabled)
        {
            await error.WriteLineAsync("寄信未啟用：Platform:Mail:Enabled 不是 true（環境變數 ENVCITE_Platform__Mail__Enabled）。");
            return 1;
        }

        var settings = services.GetRequiredService<Microsoft.Extensions.Options.IOptions<MailOptions>>().Value;
        await output.WriteLineAsync($"""
            中繼：{settings.Host}:{settings.Port}（不加密、免驗證）
            寄件者：{settings.From}
            連結來源：{settings.BaseUrl}
            """);

        var composed = await services.GetRequiredService<IMailComposer>().ComposeTestAsync(cancellationToken);
        var delivery = await mail.SendAsync(parsed.Address, composed.Subject, composed.Content, cancellationToken);
        if (delivery == MailDelivery.Sent)
        {
            await output.WriteLineAsync($"中繼已接受這封測試信。請到 {parsed.Address} 確認有收到（中繼接受不代表已投遞）。");
            return 0;
        }

        await error.WriteLineAsync(
            $"寄信失敗（{delivery}）。常見原因：這個環境的來源位址不在中繼的信任名單、Host 或 Port 不對、From 的網域被中繼拒絕。"
            + "細節看服務日誌的「Mail delivery failed」那一行（例外型別與 SMTP 狀態碼）。");
        return 1;
    }

    private static async Task<int> CreateAsync(
        IOperatorAdmin admin, IMailSender mail, ClaimsPrincipal actor, string email, string? displayName,
        TextWriter output, TextWriter error, CancellationToken cancellationToken)
    {
        if (mail.Enabled)
        {
            var invited = await admin.InviteAsync(actor, email, displayName, cancellationToken);
            if (!invited.IsOk)
            {
                return await FailAsync(error, "建立營運者", invited.Status, invited.Message);
            }

            await output.WriteLineAsync($"已建立營運者 {invited.Value!.Operator.Email}（還沒有密碼）");
            return await ReportInvitationAsync(output, error, invited.Value.Mail, email);
        }

        var created = await admin.CreateAsync(actor, email, displayName, cancellationToken);
        if (!created.IsOk)
        {
            return await FailAsync(error, "建立營運者", created.Status, created.Message);
        }

        await output.WriteLineAsync($"""
            已建立營運者 {created.Value!.Operator.Email}
            一次性密碼（只顯示這一次，第一次登入必須改掉，再註冊驗證器）：{created.Value.Password}
            登入：{UiEndpoints.LoginPath}
            """);
        return 0;
    }

    private static async Task<int> ResendInviteAsync(
        IOperatorAdmin admin, ClaimsPrincipal actor, string email,
        TextWriter output, TextWriter error, CancellationToken cancellationToken)
    {
        var target = await FindAsync(admin, email, cancellationToken);
        if (target is null)
        {
            return await NotFoundAsync(error, email);
        }

        var sent = await admin.ResendInvitationAsync(actor, target.Id, cancellationToken);
        if (!sent.IsOk)
        {
            return await FailAsync(error, "重寄邀請", sent.Status, sent.Message);
        }

        return await ReportInvitationAsync(output, error, sent.Value!.Mail, target.Email);
    }

    /// <summary>邀請信沒寄出去時，帳號已經在了：結束碼 1 讓腳本察覺，訊息說明退路。連結本身絕不印出（它等同於一次登入的鑰匙）。</summary>
    private static async Task<int> ReportInvitationAsync(TextWriter output, TextWriter error, MailDelivery delivery, string email)
    {
        if (delivery == MailDelivery.Sent)
        {
            await output.WriteLineAsync($"邀請信已寄到 {email}：連結有時限，而且只能使用一次。登入：{UiEndpoints.LoginPath}");
            return 0;
        }

        await error.WriteLineAsync($"邀請信沒有寄出（{delivery}）。帳號已建立：可執行 `operator resend-invite {email}` 重寄，或 `operator reset-password {email}` 取得一次性密碼。");
        return 1;
    }

    private static async Task<int> ResetPasswordAsync(
        IOperatorAdmin admin, ClaimsPrincipal actor, string email,
        TextWriter output, TextWriter error, CancellationToken cancellationToken)
    {
        var target = await FindAsync(admin, email, cancellationToken);
        if (target is null)
        {
            return await NotFoundAsync(error, email);
        }

        var reset = await admin.ResetPasswordAsync(actor, target.Id, cancellationToken);
        if (!reset.IsOk)
        {
            return await FailAsync(error, "重設密碼", reset.Status, reset.Message);
        }

        await output.WriteLineAsync($"""
            已重設營運者 {target.Email} 的密碼
            一次性密碼（只顯示這一次，下次登入必須改掉）：{reset.Value!.Password}
            """);
        return 0;
    }

    private static async Task<int> ResetTwoFactorAsync(
        IOperatorAdmin admin, ClaimsPrincipal actor, string email,
        TextWriter output, TextWriter error, CancellationToken cancellationToken)
    {
        var target = await FindAsync(admin, email, cancellationToken);
        if (target is null)
        {
            return await NotFoundAsync(error, email);
        }

        var reset = await admin.ResetTwoFactorAsync(actor, target.Id, cancellationToken);
        if (!reset.IsOk)
        {
            return await FailAsync(error, "重設驗證器", reset.Status, reset.Message);
        }

        await output.WriteLineAsync($"已重設營運者 {target.Email} 的驗證器；對方下次用密碼登入後必須重新註冊。");
        return 0;
    }

    private static async Task<OperatorInfo?> FindAsync(IOperatorAdmin admin, string email, CancellationToken cancellationToken)
    {
        var list = await admin.ListAsync(cancellationToken);
        return list.IsOk
            ? list.Value!.FirstOrDefault(o => string.Equals(o.Email, email, StringComparison.OrdinalIgnoreCase))
            : null;
    }

    private static async Task<int> NotFoundAsync(TextWriter error, string email)
    {
        await error.WriteLineAsync($"找不到營運者 {email}");
        return 1;
    }

    private static async Task<int> FailAsync(TextWriter error, string action, AdminStatus status, string? message)
    {
        await error.WriteLineAsync($"{action}失敗：{status} {message}");
        return 1;
    }
}