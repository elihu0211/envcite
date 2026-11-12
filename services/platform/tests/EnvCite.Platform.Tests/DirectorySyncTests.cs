using System.Security.Claims;
using System.Text.Json;

using EnvCite.Platform.Api;
using EnvCite.Platform.Data;
using EnvCite.Platform.Modules.ApiAccess;
using EnvCite.Platform.Modules.Members.DirectorySync;
using EnvCite.Platform.Modules.Tenancy;
using EnvCite.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.AspNetCore.Identity;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

using static EnvCite.Platform.Tests.PortalTestSupport;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 目錄同步的核心（身分來源把成員推進租戶）：冪等、停用不刪、最後一位擁有者、角色上限、接管、受管鎖定、
/// 租戶隔離、稽核與 fail closed。端點與介面在後續切片；這裡直接呼叫服務，租戶綁定的方式與請求相同。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed class DirectorySyncTests(PlatformFactory factory) : DirectoryTestBase(factory)
{
    private Task<AdminOutcome<DirectoryMember>> ProvisionAsync(Org org, DirectoryProvision member) =>
        InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().ProvisionAsync(org.Idp, member, Ct));

    private Task<AdminOutcome<DirectoryMember>> UpdateAsync(Org org, string id, DirectoryPatch patch) =>
        InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().UpdateAsync(org.Idp, id, patch, Ct));

    private Task<AdminOutcome<DirectoryMember>> DeactivateAsync(Org org, string id) =>
        InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().DeactivateAsync(org.Idp, id, Ct));

    private Task<AdminOutcome<DirectoryMember>> FindAsync(Org org, string id) =>
        InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().FindAsync(org.Idp, id, Ct));

    private Task<AdminOutcome<DirectoryStatus>> StatusAsync(Org org) =>
        InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().GetStatusAsync(org.Owner, Ct));

    [Fact]
    public async Task AProvisionedMemberIsAnInvitedManagedMemberAndTheMailCarriesALinkNotAPassword()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("dir-a");
            var person = Person();

            var created = await ProvisionAsync(org, person);

            Assert.True(created.IsOk, created.Message);
            var member = created.Value!;
            Assert.True(member.Invited);
            Assert.True(member.Managed);
            Assert.True(member.Active);
            Assert.Equal(CustomerRoles.Viewer, member.Role); // 沒指定角色：租戶設定的預設
            Assert.Equal(person.ExternalId, member.ExternalId);

            var mail = Assert.Single(Factory.Mail.Sent, m => m.Recipient == person.Email);
            Assert.Contains($"/portal/{org.Tenant.Identifier}/activate?userId={member.Id}&code=", mail.Body, StringComparison.Ordinal);

            // 稽核的「誰」是這把 token，不是任何一個人；內容沒有連結或密碼。
            var audit = Assert.Single(await AuditAsync(org, AuditActions.MemberProvisioned));
            Assert.Equal(AuditActors.Directory, audit.ActorType);
            Assert.Equal(org.TokenId.ToString(), audit.ActorId);
            Assert.Equal(member.Id, audit.TargetId);
            Assert.DoesNotContain("code=", audit.Detail.GetRawText(), StringComparison.Ordinal);
            Assert.Equal("sent", audit.Detail.GetProperty("mail").GetString());
        });
    }

    [Fact]
    public async Task ProvisioningTheSamePersonAgainIsAConflictNotASecondMember()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("dir-b");
            var person = Person();
            Assert.True((await ProvisionAsync(org, person)).IsOk);

            var sameExternalId = await ProvisionAsync(org, person with { Email = NewEmail() });
            var sameEmail = await ProvisionAsync(org, person with { ExternalId = "another" });

            Assert.Equal(AdminStatus.Conflict, sameExternalId.Status);
            Assert.Equal(OutcomeCodes.DirectoryExternalIdTaken, sameExternalId.Code);
            Assert.Equal(AdminStatus.Conflict, sameEmail.Status);
            Assert.Equal(OutcomeCodes.MemberEmailTaken, sameEmail.Code);
            var page = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().ListAsync(org.Idp, 0, 50, null, null, Ct));
            Assert.Equal(2, page.Value!.Total); // 擁有者加上這一位
        });
    }

    [Fact]
    public async Task NewMembersNeedMailBecauseAnAccountNobodyCanActivateIsWorseThanNone()
    {
        var org = await OrgAsync("dir-c"); // 沒有 WithMail：寄信沒啟用

        var inactive = await ProvisionAsync(org, Person(active: false)); // 停用的不需要信
        var refused = await ProvisionAsync(org, Person());

        Assert.Equal(OutcomeCodes.MailNotEnabled, refused.Code);
        Assert.True(inactive.IsOk, inactive.Message);
        Assert.False(inactive.Value!.Active);

        var status = (await StatusAsync(org)).Value!;
        Assert.Equal(1, status.Created);
        Assert.Equal(1, status.Rejected);
        Assert.Equal(OutcomeCodes.MailNotEnabled, status.LastFailureCode);
        Assert.NotEmpty(await AuditAsync(org, AuditActions.DirectorySyncRejected));
    }

    [Fact]
    public async Task NothingWorksWhileTheTenantConnectionIsOffAndTheStateIsKept()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("dir-d");
            var member = (await ProvisionAsync(org, Person())).Value!;

            var off = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>()
                .ConfigureAsync(org.Owner, new DirectorySettings(false, CustomerRoles.Viewer, false), Ct));
            Assert.True(off.IsOk);

            Assert.Equal(OutcomeCodes.DirectoryNotEnabled, (await ProvisionAsync(org, Person())).Code);
            Assert.Equal(AdminStatus.NotFound, (await UpdateAsync(org, member.Id, new DirectoryPatch(DisplayName: "x"))).Status);
            Assert.Equal(AdminStatus.NotFound, (await DeactivateAsync(org, member.Id)).Status);
            Assert.Equal(AdminStatus.NotFound, (await FindAsync(org, member.Id)).Status);

            // 既有成員原封不動。
            Assert.False((await UserAsync(org, member.Id)).Disabled);
        });
    }

    [Fact]
    public async Task TheFeatureIsAbsentWhenTheOperatorOfTheDeploymentHasNotEnabledIt()
    {
        var org = await OrgAsync("dir-e");
        using var off = Factory.WithWebHostBuilder(builder => builder.UseSetting("Platform:Directory:Enabled", "false"));

        var provisioned = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().ProvisionAsync(org.Idp, Person(), Ct), off);
        var status = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().GetStatusAsync(org.Owner, Ct), off);
        var issued = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().IssueTokenAsync(org.Owner, "x", null, Ct), off);

        Assert.Equal(AdminStatus.NotFound, provisioned.Status);
        Assert.Equal(OutcomeCodes.DirectoryNotEnabled, status.Code);
        Assert.Equal(OutcomeCodes.DirectoryNotEnabled, issued.Code);
    }

    [Fact]
    public async Task ResendingAnUnchangedUpdateWritesNothing()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("dir-f");
            var member = (await ProvisionAsync(org, Person())).Value!;

            var first = await UpdateAsync(org, member.Id, new DirectoryPatch(DisplayName: "Bea", Role: CustomerRoles.Developer));
            var syncedAt = (await UserAsync(org, member.Id)).SyncedAt;
            var again = await UpdateAsync(org, member.Id, new DirectoryPatch(DisplayName: "Bea", Role: CustomerRoles.Developer));

            Assert.True(first.IsOk, first.Message);
            Assert.Equal("Bea", again.Value!.DisplayName);
            Assert.Equal(CustomerRoles.Developer, again.Value.Role);
            Assert.Equal(syncedAt, (await UserAsync(org, member.Id)).SyncedAt); // 沒有變化：同步時間也不動
            var synced = Assert.Single(await AuditAsync(org, AuditActions.MemberAttributeSynced));
            Assert.Contains("role", synced.Detail.GetProperty("fields").EnumerateArray().Select(f => f.GetString()));
            Assert.Equal(CustomerRoles.Viewer, synced.Detail.GetProperty("previous_role").GetString());
            Assert.Equal(CustomerRoles.Developer, synced.Detail.GetProperty("current_role").GetString());
        });
    }

    [Fact]
    public async Task DeactivatingKeepsTheAccountEndsSessionsAndIsIdempotent()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("dir-g");
            var member = (await ProvisionAsync(org, Person())).Value!;
            var before = await UserAsync(org, member.Id);

            var gone = await DeactivateAsync(org, member.Id);
            var again = await DeactivateAsync(org, member.Id);

            Assert.True(gone.IsOk, gone.Message);
            Assert.False(gone.Value!.Active);
            Assert.True(again.IsOk); // 重送得到同一個結果
            var after = await UserAsync(org, member.Id);
            Assert.True(after.Disabled);
            Assert.NotEqual(before.SecurityStamp, after.SecurityStamp); // 舊 session 在下一個請求失效
            Assert.Equal(before.Email, after.Email); // 帳號保留，不是刪除
            Assert.Single(await AuditAsync(org, AuditActions.MemberDeprovisioned)); // 第二次不重複記錄
            Assert.Equal(1, (await StatusAsync(org)).Value!.Deactivated);
        });
    }

    [Fact]
    public async Task ReactivatingAMemberWhoNeverSetAPasswordSendsTheInvitationAgain()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("dir-h");
            var person = Person();
            var member = (await ProvisionAsync(org, person)).Value!;
            Assert.True((await DeactivateAsync(org, member.Id)).IsOk);

            var back = await UpdateAsync(org, member.Id, new DirectoryPatch(Active: true));

            Assert.True(back.Value!.Active);
            Assert.Equal(2, Factory.Mail.Sent.Count(m => m.Recipient == person.Email));
            Assert.Single(await AuditAsync(org, AuditActions.MemberReprovisioned));
        });
    }

    [Fact]
    public async Task TheLastActiveOwnerIsNeverDeactivatedOrDemotedByTheDirectory()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("dir-i");
            var person = (await ProvisionAsync(org, Person(role: CustomerRoles.Admin))).Value!;

            // 受管的成員被人升成擁有者（介面鎖住這個動作，這裡直接改資料來造出這個狀態），而配置連線的擁有者已離開。
            await InTenantAsync(org.Tenant, async sp =>
            {
                var users = sp.GetRequiredService<UserManager<ApplicationUser>>();
                var user = (await users.FindByIdAsync(person.Id))!;
                user.Role = CustomerRoles.Owner;
                await users.UpdateAsync(user);
                var original = (await users.FindByIdAsync(org.OwnerId))!;
                original.Disabled = true;
                await users.UpdateAsync(original);
                return true;
            });

            var disable = await DeactivateAsync(org, person.Id);
            var demote = await UpdateAsync(org, person.Id, new DirectoryPatch(Role: CustomerRoles.Viewer));

            Assert.Equal(OutcomeCodes.DirectoryLastOwner, disable.Code);
            Assert.Equal(OutcomeCodes.DirectoryLastOwner, demote.Code);
            var user = await UserAsync(org, person.Id);
            Assert.False(user.Disabled);
            Assert.Equal(CustomerRoles.Owner, user.Role);
            Assert.Equal(2, (await AuditAsync(org, AuditActions.DirectorySyncRejected)).Count);

            // 只要還有另一位啟用中的擁有者，就不是最後一位。
            await InTenantAsync(org.Tenant, async sp =>
            {
                var users = sp.GetRequiredService<UserManager<ApplicationUser>>();
                var original = (await users.FindByIdAsync(org.OwnerId))!;
                original.Disabled = false;
                await users.UpdateAsync(original);
                return true;
            });
            Assert.True((await DeactivateAsync(org, person.Id)).IsOk);
        });
    }

    [Fact]
    public async Task TheDirectoryCanGrantAtMostAdminAndOwnersAreAssignedByPeople()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("dir-j");
            var admin = await ProvisionAsync(org, Person(role: CustomerRoles.Admin));
            var owner = await ProvisionAsync(org, Person(role: CustomerRoles.Owner));
            var member = admin.Value!;
            var promote = await UpdateAsync(org, member.Id, new DirectoryPatch(Role: CustomerRoles.Owner));
            var bogus = await UpdateAsync(org, member.Id, new DirectoryPatch(Role: "SuperAdmin"));
            var defaultOwner = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>()
                .ConfigureAsync(org.Owner, new DirectorySettings(true, CustomerRoles.Owner, false), Ct));

            Assert.True(admin.IsOk, admin.Message);
            Assert.Equal(OutcomeCodes.DirectoryRoleNotMappable, owner.Code);
            Assert.Equal(AdminStatus.Forbidden, owner.Status);
            Assert.Equal(OutcomeCodes.DirectoryRoleNotMappable, promote.Code);
            Assert.Equal(OutcomeCodes.RoleInvalid, bogus.Code);
            Assert.Equal(OutcomeCodes.DirectoryRoleNotMappable, defaultOwner.Code);
            Assert.Equal(CustomerRoles.Admin, (await UserAsync(org, member.Id)).Role);
        });
    }

    [Fact]
    public async Task ExistingMembersAreNotTouchedUnlessTheTenantAllowsAdoptionAndOwnersAreNeverAdopted()
    {
        await Factory.WithMail(async () =>
        {
            var closed = await OrgAsync("dir-k1");
            var open = await OrgAsync("dir-k2", adopt: true);
            var closedEmail = NewEmail();
            var openEmail = NewEmail();
            var (closedId, _) = await AddMemberAsync(Factory, closed.Tenant.Id, closedEmail, CustomerRoles.Developer);
            var (openId, _) = await AddMemberAsync(Factory, open.Tenant.Id, openEmail, CustomerRoles.Developer);
            var (ownerOpenId, _) = await AddMemberAsync(Factory, open.Tenant.Id, NewEmail(), CustomerRoles.Owner);

            var refused = await ProvisionAsync(closed, Person(email: closedEmail));
            var viaPatch = await UpdateAsync(closed, closedId, new DirectoryPatch(Role: CustomerRoles.Viewer));
            var adopted = await ProvisionAsync(open, Person(email: openEmail, externalId: "ext-adopt"));
            var owner = (await UserAsync(open, ownerOpenId)).Email!;
            var refusedOwner = await ProvisionAsync(open, Person(email: owner));

            Assert.Equal(OutcomeCodes.DirectoryAdoptRequired, refused.Code);
            Assert.Equal(OutcomeCodes.DirectoryAdoptRequired, viaPatch.Code); // 沒接管的人，同步不能用 id 去改
            Assert.Equal(CustomerRoles.Developer, (await UserAsync(closed, closedId)).Role);
            Assert.False((await UserAsync(closed, closedId)).ManagedBy == DirectoryRoles.ManagedBy);

            Assert.True(adopted.IsOk, adopted.Message);
            Assert.Equal(openId, adopted.Value!.Id);
            Assert.True(adopted.Value.Managed);
            Assert.Equal(CustomerRoles.Developer, adopted.Value.Role); // 接管只連結，角色維持人原本設的
            Assert.Equal("ext-adopt", (await UserAsync(open, openId)).ExternalId);
            Assert.Single(await AuditAsync(open, AuditActions.MemberAdopted));
            Assert.Equal(OutcomeCodes.DirectoryRoleNotMappable, refusedOwner.Code);
            Assert.False((await UserAsync(open, ownerOpenId)).ManagedBy == DirectoryRoles.ManagedBy);
        });
    }

    [Fact]
    public async Task ManagedMembersAreLockedForPeopleUntilReleased()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("dir-l");
            var member = (await ProvisionAsync(org, Person())).Value!;
            var members = Factory.Services.GetRequiredService<IMemberAdmin>();

            var locked = await members.SetDisabledAsync(OperatorSession.Principal(), org.Tenant.Id, member.Id, true, Ct);
            var selfLocked = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IMemberSelfService>().ChangeRoleAsync(org.Owner, member.Id, CustomerRoles.Billing, Ct));
            var released = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().ReleaseManagedAsync(org.Owner, member.Id, Ct));
            var releasedAgain = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().ReleaseManagedAsync(org.Owner, member.Id, Ct));
            var unlocked = await members.SetDisabledAsync(OperatorSession.Principal(), org.Tenant.Id, member.Id, true, Ct);

            Assert.Equal(OutcomeCodes.DirectoryMemberManaged, locked.Code);
            Assert.Equal(OutcomeCodes.DirectoryMemberManaged, selfLocked.Code);
            Assert.True(released.IsOk, released.Message);
            Assert.Equal(OutcomeCodes.DirectoryNotManaged, releasedAgain.Code);
            Assert.True(unlocked.IsOk);
            var user = await UserAsync(org, member.Id);
            Assert.Null(user.ManagedBy);
            Assert.Null(user.ExternalId); // 身分來源之後能用同一個 Id 重新接管
            Assert.Single(await AuditAsync(org, AuditActions.MemberReleaseManaged));
        });
    }

    [Fact]
    public async Task EachTenantOnlySeesAndChangesItsOwnMembersAndTokens()
    {
        await Factory.WithMail(async () =>
        {
            var a = await OrgAsync("dir-m1");
            var b = await OrgAsync("dir-m2");
            var person = Person(externalId: "same-ext", email: NewEmail());
            var inA = (await ProvisionAsync(a, person)).Value!;
            var inB = await ProvisionAsync(b, person); // 同一個外部識別碼與 email 在另一個租戶是另一個人

            Assert.True(inB.IsOk, inB.Message);
            Assert.NotEqual(inA.Id, inB.Value!.Id);

            // 用 B 的 token 與 B 的租戶去碰 A 的成員：找不到。
            Assert.Equal(AdminStatus.NotFound, (await FindAsync(b, inA.Id)).Status);
            Assert.Equal(AdminStatus.NotFound, (await UpdateAsync(b, inA.Id, new DirectoryPatch(DisplayName: "x"))).Status);
            Assert.Equal(AdminStatus.NotFound, (await DeactivateAsync(b, inA.Id)).Status);
            Assert.True((await UserAsync(a, inA.Id)).Disabled == false);

            var listB = await InTenantAsync(b.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().ListAsync(b.Idp, 0, 50, null, null, Ct));
            Assert.DoesNotContain(listB.Value!.Items, m => m.Id == inA.Id);
            Assert.Equal(AdminStatus.NotFound, (await InTenantAsync(b.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().RevokeTokenAsync(b.Owner, a.TokenId, Ct))).Status);
            Assert.Equal(AdminStatus.NotFound, (await InTenantAsync(b.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().ReleaseManagedAsync(b.Owner, inA.Id, Ct))).Status);

            // 狀態與計數也是各租戶自己的。
            Assert.Equal(1, (await StatusAsync(a)).Value!.Created);
            Assert.Equal(1, (await StatusAsync(b)).Value!.Created);
        });
    }

    [Fact]
    public async Task OnlyAnActiveOwnerManagesTheConnectionAndTheRoleIsReadFromTheDatabase()
    {
        var org = await OrgAsync("dir-n");
        var (adminId, _) = await AddMemberAsync(Factory, org.Tenant.Id, NewEmail(), CustomerRoles.Admin);
        var (devId, _) = await AddMemberAsync(Factory, org.Tenant.Id, NewEmail(), CustomerRoles.Developer);
        // 偽造的 role claim 不算數：角色以資料庫為準。
        var forged = new ClaimsPrincipal(new ClaimsIdentity(
            [new Claim(PlatformClaimTypes.PortalUserId, devId), new Claim(ClaimTypes.Role, CustomerRoles.Owner)], "portal"));

        var asAdmin = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().IssueTokenAsync(Portal(adminId), "x", null, Ct));
        var asForged = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().GetStatusAsync(forged, Ct));
        var anonymous = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().GetStatusAsync(new ClaimsPrincipal(new ClaimsIdentity()), Ct));

        Assert.Equal(AdminStatus.Forbidden, asAdmin.Status);
        Assert.Equal(OutcomeCodes.DirectoryOwnerOnly, asAdmin.Code);
        Assert.Equal(OutcomeCodes.DirectoryOwnerOnly, asForged.Code);
        Assert.Equal(OutcomeCodes.DirectoryOwnerOnly, anonymous.Code);
        Assert.True((await StatusAsync(org)).IsOk);

        // 被停用的擁有者也不行。
        var disabled = await Factory.Services.GetRequiredService<IMemberAdmin>().SetDisabledAsync(OperatorSession.Principal(), org.Tenant.Id, org.OwnerId, true, Ct);
        Assert.True(disabled.IsOk);
        Assert.Equal(OutcomeCodes.DirectoryOwnerOnly, (await StatusAsync(org)).Code);
    }

    [Fact]
    public async Task ATokenIsShownOnceStoredAsAHashAndNeverAppearsInTheAudit()
    {
        var org = await OrgAsync("dir-o");

        Assert.StartsWith("scim_", org.Token, StringComparison.Ordinal);
        Assert.True(DirectoryTokenFormat.TryParse(org.Token, out var prefix, out var secret));
        Assert.False(ApiKeyFormat.TryParse(org.Token, out _, out _)); // 兩種憑證互不通用
        Assert.False(DirectoryTokenFormat.TryParse("sk_live_0123456789ab_" + secret, out _, out _));

        var stored = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<PlatformDbContext>().DirectoryTokens.AsNoTracking().SingleAsync(Ct));
        Assert.Equal(prefix, stored.Prefix);
        Assert.Equal(DirectoryTokenFormat.Hash(secret), stored.SecretHash);

        var dump = JsonSerializer.Serialize(await AuditAsync(org, AuditActions.DirectoryTokenIssued));
        Assert.DoesNotContain(secret, dump, StringComparison.Ordinal);
        Assert.Contains(prefix, dump, StringComparison.Ordinal);

        // 狀態裡看得到前綴，看不到 secret 或雜湊。
        var info = Assert.Single((await StatusAsync(org)).Value!.Tokens);
        Assert.Equal(prefix, info.Prefix);
        Assert.DoesNotContain(secret, JsonSerializer.Serialize(info), StringComparison.Ordinal);
    }

    [Fact]
    public async Task RevokingATokenIsIdempotentAndBadInputIsRejected()
    {
        var org = await OrgAsync("dir-p");
        var first = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().RevokeTokenAsync(org.Owner, org.TokenId, Ct));
        var second = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().RevokeTokenAsync(org.Owner, org.TokenId, Ct));
        var missing = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().RevokeTokenAsync(org.Owner, Guid.NewGuid(), Ct));
        var blank = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>().IssueTokenAsync(org.Owner, "  ", null, Ct));
        var past = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>()
            .IssueTokenAsync(org.Owner, "old", Factory.Time.GetUtcNow().AddMinutes(-1), Ct));

        Assert.NotNull(first.Value!.RevokedAt);
        // 第一次回的是剛設的值（100 奈秒刻度），第二次是從資料庫讀回的（Postgres 只存到微秒）：容許一個微秒的差，而不是比到最後一位。
        Assert.Equal(first.Value.RevokedAt!.Value, second.Value!.RevokedAt!.Value, TimeSpan.FromMilliseconds(1));
        Assert.Single(await AuditAsync(org, AuditActions.DirectoryTokenRevoked));
        Assert.Equal(OutcomeCodes.DirectoryTokenNotFound, missing.Code);
        Assert.Equal(OutcomeCodes.DirectoryTokenInvalid, blank.Code);
        Assert.Equal(OutcomeCodes.DirectoryTokenInvalid, past.Code);
    }

    [Fact]
    public async Task ListingFiltersByExactMatchAndNeverReturnsMoreThanThePageLimit()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("dir-q");
            var people = new List<DirectoryProvision>();
            for (var i = 0; i < 3; i++)
            {
                people.Add(Person());
                Assert.True((await ProvisionAsync(org, people[^1])).IsOk);
            }

            var all = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().ListAsync(org.Idp, 0, 2, null, null, Ct));
            var next = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().ListAsync(org.Idp, 2, 2, null, null, Ct));
            var byExternal = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().ListAsync(org.Idp, 0, 10, people[1].ExternalId, null, Ct));
            var byEmail = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().ListAsync(org.Idp, 0, 10, null, people[2].Email.ToUpperInvariant(), Ct));
            using var small = Factory.WithWebHostBuilder(builder => builder.UseSetting("Platform:Directory:MaxPageSize", "2"));
            var capped = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().ListAsync(org.Idp, 0, 100_000, null, null, Ct), small);

            Assert.Equal(4, all.Value!.Total); // 擁有者加三位
            Assert.Equal(2, all.Value.Items.Count);
            Assert.Equal(2, next.Value!.Items.Count);
            Assert.Equal(people[1].Email, Assert.Single(byExternal.Value!.Items).Email);
            Assert.Equal(people[2].Email, Assert.Single(byEmail.Value!.Items).Email);
            Assert.Equal(2, capped.Value!.Items.Count); // 身分來源要再多也以部署者的上限為準
        });
    }

    [Fact]
    public async Task WhenTheAuditCannotBeWrittenNothingChanges()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("dir-r");
            var person = Person();
            // 稽核的 ActorId 欄位寬 320：超過就寫不進去，用來模擬稽核表不可用。
            var broken = Idp(Guid.NewGuid());
            var longId = new ClaimsPrincipal(new ClaimsIdentity([new Claim(PlatformClaimTypes.DirectoryTokenId, new string('x', 400))], "directory"));

            await Assert.ThrowsAnyAsync<DbUpdateException>(() =>
                InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().ProvisionAsync(longId, person, Ct)));

            var found = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectoryProvisioning>().ListAsync(broken, 0, 50, null, person.Email, Ct));
            Assert.Equal(0, found.Value!.Total); // 沒有稽核就沒有成員
            Assert.Equal(0, (await StatusAsync(org)).Value!.Created); // 計數也一起回滾
        });
    }

    [Fact]
    public async Task TheOperatorSeesTheStatusCanSwitchTheConnectionOffAndTheChangeIsAudited()
    {
        await Factory.WithMail(async () =>
        {
            var org = await OrgAsync("dir-s");
            Assert.True((await ProvisionAsync(org, Person())).IsOk);
            var admin = Factory.Services.GetRequiredService<IDirectoryAdmin>();

            var status = await admin.GetStatusAsync(org.Tenant.Id, Ct);
            var off = await admin.DisableAsync(OperatorSession.Principal(), org.Tenant.Id, Ct);
            var offAgain = await admin.DisableAsync(OperatorSession.Principal(), org.Tenant.Id, Ct);
            var missing = await admin.GetStatusAsync("no-such-tenant", Ct);

            Assert.True(status.Value!.Settings.Enabled);
            Assert.Equal(1, status.Value.ManagedMembers);
            Assert.NotNull(status.Value.LastRequestAt);
            Assert.False(off.Value!.Settings.Enabled);
            Assert.False(offAgain.Value!.Settings.Enabled);
            Assert.Equal(AdminStatus.NotFound, missing.Status);
            Assert.Equal(OutcomeCodes.DirectoryNotEnabled, (await ProvisionAsync(org, Person())).Code); // 推送立刻被拒
            Assert.Single(await AuditAsync(org, AuditActions.DirectoryDisabled));
        });
    }

    [Fact]
    public async Task ChangingTheSettingsIsAuditedWithoutSecrets()
    {
        var org = await OrgAsync("dir-t");

        var changed = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>()
            .ConfigureAsync(org.Owner, new DirectorySettings(true, CustomerRoles.Developer, true), Ct));
        var same = await InTenantAsync(org.Tenant, sp => sp.GetRequiredService<IDirectorySelfService>()
            .ConfigureAsync(org.Owner, new DirectorySettings(true, CustomerRoles.Developer, true), Ct));

        Assert.True(changed.IsOk && same.IsOk);
        Assert.Single(await AuditAsync(org, AuditActions.DirectoryEnabled)); // 建立時開過一次，之後沒有再開
        var configured = Assert.Single(await AuditAsync(org, AuditActions.DirectoryConfigured)); // 重送相同的設定不重複記錄
        Assert.Equal(CustomerRoles.Developer, configured.Detail.GetProperty("default_role").GetString());
        Assert.Equal(CustomerRoles.Viewer, configured.Detail.GetProperty("previous_default_role").GetString());
    }
}