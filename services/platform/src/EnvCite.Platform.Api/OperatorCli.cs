using System.Security.Claims;

using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Auth;

using Microsoft.Extensions.DependencyInjection;

namespace EnvCite.Platform.Api;

/// <summary>
/// 營運者帳號的命令列入口：建立第一位營運者與救援（重設密碼、重設驗證器）。在服務自己的環境裡執行
/// （資料庫連線與設定都來自該環境），不啟動 HTTP 伺服器，跑完就結束，所以沒有常駐的管理端點可被攻擊。
/// 走的是後台建立營運者的同一條路（<see cref="IOperatorAdmin"/>），稽核的操作者記為 <c>system</c>。
/// 一次性密碼只印在標準輸出，不進日誌、不進稽核。
/// </summary>
public static class OperatorCli
{
    public const string Usage = """
        用法：
          operator create <email> [顯示名稱]
          operator reset-password <email>
          operator reset-two-factor <email>
        """;

    /// <summary>第一個引數是 <c>operator</c> 才是這個入口；其餘照常啟動伺服器。</summary>
    public static bool IsCommand(string[] args) => args is ["operator", ..];

    /// <returns>結束碼：0 成功，1 操作失敗（帳號已存在、找不到等），2 用法錯誤。</returns>
    public static async Task<int> RunAsync(
        IServiceProvider services, string[] args, TextWriter output, TextWriter error, CancellationToken cancellationToken)
    {
        var admin = services.GetRequiredService<IOperatorAdmin>();
        var actor = new ClaimsPrincipal(new ClaimsIdentity([new Claim(ClaimTypes.Role, AuditActors.System)], "operator-cli"));

        switch (args)
        {
            case ["operator", "create", var email]:
                return await CreateAsync(admin, actor, email, null, output, error, cancellationToken);
            case ["operator", "create", var email, var displayName]:
                return await CreateAsync(admin, actor, email, displayName, output, error, cancellationToken);
            case ["operator", "reset-password", var email]:
                return await ResetPasswordAsync(admin, actor, email, output, error, cancellationToken);
            case ["operator", "reset-two-factor", var email]:
                return await ResetTwoFactorAsync(admin, actor, email, output, error, cancellationToken);
            default:
                await error.WriteLineAsync(Usage);
                return 2;
        }
    }

    private static async Task<int> CreateAsync(
        IOperatorAdmin admin, ClaimsPrincipal actor, string email, string? displayName,
        TextWriter output, TextWriter error, CancellationToken cancellationToken)
    {
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
