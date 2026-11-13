using System.Security.Claims;

namespace EnvCite.Platform.Modules.Members.DirectorySync;

// 群組（SCIM Groups）：身分來源推送群組與成員，客戶的擁有者決定群組對映的角色（DirectoryManagement.SetGroupRoleAsync）。
// 與成員同一套規則：一個請求一個交易、稽核寫不進去就沒有變更、重送冪等、沒有變化的重送什麼都不寫。
internal sealed partial class DirectoryProvisioning
{
    private const int NameLength = 200;

    public async Task<AdminOutcome<DirectoryGroupPage>> ListGroupsAsync(
        ClaimsPrincipal actor, int skip, int take, string? externalId, string? displayName, CancellationToken cancellationToken)
    {
        if (await GateAsync<DirectoryGroupPage>(cancellationToken) is { } closed)
            return closed;

        var query = db.DirectoryGroups.AsNoTracking();
        if (!string.IsNullOrEmpty(externalId))
            query = query.Where(g => g.ExternalId == externalId);

        if (!string.IsNullOrWhiteSpace(displayName))
        {
            var normalized = DirectoryGroup.Normalize(displayName);
            query = query.Where(g => g.NormalizedName == normalized);
        }

        var page = Math.Clamp(take, 0, options.Value.MaxPageSize);
        var total = await query.CountAsync(cancellationToken);
        var found = await query.Include(g => g.Members).OrderBy(g => g.NormalizedName).ThenBy(g => g.Id)
            .Skip(Math.Max(skip, 0)).Take(page).ToListAsync(cancellationToken);
        await SeenAsync(cancellationToken);
        return AdminOutcome.Ok(new DirectoryGroupPage([.. found.Select(MapGroup)], total));
    }

    public async Task<AdminOutcome<DirectoryGroupDetail>> FindGroupAsync(ClaimsPrincipal actor, Guid groupId, CancellationToken cancellationToken)
    {
        if (await GateAsync<DirectoryGroupDetail>(cancellationToken) is { } closed)
            return closed;

        await SeenAsync(cancellationToken);
        return await db.DirectoryGroups.AsNoTracking().Include(g => g.Members).SingleOrDefaultAsync(g => g.Id == groupId, cancellationToken) is { } group
            ? AdminOutcome.Ok(MapGroup(group))
            : AdminOutcome.NotFound<DirectoryGroupDetail>(OutcomeCodes.DirectoryGroupNotFound);
    }

    public async Task<AdminOutcome<DirectoryGroupDetail>> CreateGroupAsync(
        ClaimsPrincipal actor, DirectoryGroupInput input, CancellationToken cancellationToken)
    {
        if (await GateAsync<DirectoryGroupDetail>(cancellationToken) is { } closed)
            return closed;

        var name = (input.DisplayName ?? string.Empty).Trim();
        var externalId = NormalizeExternalId(input.ExternalId);
        if (name.Length is 0 or > NameLength)
            return await RejectAsync<DirectoryGroupDetail>(actor, null, OutcomeCodes.DirectoryGroupInvalid, AdminStatus.Invalid, audit: false, cancellationToken, "group");

        if (externalId is { Length: 0 } or { Length: > 200 })
            return await RejectAsync<DirectoryGroupDetail>(actor, name, OutcomeCodes.DirectoryExternalIdInvalid, AdminStatus.Invalid, audit: false, cancellationToken, "group");

        var memberIds = (input.Members ?? []).Distinct(StringComparer.Ordinal).ToList();
        if (!await AllManagedAsync(memberIds, cancellationToken))
            return await RejectAsync<DirectoryGroupDetail>(actor, name, OutcomeCodes.DirectoryGroupMemberInvalid, AdminStatus.Invalid, audit: true, cancellationToken, "group");

        var normalized = DirectoryGroup.Normalize(name);
        if (await db.DirectoryGroups.AnyAsync(g => g.NormalizedName == normalized, cancellationToken))
            return await RejectAsync<DirectoryGroupDetail>(actor, name, OutcomeCodes.DirectoryGroupNameTaken, AdminStatus.Conflict, audit: true, cancellationToken, "group");

        if (externalId is not null && await db.DirectoryGroups.AnyAsync(g => g.ExternalId == externalId, cancellationToken))
            return await RejectAsync<DirectoryGroupDetail>(actor, name, OutcomeCodes.DirectoryExternalIdTaken, AdminStatus.Conflict, audit: true, cancellationToken, "group");

        return await InTransactionAsync(async () =>
        {
            var group = new DirectoryGroup { DisplayName = name, NormalizedName = normalized, ExternalId = externalId, UpdatedAt = time.GetUtcNow() };
            group.Members.AddRange(memberIds.Select(id => new DirectoryGroupMember { GroupId = group.Id, UserId = id }));
            db.DirectoryGroups.Add(group);

            // 新群組沒有對映角色（由客戶的擁有者設定），所以建立本身不改任何人的角色。
            db.StageAudit(
                actor,
                new AuditEntry(AuditActions.DirectoryGroupCreated, TenantId(), "directory_group", group.Id.ToString(), new Dictionary<string, object?>
                {
                    ["name"] = name,
                    ["members"] = memberIds.Count,
                }),
                time);
            await RecordAsync(cancellationToken: cancellationToken);
            await db.SaveChangesAsync(cancellationToken);
            return AdminOutcome.Ok(MapGroup(group));
        }, cancellationToken);
    }

