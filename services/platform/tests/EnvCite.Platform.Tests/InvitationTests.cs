using System.Text.Json;
using System.Text.RegularExpressions;

using EnvCite.Platform.Modules.Tenancy;

using static EnvCite.Platform.Tests.OperatorTestSupport;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 邀請連結（主流做法：Cognito 的邀請、Entra 與 Auth0 的啟用連結、ASP.NET Core Identity 的信箱確認）：
/// email 是登入名稱；帳號先沒有密碼，信裡只有單次、限時的連結，對方自己設定密碼並同時驗證信箱。
/// 信裡沒有密碼、連結只對路徑上的租戶有效、只是開啟連結不會用掉它、重寄會讓舊連結失效。
/// </summary>
[Collection(PlatformTestGroup.Group3)]
public sealed partial class InvitationTests(PlatformFactory factory)
{
    private IMemberAdmin Members => factory.Services.GetRequiredService<IMemberAdmin>();

    private IOperatorAdmin Operators => factory.Services.GetRequiredService<IOperatorAdmin>();

    private const string LongPassword = "correct horse battery staple";

    [GeneratedRegex(@"https://platform\.test(?<path>/\S+)")]
    private static partial Regex LinkPattern();

    /// <summary>信裡那條連結的「路徑加查詢」（測試的瀏覽器只認路徑）。</summary>
    private string LinkTo(string recipient)
    {
        var mail = factory.Mail.Sent.Last(m => m.Recipient == recipient);
        return LinkPattern().Match(mail.Body).Groups["path"].Value;
    }

    private async Task<string> AuditDumpAsync(string action)
    {
        var items = (await factory.Services.GetRequiredService<IAuditQuery>()
            .QueryAsync(new AuditFilter(Action: action, Limit: 200), CancellationToken.None)).Items;
        return JsonSerializer.Serialize(items);
    }

