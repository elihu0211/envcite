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

    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private static readonly JsonSerializerOptions Unescaped = new() { Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping };

    private static string HistoryPage(string tenant) => $"/portal/{tenant}/history";

    private static string ConversationPage(string tenant, Guid id) => PortalPaths.Conversation(tenant, id);

    // 釘選、重新命名、刪除是 POST 到對話底下的端點（側欄與對話紀錄頁的選單表單），做完導回原來的頁面。
    private static string Manage(string tenant, Guid id, string action) => $"{ConversationPage(tenant, id)}/{action}";

    private static Task<(HttpStatusCode Status, string Html, string? Location)> PinAsync(PortalBrowser who, string tenant, Guid id, bool pinned) =>
        who.SubmitAsync(HistoryPage(tenant), new() { ["pinned"] = pinned ? "true" : "false" }, Manage(tenant, id, "pin"));

    private static Task<(HttpStatusCode Status, string Html, string? Location)> RenameAsync(PortalBrowser who, string tenant, Guid id, string title) =>
        who.SubmitAsync(HistoryPage(tenant), new() { ["title"] = title }, Manage(tenant, id, "rename"));

    private static Task<(HttpStatusCode Status, string Html, string? Location)> DeleteAsync(PortalBrowser who, string tenant, Guid id) =>
        who.SubmitAsync(HistoryPage(tenant), [], Manage(tenant, id, "delete"));

    private static Task<(HttpStatusCode Status, string Html, string? Location)> ArchiveAsync(PortalBrowser who, string tenant, Guid id, bool archived) =>
        who.SubmitAsync(HistoryPage(tenant), new() { ["archived"] = archived ? "true" : "false" }, Manage(tenant, id, "archive"));

    private static Task<(HttpStatusCode Status, string Html, string? Location)> ForkAsync(PortalBrowser who, string tenant, Guid id, string title) =>
        who.SubmitAsync(HistoryPage(tenant), new() { ["title"] = title }, Manage(tenant, id, "fork"));

    private static Task<Guid?> RecordTurnAsync(WebApplicationFactory<Program> host, ClaimsPrincipal who, Guid? conversation, ConversationTurn turn) =>
        host.Services.GetRequiredService<IAskService>().RecordAsync(who, conversation, turn, Ct);

    /// <summary>頁面上有沒有一個帶這個 class 的連結指向這串對話：<c>ep-history-link</c> 是列表的列，<c>ep-recent-link</c> 是側欄的項目。</summary>
    private static bool HasLink(string html, string cssClass, string path) =>
        Regex.IsMatch(html, $"<a[^>]*class=\"[^\"]*{cssClass}[^\"]*\"[^>]*href=\"{Regex.Escape(path)}\"|<a[^>]*href=\"{Regex.Escape(path)}\"[^>]*class=\"[^\"]*{cssClass}[^\"]*\"");

    /// <summary>訊息的內容、順序、引用與問的日期：分叉要原樣複製的全部。</summary>
    private static string Shape(ConversationDetail detail) =>
        string.Join("|", detail.Messages.Select(m =>
            $"{m.Role}:{m.Content}:{m.AsOf}:{m.Outcome}:{string.Join(",", (m.Citations ?? []).Select(c => $"{c.Code}={c.Label}={c.Uri}"))}"));

    private static Guid ForkedIdOf(string? location) =>
        Guid.Parse(Regex.Match(location ?? string.Empty, "/c/([0-9a-fA-F-]{36})").Groups[1].Value);

    private static async Task<ConversationDetail?> OpenAsync(WebApplicationFactory<Program> host, ClaimsPrincipal who, Guid id) =>
        await host.Services.GetRequiredService<IAskService>().ConversationAsync(who, id, TestContext.Current.CancellationToken);

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
    public async Task AQuestionAndItsCitationsAreKeptAndAFollowUpJoinsTheSameConversation()
    {
        var host = factory;
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-on");
        var (browser, member) = await SignedInAsync(host, tenant);
        using var b = browser;

        var id = await RecordAsync(host, member, Question);
        var followUp = await RecordAsync(host, member, "固定污染源", id);

        Assert.NotNull(id);
        Assert.Equal(id, followUp); // 同一串
        Assert.Equal(1, await CountAsync(tenant.Id));

        var home = await browser.GetAsync(Home(tenant.Identifier));
        Assert.Contains("你的提問會存在", home.Html, StringComparison.Ordinal);

        var list = await browser.GetAsync(HistoryPage(tenant.Identifier));
        Assert.Contains(Question, list.Html, StringComparison.Ordinal); // 標題是第一個問題
        Assert.Contains("對話紀錄", list.Html, StringComparison.Ordinal); // 選單項
        Assert.Contains("class=\"ep-history-shell\"", list.Html, StringComparison.Ordinal);
        Assert.Contains("data-history-filter", list.Html, StringComparison.Ordinal);

        // 開啟那一串：兩個問題與出處都在，可以接著問。
        var opened = await browser.GetAsync(ConversationPage(tenant.Identifier, id!.Value));
        Assert.Equal(HttpStatusCode.OK, opened.Status);
        Assert.Equal(2, Regex.Count(opened.Html, "class=\"ep-turn\""));
        Assert.Contains("class=\"ep-answer-head\"", opened.Html, StringComparison.Ordinal);
        Assert.Contains("class=\"ep-answer-actions\"", opened.Html, StringComparison.Ordinal);
        Assert.Contains(Question, opened.Html, StringComparison.Ordinal);
        Assert.Contains("固定污染源", opened.Html, StringComparison.Ordinal);
        Assert.Contains("《空氣污染防制法》第 5-1 條", opened.Html, StringComparison.Ordinal); // 出處
        Assert.Contains("class=\"ep-composer\"", opened.Html, StringComparison.Ordinal);
    }

    // 側欄的「最近的對話」：本人的、新的在前、依日期分組；別人的（連同租戶的 Owner）看不到。
    [Fact]
    public async Task TheSideNavigationListsOnlyMyRecentConversationsNewestFirst()
    {
        var host = factory;
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
        Assert.DoesNotContain("class=\"ep-side-group\"", mine, StringComparison.Ordinal); // 平面清單，沒有日期分組
        Assert.DoesNotContain("data-recent-filter", mine, StringComparison.Ordinal);
        Assert.Contains($"href=\"{ConversationPage(tenant.Identifier, first!.Value)}\"", mine, StringComparison.Ordinal);
        Assert.Contains($"href=\"{ConversationPage(tenant.Identifier, second!.Value)}\"", mine, StringComparison.Ordinal);
        Assert.True(
            mine.IndexOf($"/{second}\"", StringComparison.Ordinal) < mine.IndexOf($"/{first}\"", StringComparison.Ordinal),
            "the newest conversation comes first");
        Assert.DoesNotContain("ep-recent", theirs, StringComparison.Ordinal);
        Assert.DoesNotContain(first.ToString()!, theirs, StringComparison.Ordinal);
    }

    [Fact]
    public async Task OnlyTheirOwnerCanSeeOrJoinAConversationNotEvenAnOwnerOfTheSameTenant()
    {
        var host = factory;
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
        var host = factory;
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-del");
        var (aliceBrowser, alice) = await SignedInAsync(host, tenant, CustomerRoles.Developer);
        var (bossBrowser, boss) = await SignedInAsync(host, tenant, CustomerRoles.Owner);
        using var a = aliceBrowser;
        using var b = bossBrowser;
        var id = (await RecordAsync(host, alice, "要被刪掉的問題"))!.Value;

        // 同組織的老闆送來別人的 Id：和「不存在」一樣只是導回，什麼也沒刪。
        await RecordAsync(host, boss, "老闆自己的問題");
        var refused = await DeleteAsync(bossBrowser, tenant.Identifier, id);
        Assert.Equal(HttpStatusCode.Redirect, refused.Status);
        Assert.Equal(2, await CountAsync(tenant.Id));

        var deleted = await DeleteAsync(aliceBrowser, tenant.Identifier, id);
        Assert.Equal(HttpStatusCode.Redirect, deleted.Status);
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

    // 釘選與改名：釘選的在側欄自成一組、排在最上面；兩者都不算新的活動（不改最後更新時間，所以不改排序也不延長保存）。
    [Fact]
    public async Task PinnedConversationsFloatToTheirOwnGroupAndRenamingChangesOnlyTheTitle()
    {
        var host = factory;
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-pin");
        var (browser, alice) = await SignedInAsync(host, tenant, CustomerRoles.Developer);
        using var b = browser;
        var t = tenant.Identifier;
        var older = (await RecordAsync(host, alice, Question))!.Value;
        var newer = (await RecordAsync(host, alice, "固定污染源"))!.Value;
        var before = (await OpenAsync(host, alice, older))!;

        Assert.Equal(HttpStatusCode.Redirect, (await PinAsync(browser, t, older, pinned: true)).Status);

        var page = (await browser.GetAsync(HistoryPage(t))).Html;
        Assert.Contains("已釘選", page, StringComparison.Ordinal);
        Assert.True(
            page.IndexOf($"href=\"{ConversationPage(t, older)}\"", StringComparison.Ordinal) < page.IndexOf($"href=\"{ConversationPage(t, newer)}\"", StringComparison.Ordinal),
            "the pinned conversation comes first even though it is the older one");
        // 選單是原生 popover 加普通表單：沒有 JavaScript 也能用。
        Assert.Contains($"popovertarget=\"ep-conv-{older:N}\"", page, StringComparison.Ordinal);
        Assert.Contains($"action=\"{Manage(t, older, "rename")}\"", page, StringComparison.Ordinal);
        Assert.Contains($"action=\"{Manage(t, older, "delete")}\"", page, StringComparison.Ordinal);

        Assert.Equal(HttpStatusCode.Redirect, (await RenameAsync(browser, t, older, "  環境\n 稽查  ")).Status);
        await RenameAsync(browser, t, older, "   "); // 空白的標題不接受，維持原樣
        var after = (await OpenAsync(host, alice, older))!;
        Assert.Equal(("環境 稽查", true), (after.Title, after.Pinned));
        Assert.Equal(before.UpdatedAt, after.UpdatedAt);

        await PinAsync(browser, t, older, pinned: false);
        Assert.DoesNotContain("已釘選", (await browser.GetAsync(HistoryPage(t))).Html, StringComparison.Ordinal);
        Assert.False((await OpenAsync(host, alice, older))!.Pinned);
    }

    [Fact]
    public async Task SomeoneElsesConversationCannotBePinnedOrRenamedNotEvenByAnOwnerOrAnotherTenant()
    {
        var host = factory;
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-pin-iso");
        var (other, _) = await factory.CreateTenantWithKeyAsync("ch-pin-iso2");
        var (aliceBrowser, alice) = await SignedInAsync(host, tenant, CustomerRoles.Developer);
        var (bossBrowser, _) = await SignedInAsync(host, tenant, CustomerRoles.Owner);
        var (strangerBrowser, _) = await SignedInAsync(host, other);
        using var a = aliceBrowser;
        using var b = bossBrowser;
        using var s = strangerBrowser;
        var id = (await RecordAsync(host, alice, Question))!.Value;

        await PinAsync(bossBrowser, tenant.Identifier, id, pinned: true);
        await RenameAsync(bossBrowser, tenant.Identifier, id, "被改名");
        await DeleteAsync(bossBrowser, tenant.Identifier, id);
        var crossTenant = await PinAsync(strangerBrowser, tenant.Identifier, id, pinned: true);

        var detail = (await OpenAsync(host, alice, id))!;
        Assert.Equal((Question, false), (detail.Title, detail.Pinned));
        Assert.NotEqual(HttpStatusCode.OK, crossTenant.Status);
    }

    // 封存：從側欄與預設列表消失、只在「已封存」找得到；封存時取消釘選；不算新的活動（不改最後更新時間）。取消封存就回來。
    [Fact]
    public async Task ArchivingMovesAConversationFromTheListsToTheArchivedViewAndUnarchivingBringsItBack()
    {
        var host = factory;
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-arch");
        var (browser, alice) = await SignedInAsync(host, tenant, CustomerRoles.Developer);
        using var b = browser;
        var t = tenant.Identifier;
        var older = (await RecordAsync(host, alice, Question))!.Value;
        var newer = (await RecordAsync(host, alice, "固定污染源"))!.Value;
        await PinAsync(browser, t, older, pinned: true);
        var before = (await OpenAsync(host, alice, older))!;

        // 選單是普通表單：封存與分叉各一個，沒有 JavaScript 也能用。
        var menu = (await browser.GetAsync(HistoryPage(t))).Html;
        Assert.Contains($"action=\"{Manage(t, older, "archive")}\"", menu, StringComparison.Ordinal);
        Assert.Contains($"action=\"{Manage(t, older, "fork")}\"", menu, StringComparison.Ordinal);

        Assert.Equal(HttpStatusCode.Redirect, (await ArchiveAsync(browser, t, older, archived: true)).Status);

        var list = (await browser.GetAsync(HistoryPage(t))).Html;
        Assert.False(HasLink(list, "ep-history-link", ConversationPage(t, older)));
        Assert.False(HasLink(list, "ep-recent-link", ConversationPage(t, older)));
        Assert.True(HasLink(list, "ep-history-link", ConversationPage(t, newer)));
        Assert.Contains("?view=archived", list, StringComparison.Ordinal); // 兩個檢視的分頁連結

        var archived = (await browser.GetAsync($"{HistoryPage(t)}?view=archived")).Html;
        Assert.True(HasLink(archived, "ep-history-link", ConversationPage(t, older)));
        Assert.False(HasLink(archived, "ep-history-link", ConversationPage(t, newer)));
        Assert.False(HasLink(archived, "ep-recent-link", ConversationPage(t, older))); // 側欄也沒有
        Assert.DoesNotContain("已釘選", archived, StringComparison.Ordinal);
        Assert.Contains("name=\"archived\" value=\"false\"", archived, StringComparison.Ordinal); // 已封存的列是「取消封存」

        var after = (await OpenAsync(host, alice, older))!; // 網址仍可開啟
        Assert.False(after.Pinned);
        Assert.Equal(before.UpdatedAt, after.UpdatedAt);

        Assert.Equal(HttpStatusCode.Redirect, (await ArchiveAsync(browser, t, older, archived: false)).Status);
        Assert.True(HasLink((await browser.GetAsync(HistoryPage(t))).Html, "ep-history-link", ConversationPage(t, older)));
        var empty = (await browser.GetAsync($"{HistoryPage(t)}?view=archived")).Html;
        Assert.False(HasLink(empty, "ep-history-link", ConversationPage(t, older)));
        Assert.Contains("沒有封存的對話", empty, StringComparison.Ordinal);
        Assert.False((await OpenAsync(host, alice, older))!.Pinned); // 取消封存不會把釘選帶回來

        // 不認得的檢視當作預設。
        Assert.True(HasLink((await browser.GetAsync($"{HistoryPage(t)}?view=bogus")).Html, "ep-history-link", ConversationPage(t, newer)));
    }

    [Fact]
    public async Task AFollowUpInAnArchivedConversationBringsItBackToTheList()
    {
        var host = factory;
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-arch-follow");
        var (browser, alice) = await SignedInAsync(host, tenant, CustomerRoles.Developer);
        using var b = browser;
        var t = tenant.Identifier;
        var id = (await RecordAsync(host, alice, Question))!.Value;
        await ArchiveAsync(browser, t, id, archived: true);

        Assert.Equal(id, await RecordAsync(host, alice, "固定污染源", id));

        Assert.True(HasLink((await browser.GetAsync(HistoryPage(t))).Html, "ep-history-link", ConversationPage(t, id)));
        Assert.False(HasLink((await browser.GetAsync($"{HistoryPage(t)}?view=archived")).Html, "ep-history-link", ConversationPage(t, id)));
    }

    [Fact]
    public async Task SomeoneElsesConversationCannotBeArchivedOrForkedNotEvenByAnOwnerOrAnotherTenant()
    {
        var host = factory;
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-arch-iso");
        var (other, _) = await factory.CreateTenantWithKeyAsync("ch-arch-iso2");
        var (aliceBrowser, alice) = await SignedInAsync(host, tenant, CustomerRoles.Developer);
        var (bossBrowser, _) = await SignedInAsync(host, tenant, CustomerRoles.Owner);
        var (strangerBrowser, _) = await SignedInAsync(host, other);
        using var a = aliceBrowser;
        using var b = bossBrowser;
        using var s = strangerBrowser;
        var t = tenant.Identifier;
        var id = (await RecordAsync(host, alice, Question))!.Value;

        var archive = await ArchiveAsync(bossBrowser, t, id, archived: true);
        var fork = await ForkAsync(bossBrowser, t, id, "偷來的分叉");
        var crossArchive = await ArchiveAsync(strangerBrowser, t, id, archived: true);
        var crossFork = await ForkAsync(strangerBrowser, t, id, "偷來的分叉");

        // 與「不存在」一樣只是導回：沒封存、也沒複製出任何一串。
        Assert.Equal(HttpStatusCode.Redirect, archive.Status);
        Assert.Equal(HttpStatusCode.Redirect, fork.Status);
        Assert.DoesNotContain("/c/", fork.Location ?? string.Empty, StringComparison.Ordinal);
        Assert.NotEqual(HttpStatusCode.OK, crossArchive.Status);
        Assert.NotEqual(HttpStatusCode.OK, crossFork.Status);
        Assert.Equal(1, await CountAsync(tenant.Id));
        Assert.True(HasLink((await aliceBrowser.GetAsync(HistoryPage(t))).Html, "ep-history-link", ConversationPage(t, id)));
    }

    // 分叉：整串複製（訊息、引用、問的日期與順序），新的一串各自發展；不帶釘選與封存，不改原本那串。
    [Fact]
    public async Task ForkCopiesTheWholeConversationAndTheTwoThenGoTheirOwnWay()
    {
        var host = factory;
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-fork");
        var (browser, alice) = await SignedInAsync(host, tenant, CustomerRoles.Developer);
        using var b = browser;
        var t = tenant.Identifier;
        var citation = new ConversationCitation("L1", "空氣污染防制法 第 32 條", "https://envcite/tw/law/O0020001/2018-08-01/zho/art_32");
        var source = (await RecordTurnAsync(host, alice, null, new ConversationTurn(Question, "不得燃燒 [L1]。", ConversationOutcome.Answered, [citation], "2024-01-01")))!.Value;
        factory.Time.Advance(TimeSpan.FromMinutes(1)); // 測試的時鐘是凍結的：兩輪同一刻，讀回來的先後就不確定
        await RecordTurnAsync(host, alice, source, new ConversationTurn("那罰款多少", "找不到依據。", ConversationOutcome.Refused, []));
        await PinAsync(browser, t, source, pinned: true);
        var original = (await OpenAsync(host, alice, source))!;

        var forked = await ForkAsync(browser, t, source, "  分叉\n 測試 ");

        Assert.Equal(HttpStatusCode.Redirect, forked.Status);
        var forkId = ForkedIdOf(forked.Location); // 做完前往新的那一串，不是導回
        Assert.NotEqual(source, forkId);
        var copy = (await OpenAsync(host, alice, forkId))!;
        Assert.Equal(("分叉 測試", false), (copy.Title, copy.Pinned)); // 標題同改名的正規化；釘選不帶過來
        Assert.Equal(4, copy.Messages.Count);
        Assert.Equal(Shape(original), Shape(copy));
        Assert.True(copy.UpdatedAt >= original.UpdatedAt); // 新的活動：保存期限從現在起算

        var untouched = (await OpenAsync(host, alice, source))!;
        Assert.Equal((original.Title, true, original.UpdatedAt), (untouched.Title, untouched.Pinned, untouched.UpdatedAt));

        // 各自發展：在分叉上接著問，原本那串不變。
        Assert.Equal(forkId, await RecordAsync(host, alice, "只在分叉上問", forkId));
        Assert.Equal(original.Messages.Count, (await OpenAsync(host, alice, source))!.Messages.Count);
        Assert.Equal(original.Messages.Count + 2, (await OpenAsync(host, alice, forkId))!.Messages.Count);
        Assert.Equal(2, await CountAsync(tenant.Id));

        // 分叉出來的是一串可以開啟、接著問的對話。
        var opened = await browser.GetAsync(ConversationPage(t, forkId));
        Assert.Equal(HttpStatusCode.OK, opened.Status);
        Assert.Contains("class=\"ep-composer\"", opened.Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task AForkOfAnArchivedConversationLandsInTheNormalList()
    {
        var host = factory;
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-fork-arch");
        var (browser, alice) = await SignedInAsync(host, tenant, CustomerRoles.Developer);
        using var b = browser;
        var t = tenant.Identifier;
        var source = (await RecordAsync(host, alice, Question))!.Value;
        await ArchiveAsync(browser, t, source, archived: true);

        var forkId = ForkedIdOf((await ForkAsync(browser, t, source, "從封存分叉")).Location);

        var list = (await browser.GetAsync(HistoryPage(t))).Html;
        Assert.True(HasLink(list, "ep-history-link", ConversationPage(t, forkId)));
        Assert.False(HasLink(list, "ep-history-link", ConversationPage(t, source))); // 原本那串仍在封存
    }

    [Fact]
    public async Task AForkWithABlankTitleCopiesNothing()
    {
        var host = factory;
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-fork-blank");
        var (browser, alice) = await SignedInAsync(host, tenant, CustomerRoles.Developer);
        using var b = browser;
        var id = (await RecordAsync(host, alice, Question))!.Value;

        var forked = await ForkAsync(browser, tenant.Identifier, id, "   ");

        Assert.Equal(HttpStatusCode.Redirect, forked.Status);
        Assert.DoesNotContain("/c/", forked.Location ?? string.Empty, StringComparison.Ordinal);
        Assert.Equal(1, await CountAsync(tenant.Id));
    }

    // 搜尋只比對標題（內容是客戶案情，不建索引）；使用者打的 % 與 _ 是文字，不是萬用字元；不分大小寫。
    [Fact]
    public async Task TitleSearchMatchesTitlesOnlyAndWildcardsAreJustText()
    {
        var host = factory;
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-find");
        var (browser, alice) = await SignedInAsync(host, tenant, CustomerRoles.Developer);
        using var b = browser;
        var ask = host.Services.GetRequiredService<IAskService>();
        foreach (var title in new[] { "噪音管制標準", "固定污染源", "達成率 100% 的做法", "PM2.5 管制" })
        {
            await ask.RecordAsync(alice, null, title, "答案", Ct);
        }

        await ask.RecordAsync(alice, null, "別的問題", "只在答案裡的詞", Ct);

        // 側欄永遠列最近的對話，所以只看主要內容區。
        async Task<string> Found(string query)
        {
            var html = (await browser.GetAsync($"{HistoryPage(tenant.Identifier)}?q={Uri.EscapeDataString(query)}")).Html;
            return html[html.IndexOf("<main", StringComparison.Ordinal)..];
        }

        var noise = await Found("噪音");
        Assert.Contains("噪音管制標準", noise, StringComparison.Ordinal);
        Assert.DoesNotContain("固定污染源", noise, StringComparison.Ordinal);

        var percent = await Found("%");
        Assert.Contains("達成率 100% 的做法", percent, StringComparison.Ordinal);
        Assert.DoesNotContain("噪音管制標準", percent, StringComparison.Ordinal);

        Assert.Contains("沒有符合的對話", await Found("_"), StringComparison.Ordinal);
        Assert.Contains("沒有符合的對話", await Found("只在答案裡的詞"), StringComparison.Ordinal);
        Assert.Contains("PM2.5 管制", await Found("pm2"), StringComparison.Ordinal);
    }

    // 引用以結構存下來（代號、法規名、網址；不存條文本文），開啟舊對話時連同問的是哪一天一起還原。
    [Fact]
    public async Task CitationsAreStoredStructuredAndAStoredConversationShowsThemWithTheDateAskedAbout()
    {
        var host = factory;
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-cite");
        var (browser, alice) = await SignedInAsync(host, tenant, CustomerRoles.Developer);
        using var b = browser;
        var citation = new ConversationCitation("L1", "空氣污染防制法 第 5-1 條", "https://example.test/law/5-1");

        var id = (await host.Services.GetRequiredService<IAskService>().RecordAsync(
            alice, null, new ConversationTurn(Question, "不得露天燃燒 [L1]。", ConversationOutcome.Answered, [citation], "2021-01-01"), Ct))!.Value;

        var detail = (await OpenAsync(host, alice, id))!;
        Assert.Equal("2021-01-01", detail.Messages[0].AsOf);
        Assert.Equal(("不得露天燃燒 [L1]。", ConversationOutcome.Answered), (detail.Messages[1].Content, detail.Messages[1].Outcome));
        Assert.Equal(citation, Assert.Single(detail.Messages[1].Citations!));

        var opened = await browser.GetAsync(ConversationPage(tenant.Identifier, id));
        // 正文裡的 [L1] 已由排版引擎轉成可點擊徽章（<button class="ep-cite-inline">[L1]</button>），純文字還原應一致。
        var plainText = System.Text.RegularExpressions.Regex.Replace(opened.Html, "<[^>]+>", string.Empty);
        Assert.Contains("不得露天燃燒 [L1]。", plainText, StringComparison.Ordinal);
        Assert.Contains("ep-cite-inline", opened.Html, StringComparison.Ordinal);
        Assert.Contains("[L1] 空氣污染防制法 第 5-1 條（2021-01-01 有效） https://example.test/law/5-1", opened.Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task ConversationsPastTheRetentionPeriodAreDeletedAndRecentOnesStay()
    {
        var host = factory;
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