    public async Task<AdminOutcome<DirectoryGroupDetail>> UpdateGroupAsync(
        ClaimsPrincipal actor, Guid groupId, DirectoryGroupPatch patch, CancellationToken cancellationToken)
    {
        if (await GateAsync<DirectoryGroupDetail>(cancellationToken) is { } closed)
            return closed;

        if (await db.DirectoryGroups.Include(g => g.Members).SingleOrDefaultAsync(g => g.Id == groupId, cancellationToken) is not { } group)
            return AdminOutcome.NotFound<DirectoryGroupDetail>(OutcomeCodes.DirectoryGroupNotFound);

        var name = patch.DisplayName is null ? group.DisplayName : patch.DisplayName.Trim();
        var externalId = NormalizeExternalId(patch.ExternalId);
        if (name.Length is 0 or > NameLength)
            return await RejectAsync<DirectoryGroupDetail>(actor, group.DisplayName, OutcomeCodes.DirectoryGroupInvalid, AdminStatus.Invalid, audit: false, cancellationToken, "group");

        if (externalId is { Length: 0 } or { Length: > 200 })
            return await RejectAsync<DirectoryGroupDetail>(actor, group.DisplayName, OutcomeCodes.DirectoryExternalIdInvalid, AdminStatus.Invalid, audit: false, cancellationToken, "group");

        var normalized = DirectoryGroup.Normalize(name);
        if (normalized != group.NormalizedName && await db.DirectoryGroups.AnyAsync(g => g.NormalizedName == normalized && g.Id != group.Id, cancellationToken))
            return await RejectAsync<DirectoryGroupDetail>(actor, name, OutcomeCodes.DirectoryGroupNameTaken, AdminStatus.Conflict, audit: true, cancellationToken, "group");

        if (externalId is not null && externalId != group.ExternalId
            && await db.DirectoryGroups.AnyAsync(g => g.ExternalId == externalId && g.Id != group.Id, cancellationToken))
        {
            return await RejectAsync<DirectoryGroupDetail>(actor, name, OutcomeCodes.DirectoryExternalIdTaken, AdminStatus.Conflict, audit: true, cancellationToken, "group");
        }

        // 成員的變更依序套用在目前的成員上（Replace 整組換、Add 加、Remove 移除；不在的移除略過，所以重送安全）。
        var current = group.Members.Select(m => m.UserId).ToHashSet(StringComparer.Ordinal);
        var next = new HashSet<string>(current, StringComparer.Ordinal);
        var referenced = new List<string>();
        foreach (var change in patch.Members ?? [])
        {
            switch (change.Kind)
            {
                case GroupMemberOp.Replace:
                    next = new HashSet<string>(change.Ids, StringComparer.Ordinal);
                    referenced.AddRange(change.Ids);
                    break;
                case GroupMemberOp.Add:
                    next.UnionWith(change.Ids);
                    referenced.AddRange(change.Ids);
                    break;
                default:
                    next.ExceptWith(change.Ids);
                    break;
            }
        }

        // 只有「這次新加入」的才需要是受管成員：已經在群組裡的人之後被解除管理，不該讓整個更新失敗。
        var added = next.Except(current).ToList();
        if (!await AllManagedAsync(added, cancellationToken))
            return await RejectAsync<DirectoryGroupDetail>(actor, group.DisplayName, OutcomeCodes.DirectoryGroupMemberInvalid, AdminStatus.Invalid, audit: true, cancellationToken, "group");

        var removed = current.Except(next).ToList();
        var renamed = name != group.DisplayName;
        var linked = externalId is not null && externalId != group.ExternalId;
        if (!renamed && !linked && added.Count == 0 && removed.Count == 0)
        {
            await SeenAsync(cancellationToken);
            return AdminOutcome.Ok(MapGroup(group));
        }

        return await InTransactionAsync(async () =>
        {
            group.DisplayName = name;
            group.NormalizedName = normalized;
            group.ExternalId = externalId ?? group.ExternalId;
            group.UpdatedAt = time.GetUtcNow();
            foreach (var gone in group.Members.Where(m => removed.Contains(m.UserId)).ToList())
            {
                group.Members.Remove(gone);
                db.DirectoryGroupMembers.Remove(gone);
            }

            foreach (var id in added)
                group.Members.Add(new DirectoryGroupMember { GroupId = group.Id, UserId = id });

            await db.SaveChangesAsync(cancellationToken);

            // 沒有對映角色的群組，成員變動不影響任何人的角色。
            var changed = group.Role is null ? 0 : await groupRoles.RecomputeAsync(actor, [.. added, .. removed], cancellationToken);
            db.StageAudit(
                actor,
                new AuditEntry(AuditActions.DirectoryGroupUpdated, TenantId(), "directory_group", group.Id.ToString(), new Dictionary<string, object?>
                {
                    ["name"] = group.DisplayName,
                    ["renamed"] = renamed,
                    ["added"] = added.Count,
                    ["removed"] = removed.Count,
                    ["members_changed"] = changed,
                }),
                time);
            await RecordAsync(updated: changed, cancellationToken: cancellationToken);
            await db.SaveChangesAsync(cancellationToken);
            return AdminOutcome.Ok(MapGroup(group));
        }, cancellationToken);
    }

