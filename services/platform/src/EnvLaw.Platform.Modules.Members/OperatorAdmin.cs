using System.Security.Claims;

using EnvLaw.Platform.Data;
using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Identity;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace EnvLaw.Platform.Modules.Members;

/// <summary>
/// 管理營運者帳號（<c>/admin/operators</c> 與本機開發的種子資料共用）。結構與 <see cref="MemberAdmin"/> 相同：
/// 一次性初始密碼只回傳一次、資料庫只存雜湊、不進日誌與稽核；每個動作留稽核。營運者沒有租戶，所以不必綁定租戶 scope。
/// 沒有自助註冊：這是建立帳號的唯一途徑，呼叫端（持營運者金鑰的人）負責把初始密碼安全地交給對方。
/// </summary>
internal sealed class OperatorAdmin(IServiceScopeFactory scopes, TimeProvider time) : IOperatorAdmin
{
    public async Task<AdminOutcome<IReadOnlyList<OperatorInfo>>> ListAsync(CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var users = scope.ServiceProvider.GetRequiredService<UserManager<OperatorUser>>();
        var list = await users.Users.AsNoTracking().OrderBy(u => u.Email).ToListAsync(cancellationToken);
        return AdminOutcome.Ok<IReadOnlyList<OperatorInfo>>([.. list.Select(Info)]);
    }

    public async Task<AdminOutcome<IssuedOperatorPassword>> CreateAsync(
        ClaimsPrincipal actor, string email, string? displayName, CancellationToken cancellationToken)
    {
        if (!EmailAddresses.IsValid(email))
        {
            return AdminOutcome.Invalid<IssuedOperatorPassword>("Email 格式不正確。");
        }

        if (displayName is { Length: > 200 })
        {
            return AdminOutcome.Invalid<IssuedOperatorPassword>("顯示名稱最長 200 字元。");
        }

        await using var scope = scopes.CreateAsyncScope();
        var users = scope.ServiceProvider.GetRequiredService<UserManager<OperatorUser>>();
        var user = new OperatorUser
        {
            UserName = email.Trim(),
            Email = email.Trim(),
            DisplayName = string.IsNullOrWhiteSpace(displayName) ? null : displayName.Trim(),
            MustChangePassword = true,
        };

        var password = OneTimePassword.New();
        var created = await users.CreateAsync(user, password);
        if (!created.Succeeded)
        {
            return created.Errors.Any(e => e.Code is nameof(IdentityErrorDescriber.DuplicateUserName) or nameof(IdentityErrorDescriber.DuplicateEmail))
                ? AdminOutcome.Conflict<IssuedOperatorPassword>("已經有這個 email 的營運者。")
                : AdminOutcome.Invalid<IssuedOperatorPassword>(created.Errors.First().Description);
        }

        // 稽核的 Detail 只有 email，絕不放密碼（也沒有雜湊）。
        await Audit(scope).AppendAsync(
            actor,
            new AuditEntry(AuditActions.OperatorCreated, TargetType: "operator", TargetId: user.Id.ToString(), Detail: new Dictionary<string, object?> { ["email"] = user.Email }),
            cancellationToken);

        return AdminOutcome.Ok(new IssuedOperatorPassword(Info(user), password));
    }

    public async Task<AdminOutcome<IssuedOperatorPassword>> ResetPasswordAsync(
        ClaimsPrincipal actor, string operatorId, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var users = scope.ServiceProvider.GetRequiredService<UserManager<OperatorUser>>();
        if (await users.FindOperatorAsync(operatorId) is not { } user)
        {
            return AdminOutcome.NotFound<IssuedOperatorPassword>("Operator not found.");
        }

        var password = OneTimePassword.New();
        user.PasswordHash = users.PasswordHasher.HashPassword(user, password);
        user.MustChangePassword = true;
        user.AccessFailedCount = 0;
        user.LockoutEnd = null;
        var updated = await users.UpdateAsync(user);
        if (!updated.Succeeded)
        {
            return AdminOutcome.Invalid<IssuedOperatorPassword>(updated.Errors.First().Description);
        }

        // 讓既有的 session 失效：被重設通常是因為帳號可能已經不安全。
        await users.UpdateSecurityStampAsync(user);
        await Audit(scope).AppendAsync(
            actor,
            new AuditEntry(AuditActions.OperatorPasswordReset, TargetType: "operator", TargetId: user.Id.ToString(), Detail: new Dictionary<string, object?> { ["email"] = user.Email }),
            cancellationToken);

        return AdminOutcome.Ok(new IssuedOperatorPassword(Info(user), password));
    }

    public async Task<AdminOutcome<OperatorInfo>> ResetTwoFactorAsync(
        ClaimsPrincipal actor, string operatorId, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var users = scope.ServiceProvider.GetRequiredService<UserManager<OperatorUser>>();
        if (await users.FindOperatorAsync(operatorId) is not { } user)
        {
            return AdminOutcome.NotFound<OperatorInfo>("Operator not found.");
        }

        await users.SetTwoFactorEnabledAsync(user, false);
        await users.RemoveAuthenticationTokenAsync(user, TwoFactorTokens.Provider, TwoFactorTokens.AuthenticatorKey);
        await users.RemoveAuthenticationTokenAsync(user, TwoFactorTokens.Provider, TwoFactorTokens.RecoveryCodes);
        user.LastTotpStep = 0;
        user.AccessFailedCount = 0;
        user.LockoutEnd = null;
        await users.UpdateAsync(user);

        // 讓既有的 session 失效：會需要重設通常是因為裝置遺失，可能已落入他人手中。
        await users.UpdateSecurityStampAsync(user);
        await Audit(scope).AppendAsync(
            actor,
            new AuditEntry(AuditActions.OperatorMfaReset, TargetType: "operator", TargetId: user.Id.ToString(), Detail: new Dictionary<string, object?> { ["email"] = user.Email }),
            cancellationToken);

        return AdminOutcome.Ok(Info(user));
    }

    public async Task<AdminOutcome<OperatorInfo>> SetDisabledAsync(
        ClaimsPrincipal actor, string operatorId, bool disabled, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var users = scope.ServiceProvider.GetRequiredService<UserManager<OperatorUser>>();
        if (await users.FindOperatorAsync(operatorId) is not { } user)
        {
            return AdminOutcome.NotFound<OperatorInfo>("Operator not found.");
        }

        if (user.Disabled == disabled)
        {
            return AdminOutcome.Ok(Info(user)); // 已經是那個狀態：不重複記錄
        }

        user.Disabled = disabled;
        await users.UpdateAsync(user);

        // 停用要讓對方所有 session 立刻失效（授權也直接檢查旗標）；重新啟用也換一次，舊的 cookie 不會復活。
        await users.UpdateSecurityStampAsync(user);
        await Audit(scope).AppendAsync(
            actor,
            new AuditEntry(disabled ? AuditActions.OperatorDisabled : AuditActions.OperatorEnabled, TargetType: "operator", TargetId: user.Id.ToString(),
                Detail: new Dictionary<string, object?> { ["email"] = user.Email }),
            cancellationToken);

        return AdminOutcome.Ok(Info(user));
    }

    private static IAuditLog Audit(AsyncServiceScope scope) => scope.ServiceProvider.GetRequiredService<IAuditLog>();

    private OperatorInfo Info(OperatorUser u) =>
        new(u.Id.ToString(), u.Email ?? string.Empty, u.DisplayName, u.MustChangePassword,
            u.LockoutEnd is { } end && end > time.GetUtcNow(), u.TwoFactorEnabled, u.Disabled);
}