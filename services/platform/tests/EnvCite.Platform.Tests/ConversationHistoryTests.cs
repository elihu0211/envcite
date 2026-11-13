using System.Net;
using System.Security.Claims;
using System.Text.Json;
using System.Text.RegularExpressions;

using EnvCite.Platform.Modules.Conversations;
using EnvCite.Platform.Modules.Tenancy;

using Microsoft.AspNetCore.Mvc.Testing;

using Npgsql;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 問答歷史：預設關閉（問題文字是客戶案件的內容）；開啟後只有本人看得到、能刪、超過保留天數自動刪。
/// 共用測試主機沒開，這裡的主機把它打開。
/// </summary>
[Collection(PlatformTestGroup.Group4)]
public sealed class ConversationHistoryTests(PlatformFactory factory)
{
    private const string Question = "噪音管制標準";

    private static readonly JsonSerializerOptions Unescaped = new() { Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping };

    private WebApplicationFactory<Program> Enabled() =>
        factory.WithWebHostBuilder(b => b.UseSetting("Platform:ConversationHistory:Enabled", "true"));

    private static string HistoryPage(string tenant) => $"/portal/{tenant}/history";

    private static string ConversationPage(string tenant, Guid id) => PortalPaths.Conversation(tenant, id);

    // 表單名稱要明說（靜態 SSR 的 POST 靠 _handler 分辨是哪個表單）。
    private static Dictionary<string, string> Delete(Guid id) => new() { ["_handler"] = "portal-history-delete", ["Input.Delete"] = id.ToString() };

    private const string Citation = "《空氣污染防制法》第 5-1 條（現行） https://example.test/law/5-1";

