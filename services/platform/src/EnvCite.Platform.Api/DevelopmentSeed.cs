using System.Security.Claims;

using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Auth;

using Microsoft.Extensions.DependencyInjection;

namespace EnvCite.Platform.Api;

/// <summary>剛建立的第一位擁有者與他的一次性初始密碼（只回傳這一次，資料庫只存雜湊）。</summary>
public sealed record SeededOwner(string TenantIdentifier, string Email, string Password);

/// <summary>剛建立的示範營運者與他的一次性初始密碼（只回傳這一次，資料庫只存雜湊）。</summary>
public sealed record SeededOperator(string Email, string Password);

/// <summary>
/// 本機開發的第一組資料：租戶 <see cref="TenantIdentifier"/> 與它的第一位擁有者，以及一位示範營運者
/// （<see cref="OperatorEmail"/>，登入後台 <c>/ui</c> 用）。只在 Development 且
/// <c>Platform:SeedDevelopmentData</c> 開著時由 Program.cs 呼叫；冪等，租戶已經有成員就不動。
/// 走的是營運者開通客戶的同一條路（<see cref="ITenantProvisioner"/>、<see cref="IMemberAdmin"/>），
/// 所以本機每次起新資料庫都在演練正式環境的開通流程；稽核的操作者記為 <c>system</c>。
/// 密碼是系統產生的一次性密碼，不是隨程式出貨的預設值：呼叫端只能把它印在終端機，不要寫進日誌。
/// 營運者沒有本機的例外：第一次登入同樣要改密碼、再註冊驗證器（任何 TOTP 驗證器都行）。
/// </summary>
public static class DevelopmentSeed
{
    public const string TenantIdentifier = "dev";
    public const string OwnerEmail = "owner@example.test";
    public const string OperatorEmail = "operator@example.test";

    /// <returns>剛建立的擁有者；租戶已有成員（或擁有者早就建好）時為 null。</returns>
    public static async Task<SeededOwner?> RunAsync(
        IServiceProvider services, string tenantIdentifier, string ownerEmail, CancellationToken cancellationToken)
    {
        await using var scope = services.CreateAsyncScope();
        var directory = scope.ServiceProvider.GetRequiredService<ITenantDirectory>();
        var members = scope.ServiceProvider.GetRequiredService<IMemberAdmin>();
        var actor = new ClaimsPrincipal(new ClaimsIdentity([new Claim(ClaimTypes.Role, AuditActors.System)], "development-seed"));

        var tenant = (await directory.ListAsync(cancellationToken)).FirstOrDefault(t => t.Identifier == tenantIdentifier);
        var tenantId = tenant?.Id;
        if (tenantId is null)
        {
            var created = await scope.ServiceProvider.GetRequiredService<ITenantProvisioner>()
                .CreateAsync(actor, tenantIdentifier, "開發用租戶", cancellationToken);
            tenantId = Required(created, "create the tenant").Id;
        }

        if (Required(await members.ListAsync(tenantId, cancellationToken), "list the members").Count > 0)
        {
            return null;
        }

        var issued = Required(
            await members.CreateAsync(actor, tenantId, ownerEmail, "開發者", CustomerRoles.Owner, cancellationToken),
            "create the owner");
        return new SeededOwner(tenantIdentifier, ownerEmail, issued.Password);
    }

    public static Task<SeededOwner?> RunAsync(IServiceProvider services, CancellationToken cancellationToken) =>
        RunAsync(services, TenantIdentifier, OwnerEmail, cancellationToken);

    /// <summary>
    /// 示範營運者：營運者帳號一個都沒有時才建（已經有人在用的環境不會被硬塞一位進去）。與擁有者相同的規則：
    /// 走營運者建立帳號的同一條路（<see cref="IOperatorAdmin"/>），稽核的操作者記為 <c>system</c>。
    /// </summary>
    /// <returns>剛建立的營運者；已經有營運者時為 null。</returns>
    public static async Task<SeededOperator?> RunOperatorAsync(
        IServiceProvider services, string operatorEmail, CancellationToken cancellationToken)
    {
        var operators = services.GetRequiredService<IOperatorAdmin>();
        if (Required(await operators.ListAsync(cancellationToken), "list the operators").Count > 0)
        {
            return null;
        }

        var actor = new ClaimsPrincipal(new ClaimsIdentity([new Claim(ClaimTypes.Role, AuditActors.System)], "development-seed"));
        var issued = Required(await operators.CreateAsync(actor, operatorEmail, "開發者", cancellationToken), "create the operator");
        return new SeededOperator(operatorEmail, issued.Password);
    }

    public static Task<SeededOperator?> RunOperatorAsync(IServiceProvider services, CancellationToken cancellationToken) =>
        RunOperatorAsync(services, OperatorEmail, cancellationToken);

    /// <summary>印在終端機的提示。密碼只在這裡出現一次。</summary>
    public static string Describe(SeededOwner owner) =>
        $"""
        [dev seed] 已建立租戶 {owner.TenantIdentifier} 與擁有者 {owner.Email}
        [dev seed] 一次性密碼（只顯示這一次，第一次登入必須改掉）：{owner.Password}
        [dev seed] 登入：/portal/{owner.TenantIdentifier}/login
        """;

    /// <summary>印在終端機的提示。密碼只在這裡出現一次。</summary>
    public static string Describe(SeededOperator seeded) =>
        $"""
        [dev seed] 已建立營運者 {seeded.Email}
        [dev seed] 一次性密碼（只顯示這一次，第一次登入必須改掉，再註冊驗證器）：{seeded.Password}
        [dev seed] 登入：{UiEndpoints.LoginPath}
        """;

    // 種子失敗要讓啟動失敗：開發者看到的是明確的錯誤，而不是一個少了資料的環境。
    private static T Required<T>(AdminOutcome<T> outcome, string what) =>
        outcome.IsOk
            ? outcome.Value!
            : throw new InvalidOperationException($"Development seed could not {what}: {outcome.Status} {outcome.Message}");
}