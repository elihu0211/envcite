using System.Security.Claims;
using System.Text.Json;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 群組對映角色：群組由身分來源推送，角色由客戶的擁有者對映；受管成員的角色 = 所屬有對映的群組裡最高的，
/// 一個都沒有就是預設角色。涵蓋重算的規則、擁有者與解除管理的例外、冪等、租戶隔離、稽核與 fail closed。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed class DirectoryGroupTests(PlatformFactory factory) : DirectoryTestBase(factory)
{
    private Task<AdminOutcome<DirectoryMember>> NewMemberAsync(Org org) =>
        InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().ProvisionAsync(org.Idp, Person(), Ct));

    private async Task<string> MemberIdAsync(Org org) => (await NewMemberAsync(org)).Value!.Id;

    private Task<AdminOutcome<DirectoryGroupDetail>> CreateAsync(Org org, string name, params string[] members) =>
        InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>()
            .CreateGroupAsync(org.Idp, new DirectoryGroupInput(name, $"g-{Guid.NewGuid():N}", members), Ct));

    private Task<AdminOutcome<DirectoryGroupDetail>> UpdateAsync(Org org, Guid id, DirectoryGroupPatch patch) =>
        InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().UpdateGroupAsync(org.Idp, id, patch, Ct));

    private Task<AdminOutcome<DirectoryGroupInfo>> MapAsync(Org org, Guid id, string? role) =>
        InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().SetGroupRoleAsync(org.Owner, id, role, Ct));

    private Task<AdminOutcome<DirectoryStatus>> StatusAsync(Org org) =>
        InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().GetStatusAsync(org.Owner, Ct));

    private async Task<string> RoleAsync(Org org, string userId) => (await UserAsync(org, userId)).Role;

    private static DirectoryGroupPatch Members(GroupMemberOp kind, params string[] ids) => new(Members: [new DirectoryGroupMemberChange(kind, ids)]);

    [Fact]
    public async Task ANewGroupHasNoRoleAndCreatingItChangesNobodysRole()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("grp-a");
            var member = await MemberIdAsync(org);

            var created = await CreateAsync(org, "Engineering", member);

            Assert.True(created.IsOk, created.Message);
            Assert.Null(created.Value!.Role);
            Assert.Equal([member], created.Value.Members);
            Assert.Equal(CustomerRoles.Viewer, await RoleAsync(org, member));
            Assert.Single(await AuditAsync(org, AuditActions.DirectoryGroupCreated));
            var info = Assert.Single((await StatusAsync(org)).Value!.Groups);
            Assert.Equal(("Engineering", 1, (string?)null), (info.DisplayName, info.MemberCount, info.Role));
        });
    }

    [Fact]
    public async Task OnlyManagedMembersOfThisTenantCanBeInAGroup()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("grp-b");
            var other = await OrgAsync("grp-b2");
            var (manualId, _) = await AddMemberAsync(Factory, org.Tenant.Id, NewEmail(), CustomerRoles.Developer);
            var foreign = await MemberIdAsync(other);

            var manual = await CreateAsync(org, "Manual", manualId);
            var unknown = await CreateAsync(org, "Unknown", Guid.NewGuid().ToString());
            var crossTenant = await CreateAsync(org, "Foreign", foreign);

            Assert.All(new[] { manual, unknown, crossTenant }, outcome =>
            {
                Assert.Equal(AdminStatus.Invalid, outcome.Status);
                Assert.Equal(OutcomeCodes.DirectoryGroupMemberInvalid, outcome.Code);
            });
            Assert.Empty((await StatusAsync(org)).Value!.Groups); // 整個請求被拒，沒有半個群組
        });
    }

    [Fact]
    public async Task DuplicateNamesAndExternalIdsAreConflictsButOtherTenantsMayReuseThem()
    {
        await Factory.WithMail(async () =>
        {
            var a = await OrgAsync("grp-c1");
            var b = await OrgAsync("grp-c2");
            var first = (await CreateAsync(a, "Admins")).Value!;

            var sameName = await CreateAsync(a, "  ADMINS ");
            var sameExternal = await InTenantAsync(a.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>()
                .CreateGroupAsync(a.Idp, new DirectoryGroupInput("Other", first.ExternalId), Ct));
            var blank = await CreateAsync(a, "   ");
            var elsewhere = await CreateAsync(b, "Admins");

            Assert.Equal(OutcomeCodes.DirectoryGroupNameTaken, sameName.Code);
            Assert.Equal(OutcomeCodes.DirectoryExternalIdTaken, sameExternal.Code);
            Assert.Equal(OutcomeCodes.DirectoryGroupInvalid, blank.Code);
            Assert.True(elsewhere.IsOk, elsewhere.Message);
        });
    }

    [Fact]
    public async Task MappingAGroupToARoleRecalculatesItsMembersAndAuditsEachChange()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("grp-d");
            var one = await MemberIdAsync(org);
            var two = await MemberIdAsync(org);
            var group = (await CreateAsync(org, "Engineering", one, two)).Value!;
            var stamp = (await UserAsync(org, one)).SecurityStamp;

            var mapped = await MapAsync(org, group.Id, CustomerRoles.Developer);

            Assert.True(mapped.IsOk, mapped.Message);
            Assert.Equal(CustomerRoles.Developer, mapped.Value!.Role);
            Assert.Equal(CustomerRoles.Developer, await RoleAsync(org, one));
            Assert.Equal(CustomerRoles.Developer, await RoleAsync(org, two));
            Assert.NotEqual(stamp, (await UserAsync(org, one)).SecurityStamp); // 角色在 cookie 的 claim 裡，舊 session 失效
            var synced = await AuditAsync(org, AuditActions.MemberAttributeSynced);
            Assert.Equal(2, synced.Count);
            Assert.All(synced, e =>
            {
                Assert.Equal("group", e.Detail.GetProperty("via").GetString());
                Assert.Equal(CustomerRoles.Viewer, e.Detail.GetProperty("previous_role").GetString());
                Assert.Equal(CustomerRoles.Developer, e.Detail.GetProperty("current_role").GetString());
            });
            var event_ = Assert.Single(await AuditAsync(org, AuditActions.DirectoryGroupRoleMapped));
            Assert.Equal(2, event_.Detail.GetProperty("members_changed").GetInt32());
            Assert.Equal(2, (await StatusAsync(org)).Value!.Updated);

            // 再存一次同樣的對映：什麼都不寫。
            Assert.True((await MapAsync(org, group.Id, CustomerRoles.Developer)).IsOk);
            Assert.Single(await AuditAsync(org, AuditActions.DirectoryGroupRoleMapped));
        });
    }

    [Fact]
    public async Task AMemberGetsTheHighestMappedRoleAndFallsBackToTheDefaultWhenNoMappedGroupIsLeft()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("grp-e", defaultRole: CustomerRoles.Developer);
            var member = await MemberIdAsync(org); // 新成員用預設角色
            var viewers = (await CreateAsync(org, "Readers", member)).Value!;
            var admins = (await CreateAsync(org, "Admins", member)).Value!;
            Assert.Equal(CustomerRoles.Developer, await RoleAsync(org, member));

            await MapAsync(org, viewers.Id, CustomerRoles.Viewer);
            Assert.Equal(CustomerRoles.Viewer, await RoleAsync(org, member));
            await MapAsync(org, admins.Id, CustomerRoles.Admin);
            Assert.Equal(CustomerRoles.Admin, await RoleAsync(org, member)); // 取最高

            await UpdateAsync(org, admins.Id, Members(GroupMemberOp.Remove, member));
            Assert.Equal(CustomerRoles.Viewer, await RoleAsync(org, member)); // 剩下的群組
            await UpdateAsync(org, viewers.Id, Members(GroupMemberOp.Remove, member));
            Assert.Equal(CustomerRoles.Developer, await RoleAsync(org, member)); // 一個有對映的群組都沒有：預設角色
        });
    }

    [Fact]
    public async Task OwnersAndReleasedMembersAreNeverTouchedByAGroupMapping()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("grp-f");
            var promoted = await MemberIdAsync(org);
            var released = await MemberIdAsync(org);
            var group = (await CreateAsync(org, "Engineering", promoted, released)).Value!;
            await InTenantAsync(org.Tenant, async sp =>
            {
                var users = sp.GetRequiredService<Microsoft.AspNetCore.Identity.UserManager<ApplicationUser>>();
                var user = (await users.FindByIdAsync(promoted))!;
                user.Role = CustomerRoles.Owner; // 人升的（介面鎖住，這裡直接改資料造出這個狀態）
                await users.UpdateAsync(user);
                return true;
            });
            Assert.True((await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().ReleaseManagedAsync(org.Owner, released, Ct))).IsOk);

            await MapAsync(org, group.Id, CustomerRoles.Admin);

            Assert.Equal(CustomerRoles.Owner, await RoleAsync(org, promoted));
            Assert.Equal(CustomerRoles.Viewer, await RoleAsync(org, released)); // 解除管理後回到人手上
        });
    }

    [Fact]
    public async Task UpdatingAGroupAppliesMemberChangesInOrderAndAResendWritesNothing()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("grp-g");
            var one = await MemberIdAsync(org);
            var two = await MemberIdAsync(org);
            var three = await MemberIdAsync(org);
            var group = (await CreateAsync(org, "Team", one)).Value!;
            await MapAsync(org, group.Id, CustomerRoles.Developer);

            var patch = new DirectoryGroupPatch(
                DisplayName: "Team A",
                Members: [new DirectoryGroupMemberChange(GroupMemberOp.Add, [two, three]), new DirectoryGroupMemberChange(GroupMemberOp.Remove, [one, Guid.NewGuid().ToString()])]);
            var updated = await UpdateAsync(org, group.Id, patch);
            var events = (await AuditAsync(org, AuditActions.DirectoryGroupUpdated)).Count;
            var again = await UpdateAsync(org, group.Id, patch);

            Assert.True(updated.IsOk, updated.Message);
            Assert.Equal("Team A", updated.Value!.DisplayName);
            Assert.Equal(new[] { two, three }.Order(StringComparer.Ordinal), updated.Value.Members.Order(StringComparer.Ordinal));
            Assert.Equal(CustomerRoles.Developer, await RoleAsync(org, two));
            Assert.Equal(CustomerRoles.Viewer, await RoleAsync(org, one)); // 離開有對映的群組，回到預設
            Assert.True(again.IsOk);
            Assert.Equal(events, (await AuditAsync(org, AuditActions.DirectoryGroupUpdated)).Count); // 重送：沒有新事件

            var replaced = await UpdateAsync(org, group.Id, Members(GroupMemberOp.Replace, one));
            Assert.Equal([one], replaced.Value!.Members);
            Assert.Equal(CustomerRoles.Developer, await RoleAsync(org, one));
            Assert.Equal(CustomerRoles.Viewer, await RoleAsync(org, two));

            var invalid = await UpdateAsync(org, group.Id, Members(GroupMemberOp.Add, Guid.NewGuid().ToString()));
            Assert.Equal(OutcomeCodes.DirectoryGroupMemberInvalid, invalid.Code);
            var clash = await InTenantAsync(org.Tenant, async sp =>
            {
                var other = (await sp.GetRequiredService<IDirectoryProvisioning>().CreateGroupAsync(org.Idp, new DirectoryGroupInput("Other"), Ct)).Value!;
                return await sp.GetRequiredService<IDirectoryProvisioning>().UpdateGroupAsync(org.Idp, other.Id, new DirectoryGroupPatch(DisplayName: "team a"), Ct);
            });
            Assert.Equal(OutcomeCodes.DirectoryGroupNameTaken, clash.Code);
        });
    }

    [Fact]
    public async Task DeletingAGroupTakesItsRoleAwayAndRepeatingItIs404()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("grp-h");
            var member = await MemberIdAsync(org);
            var group = (await CreateAsync(org, "Engineering", member)).Value!;
            await MapAsync(org, group.Id, CustomerRoles.Admin);
            Assert.Equal(CustomerRoles.Admin, await RoleAsync(org, member));

            var deleted = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().DeleteGroupAsync(org.Idp, group.Id, Ct));
            var again = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().DeleteGroupAsync(org.Idp, group.Id, Ct));

            Assert.True(deleted.IsOk, deleted.Message);
            Assert.Equal(CustomerRoles.Viewer, await RoleAsync(org, member));
            Assert.Equal(OutcomeCodes.DirectoryGroupNotFound, again.Code);
            Assert.Single(await AuditAsync(org, AuditActions.DirectoryGroupDeleted));
            Assert.Empty((await StatusAsync(org)).Value!.Groups);
        });
    }

    [Fact]
    public async Task OnlyTheOwnerMapsRolesAndNeverAboveAdmin()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("grp-i");
            var (adminId, _) = await AddMemberAsync(Factory, org.Tenant.Id, NewEmail(), CustomerRoles.Admin);
            var group = (await CreateAsync(org, "Engineering", await MemberIdAsync(org))).Value!;

            var asAdmin = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().SetGroupRoleAsync(Portal(adminId), group.Id, CustomerRoles.Admin, Ct));
            var owner = await MapAsync(org, group.Id, CustomerRoles.Owner);
            var bogus = await MapAsync(org, group.Id, "SuperAdmin");
            var missing = await MapAsync(org, Guid.NewGuid(), CustomerRoles.Admin);
            var unmapped = await MapAsync(org, group.Id, null);

            Assert.Equal(OutcomeCodes.DirectoryOwnerOnly, asAdmin.Code);
            Assert.Equal(OutcomeCodes.DirectoryRoleNotMappable, owner.Code);
            Assert.Equal(OutcomeCodes.RoleInvalid, bogus.Code);
            Assert.Equal(OutcomeCodes.DirectoryGroupNotFound, missing.Code);
            Assert.True(unmapped.IsOk);
            Assert.Null(unmapped.Value!.Role);
        });
    }

    [Fact]
    public async Task EachTenantOnlySeesAndChangesItsOwnGroups()
    {
        await Factory.WithMail(async () =>
        {
            var a = await OrgAsync("grp-j1");
            var b = await OrgAsync("grp-j2");
            var group = (await CreateAsync(a, "Engineering", await MemberIdAsync(a))).Value!;

            var found = await InTenantAsync(b.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().FindGroupAsync(b.Idp, group.Id, Ct));
            var updated = await UpdateAsync(b, group.Id, new DirectoryGroupPatch(DisplayName: "Hijacked"));
            var deleted = await InTenantAsync(b.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().DeleteGroupAsync(b.Idp, group.Id, Ct));
            var mapped = await MapAsync(b, group.Id, CustomerRoles.Admin);
            var listed = await InTenantAsync(b.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().ListGroupsAsync(b.Idp, 0, 50, null, null, Ct));

            Assert.All(new[] { found.Code, updated.Code, deleted.Code, mapped.Code }, code => Assert.Equal(OutcomeCodes.DirectoryGroupNotFound, code));
            Assert.Equal(0, listed.Value!.Total);
            Assert.Equal("Engineering", (await InTenantAsync(a.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().FindGroupAsync(a.Idp, group.Id, Ct))).Value!.DisplayName);
        });
    }

    [Fact]
    public async Task WhenTheAuditCannotBeWrittenTheGroupAndTheRolesDoNotChange()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("grp-k");
            var inside = await MemberIdAsync(org);
            var outside = await MemberIdAsync(org);
            var group = (await CreateAsync(org, "Engineering", inside)).Value!;
            await MapAsync(org, group.Id, CustomerRoles.Admin);
            // 稽核的 ActorId 欄位寬 320：超過就寫不進去，用來模擬稽核表不可用。
            var broken = new ClaimsPrincipal(new ClaimsIdentity([new Claim(PlatformClaimTypes.DirectoryTokenId, new string('x', 400))], "directory"));

            await Assert.ThrowsAnyAsync<DbUpdateException>(() => InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>()
                .UpdateGroupAsync(broken, group.Id, new DirectoryGroupPatch(Members: [new DirectoryGroupMemberChange(GroupMemberOp.Add, [outside]), new DirectoryGroupMemberChange(GroupMemberOp.Remove, [inside])]), Ct)));

            var after = (await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().FindGroupAsync(org.Idp, group.Id, Ct))).Value!;
            Assert.Equal([inside], after.Members); // 成員沒變
            Assert.Equal(CustomerRoles.Admin, await RoleAsync(org, inside)); // 角色也沒被重算
            Assert.Equal(CustomerRoles.Viewer, await RoleAsync(org, outside));
        });
    }

    [Fact]
    public async Task GroupAuditEventsCarryNamesAndCountsButNeverMemberLists()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("grp-l");
            var member = await MemberIdAsync(org);
            var group = (await CreateAsync(org, "Engineering", member)).Value!;
            await MapAsync(org, group.Id, CustomerRoles.Developer);
            await UpdateAsync(org, group.Id, Members(GroupMemberOp.Remove, member));

            var events = new[] { AuditActions.DirectoryGroupCreated, AuditActions.DirectoryGroupUpdated, AuditActions.DirectoryGroupRoleMapped };
            foreach (var action in events)
            {
                var dump = JsonSerializer.Serialize(await AuditAsync(org, action));
                Assert.Contains("Engineering", dump, StringComparison.Ordinal);
                Assert.DoesNotContain(member, dump.Replace($"\"TargetId\":\"{group.Id}\"", string.Empty, StringComparison.Ordinal), StringComparison.Ordinal);
            }
        });
    }
}