    private async Task<(TenantResponse Tenant, string Email, MemberInvitation Invitation)> InviteMemberAsync(string prefix, string role = CustomerRoles.Owner)
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync(prefix);
        var email = NewEmail();
        var invited = await Members.InviteAsync(OperatorSession.Principal(), tenant.Id, email, "Ann", role, CancellationToken.None);
        Assert.True(invited.IsOk, invited.Message);
        return (tenant, email, invited.Value!);
    }

    [Fact]
    public async Task AnInvitedMemberHasNoPasswordAndTheMailCarriesALinkNotAPassword()
    {
        await factory.WithMail(async () =>
        {
            var (tenant, email, invitation) = await InviteMemberAsync("inv-a");

            Assert.Equal(MailDelivery.Sent, invitation.Mail);
            Assert.True(invitation.Member.Invited);
            Assert.False(invitation.Member.EmailConfirmed);
            var mail = Assert.Single(factory.Mail.Sent, m => m.Recipient == email);
            Assert.Contains($"https://platform.test/portal/{tenant.Identifier}/activate?userId={invitation.Member.Id}&code=", mail.Body, StringComparison.Ordinal);
            Assert.Contains($"租戶識別碼：{tenant.Identifier}", mail.Body, StringComparison.Ordinal);

            // 在對方設定密碼之前，任何密碼都登不進去，而且訊息與密碼錯相同。
            using var browser = new PortalBrowser(factory);
            var attempt = await browser.SubmitAsync(Login(tenant.Identifier), Credentials(email, LongPassword));
            Assert.Null(attempt.Location);
            Assert.Contains("電子郵件或密碼不正確", attempt.Html, StringComparison.Ordinal);
        });
    }

    [Fact]
    public async Task OpeningTheLinkOnlyShowsTheFormAndDoesNotUseItUp()
    {
        await factory.WithMail(async () =>
        {
            var (tenant, email, _) = await InviteMemberAsync("inv-b");
            var link = LinkTo(email);
            using var scanner = new PortalBrowser(factory); // 信件掃描器：只是預先開啟網址

            var first = await scanner.GetAsync(link);
            var second = await scanner.GetAsync(link);

            Assert.Equal(System.Net.HttpStatusCode.OK, first.Status);
            Assert.Contains("name=\"Input.New\"", first.Html, StringComparison.Ordinal);
            Assert.Contains("name=\"Input.New\"", second.Html, StringComparison.Ordinal);

            // 設定密碼的兩個欄位都是 PasswordField：有眼睛鈕，而且仍是「新密碼」讓密碼管理員產生並記住。
            PasswordFieldTests.AssertActivationFields(first.Html);

            // 權杖在網址裡，不讓它經 Referer 流到別處。
            using var client = factory.CreateClient();
            using var response = await client.GetAsync(new Uri(link, UriKind.Relative), TestContext.Current.CancellationToken);
            Assert.Equal("no-referrer", Assert.Single(response.Headers.GetValues("Referrer-Policy")));
            Assert.Contains("no-store", string.Join(',', response.Headers.GetValues("Cache-Control")), StringComparison.Ordinal);
        });
    }

    // 密碼規則：太容易被猜到、含有自己的 email，都被擋下並說明原因；連結仍有效，換一個好的就成功。不強制大小寫與符號。
    [Fact]
    public async Task APasswordThatIsEasyToGuessOrContainsTheEmailIsRefusedWithAReasonAndTheLinkSurvives()
    {
        await factory.WithMail(async () =>
        {
            var (tenant, email, _) = await InviteMemberAsync("inv-policy");
            var link = LinkTo(email);
            using var browser = new PortalBrowser(factory);

            var guessable = await browser.SubmitAsync(link, ActivationForm("Password123!!", "Password123!!"));
            Assert.Null(guessable.Location);
            Assert.Contains("太容易被猜到", guessable.Html, StringComparison.Ordinal);

            var name = email[..email.IndexOf('@', StringComparison.Ordinal)];
            var withEmail = $"{name}-purple-walrus-lamp";
            var containsEmail = await browser.SubmitAsync(link, ActivationForm(withEmail, withEmail));
            Assert.Null(containsEmail.Location);
            Assert.Contains("不能包含你的 email", containsEmail.Html, StringComparison.Ordinal);
            Assert.DoesNotContain(withEmail, containsEmail.Html, StringComparison.Ordinal); // 密碼不寫回表單

            // 沒有大小寫與符號的句子照樣過。
            var done = await browser.SubmitAsync(link, ActivationForm("blue bicycle milk tuesday", "blue bicycle milk tuesday"));
            Assert.Equal($"{Login(tenant.Identifier)}?activated=1", done.Location);
        });
    }

    [Fact]
    public async Task SettingAPasswordThroughTheLinkActivatesVerifiesAndConsumesIt()
    {
        await factory.WithMail(async () =>
        {
            var (tenant, email, invitation) = await InviteMemberAsync("inv-c");
            var link = LinkTo(email);
            using var browser = new PortalBrowser(factory);
            var staleForm = PortalBrowser.HiddenFields((await browser.GetAsync(link)).Html); // 之後被重送的舊表單

            // 密碼太短：被擋下，連結仍有效，可以重打。
            var tooShort = await browser.SubmitAsync(link, ActivationForm("short", "short"));
            Assert.Null(tooShort.Location);
            Assert.Contains("至少", tooShort.Html, StringComparison.Ordinal);

            var mismatch = await browser.SubmitAsync(link, ActivationForm(LongPassword, LongPassword + "x"));
            Assert.Null(mismatch.Location);
            Assert.Contains("兩次輸入的新密碼不一樣", mismatch.Html, StringComparison.Ordinal);

            var done = await browser.SubmitAsync(link, ActivationForm(LongPassword, LongPassword));
            Assert.Equal($"{Login(tenant.Identifier)}?activated=1", done.Location);

            // 連結用過就失效：再開、再送（舊表單，還沒登入所以防偽權杖仍有效）都是同一句「無效」。
            using var later = new PortalBrowser(factory);
            Assert.Contains("無效或已過期", (await later.GetAsync(link)).Html, StringComparison.Ordinal);
            var replay = await browser.PostAsync(link, new Dictionary<string, string>(staleForm) { ["Input.New"] = "another long password!", ["Input.Confirm"] = "another long password!" });
            Assert.Null(replay.Location);
            Assert.Contains("無效或已過期", replay.Html, StringComparison.Ordinal);
            Assert.NotNull(invitation);

            // 登入頁說明已啟用；用剛設的密碼登入，不必再改（密碼本來就是自己設的）。
            Assert.Contains("帳號已啟用", (await browser.GetAsync(done.Location!)).Html, StringComparison.Ordinal);
            var signedIn = await browser.SubmitAsync(Login(tenant.Identifier), Credentials(email, LongPassword));
            Assert.Equal(Home(tenant.Identifier), signedIn.Location);

            var member = Assert.Single((await Members.ListAsync(tenant.Id, CancellationToken.None)).Value!);
            Assert.True(member.EmailConfirmed);
            Assert.False(member.Invited);
            Assert.False(member.MustChangePassword);

        });
    }

    [Fact]
    public async Task TheLinkWorksOnlyForTheTenantInThePath()
    {
        await factory.WithMail(async () =>
        {
            var (tenant, email, _) = await InviteMemberAsync("inv-d");
            var (other, _) = await factory.CreateTenantWithKeyAsync("inv-d2");
            var link = LinkTo(email);
            var wrongTenant = link.Replace(tenant.Identifier, other.Identifier, StringComparison.Ordinal);
            using var browser = new PortalBrowser(factory);
            var form = PortalBrowser.HiddenFields((await browser.GetAsync(link)).Html); // 對的租戶的有效表單

            var page = await browser.GetAsync(wrongTenant);
            var attempt = await browser.PostAsync(wrongTenant, new Dictionary<string, string>(form) { ["Input.New"] = LongPassword, ["Input.Confirm"] = LongPassword });

            Assert.Contains("無效或已過期", page.Html, StringComparison.Ordinal);
            Assert.Null(attempt.Location);
            Assert.Contains("無效或已過期", attempt.Html, StringComparison.Ordinal);
            Assert.True(Assert.Single((await Members.ListAsync(tenant.Id, CancellationToken.None)).Value!).Invited); // 原租戶的帳號沒被動到
        });
    }

    [Theory]
    [InlineData("userId=not-a-user&code=AAAA")]
    [InlineData("userId=&code=")]
    [InlineData("code=%%%")]
    [InlineData("")]
    public async Task AGarbageLinkIsTheSameInvalidPageAsAnExpiredOne(string query)
    {
        await factory.WithMail(async () =>
        {
            var (tenant, _) = await factory.CreateTenantWithKeyAsync("inv-e");
            using var browser = new PortalBrowser(factory);

            var page = await browser.GetAsync($"{PortalPaths.Activate(tenant.Identifier)}?{query}");

            Assert.Equal(System.Net.HttpStatusCode.OK, page.Status);
            Assert.Contains("無效或已過期", page.Html, StringComparison.Ordinal);
            Assert.DoesNotContain("name=\"Input.New\"", page.Html, StringComparison.Ordinal);
        });
    }

    [Fact]
    public async Task ResendingMakesTheOldLinkUselessAndTheNewOneWorks()
    {
        await factory.WithMail(async () =>
        {
            var (tenant, email, invitation) = await InviteMemberAsync("inv-f");
            var oldLink = LinkTo(email);

            var resent = await Members.ResendInvitationAsync(OperatorSession.Principal(), tenant.Id, invitation.Member.Id, CancellationToken.None);

            Assert.True(resent.IsOk, resent.Message);
            Assert.Equal(MailDelivery.Sent, resent.Value!.Mail);
            var newLink = LinkTo(email);
            Assert.NotEqual(oldLink, newLink);
            using var browser = new PortalBrowser(factory);
            Assert.Contains("無效或已過期", (await browser.GetAsync(oldLink)).Html, StringComparison.Ordinal);
            var done = await browser.SubmitAsync(newLink, ActivationForm(LongPassword, LongPassword));
            Assert.Equal($"{Login(tenant.Identifier)}?activated=1", done.Location);
        });
    }

    // 信是 HTML 加純文字：兩版是同一條連結、同一個租戶；HTML 版沒有圖片與追蹤，唯一的外部網址是啟用連結。
    [Fact]
    public async Task TheMemberInvitationCarriesTheSameLinkAndTenantInTheTextAndTheHtml()
    {
        await factory.WithMail(async () =>
        {
            var (tenant, email, invitation) = await InviteMemberAsync("inv-html");

            var mail = Assert.Single(factory.Mail.Sent, m => m.Recipient == email);
            var link = Regex.Match(mail.Body, @"https://platform\.test/\S+").Value;
            Assert.Contains($"/portal/{tenant.Identifier}/activate?userId={invitation.Member.Id}&code=", link, StringComparison.Ordinal);
            var html = System.Net.WebUtility.HtmlDecode(mail.Html);
            Assert.Contains(link, html, StringComparison.Ordinal);
            Assert.Contains($"租戶識別碼：{tenant.Identifier}", html, StringComparison.Ordinal);
            Assert.Contains("設定密碼並啟用帳號", html, StringComparison.Ordinal);
            Assert.DoesNotContain("<img", mail.Html, StringComparison.OrdinalIgnoreCase);
            Assert.DoesNotMatch(@"(?i)https?:", mail.Html.Replace(System.Net.WebUtility.HtmlEncode(link), string.Empty, StringComparison.Ordinal));
        });
    }

    [Fact]
    public async Task TheOperatorInvitationHasNoTenantLineAndTheVerificationMailHasNoPasswordButton()
    {
        await factory.WithMail(async () =>
        {
            var email = NewEmail();
            var invited = await Operators.InviteAsync(OperatorSession.Principal(), email, "Op", CancellationToken.None);
            Assert.True(invited.IsOk, invited.Message);

            var mail = Assert.Single(factory.Mail.Sent, m => m.Recipient == email);
            Assert.Contains("/ui/activate?userId=", mail.Html, StringComparison.Ordinal);
            Assert.DoesNotContain("租戶識別碼", mail.Html + mail.Body, StringComparison.Ordinal);
        });

        var (tenant, _) = await factory.CreateTenantWithKeyAsync("inv-v");
        var existing = NewEmail();
        var (id, _) = await AddMemberAsync(factory, tenant.Id, existing);
        await factory.WithMail(async () =>
        {
            var resent = await Members.ResendInvitationAsync(OperatorSession.Principal(), tenant.Id, id, CancellationToken.None);
            Assert.True(resent.IsOk, resent.Message);

            var mail = Assert.Single(factory.Mail.Sent, m => m.Recipient == existing);
            Assert.Contains(">驗證信箱</a>", mail.Html, StringComparison.Ordinal);
            Assert.DoesNotContain("設定密碼", mail.Html, StringComparison.Ordinal);
            Assert.Contains($"租戶識別碼：{tenant.Identifier}", System.Net.WebUtility.HtmlDecode(mail.Html), StringComparison.Ordinal);
        });
    }

    // 已經有密碼、信箱還沒驗證（例如邀請功能上線前就存在的帳號）：寄驗證連結，開啟後只確認信箱。
    [Fact]
    public async Task AnExistingAccountGetsAVerificationLinkThatOnlyConfirmsTheEmail()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("inv-g");
        var email = NewEmail();
        var (id, initial) = await AddMemberAsync(factory, tenant.Id, email);
        await factory.WithMail(async () =>
        {
            var resent = await Members.ResendInvitationAsync(OperatorSession.Principal(), tenant.Id, id, CancellationToken.None);
            Assert.True(resent.IsOk, resent.Message);
            var mail = Assert.Single(factory.Mail.Sent, m => m.Recipient == email);
            Assert.Contains("驗證這個信箱", mail.Body, StringComparison.Ordinal);

            using var browser = new PortalBrowser(factory);
            var page = await browser.GetAsync(LinkTo(email));
            Assert.DoesNotContain("name=\"Input.New\"", page.Html, StringComparison.Ordinal);
            var done = await browser.SubmitAsync(LinkTo(email), new Dictionary<string, string>());
            Assert.Equal($"{Login(tenant.Identifier)}?activated=1", done.Location);

            Assert.True(Assert.Single((await Members.ListAsync(tenant.Id, CancellationToken.None)).Value!).EmailConfirmed);
            // 原本的密碼不受影響；重寄一次則回「已經驗證」。
            Assert.Equal(PasswordPage(tenant.Identifier), (await browser.SubmitAsync(Login(tenant.Identifier), Credentials(email, initial))).Location);
            var again = await Members.ResendInvitationAsync(OperatorSession.Principal(), tenant.Id, id, CancellationToken.None);
            Assert.Equal((AdminStatus.Conflict, OutcomeCodes.AccountAlreadyActive), (again.Status, again.Code));
        });
    }

    [Fact]
    public async Task ADeliveryFailureStillCreatesTheAccountAndTheOperatorCanResend()
    {
        await factory.WithMail(async () =>
        {
            var (tenant, email, invitation) = await InviteMemberAsync("inv-h");
            Assert.Equal(MailDelivery.Sent, invitation.Mail);
            var (tenant2, _) = await factory.CreateTenantWithKeyAsync("inv-h2");

            factory.Mail.Result = MailDelivery.Failed;
            var failed = await Members.InviteAsync(OperatorSession.Principal(), tenant2.Id, NewEmail(), null, CustomerRoles.Viewer, CancellationToken.None);

            Assert.True(failed.IsOk, failed.Message);
            Assert.Equal(MailDelivery.Failed, failed.Value!.Mail);
            Assert.Single((await Members.ListAsync(tenant2.Id, CancellationToken.None)).Value!);

            factory.Mail.Result = MailDelivery.Sent;
            var resent = await Members.ResendInvitationAsync(OperatorSession.Principal(), tenant2.Id, failed.Value.Member.Id, CancellationToken.None);
            Assert.Equal(MailDelivery.Sent, resent.Value!.Mail);
            Assert.NotEmpty(email);
            Assert.NotNull(tenant);
        });
    }

    [Fact]
    public async Task WithoutMailThereAreNoInvitationsAndTheTemporaryPasswordPathIsUnchanged()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("inv-i");

        var invited = await Members.InviteAsync(OperatorSession.Principal(), tenant.Id, NewEmail(), null, CustomerRoles.Viewer, CancellationToken.None);

        Assert.Equal((AdminStatus.Conflict, OutcomeCodes.MailNotEnabled), (invited.Status, invited.Code));
        Assert.Empty((await Members.ListAsync(tenant.Id, CancellationToken.None)).Value!);
        var operatorInvite = await Operators.InviteAsync(OperatorSession.Principal(), NewOperatorEmail(), null, CancellationToken.None);
        Assert.Equal(OutcomeCodes.MailNotEnabled, operatorInvite.Code);
        var temporary = await Members.CreateAsync(OperatorSession.Principal(), tenant.Id, NewEmail(), null, CustomerRoles.Viewer, CancellationToken.None);
        Assert.True(temporary.IsOk);
        Assert.True(temporary.Value!.Member.MustChangePassword);
    }

    [Fact]
    public async Task ADisabledMemberCannotBeInvitedAgainAndTheirLinkStopsWorking()
    {
        await factory.WithMail(async () =>
        {
            var (tenant, email, invitation) = await InviteMemberAsync("inv-j");
            var link = LinkTo(email);
            Assert.True((await Members.SetDisabledAsync(OperatorSession.Principal(), tenant.Id, invitation.Member.Id, true, CancellationToken.None)).IsOk);

            var resend = await Members.ResendInvitationAsync(OperatorSession.Principal(), tenant.Id, invitation.Member.Id, CancellationToken.None);

            Assert.Equal(OutcomeCodes.AccountDisabled, resend.Code);
            using var browser = new PortalBrowser(factory);
            Assert.Contains("無效或已過期", (await browser.GetAsync(link)).Html, StringComparison.Ordinal);
        });
    }

    [Fact]
    public async Task AnInvalidEmailIsRejectedAndNothingIsSent()
    {
        await factory.WithMail(async () =>
        {
            var (tenant, _) = await factory.CreateTenantWithKeyAsync("inv-k");
            var before = factory.Mail.Sent.Count;

            var invited = await Members.InviteAsync(OperatorSession.Principal(), tenant.Id, "not-an-email", null, CustomerRoles.Viewer, CancellationToken.None);

            Assert.Equal(OutcomeCodes.EmailInvalid, invited.Code);
            Assert.Equal(before, factory.Mail.Sent.Count);
            Assert.Empty((await Members.ListAsync(tenant.Id, CancellationToken.None)).Value!);
        });
    }

    // 連結（權杖）與密碼都不進稽核與日誌；稽核只記信箱、角色與寄信結果。
    [Fact]
    public async Task TheAuditRecordsWhatHappenedButNeverTheLinkOrThePassword()
    {
        await factory.WithMail(async () =>
        {
            var (tenant, email, invitation) = await InviteMemberAsync("inv-l");
            var link = LinkTo(email);
            var code = Regex.Match(link, "code=(?<c>[^&]+)").Groups["c"].Value;
            using var browser = new PortalBrowser(factory);
            _ = await browser.SubmitAsync(link, ActivationForm(LongPassword, LongPassword));

            var invited = await AuditDumpAsync(AuditActions.MemberInvited);
            var activated = await AuditDumpAsync(AuditActions.MemberActivated);

            Assert.Contains(invitation.Member.Id, invited, StringComparison.Ordinal);
            Assert.Contains("\"mail\":\"sent\"", invited.Replace(" ", string.Empty), StringComparison.Ordinal);
            Assert.Contains(invitation.Member.Id, activated, StringComparison.Ordinal);
            Assert.Contains("set_password", activated, StringComparison.Ordinal);
            foreach (var secret in new[] { code, LongPassword })
                Assert.DoesNotContain(secret, invited + activated, StringComparison.Ordinal);

            Assert.NotNull(tenant);
        });
    }

    [Fact]
    public void TheLinkLifetimeIsTheIdentityTokenLifetime()
    {
        var mail = factory.Services.GetRequiredService<IOptions<MailOptions>>().Value;
        var token = factory.Services.GetRequiredService<IOptions<Microsoft.AspNetCore.Identity.DataProtectionTokenProviderOptions>>().Value;

        Assert.Equal(mail.LinkLifetime, token.TokenLifespan);
        Assert.Equal(TimeSpan.FromDays(3), mail.LinkLifetime);
    }

    // ---- 營運者 ------------------------------------------------------------------------------

    [Fact]
    public async Task AnInvitedOperatorSetsAPasswordThroughTheLinkAndThenStillMustEnrollAnAuthenticator()
    {
        await factory.WithMail(async () =>
        {
            var email = NewOperatorEmail();

            var invited = await Operators.InviteAsync(OperatorSession.Principal(), email, "王小明", CancellationToken.None);

            Assert.True(invited.IsOk, invited.Message);
            Assert.Equal(MailDelivery.Sent, invited.Value!.Mail);
            Assert.True(invited.Value.Operator.Invited);
            var link = LinkTo(email);
            Assert.StartsWith("/ui/activate?userId=", link, StringComparison.Ordinal);
            using var browser = new PortalBrowser(factory);
            Assert.Null((await browser.SubmitAsync(UiLogin, Credentials(email, LongPassword))).Location); // 還沒有密碼

            var activation = (await browser.GetAsync(link)).Html;
            Assert.Contains("name=\"Input.New\"", activation, StringComparison.Ordinal);
            PasswordFieldTests.AssertActivationFields(activation);
            var done = await browser.SubmitAsync(link, ActivationForm(LongPassword, LongPassword));
            Assert.Equal($"{UiLogin}?activated=1", done.Location);

            // 密碼是自己設的，不必再改；但驗證器仍是強制的。
            Assert.Equal(UiSecurity, (await browser.SubmitAsync(UiLogin, Credentials(email, LongPassword))).Location);
            var listed = Assert.Single((await Operators.ListAsync(CancellationToken.None)).Value!, o => o.Email == email);
            Assert.True(listed.EmailConfirmed);
            Assert.False(listed.Invited);

            using var replay = new PortalBrowser(factory);
            Assert.Contains("無效或已過期", (await replay.GetAsync(link)).Html, StringComparison.Ordinal);
        });
    }

    [Fact]
    public async Task ResendingAnOperatorInvitationRevokesTheOldLink()
    {
        await factory.WithMail(async () =>
        {
            var email = NewOperatorEmail();
            var invited = await Operators.InviteAsync(OperatorSession.Principal(), email, null, CancellationToken.None);
            var oldLink = LinkTo(email);

            var resent = await Operators.ResendInvitationAsync(OperatorSession.Principal(), invited.Value!.Operator.Id, CancellationToken.None);

            Assert.True(resent.IsOk, resent.Message);
            using var browser = new PortalBrowser(factory);
            Assert.Contains("無效或已過期", (await browser.GetAsync(oldLink)).Html, StringComparison.Ordinal);
            Assert.Contains("name=\"Input.New\"", (await browser.GetAsync(LinkTo(email))).Html, StringComparison.Ordinal);
            Assert.Contains("\"mail\":\"sent\"", (await AuditDumpAsync(AuditActions.OperatorInvitationResent)).Replace(" ", string.Empty), StringComparison.Ordinal);
        });
    }

    private static Dictionary<string, string> ActivationForm(string password, string confirm) =>
        new() { ["Input.New"] = password, ["Input.Confirm"] = confirm };
}