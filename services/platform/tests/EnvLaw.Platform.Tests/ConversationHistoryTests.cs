using System.Net;
using System.Text.Json;
using System.Text.RegularExpressions;

using EnvLaw.Platform.Modules.Conversations;
using EnvLaw.Platform.Modules.Tenancy;
using EnvLaw.Platform.SharedKernel;

using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

using Npgsql;

using static EnvLaw.Platform.Tests.PortalTestSupport;

namespace EnvLaw.Platform.Tests;

/// <summary>
/// 問答歷史：預設關閉（問題文字是客戶案件的內容）；開啟後只有本人看得到、能刪、超過保留天數自動刪。
/// 共用測試主機沒開，這裡的主機把它打開。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed partial class ConversationHistoryTests(PlatformFactory factory)
{
    private const string Question = "噪音管制標準";

    private static readonly JsonSerializerOptions Unescaped = new() { Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping };

    private WebApplicationFactory<Program> Enabled() =>
        factory.WithWebHostBuilder(b => b.UseSetting("Platform:ConversationHistory:Enabled", "true"));

    private static string HistoryPage(string tenant) => $"/portal/{tenant}/history";

    private static string SearchPage(string tenant) => $"/portal/{tenant}/search";

    private static Dictionary<string, string> Ask(string text, string? conversationId = null)
    {
        var form = new Dictionary<string, string> { ["Input.Query"] = text };
        if (conversationId is not null)
        {
            form["Input.ConversationId"] = conversationId;
        }

        return form;
    }

    // 表單名稱要明說（靜態 SSR 的 POST 靠 _handler 分辨是哪個表單）。
    private static Dictionary<string, string> Delete(string id) => new() { ["_handler"] = "portal-history-delete", ["Input.Delete"] = id };

    [GeneratedRegex("name=\"Input\\.ConversationId\" value=\"([^\"]+)\"")]
    private static partial Regex HiddenConversation();

    private async Task<PortalBrowser> SignedInAsync(WebApplicationFactory<Program> host, TenantResponse tenant, string role = CustomerRoles.Owner)
    {
        var email = NewEmail();
        var (_, initial) = await AddMemberAsync(factory, tenant.Id, email, role);
        var browser = new PortalBrowser(host);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);
        return browser;
    }

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
        var (tenant, email, initial) = await MemberAsync(factory, "ch-off");
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var results = await browser.SubmitAsync(SearchPage(tenant.Identifier), Ask(Question));

        Assert.Contains("查詢文字不會被記錄", results.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("對話紀錄", results.Html, StringComparison.Ordinal); // 選單裡也沒有
        Assert.Equal(0, await CountAsync(tenant.Id));
        var page = await browser.GetAsync(HistoryPage(tenant.Identifier));
        Assert.Contains("沒有開啟對話紀錄", page.Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task WhenEnabledAQuestionAndItsCitationsAreKeptAndAFollowUpJoinsTheSameConversation()
    {
        using var host = Enabled();
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-on");
        using var browser = await SignedInAsync(host, tenant);

        var first = await browser.SubmitAsync(SearchPage(tenant.Identifier), Ask(Question));
        Assert.Contains("你的提問會存在", first.Html, StringComparison.Ordinal); // 不再說「不會被記錄」
        Assert.DoesNotContain("查詢文字不會被記錄", first.Html, StringComparison.Ordinal);
        var id = HiddenConversation().Match(first.Html).Groups[1].Value;
        Assert.NotEmpty(id);

        var second = await browser.SubmitAsync(SearchPage(tenant.Identifier), Ask("固定污染源", id));
        Assert.Equal(id, HiddenConversation().Match(second.Html).Groups[1].Value); // 同一串
        Assert.Equal(1, await CountAsync(tenant.Id));

        var list = await browser.GetAsync(HistoryPage(tenant.Identifier));
        Assert.Contains(Question, list.Html, StringComparison.Ordinal); // 標題是第一個問題
        Assert.Contains("對話紀錄", list.Html, StringComparison.Ordinal); // 選單項

        var detail = await browser.GetAsync($"{HistoryPage(tenant.Identifier)}/{id}");
        Assert.Equal(HttpStatusCode.OK, detail.Status);
        Assert.Contains(Question, detail.Html, StringComparison.Ordinal);
        Assert.Contains("固定污染源", detail.Html, StringComparison.Ordinal);
        Assert.Contains("《空氣污染防制法》第 5-1 條", detail.Html, StringComparison.Ordinal); // 出處
    }

    [Fact]
    public async Task OnlyTheirOwnerCanSeeOrJoinAConversationNotEvenAnOwnerOfTheSameTenant()
    {
        using var host = Enabled();
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-iso");
        using var alice = await SignedInAsync(host, tenant, CustomerRoles.Developer);
        using var boss = await SignedInAsync(host, tenant, CustomerRoles.Owner);
        var (other, _) = await factory.CreateTenantWithKeyAsync("ch-iso2");
        using var stranger = await SignedInAsync(host, other);

        var asked = await alice.SubmitAsync(SearchPage(tenant.Identifier), Ask("機密案件關鍵字"));
        var id = HiddenConversation().Match(asked.Html).Groups[1].Value;

        // 同組織的 Owner、別的租戶的人在自己的租戶：看不到，而且和「不存在」是同一個畫面。
        foreach (var (who, tenantId) in new[] { (boss, tenant.Identifier), (stranger, other.Identifier) })
        {
            var list = await who.GetAsync(HistoryPage(tenantId));
            Assert.DoesNotContain("機密案件關鍵字", list.Html, StringComparison.Ordinal);
            var detail = await who.GetAsync($"{HistoryPage(tenantId)}/{id}");
            Assert.DoesNotContain("機密案件關鍵字", detail.Html, StringComparison.Ordinal);
            Assert.Contains("找不到這個對話", detail.Html, StringComparison.Ordinal);
        }

        // 別的租戶的人走到這個租戶的路徑：入口的授權就擋下，內容不會出現。
        var crossTenant = await stranger.GetAsync($"{HistoryPage(tenant.Identifier)}/{id}");
        Assert.DoesNotContain("機密案件關鍵字", crossTenant.Html, StringComparison.Ordinal);
        Assert.NotEqual(HttpStatusCode.OK, crossTenant.Status);

        // 把別人的對話 Id 塞進自己的表單：當作另開一串，原本那串沒有多出任何訊息。
        var hijack = await boss.SubmitAsync(SearchPage(tenant.Identifier), Ask("老闆的問題", id));
        Assert.NotEqual(id, HiddenConversation().Match(hijack.Html).Groups[1].Value);
        var aliceDetail = await alice.GetAsync($"{HistoryPage(tenant.Identifier)}/{id}");
        Assert.DoesNotContain("老闆的問題", aliceDetail.Html, StringComparison.Ordinal);
        Assert.Equal(2, await CountAsync(tenant.Id));
    }

    [Fact]
    public async Task DeletingRemovesTheConversationAuditsOnlyItsIdAndIsRefusedForSomeoneElses()
    {
        using var host = Enabled();
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("ch-del");
        using var alice = await SignedInAsync(host, tenant, CustomerRoles.Developer);
        using var boss = await SignedInAsync(host, tenant, CustomerRoles.Owner);

        var asked = await alice.SubmitAsync(SearchPage(tenant.Identifier), Ask("要被刪掉的問題"));
        var id = HiddenConversation().Match(asked.Html).Groups[1].Value;

        // 刪除表單只在有自己的對話時才出現，所以老闆先有一串自己的。
        await boss.SubmitAsync(SearchPage(tenant.Identifier), Ask("老闆自己的問題"));
        var refused = await boss.SubmitAsync(HistoryPage(tenant.Identifier), Delete(id));
        Assert.Contains("找不到這個對話", refused.Html, StringComparison.Ordinal);
        Assert.Equal(2, await CountAsync(tenant.Id));

        var deleted = await alice.SubmitAsync(HistoryPage(tenant.Identifier), Delete(id));
        Assert.Contains("已刪除", deleted.Html, StringComparison.Ordinal);
        Assert.Equal(1, await CountAsync(tenant.Id)); // 只少了她那一串
        var gone = await alice.GetAsync($"{HistoryPage(tenant.Identifier)}/{id}");
        Assert.Contains("找不到這個對話", gone.Html, StringComparison.Ordinal);

        var events = (await factory.Services.GetRequiredService<IAuditQuery>()
            .QueryAsync(new AuditFilter(Tenant: tenant.Id, Action: AuditActions.ConversationDeleted), CancellationToken.None)).Items;
        var evt = Assert.Single(events);
        Assert.Equal(id, evt.TargetId);
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
        using var oldMember = await SignedInAsync(host, old);
        using var recentMember = await SignedInAsync(host, recent);
        await oldMember.SubmitAsync(SearchPage(old.Identifier), Ask("很久以前的問題"));
        await recentMember.SubmitAsync(SearchPage(recent.Identifier), Ask("剛剛的問題"));
        await AgeAsync(old.Id, days: 91); // 預設保留 90 天

        var purged = await host.Services.GetRequiredService<ConversationRetention>().PurgeAsync(CancellationToken.None);

        Assert.True(purged >= 1);
        Assert.Equal(0, await CountAsync(old.Id));
        Assert.Equal(1, await CountAsync(recent.Id));
    }

    [Fact]
    public async Task RetentionDoesNothingWhileHistoryIsOff()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "ch-off2");
        using var host = Enabled();
        using var browser = new PortalBrowser(host);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);
        await browser.SubmitAsync(SearchPage(tenant.Identifier), Ask(Question));
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