    public async Task<AdminOutcome<bool>> DeleteGroupAsync(ClaimsPrincipal actor, Guid groupId, CancellationToken cancellationToken)
    {
        if (await GateAsync<bool>(cancellationToken) is { } closed)
            return closed;

        if (await db.DirectoryGroups.Include(g => g.Members).SingleOrDefaultAsync(g => g.Id == groupId, cancellationToken) is not { } group)
            return AdminOutcome.NotFound<bool>(OutcomeCodes.DirectoryGroupNotFound);

        return await InTransactionAsync(async () =>
        {
            var members = group.Members.Select(m => m.UserId).ToList();
            db.DirectoryGroups.Remove(group);
            await db.SaveChangesAsync(cancellationToken);

            // 刪掉的群組給的角色跟著消失：成員依剩下的群組（或預設角色）重算。
            var changed = group.Role is null ? 0 : await groupRoles.RecomputeAsync(actor, members, cancellationToken);
            db.StageAudit(
                actor,
                new AuditEntry(AuditActions.DirectoryGroupDeleted, TenantId(), "directory_group", group.Id.ToString(), new Dictionary<string, object?>
                {
                    ["name"] = group.DisplayName,
                    ["members"] = members.Count,
                    ["members_changed"] = changed,
                }),
                time);
            await RecordAsync(updated: changed, cancellationToken: cancellationToken);
            await db.SaveChangesAsync(cancellationToken);
            return AdminOutcome.Ok(true);
        }, cancellationToken);
    }

    /// <summary>每一個 Id 都是這個租戶裡由目錄同步管理的成員（租戶的查詢濾鏡讓別的租戶的 Id 算不存在）。</summary>
    private async Task<bool> AllManagedAsync(List<string> ids, CancellationToken cancellationToken)
    {
        if (ids.Count == 0)
            return true;

        var managed = await users.Users.CountAsync(u => ids.Contains(u.Id) && u.ManagedBy == DirectoryRoles.ManagedBy, cancellationToken);
        return managed == ids.Count;
    }

    private static DirectoryGroupDetail MapGroup(DirectoryGroup g) =>
        new(g.Id, g.ExternalId, g.DisplayName, g.Role, [.. g.Members.Select(m => m.UserId).Order(StringComparer.Ordinal)]);
}