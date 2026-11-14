using System.Net;

namespace EnvCite.Platform.Tests;

[Collection(PlatformTestGroup.Group4)]
public sealed class LawCorpusWorkbenchTests(PlatformFactory factory)
{
    private async Task<(string Tenant, PortalBrowser Browser)> SignedInOwnerAsync(string prefix)
    {
        var (tenant, email, initial) = await MemberAsync(factory, prefix, CustomerRoles.Owner);
        var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);
        return (tenant.Identifier, browser);
    }

    [Fact]
    public async Task OverviewUsesDomainFirstDefaultWideStufenbauAndPreservesCorrectTitle()
    {
        var (tenant, browser) = await SignedInOwnerAsync("wb-overview");
        using var _ = browser;

        var page = await browser.GetAsync($"{Home(tenant)}/laws");

        Assert.Equal(HttpStatusCode.OK, page.Status);
        Assert.Contains("法規體系與合規", page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("法規本體", page.Html, StringComparison.Ordinal);
        Assert.Contains("ep-workbench-shell", page.Html, StringComparison.Ordinal);
        Assert.Contains("value=\"O\" selected", page.Html, StringComparison.Ordinal);
        Assert.Contains("data-ontology-graph=", page.Html, StringComparison.Ordinal);
        Assert.Contains("data-graph-inspector=", page.Html, StringComparison.Ordinal);
        Assert.Contains("data-graph-action=\"layout-stufenbau\"", page.Html, StringComparison.Ordinal);
        Assert.Contains("data-graph-action=\"layout-concentric\"", page.Html, StringComparison.Ordinal);
        Assert.Contains("id=\"ep-tab-graph\"", page.Html, StringComparison.Ordinal);
        Assert.Contains("id=\"ep-tab-structure\"", page.Html, StringComparison.Ordinal);
        Assert.Contains("id=\"ep-tab-reasoning\"", page.Html, StringComparison.Ordinal);

        var script = await browser.GetAsync("/_content/EnvCite.Platform.Web/design/ontology-graph.js");
        Assert.Equal(HttpStatusCode.OK, script.Status);
        Assert.Contains("computeStufenbauPositions", script.Html, StringComparison.Ordinal);
        Assert.Contains("renderInspector", script.Html, StringComparison.Ordinal);
        Assert.Contains("buildVisibleGraphModel", script.Html, StringComparison.Ordinal);
        Assert.Contains("collectLineage", script.Html, StringComparison.Ordinal);
        Assert.Contains("labelAutoRotate: false", script.Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task FocusModePreservesContextAcrossTabsAndRendersRichGraphPayload()
    {
        var (tenant, browser) = await SignedInOwnerAsync("wb-focus");
        using var _ = browser;

        var focusPath = $"{Home(tenant)}/laws?jurisdiction=TW-CENTRAL&idScheme=moj_pcode&localId={FakeOntologyServer.KnownLawId}";
        var graphPage = await browser.GetAsync(focusPath);

        Assert.Equal(HttpStatusCode.OK, graphPage.Status);
        Assert.Contains("\"localId\":\"O0020001\"", graphPage.Html, StringComparison.Ordinal);
        Assert.Contains("\"focus\":true", graphPage.Html, StringComparison.Ordinal);
        Assert.Contains("\"hierarchyTier\":", graphPage.Html, StringComparison.Ordinal);
        Assert.Contains("tab=structure", graphPage.Html, StringComparison.Ordinal);
        Assert.Contains($"localId={FakeOntologyServer.KnownLawId}", graphPage.Html, StringComparison.Ordinal);

        var structurePage = await browser.GetAsync($"{focusPath}&tab=structure");

        Assert.Equal(HttpStatusCode.OK, structurePage.Status);
        Assert.Contains("id=\"law-detail-title\"", structurePage.Html, StringComparison.Ordinal);
        Assert.Contains($"TW-CENTRAL/moj_pcode/{FakeOntologyServer.KnownLawId}", structurePage.Html, StringComparison.Ordinal);
        Assert.Contains("tab=graph", structurePage.Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task SwitchingToAllDomainsSelectsAllAndReasoningPostStaysOnReasoningTab()
    {
        var (tenant, browser) = await SignedInOwnerAsync("wb-reason");
        using var _ = browser;

        var allPage = await browser.GetAsync($"{Home(tenant)}/laws?cat=ALL");
        Assert.Equal(HttpStatusCode.OK, allPage.Status);
        Assert.Contains("value=\"ALL\" selected", allPage.Html, StringComparison.Ordinal);

        var reasoningPath = $"{Home(tenant)}/laws?tab=reasoning";
        var evaluated = await browser.SubmitAsync(
            reasoningPath,
            new Dictionary<string, string>
            {
                ["Input.ConductOn"] = "2026-01-15",
                ["Input.DecidedOn"] = "2026-02-01",
                ["Input.GainedBenefit"] = "50000",
            },
            reasoningPath);

        Assert.Equal(HttpStatusCode.OK, evaluated.Status);
        Assert.Contains("id=\"reasoning-panel-title\"", evaluated.Html, StringComparison.Ordinal);
        Assert.Contains("id=\"ep-tab-reasoning\" href=", evaluated.Html, StringComparison.Ordinal);
        Assert.Contains("aria-current=\"page\"", evaluated.Html, StringComparison.Ordinal);
    }

    [Fact]
    public async Task EnglishWorkbenchTranslatesChromeAndGraphLabels()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "wb-en", name: "Alice");
        var home = Home(tenant.Identifier);
        using var browser = new PortalBrowser(factory);
        await browser.SubmitAsync(Login(tenant.Identifier), new() { ["culture"] = "en", ["returnUrl"] = Login(tenant.Identifier) }, $"{home}/language");
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var focus = $"{home}/laws?jurisdiction=TW-CENTRAL&idScheme=moj_pcode&localId={FakeOntologyServer.KnownLawId}";
        var page = await browser.GetAsync(focus);

        Assert.Equal(HttpStatusCode.OK, page.Status);
        Assert.Contains("<html lang=\"en\">", page.Html, StringComparison.Ordinal);
        Assert.Contains("Law Hierarchy & Compliance", page.Html, StringComparison.Ordinal);
        Assert.Contains("Stufenbau Tiers", page.Html, StringComparison.Ordinal);
        Assert.Contains("Concentric Focus", page.Html, StringComparison.Ordinal);
        Assert.Contains("Delegation Graph", page.Html, StringComparison.Ordinal);
        Assert.Contains("Structure & Articles", page.Html, StringComparison.Ordinal);
        Assert.Contains("Expand All Sub-Groups", page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("授權知識圖譜", page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("法規體系與合規", page.Html, StringComparison.Ordinal);
        Assert.DoesNotContain("位階分層", page.Html, StringComparison.Ordinal);
    }
}