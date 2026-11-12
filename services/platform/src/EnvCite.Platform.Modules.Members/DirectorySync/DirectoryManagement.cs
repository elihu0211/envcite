using System.Security.Claims;

using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Identity;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Modules.Members.DirectorySync;

/// <summary>
/// 目錄同步的管理動作（設定、token、解除管理）：客戶的擁有者（<see cref="DirectorySelfService"/>）與營運者
/// （<see cref="DirectoryAdmin"/>，唯讀加緊急關閉）共用，所以規則與稽核只有一份。呼叫端負責「誰可以做」；這裡只管「怎麼做」。
/// 必須在已經綁定租戶的 scope 裡使用。部署者沒開功能面時一律 <see cref="OutcomeCodes.DirectoryNotEnabled"/>。
/// </summary>
internal sealed class DirectoryManagement(
    PlatformDbContext db,
    UserManager<ApplicationUser> users,
    MemberCore members,
    IOptions<DirectoryOptions> options,
    IMultiTenantContextAccessor<TenantInfo> tenantAccessor,
    TimeProvider time)
{
    private const int NameLength = 100;

    public async Task<AdminOutcome<DirectoryStatus>> StatusAsync(CancellationToken cancellationToken) =>
        options.Value.Enabled
            ? AdminOutcome.Ok(await ReadStatusAsync(cancellationToken))
            : AdminOutcome.NotFound<DirectoryStatus>(OutcomeCodes.DirectoryNotEnabled);

    public async Task<AdminOutcome<DirectoryStatus>> ConfigureAsync(
        ClaimsPrincipal actor, DirectorySettings settings, CancellationToken cancellationToken)
    {
        if (!options.Value.Enabled)
        {
            return AdminOutcome.NotFound<DirectoryStatus>(OutcomeCodes.DirectoryNotEnabled);
        }

        if (!CustomerRoles.All.Contains(settings.DefaultRole))
        {
            return AdminOutcome.Invalid<DirectoryStatus>(OutcomeCodes.RoleInvalid);
        }

        if (!DirectoryRoles.Assignable.Contains(settings.DefaultRole))
        {
            return AdminOutcome.Forbidden<DirectoryStatus>(OutcomeCodes.DirectoryRoleNotMappable);
        }

        var connection = await db.DirectoryConnections.SingleOrDefaultAsync(cancellationToken);
        if (connection is null)
        {
            connection = new DirectoryConnection();
            db.DirectoryConnections.Add(connection);
        }

        var previous = new DirectorySettings(connection.Enabled, connection.DefaultRole, connection.AdoptExistingMembers);
        (connection.Enabled, connection.DefaultRole, connection.AdoptExistingMembers) =
            (settings.Enabled, settings.DefaultRole, settings.AdoptExistingMembers);
        connection.UpdatedAt = time.GetUtcNow();

        if (previous.Enabled != settings.Enabled)
        {
            Stage(actor, settings.Enabled ? AuditActions.DirectoryEnabled : AuditActions.DirectoryDisabled, "connection", connection.Id.ToString(), new Dictionary<string, object?>());
        }

        if (previous.DefaultRole != settings.DefaultRole || previous.AdoptExistingMembers != settings.AdoptExistingMembers)
        {
            Stage(actor, AuditActions.DirectoryConfigured, "connection", connection.Id.ToString(), new Dictionary<string, object?>
            {
                ["default_role"] = settings.DefaultRole,
                ["adopt_existing_members"] = settings.AdoptExistingMembers,
                ["previous_default_role"] = previous.DefaultRole,
                ["previous_adopt_existing_members"] = previous.AdoptExistingMembers,
            });
        }

        await db.SaveChangesAsync(cancellationToken);
        return AdminOutcome.Ok(await ReadStatusAsync(cancellationToken));
    }

    /// <summary>緊急關閉：冪等，沒有連線或已經關閉就什麼都不寫。</summary>
    public async Task<AdminOutcome<DirectoryStatus>> DisableAsync(ClaimsPrincipal actor, CancellationToken cancellationToken)
    {
        if (!options.Value.Enabled)
        {
            return AdminOutcome.NotFound<DirectoryStatus>(OutcomeCodes.DirectoryNotEnabled);
        }

        if (await db.DirectoryConnections.SingleOrDefaultAsync(cancellationToken) is { Enabled: true } connection)
        {
            connection.Enabled = false;
            connection.UpdatedAt = time.GetUtcNow();
            Stage(actor, AuditActions.DirectoryDisabled, "connection", connection.Id.ToString(), new Dictionary<string, object?>());
            await db.SaveChangesAsync(cancellationToken);
        }

        return AdminOutcome.Ok(await ReadStatusAsync(cancellationToken));
    }

    public async Task<AdminOutcome<IssuedDirectoryToken>> IssueTokenAsync(
        ClaimsPrincipal actor, string name, DateTimeOffset? expiresAt, CancellationToken cancellationToken)
    {
        if (!options.Value.Enabled)
        {
            return AdminOutcome.NotFound<IssuedDirectoryToken>(OutcomeCodes.DirectoryNotEnabled);
        }

        var now = time.GetUtcNow();
        var trimmed = (name ?? string.Empty).Trim();
        if (trimmed.Length is 0 or > NameLength || expiresAt <= now)
        {
            return AdminOutcome.Invalid<IssuedDirectoryToken>(OutcomeCodes.DirectoryTokenInvalid);
        }

        var (token, prefix, secretHash) = DirectoryTokenFormat.Generate();
        var entity = new DirectoryToken { Name = trimmed, Prefix = prefix, SecretHash = secretHash, CreatedAt = now, ExpiresAt = expiresAt };
        db.DirectoryTokens.Add(entity);

        // 稽核只記名稱、前綴與期限；完整 token 與雜湊都不進稽核。
        Stage(actor, AuditActions.DirectoryTokenIssued, "directory_token", entity.Id.ToString(), new Dictionary<string, object?>
        {
            ["name"] = entity.Name,
            ["prefix"] = entity.Prefix,
            ["expires_at"] = entity.ExpiresAt,
        });
        await db.SaveChangesAsync(cancellationToken);
        return AdminOutcome.Ok(new IssuedDirectoryToken(token, Info(entity)));
    }

    public async Task<AdminOutcome<DirectoryTokenInfo>> RevokeTokenAsync(ClaimsPrincipal actor, Guid tokenId, CancellationToken cancellationToken)
    {
        if (!options.Value.Enabled)
        {
            return AdminOutcome.NotFound<DirectoryTokenInfo>(OutcomeCodes.DirectoryNotEnabled);
        }

        // 租戶的查詢濾鏡讓別的租戶的 token 在這裡找不到。
        if (await db.DirectoryTokens.SingleOrDefaultAsync(t => t.Id == tokenId, cancellationToken) is not { } token)
        {
            return AdminOutcome.NotFound<DirectoryTokenInfo>(OutcomeCodes.DirectoryTokenNotFound);
        }

        if (token.RevokedAt is null)
        {
            token.RevokedAt = time.GetUtcNow();
            Stage(actor, AuditActions.DirectoryTokenRevoked, "directory_token", token.Id.ToString(), new Dictionary<string, object?>
            {
                ["name"] = token.Name,
                ["prefix"] = token.Prefix,
            });
            await db.SaveChangesAsync(cancellationToken);
        }

        return AdminOutcome.Ok(Info(token));
    }

    public async Task<AdminOutcome<MemberInfo>> ReleaseManagedAsync(ClaimsPrincipal actor, string userId, CancellationToken cancellationToken)
    {
        if (!options.Value.Enabled)
        {
            return AdminOutcome.NotFound<MemberInfo>(OutcomeCodes.DirectoryNotEnabled);
        }

        if (await users.FindByIdAsync(userId) is not { } user)
        {
            return AdminOutcome.NotFound<MemberInfo>(OutcomeCodes.MemberNotFound);
        }

        if (user.ManagedBy != DirectoryRoles.ManagedBy)
        {
            return AdminOutcome.Conflict<MemberInfo>(OutcomeCodes.DirectoryNotManaged);
        }

        await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);

        // 連 ExternalId 一起清：不然身分來源之後用同一個 Id 重新建立會撞「external id 已被使用」，也就接管不回去。
        (user.ManagedBy, user.ExternalId) = (null, null);
        var updated = await users.UpdateAsync(user);
        if (!updated.Succeeded)
        {
            return IdentityOutcomes.Rejected<MemberInfo>(updated.Errors.First());
        }

        db.StageAudit(
            actor,
            new AuditEntry(AuditActions.MemberReleaseManaged, tenantAccessor.TenantInfo().Id, "member", user.Id, new Dictionary<string, object?> { ["email"] = user.Email }),
            time);
        await db.SaveChangesAsync(cancellationToken);
        await transaction.CommitAsync(cancellationToken);
        return AdminOutcome.Ok(members.Info(user));
    }

    private async Task<DirectoryStatus> ReadStatusAsync(CancellationToken cancellationToken)
    {
        var connection = await db.DirectoryConnections.AsNoTracking().SingleOrDefaultAsync(cancellationToken);
        var tokens = await db.DirectoryTokens.AsNoTracking().OrderByDescending(t => t.CreatedAt).ToListAsync(cancellationToken);
        var managed = await users.Users.CountAsync(u => u.ManagedBy == DirectoryRoles.ManagedBy, cancellationToken);
        return connection is null
            ? new DirectoryStatus(new DirectorySettings(false, CustomerRoles.Viewer, false), null, null, 0, 0, 0, 0, managed, [.. tokens.Select(Info)])
            : new DirectoryStatus(
                new DirectorySettings(connection.Enabled, connection.DefaultRole, connection.AdoptExistingMembers),
                connection.LastRequestAt, connection.LastFailureCode,
                connection.CreatedCount, connection.UpdatedCount, connection.DeactivatedCount, connection.RejectedCount, managed, [.. tokens.Select(Info)]);
    }

    private void Stage(ClaimsPrincipal actor, string action, string targetType, string targetId, IReadOnlyDictionary<string, object?> detail) =>
        db.StageAudit(actor, new AuditEntry(action, tenantAccessor.TenantInfo().Id, targetType, targetId, detail), time);

    private static DirectoryTokenInfo Info(DirectoryToken t) =>
        new(t.Id, t.Name, t.Prefix, t.CreatedAt, t.ExpiresAt, t.RevokedAt, t.LastUsedAt);
}