    /// <summary>一位成員：瀏覽器（已登入）與他的 principal（直接呼叫服務用）。</summary>
    private async Task<(PortalBrowser Browser, ClaimsPrincipal Member)> SignedInAsync(
        WebApplicationFactory<Program> host, TenantResponse tenant, string role = CustomerRoles.Owner)
    {
        var email = NewEmail();
        var (userId, initial) = await AddMemberAsync(factory, tenant.Id, email, role);
        var browser = new PortalBrowser(host);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);
        return (browser, await MemberPrincipalAsync(factory, tenant.Id, userId));
    }

    private static Task<Guid?> RecordAsync(WebApplicationFactory<Program> host, ClaimsPrincipal who, string question, Guid? conversation = null) =>
        host.Services.GetRequiredService<IAskService>().RecordAsync(who, conversation, question, Citation, TestContext.Current.CancellationToken);

    private async Task<long> CountAsync(string tenantId)
    {
        await using var connection = new NpgsqlConnection(factory.Services.GetRequiredService<IOptions<DatabaseOptions>>().Value.Platform);
        await connection.OpenAsync();
        await using var command = new NpgsqlCommand("""select count(*) from platform."Conversations" where "TenantId" = @t""", connection);
        command.Parameters.AddWithValue("t", tenantId);
        return (long)(await command.ExecuteScalarAsync())!;
    }

    private async Task AgeAsync(string tenantId, int days)
    {
        await using var connection = new NpgsqlConnection(factory.Services.GetRequiredService<IOptions<DatabaseOptions>>().Value.Platform);
        await connection.OpenAsync();
        await using var command = new NpgsqlCommand(
            """update platform."Conversations" set "UpdatedAt" = now() - make_interval(days => @d) where "TenantId" = @t""", connection);
        command.Parameters.AddWithValue("d", days);
        command.Parameters.AddWithValue("t", tenantId);
        await command.ExecuteNonQueryAsync();
    }

    [Fact]
    public async Task NothingIsStoredByDefault()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-off");
        var (browser, member) = await SignedInAsync(factory, tenant);
        using var b = browser;

        var home = await browser.GetAsync(Home(tenant.Identifier));
        var recorded = await RecordAsync(factory, member, Question);

        Assert.Contains("查詢文字不會被記錄", home.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("對話紀錄", home.Html, StringComparison.Ordinal); // 選單裡也沒有
        Assert.Null(recorded);
        Assert.Equal(0, await CountAsync(tenant.Id));
        var page = await browser.GetAsync(HistoryPage(tenant.Identifier));
        Assert.Contains("沒有開啟對話紀錄", page.Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task WhenEnabledAQuestionAndItsCitationsAreKeptAndAFollowUpJoinsTheSameConversation()
    {
        using var host = Enabled();
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-on");
        var (browser, member) = await SignedInAsync(host, tenant);
        using var b = browser;

        var id = await RecordAsync(host, member, Question);
        var followUp = await RecordAsync(host, member, "固定污染源", id);

        Assert.NotNull(id);
        Assert.Equal(id, followUp); // 同一串
        Assert.Equal(1, await CountAsync(tenant.Id));

        var home = await browser.GetAsync(Home(tenant.Identifier));
        Assert.Contains("你的提問會存在", home.Html, StringComparison.Ordinal); // 不再說「不會被記錄」
        Assert.DoesNotContain("查詢文字不會被記錄", home.Html, StringComparison.Ordinal);

        var list = await browser.GetAsync(HistoryPage(tenant.Identifier));
        Assert.Contains(Question, list.Html, StringComparison.Ordinal); // 標題是第一個問題
        Assert.Contains("對話紀錄", list.Html, StringComparison.Ordinal); // 選單項

        // 開啟那一串：兩個問題與出處都在，可以接著問。
        var opened = await browser.GetAsync(ConversationPage(tenant.Identifier, id!.Value));
        Assert.Equal(HttpStatusCode.OK, opened.Status);
        Assert.Equal(2, Regex.Count(opened.Html, "class=\"ep-turn\""));
        Assert.Contains(Question, opened.Html, StringComparison.Ordinal);
        Assert.Contains("固定污染源", opened.Html, StringComparison.Ordinal);
        Assert.Contains("《空氣污染防制法》第 5-1 條", opened.Html, StringComparison.Ordinal); // 出處
        Assert.Contains("class=\"ep-composer\"", opened.Html, StringComparison.Ordinal);
    }

    // 側欄的「最近的對話」：本人的、新的在前、依日期分組；別人的（連同租戶的 Owner）看不到；沒開啟就整段不畫。
    [Fact]
    public async Task TheSideNavigationListsOnlyMyRecentConversationsNewestFirst()
    {
        using var host = Enabled();
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-recent");
        var (aliceBrowser, alice) = await SignedInAsync(host, tenant, CustomerRoles.Developer);
        var (bossBrowser, _) = await SignedInAsync(host, tenant, CustomerRoles.Owner);
        using var a = aliceBrowser;
        using var b = bossBrowser;
        var first = await RecordAsync(host, alice, Question);
        var second = await RecordAsync(host, alice, "固定污染源");
        Assert.NotEqual(first, second);

        var mine = (await aliceBrowser.GetAsync(HistoryPage(tenant.Identifier))).Html;
        var theirs = (await bossBrowser.GetAsync(HistoryPage(tenant.Identifier))).Html;

        Assert.Contains("class=\"ep-recent\"", mine, StringComparison.Ordinal);
        Assert.Contains("今天", mine, StringComparison.Ordinal);
        Assert.Contains($"href=\"{ConversationPage(tenant.Identifier, first!.Value)}\"", mine, StringComparison.Ordinal);
        Assert.Contains($"href=\"{ConversationPage(tenant.Identifier, second!.Value)}\"", mine, StringComparison.Ordinal);
        Assert.True(
            mine.IndexOf($"/{second}\"", StringComparison.Ordinal) < mine.IndexOf($"/{first}\"", StringComparison.Ordinal),
            "the newest conversation comes first");
        Assert.DoesNotContain("ep-recent", theirs, StringComparison.Ordinal);
        Assert.DoesNotContain(first.ToString()!, theirs, StringComparison.Ordinal);
    }

    [Fact]
    public async Task WithoutHistoryTheSideNavigationHasNoRecentConversations()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-norecent");
        var (browser, member) = await SignedInAsync(factory, tenant);
        using var b = browser;
        await RecordAsync(factory, member, Question);

        var home = await browser.GetAsync(Home(tenant.Identifier));

        Assert.DoesNotContain("ep-recent", home.Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task OnlyTheirOwnerCanSeeOrJoinAConversationNotEvenAnOwnerOfTheSameTenant()
    {
        using var host = Enabled();
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-iso");
        var (aliceBrowser, alice) = await SignedInAsync(host, tenant, CustomerRoles.Developer);
        var (bossBrowser, boss) = await SignedInAsync(host, tenant, CustomerRoles.Owner);
        var (other, _) = await factory.CreateTenantWithKeyAsync("ch-iso2");
        var (strangerBrowser, _) = await SignedInAsync(host, other);
        using var a = aliceBrowser;
        using var b = bossBrowser;
        using var s = strangerBrowser;

        var id = (await RecordAsync(host, alice, "機密案件關鍵字"))!.Value;

        // 同組織的 Owner、別的租戶的人在自己的租戶：看不到，而且和「不存在」是同一個畫面。
        foreach (var (who, tenantId) in new[] { (bossBrowser, tenant.Identifier), (strangerBrowser, other.Identifier) })
        {
            var list = await who.GetAsync(HistoryPage(tenantId));
            Assert.DoesNotContain("機密案件關鍵字", list.Html, StringComparison.Ordinal);
            var opened = await who.GetAsync(ConversationPage(tenantId, id));
            Assert.DoesNotContain("機密案件關鍵字", opened.Html, StringComparison.Ordinal);
            Assert.Contains("找不到這個對話", opened.Html, StringComparison.Ordinal);
        }

        // 別的租戶的人走到這個租戶的路徑：入口的授權就擋下，內容不會出現。
        var crossTenant = await strangerBrowser.GetAsync(ConversationPage(tenant.Identifier, id));
        Assert.DoesNotContain("機密案件關鍵字", crossTenant.Html, StringComparison.Ordinal);
        Assert.NotEqual(HttpStatusCode.OK, crossTenant.Status);

        // 把別人的對話 Id 當成自己的接續：當作另開一串，原本那串沒有多出任何訊息。
        var hijack = await RecordAsync(host, boss, "老闆的問題", id);
        Assert.NotEqual(id, hijack);
        var aliceOpened = await aliceBrowser.GetAsync(ConversationPage(tenant.Identifier, id));
        Assert.DoesNotContain("老闆的問題", aliceOpened.Html, StringComparison.Ordinal);
        Assert.Contains("機密案件關鍵字", aliceOpened.Html, StringComparison.Ordinal);
        Assert.Equal(2, await CountAsync(tenant.Id));
    }

    [Fact]
    public async Task DeletingRemovesTheConversationAuditsOnlyItsIdAndIsRefusedForSomeoneElses()
    {
        using var host = Enabled();
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-del");
        var (aliceBrowser, alice) = await SignedInAsync(host, tenant, CustomerRoles.Developer);
        var (bossBrowser, boss) = await SignedInAsync(host, tenant, CustomerRoles.Owner);
        using var a = aliceBrowser;
        using var b = bossBrowser;
        var id = (await RecordAsync(host, alice, "要被刪掉的問題"))!.Value;

        // 刪除表單只在有自己的對話時才出現，所以老闆先有一串自己的。
        await RecordAsync(host, boss, "老闆自己的問題");
        var refused = await bossBrowser.SubmitAsync(HistoryPage(tenant.Identifier), Delete(id));
        Assert.Contains("找不到這個對話", refused.Html, StringComparison.Ordinal);
        Assert.Equal(2, await CountAsync(tenant.Id));

        var deleted = await aliceBrowser.SubmitAsync(HistoryPage(tenant.Identifier), Delete(id));
        Assert.Contains("已刪除", deleted.Html, StringComparison.Ordinal);
        Assert.Equal(1, await CountAsync(tenant.Id)); // 只少了她那一串
        var gone = await aliceBrowser.GetAsync(ConversationPage(tenant.Identifier, id));
        Assert.Contains("找不到這個對話", gone.Html, StringComparison.Ordinal);

        var events = (await factory.Services.GetRequiredService<IAuditQuery>()
            .QueryAsync(new AuditFilter(Tenant: tenant.Id, Action: AuditActions.ConversationDeleted), CancellationToken.None)).Items;
        var evt = Assert.Single(events);
        Assert.Equal(id.ToString(), evt.TargetId);
        // 稽核不存內容。預設的 JSON 編碼會把中文轉成 \uXXXX，那樣比對永遠找不到，所以改用不跳脫的編碼。
        var written = JsonSerializer.Serialize(evt, Unescaped);
        Assert.DoesNotContain("要被刪掉的問題", written, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ConversationsPastTheRetentionPeriodAreDeletedAndRecentOnesStay()
    {
        using var host = Enabled();
        var (old, _) = await factory.CreateTenantWithKeyAsync("ch-old");
        var (recent, _) = await factory.CreateTenantWithKeyAsync("ch-new");
        var (oldBrowser, oldMember) = await SignedInAsync(host, old);
        var (recentBrowser, recentMember) = await SignedInAsync(host, recent);
        using var o = oldBrowser;
        using var r = recentBrowser;
        await RecordAsync(host, oldMember, "很久以前的問題");
        await RecordAsync(host, recentMember, "剛剛的問題");
        await AgeAsync(old.Id, days: 91); // 預設保留 90 天

        var purged = await host.Services.GetRequiredService<ConversationRetention>().PurgeAsync(CancellationToken.None);

        Assert.True(purged >= 1);
        Assert.Equal(0, await CountAsync(old.Id));
        Assert.Equal(1, await CountAsync(recent.Id));
    }

    [Fact]
    public async Task RetentionDoesNothingWhileHistoryIsOff()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-off2");
        using var host = Enabled();
        var (browser, member) = await SignedInAsync(host, tenant);
        using var b = browser;
        await RecordAsync(host, member, Question);
        await AgeAsync(tenant.Id, days: 400);

        var purged = await factory.Services.GetRequiredService<ConversationRetention>().PurgeAsync(CancellationToken.None); // 共用主機：關閉

        Assert.Equal(0, purged);
        Assert.Equal(1, await CountAsync(tenant.Id));
    }

    [Fact]
    public void TheRetentionPeriodMustBeBetweenOneDayAndTenYears()
    {
        foreach (var days in new[] { "0", "3651" })
        {
            var ex = Assert.ThrowsAny<Exception>(() =>
            {
                using var bad = factory.WithWebHostBuilder(b => b.UseSetting("Platform:ConversationHistory:RetentionDays", days));
                _ = bad.Services;
            });
            Assert.Contains("RetentionDays", ex.ToString(), StringComparison.Ordinal);
        }
    }
}