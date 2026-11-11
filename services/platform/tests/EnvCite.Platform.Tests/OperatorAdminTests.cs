using EnvCite.Platform.SharedKernel;

using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

using static EnvCite.Platform.Tests.OperatorTestSupport;
using static EnvCite.Platform.Tests.PortalTestSupport;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 營運者帳號的管理服務（<see cref="IOperatorAdmin"/>，後台、命令列與本機種子共用）：驗證、狀態、稽核。
/// 一次性密碼不外流由 <c>OperatorCliTests</c> 與 <c>DevelopmentSeedTests</c> 測。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed class OperatorAdminTests(PlatformFactory factory)
{
    private IOperatorAdmin Admin => factory.Services.GetRequiredService<IOperatorAdmin>();

    private async Task<List<AuditRecord>> EventsAsync(string action, string id) =>
        [.. (await factory.Services.GetRequiredService<IAuditQuery>().QueryAsync(new AuditFilter(Action: action, Limit: 200), CancellationToken.None))
            .Items.Where(e => e.TargetId == id)];

    [Fact]
    public async Task AnOperatorIsCreatedAndSignsInWithTheOneTimePassword()
    {
        var email = NewOperatorEmail();

        var created = await Admin.CreateAsync(OperatorSession.Principal(), email, "王小明", CancellationToken.None);

        Assert.True(created.IsOk);
        var issued = created.Value!;
        Assert.True(issued.Password.Length >= 16);
        using var browser = new PortalBrowser(factory);
        Assert.Equal(UiPassword, (await browser.SubmitAsync(UiLogin, Credentials(email, issued.Password))).Location); // 第一次必須改密碼

        var listed = Assert.Single((await Admin.ListAsync(CancellationToken.None)).Value!, o => o.Id == issued.Operator.Id);
        Assert.Equal((email, true, false, false), (listed.Email, listed.MustChangePassword, listed.TwoFactorEnabled, listed.Disabled));
    }

    [Theory]
    [InlineData("not-an-email")]
    [InlineData("")]
    [InlineData(null)]
    public async Task BadInputIsInvalidAndCreatesNothing(string? email)
    {
        var before = (await Admin.ListAsync(CancellationToken.None)).Value!.Count;

        var created = await Admin.CreateAsync(OperatorSession.Principal(), email ?? string.Empty, null, CancellationToken.None);

        Assert.Equal(AdminStatus.Invalid, created.Status);
        Assert.Equal(before, (await Admin.ListAsync(CancellationToken.None)).Value!.Count);
    }

    [Fact]
    public async Task ADuplicateEmailInAnyCaseConflictsAndAnUnknownIdIsNotFound()
    {
        var actor = OperatorSession.Principal();
        var email = NewOperatorEmail();
        Assert.True((await Admin.CreateAsync(actor, email, null, CancellationToken.None)).IsOk);

        var duplicate = await Admin.CreateAsync(actor, email.ToUpperInvariant(), null, CancellationToken.None);

        Assert.Equal(AdminStatus.Conflict, duplicate.Status);
        foreach (var id in new[] { Guid.NewGuid().ToString(), "not-a-guid" })
        {
            Assert.Equal(AdminStatus.NotFound, (await Admin.ResetPasswordAsync(actor, id, CancellationToken.None)).Status);
            Assert.Equal(AdminStatus.NotFound, (await Admin.ResetTwoFactorAsync(actor, id, CancellationToken.None)).Status);
            Assert.Equal(AdminStatus.NotFound, (await Admin.SetDisabledAsync(actor, id, true, CancellationToken.None)).Status);
            Assert.Equal(AdminStatus.NotFound, (await Admin.SetDisabledAsync(actor, id, false, CancellationToken.None)).Status);
        }
    }

    [Fact]
    public async Task ResetDisableAndEnableWorkAndEachIsAuditedWithTheOperatorWhoDidIt()
    {
        var actor = OperatorSession.Principal();
        var email = NewOperatorEmail();
        var issued = (await Admin.CreateAsync(actor, email, "王小明", CancellationToken.None)).Value!;
        var id = issued.Operator.Id;

        // 重設密碼：新的一次性密碼，舊的不能用，要求再改密碼。
        var second = (await Admin.ResetPasswordAsync(actor, id, CancellationToken.None)).Value!.Password;
        Assert.NotEqual(issued.Password, second);
        using var browser = new PortalBrowser(factory);
        Assert.Contains("電子郵件或密碼不正確", (await browser.SubmitAsync(UiLogin, Credentials(email, issued.Password))).Html, StringComparison.Ordinal);
        Assert.Equal(UiPassword, (await browser.SubmitAsync(UiLogin, Credentials(email, second))).Location);

        // 停用：不能登入；再停用一次是冪等的，不重複記錄。
        Assert.True((await Admin.SetDisabledAsync(actor, id, true, CancellationToken.None)).Value!.Disabled);
        Assert.True((await Admin.SetDisabledAsync(actor, id, true, CancellationToken.None)).IsOk);
        using var blocked = new PortalBrowser(factory);
        Assert.Contains("電子郵件或密碼不正確", (await blocked.SubmitAsync(UiLogin, Credentials(email, second))).Html, StringComparison.Ordinal);
        Assert.Single(await EventsAsync(AuditActions.OperatorDisabled, id));

        // 重新啟用後可以再登入。
        Assert.False((await Admin.SetDisabledAsync(actor, id, false, CancellationToken.None)).Value!.Disabled);
        using var back = new PortalBrowser(factory);
        Assert.Equal(UiPassword, (await back.SubmitAsync(UiLogin, Credentials(email, second))).Location);

        foreach (var action in new[] { AuditActions.OperatorCreated, AuditActions.OperatorPasswordReset, AuditActions.OperatorDisabled, AuditActions.OperatorEnabled })
        {
            var evt = Assert.Single(await EventsAsync(action, id));
            Assert.Equal((AuditActors.OperatorUser, PlatformFactory.OperatorEmail, (string?)null), (evt.ActorType, evt.ActorId, evt.TenantId));
            Assert.Equal(email, evt.Detail.GetProperty("email").GetString());
        }
    }

    [Fact]
    public async Task ATwoFactorResetIsAuditedAndAsksForANewAuthenticator()
    {
        var op = await factory.EnsureOperatorAsync(NewOperatorEmail());

        var reset = await Admin.ResetTwoFactorAsync(OperatorSession.Principal(), op.Id.ToString(), CancellationToken.None);

        Assert.True(reset.IsOk);
        Assert.False(reset.Value!.TwoFactorEnabled);
        var evt = Assert.Single(await EventsAsync(AuditActions.OperatorMfaReset, op.Id.ToString()));
        Assert.Equal(AuditActors.OperatorUser, evt.ActorType);
    }

    [Fact]
    public void TheOperatorTableIsNotTheMemberTable()
    {
        // 營運者是另一個 user store：不在成員（隨租戶）的 Identity 表裡，也沒有租戶篩選。
        using var scope = factory.Services.CreateScope();
        var db = scope.ServiceProvider.GetRequiredService<Data.PlatformDbContext>();
        var table = db.Model.FindEntityType(typeof(Data.OperatorUser))!;
        var member = db.Model.FindEntityType(typeof(Data.ApplicationUser))!;

        Assert.Equal("Operators", table.GetTableName());
        Assert.NotEqual(member.GetTableName(), table.GetTableName());
        Assert.Empty(table.GetDeclaredQueryFilters());
        Assert.Null(table.FindProperty("TenantId"));
    }
}