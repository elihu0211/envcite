using System.Security.Claims;

using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Identity;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Options;

namespace EnvCite.Platform.Modules.Members.DirectorySync;

/// <inheritdoc cref="IDirectoryProvisioning"/>
/// <remarks>
/// 一個請求一個交易：成員的變更、狀態計數與稽核事件一起成功或一起失敗，所以稽核寫不進去就沒有變更（fail closed），
/// 身分來源收到 5xx 會重送。寄邀請信在交易之內但不使它失敗（<see cref="MailDelivery"/> 記進稽核）；
/// 信寄出後才提交失敗的話，那封信的連結指向不存在的帳號，無害，重送時會再寄一封。
/// </remarks>
internal sealed class DirectoryProvisioning(
    UserManager<ApplicationUser> users,
    PlatformDbContext db,
    MemberCore members,
    IMailSender mail,
    IOptions<DirectoryOptions> options,
    IMultiTenantContextAccessor<TenantInfo> tenantAccessor,
    TimeProvider time) : IDirectoryProvisioning
{
    private static readonly TimeSpan SeenThrottle = TimeSpan.FromMinutes(1);

    public async Task<AdminOutcome<DirectoryPage>> ListAsync(
        ClaimsPrincipal actor, int skip, int take, string? externalId, string? email, CancellationToken cancellationToken)
    {
        if (await GateAsync<DirectoryPage>(cancellationToken) is { } closed)
        {
            return closed;
        }

        var query = users.Users.AsNoTracking();
        if (!string.IsNullOrEmpty(externalId))
        {
            query = query.Where(u => u.ExternalId == externalId);
        }

        if (!string.IsNullOrEmpty(email))
        {
            var normalized = users.NormalizeEmail(email);
            query = query.Where(u => u.NormalizedEmail == normalized);
        }

        var page = Math.Clamp(take, 0, options.Value.MaxPageSize);
        var total = await query.CountAsync(cancellationToken);
        var found = await query.OrderBy(u => u.Email).ThenBy(u => u.Id).Skip(Math.Max(skip, 0)).Take(page).ToListAsync(cancellationToken);
        await SeenAsync(cancellationToken);
        return AdminOutcome.Ok(new DirectoryPage([.. found.Select(Map)], total));
    }

    public async Task<AdminOutcome<DirectoryMember>> FindAsync(ClaimsPrincipal actor, string userId, CancellationToken cancellationToken)
    {
        if (await GateAsync<DirectoryMember>(cancellationToken) is { } closed)
        {
            return closed;
        }

        await SeenAsync(cancellationToken);

        // 租戶的查詢濾鏡讓別的租戶的人在這裡找不到。
        return await users.FindByIdAsync(userId) is { } user
            ? AdminOutcome.Ok(Map(user))
            : AdminOutcome.NotFound<DirectoryMember>(OutcomeCodes.MemberNotFound);
    }

    public async Task<AdminOutcome<DirectoryMember>> ProvisionAsync(
        ClaimsPrincipal actor, DirectoryProvision member, CancellationToken cancellationToken)
    {
        if (await GateAsync<DirectoryMember>(cancellationToken) is { } closed)
        {
            return closed;
        }

        var connection = await ConnectionAsync(cancellationToken);
        var role = member.Role ?? connection.DefaultRole;
        var email = (member.Email ?? string.Empty).Trim();
        var externalId = NormalizeExternalId(member.ExternalId);
        if (NewAccountInput.Problem(email, member.DisplayName, role) is { } problem)
        {
            return await RejectAsync<DirectoryMember>(actor, null, problem, AdminStatus.Invalid, audit: false, cancellationToken);
        }

        if (externalId is { Length: 0 } or { Length: > 200 })
        {
            return await RejectAsync<DirectoryMember>(actor, null, OutcomeCodes.DirectoryExternalIdInvalid, AdminStatus.Invalid, audit: false, cancellationToken);
        }

        if (!DirectoryRoles.Assignable.Contains(role))
        {
            return await RejectAsync<DirectoryMember>(actor, email, OutcomeCodes.DirectoryRoleNotMappable, AdminStatus.Forbidden, audit: true, cancellationToken);
        }

        // 重複建立是衝突，不是第二筆：身分來源重送、逾時後重試都會走到這裡。
        if (externalId is not null && await users.Users.AnyAsync(u => u.ExternalId == externalId, cancellationToken))
        {
            return await RejectAsync<DirectoryMember>(actor, email, OutcomeCodes.DirectoryExternalIdTaken, AdminStatus.Conflict, audit: true, cancellationToken);
        }

        if (await users.FindByEmailAsync(email) is { } existing)
        {
            return await AdoptAsync(actor, connection, existing, externalId, member, cancellationToken);
        }

        // 新成員走邀請信，沒有寄信能力就建不起來；不建一個沒人登得進去的帳號。
        if (member.Active && !mail.Enabled)
        {
            return await RejectAsync<DirectoryMember>(actor, email, OutcomeCodes.MailNotEnabled, AdminStatus.Conflict, audit: true, cancellationToken);
        }

        return await InTransactionAsync(async () =>
        {
            var now = time.GetUtcNow();
            var user = new ApplicationUser
            {
                UserName = email,
                Email = email,
                DisplayName = Clean(member.DisplayName),
                Role = role,
                ExternalId = externalId,
                ManagedBy = DirectoryRoles.ManagedBy,
                SyncedAt = now,
                Disabled = !member.Active,
            };

            // 沒有密碼：在對方開啟邀請連結設定之前，這個帳號登不進去。
            var created = await users.CreateAsync(user);
            if (!created.Succeeded)
            {
                return IdentityOutcomes.CreateFailed<DirectoryMember>(created, OutcomeCodes.MemberEmailTaken);
            }

            var delivery = member.Active ? await members.SendInvitationAsync(user, tenantAccessor.TenantInfo(), cancellationToken) : MailDelivery.NotRequested;
            StageAudit(actor, AuditActions.MemberProvisioned, user, new Dictionary<string, object?>
            {
                ["email"] = user.Email,
                ["role"] = user.Role,
                ["active"] = member.Active,
                ["mail"] = delivery.AuditStatus(),
            });
            await RecordAsync(created: 1, cancellationToken: cancellationToken);
            await db.SaveChangesAsync(cancellationToken);
            return AdminOutcome.Ok(Map(user));
        }, cancellationToken);
    }

    public async Task<AdminOutcome<DirectoryMember>> UpdateAsync(
        ClaimsPrincipal actor, string userId, DirectoryPatch patch, CancellationToken cancellationToken)
    {
        if (await GateAsync<DirectoryMember>(cancellationToken) is { } closed)
        {
            return closed;
        }

        if (await users.FindByIdAsync(userId) is not { } user)
        {
            return AdminOutcome.NotFound<DirectoryMember>(OutcomeCodes.MemberNotFound);
        }

        return await ApplyAsync(actor, user, patch, deleting: false, cancellationToken);
    }

    public async Task<AdminOutcome<DirectoryMember>> DeactivateAsync(ClaimsPrincipal actor, string userId, CancellationToken cancellationToken)
    {
        if (await GateAsync<DirectoryMember>(cancellationToken) is { } closed)
        {
            return closed;
        }

        if (await users.FindByIdAsync(userId) is not { } user)
        {
            return AdminOutcome.NotFound<DirectoryMember>(OutcomeCodes.MemberNotFound);
        }

        return await ApplyAsync(actor, user, new DirectoryPatch(Active: false), deleting: true, cancellationToken);
    }

    private async Task<AdminOutcome<DirectoryMember>> AdoptAsync(
        ClaimsPrincipal actor, DirectoryConnection connection, ApplicationUser existing, string? externalId, DirectoryProvision member,
        CancellationToken cancellationToken)
    {
        // 已經是同步建立的：同 email 再建一次是重複。
        if (existing.ManagedBy == DirectoryRoles.ManagedBy)
        {
            return await RejectAsync<DirectoryMember>(actor, existing.Email, OutcomeCodes.MemberEmailTaken, AdminStatus.Conflict, audit: true, cancellationToken);
        }

        if (!connection.AdoptExistingMembers)
        {
            return await RejectAsync<DirectoryMember>(actor, existing.Email, OutcomeCodes.DirectoryAdoptRequired, AdminStatus.Conflict, audit: true, cancellationToken);
        }

        // 擁有者只由人指派，也就不交給身分來源管：接管等於讓下一輪同步能停用他。
        if (existing.Role == CustomerRoles.Owner)
        {
            return await RejectAsync<DirectoryMember>(actor, existing.Email, OutcomeCodes.DirectoryRoleNotMappable, AdminStatus.Forbidden, audit: true, cancellationToken);
        }

        return await InTransactionAsync(async () =>
        {
            // 接管只連結，不改角色與停用：人原本設定的維持原樣，之後才由身分來源的更新決定。
            existing.ExternalId = externalId;
            existing.ManagedBy = DirectoryRoles.ManagedBy;
            existing.SyncedAt = time.GetUtcNow();
            var updated = await users.UpdateAsync(existing);
            if (!updated.Succeeded)
            {
                return IdentityOutcomes.Rejected<DirectoryMember>(updated.Errors.First());
            }

            StageAudit(actor, AuditActions.MemberAdopted, existing, new Dictionary<string, object?>
            {
                ["email"] = existing.Email,
                ["role"] = existing.Role,
            });
            await RecordAsync(updated: 1, cancellationToken: cancellationToken);
            await db.SaveChangesAsync(cancellationToken);
            return AdminOutcome.Ok(Map(existing));
        }, cancellationToken);
    }

    private async Task<AdminOutcome<DirectoryMember>> ApplyAsync(
        ClaimsPrincipal actor, ApplicationUser user, DirectoryPatch patch, bool deleting, CancellationToken cancellationToken)
    {
        // 不是同步管理的人，同步不能動：要先經 POST 接管（租戶允許時）。
        if (user.ManagedBy != DirectoryRoles.ManagedBy)
        {
            return await RejectAsync<DirectoryMember>(actor, user.Email, OutcomeCodes.DirectoryAdoptRequired, AdminStatus.Conflict, audit: true, cancellationToken);
        }

        if (patch.Email is { } email && !string.Equals(email.Trim(), user.Email, StringComparison.OrdinalIgnoreCase))
        {
            return await RejectAsync<DirectoryMember>(actor, user.Email, OutcomeCodes.DirectoryEmailImmutable, AdminStatus.Conflict, audit: true, cancellationToken);
        }

        var externalId = NormalizeExternalId(patch.ExternalId);
        if (externalId is { Length: 0 } or { Length: > 200 })
        {
            return await RejectAsync<DirectoryMember>(actor, user.Email, OutcomeCodes.DirectoryExternalIdInvalid, AdminStatus.Invalid, audit: false, cancellationToken);
        }

        if (patch.DisplayName is { Length: > 200 })
        {
            return await RejectAsync<DirectoryMember>(actor, user.Email, OutcomeCodes.DisplayNameTooLong, AdminStatus.Invalid, audit: false, cancellationToken);
        }

        if (patch.Role is { } role)
        {
            if (!CustomerRoles.All.Contains(role))
            {
                return await RejectAsync<DirectoryMember>(actor, user.Email, OutcomeCodes.RoleInvalid, AdminStatus.Invalid, audit: false, cancellationToken);
            }

            if (!DirectoryRoles.Assignable.Contains(role))
            {
                return await RejectAsync<DirectoryMember>(actor, user.Email, OutcomeCodes.DirectoryRoleNotMappable, AdminStatus.Forbidden, audit: true, cancellationToken);
            }
        }

        if (externalId is not null && externalId != user.ExternalId
            && await users.Users.AnyAsync(u => u.ExternalId == externalId && u.Id != user.Id, cancellationToken))
        {
            return await RejectAsync<DirectoryMember>(actor, user.Email, OutcomeCodes.DirectoryExternalIdTaken, AdminStatus.Conflict, audit: true, cancellationToken);
        }

        var leavesOwner = user.Role == CustomerRoles.Owner && (patch.Role is { } r && r != CustomerRoles.Owner);
        var disables = patch.Active == false && !user.Disabled;
        if ((leavesOwner || (disables && user.Role == CustomerRoles.Owner)) && !await AnotherActiveOwnerAsync(user, cancellationToken))
        {
            return await RejectAsync<DirectoryMember>(actor, user.Email, OutcomeCodes.DirectoryLastOwner, AdminStatus.Conflict, audit: true, cancellationToken);
        }

        var enables = patch.Active == true && user.Disabled;
        var displayName = patch.DisplayName is null ? user.DisplayName : Clean(patch.DisplayName);
        var changedAttributes = new List<string>();
        if (displayName != user.DisplayName)
        {
            changedAttributes.Add("display_name");
        }

        if (externalId is not null && externalId != user.ExternalId)
        {
            changedAttributes.Add("external_id");
        }

        if (patch.Role is { } newRole && newRole != user.Role)
        {
            changedAttributes.Add("role");
        }

        if (changedAttributes.Count == 0 && !disables && !enables)
        {
            // 沒有變化的重送：什麼都不寫，連同步時間也不動。
            await SeenAsync(cancellationToken);
            return AdminOutcome.Ok(Map(user));
        }

        return await InTransactionAsync(async () =>
        {
            var previousRole = user.Role;
            user.DisplayName = displayName;
            user.ExternalId = externalId ?? user.ExternalId;
            user.Role = patch.Role ?? user.Role;
            user.SyncedAt = time.GetUtcNow();
            if (disables)
            {
                user.Disabled = true;
            }
            else if (enables)
            {
                user.Disabled = false;
            }

            var updated = await users.UpdateAsync(user);
            if (!updated.Succeeded)
            {
                return IdentityOutcomes.Rejected<DirectoryMember>(updated.Errors.First());
            }

            // 角色在 cookie 的 claim 裡，停用要立刻擋下、重新啟用要從乾淨狀態開始：都換安全戳記，舊 session 失效。
            if (disables || enables || changedAttributes.Contains("role"))
            {
                await users.UpdateSecurityStampAsync(user);
            }

            // 重新啟用一個還沒設過密碼的人，把邀請信補上；寄信沒啟用就只啟用，不拒絕（營運者可以改用暫時密碼），結果記在稽核。
            var mailStatus = (string?)null;
            if (enables && string.IsNullOrEmpty(user.PasswordHash))
            {
                mailStatus = mail.Enabled
                    ? (await members.SendInvitationAsync(user, tenantAccessor.TenantInfo(), cancellationToken)).AuditStatus()
                    : MailDelivery.Disabled.AuditStatus();
            }

            if (disables)
            {
                StageAudit(actor, AuditActions.MemberDeprovisioned, user, new Dictionary<string, object?> { ["email"] = user.Email, ["deleted"] = deleting });
                await RecordAsync(deactivated: 1, cancellationToken: cancellationToken);
            }
            else if (enables)
            {
                StageAudit(actor, AuditActions.MemberReprovisioned, user, new Dictionary<string, object?> { ["email"] = user.Email, ["mail"] = mailStatus });
                await RecordAsync(updated: 1, cancellationToken: cancellationToken);
            }

            if (changedAttributes.Count > 0)
            {
                StageAudit(actor, AuditActions.MemberAttributeSynced, user, new Dictionary<string, object?>
                {
                    ["email"] = user.Email,
                    ["fields"] = changedAttributes,
                    ["previous_role"] = changedAttributes.Contains("role") ? previousRole : null,
                    ["current_role"] = changedAttributes.Contains("role") ? user.Role : null,
                });
                if (!disables && !enables)
                {
                    await RecordAsync(updated: 1, cancellationToken: cancellationToken);
                }
            }

            await db.SaveChangesAsync(cancellationToken);
            return AdminOutcome.Ok(Map(user));
        }, cancellationToken);
    }

    /// <summary>租戶裡除了這位之外，還有沒有啟用中的擁有者。</summary>
    private Task<bool> AnotherActiveOwnerAsync(ApplicationUser user, CancellationToken cancellationToken) =>
        users.Users.AnyAsync(u => u.Role == CustomerRoles.Owner && !u.Disabled && u.Id != user.Id, cancellationToken);

    /// <summary>
    /// 功能面沒開、或這個租戶沒開連線：路徑看起來就不存在（NotFound），不透露租戶有沒有這個功能。
    /// 開著就回 null，呼叫端繼續。
    /// </summary>
    private async Task<AdminOutcome<T>?> GateAsync<T>(CancellationToken cancellationToken)
    {
        if (!options.Value.Enabled)
        {
            return AdminOutcome.NotFound<T>(OutcomeCodes.DirectoryNotEnabled);
        }

        var connection = await db.DirectoryConnections.AsNoTracking().SingleOrDefaultAsync(cancellationToken);
        return connection is { Enabled: true } ? null : AdminOutcome.NotFound<T>(OutcomeCodes.DirectoryNotEnabled);
    }

    private Task<DirectoryConnection> ConnectionAsync(CancellationToken cancellationToken) =>
        db.DirectoryConnections.AsNoTracking().SingleAsync(cancellationToken);

    /// <summary>
    /// 被政策拒絕：記一筆稽核（<paramref name="audit"/> 時）並累計；輸入不合法這類單純的錯誤只累計，不記稽核。
    /// 拒絕本身不改任何成員，所以這個交易只有稽核與狀態。
    /// </summary>
    private async Task<AdminOutcome<T>> RejectAsync<T>(
        ClaimsPrincipal actor, string? email, string code, AdminStatus status, bool audit, CancellationToken cancellationToken)
    {
        await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);
        if (audit)
        {
            db.StageAudit(
                actor,
                new AuditEntry(AuditActions.DirectorySyncRejected, TenantId(), "member", null, new Dictionary<string, object?> { ["email"] = email, ["code"] = code }),
                time);
        }

        await RecordAsync(rejected: 1, failureCode: code, cancellationToken: cancellationToken);
        await db.SaveChangesAsync(cancellationToken);
        await transaction.CommitAsync(cancellationToken);
        return status switch
        {
            AdminStatus.Forbidden => AdminOutcome.Forbidden<T>(code),
            AdminStatus.Conflict => AdminOutcome.Conflict<T>(code),
            _ => AdminOutcome.Invalid<T>(code),
        };
    }

    private async Task<AdminOutcome<T>> InTransactionAsync<T>(Func<Task<AdminOutcome<T>>> body, CancellationToken cancellationToken)
    {
        await using var transaction = await db.Database.BeginTransactionAsync(cancellationToken);
        var outcome = await body();
        if (!outcome.IsOk)
        {
            // 內層失敗（例如 Identity 拒絕）：什麼都不留。
            await transaction.RollbackAsync(cancellationToken);
            db.ChangeTracker.Clear();
            return outcome;
        }

        await transaction.CommitAsync(cancellationToken);
        return outcome;
    }

    private void StageAudit(ClaimsPrincipal actor, string action, ApplicationUser target, Dictionary<string, object?> detail) =>
        db.StageAudit(actor, new AuditEntry(action, TenantId(), "member", target.Id, detail), time);

    private string TenantId() => tenantAccessor.TenantInfo().Id;

    /// <summary>
    /// 最後請求時間與計數：用 SQL 的原子遞增（身分來源會平行送很多請求，讀了再寫會漏算）。
    /// 成功的請求清掉上次的失敗代碼；讀取只更新時間，且一分鐘最多一次。
    /// </summary>
    private async Task RecordAsync(
        int created = 0, int updated = 0, int deactivated = 0, int rejected = 0, string? failureCode = null, CancellationToken cancellationToken = default)
    {
        var now = time.GetUtcNow();
        await db.DirectoryConnections.ExecuteUpdateAsync(
            set => set
                .SetProperty(c => c.LastRequestAt, now)
                .SetProperty(c => c.LastFailureCode, rejected > 0 ? failureCode : null)
                .SetProperty(c => c.CreatedCount, c => c.CreatedCount + created)
                .SetProperty(c => c.UpdatedCount, c => c.UpdatedCount + updated)
                .SetProperty(c => c.DeactivatedCount, c => c.DeactivatedCount + deactivated)
                .SetProperty(c => c.RejectedCount, c => c.RejectedCount + rejected),
            cancellationToken);
    }

    private async Task SeenAsync(CancellationToken cancellationToken)
    {
        var now = time.GetUtcNow();
        var stale = now - SeenThrottle;
        await db.DirectoryConnections
            .Where(c => c.LastRequestAt == null || c.LastRequestAt < stale)
            .ExecuteUpdateAsync(set => set.SetProperty(c => c.LastRequestAt, now), cancellationToken);
    }

    private static string? NormalizeExternalId(string? externalId) => externalId?.Trim();

    private static string? Clean(string? displayName) => string.IsNullOrWhiteSpace(displayName) ? null : displayName.Trim();

    internal static DirectoryMember Map(ApplicationUser u) =>
        new(u.Id, u.ExternalId, u.Email ?? string.Empty, u.DisplayName, u.Role, !u.Disabled,
            string.IsNullOrEmpty(u.PasswordHash), u.ManagedBy == DirectoryRoles.ManagedBy, u.SyncedAt);
}

internal static class TenantAccessorExtensions
{
    public static TenantInfo TenantInfo(this IMultiTenantContextAccessor<TenantInfo> accessor) =>
        accessor.MultiTenantContext?.TenantInfo ?? throw new InvalidOperationException("No tenant resolved for the directory request.");
}