using System.Net;
using System.Net.Http.Json;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Xml.Linq;

using EnvCite.Platform.Modules.Members;
using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Presentation;

using Microsoft.Extensions.DependencyInjection;

using static EnvCite.Platform.Tests.PortalTestSupport;

namespace EnvCite.Platform.Tests;

/// <summary>代碼本身：每個代碼有英文、中英文資源檔，且三者一致；英文裡沒有中文。</summary>
public static partial class OutcomeCodeCatalogTests
{
    private static IReadOnlyList<string> Codes() =>
        [.. typeof(OutcomeCodes).GetFields(BindingFlags.Public | BindingFlags.Static)
            .Where(f => f.IsLiteral && f.FieldType == typeof(string)).Select(f => (string)f.GetRawConstantValue()!)];

    private static Dictionary<string, string> Labels(string file, [CallerFilePath] string here = "") =>
        XDocument.Load(Path.GetFullPath(Path.Combine(
                Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.Web", "Resources", file)))
            .Root!.Elements("data")
            .Where(e => ((string)e.Attribute("name")!).StartsWith("Outcome.", StringComparison.Ordinal))
            .ToDictionary(e => ((string)e.Attribute("name")!)["Outcome.".Length..], e => (string)e.Element("value")!, StringComparer.Ordinal);

    [Fact]
    public static void EveryCodeHasAnEnglishTextAndBothResourceEntries()
    {
        var codes = Codes();

        Assert.NotEmpty(codes);
        Assert.Empty(codes.Except(OutcomeCodes.English.Keys));
        Assert.Empty(OutcomeCodes.English.Keys.Except(codes));
        Assert.Empty(codes.Except(Labels("Labels.resx").Keys));
        Assert.Empty(Labels("Labels.resx").Keys.Except(codes));
        Assert.Empty(codes.Except(Labels("Labels.en.resx").Keys));
        Assert.Empty(Labels("Labels.en.resx").Keys.Except(codes));
    }

    // API 的英文（OutcomeCodes）與介面的英文（Labels.en.resx）是同一句話，不各寫各的。
    [Fact]
    public static void TheApiEnglishAndTheEnglishResourceAreTheSame() =>
        Assert.All(Labels("Labels.en.resx"), pair => Assert.Equal(OutcomeCodes.English[pair.Key], pair.Value));

    [Fact]
    public static void TheApiTextNeverContainsChinese() =>
        Assert.All(OutcomeCodes.English, pair => Assert.False(Cjk().IsMatch(pair.Value), pair.Key));

    [Fact]
    public static void CodesAreStableLowerSnakeDotted() =>
        Assert.All(Codes(), code => Assert.Matches(@"^[a-z_]+\.[a-z_]+$", code));

    [Fact]
    public static void TheArgumentsAreFilledIntoTheText()
    {
        Assert.Equal(
            "Unknown plan 'gold'. Defined plans: basic, standard.",
            OutcomeCodes.Describe(OutcomeCodes.PlanUnknown, ["gold", "basic, standard"]));
        Assert.Equal("some.future_code", OutcomeCodes.Describe("some.future_code"));
    }

    [Theory]
    [InlineData("zh-TW", "這個識別碼已被使用。")]
    [InlineData("en", "This identifier is already in use.")]
    public static void TheUiSpeaksTheUsersLanguageFromTheCode(string culture, string expected)
    {
        using var _ = TestCulture.Use(culture);
        var outcome = AdminOutcome.Conflict<CreatedTenant>(OutcomeCodes.TenantIdentifierTaken);

        Assert.Equal(expected, outcome.Localized());
    }

    [Theory]
    [InlineData("zh-TW", "新密碼至少要 12 個字元。")]
    [InlineData("en", "The new password must be at least 12 characters.")]
    public static void ParametersAreFilledInEachLanguage(string culture, string expected)
    {
        using var _ = TestCulture.Use(culture);
        var outcome = AdminOutcome.Invalid<bool>(OutcomeCodes.PasswordTooShort, 12);

        Assert.Equal(expected, outcome.Localized());
    }

    // 沒有代碼的結果（例如來自上游的說明）：退回英文的 Message，不是空白。
    [Fact]
    public static void WithoutACodeTheEnglishMessageIsShown() =>
        Assert.Equal("boom", new AdminOutcome<bool>(AdminStatus.Invalid, false, "boom").Localized());

    [GeneratedRegex(@"[一-鿿]", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    private static partial Regex Cjk();
}

/// <summary>服務層的失敗回傳預期的代碼，英文訊息，沒有中文；API 的錯誤回應帶 <c>code</c>。</summary>
[Collection(PlatformTestGroup.Name)]
public sealed class OutcomeCodeBehaviourTests(PlatformFactory factory)
{
    private T Service<T>() where T : notnull => factory.Services.GetRequiredService<T>();

    private static void AssertFailure<T>(AdminOutcome<T> outcome, AdminStatus status, string code)
    {
        Assert.Equal(status, outcome.Status);
        Assert.Equal(code, outcome.Code);
        Assert.Equal(OutcomeCodes.Describe(code, outcome.Args), outcome.Message);
        Assert.DoesNotMatch(@"[一-鿿]", outcome.Message!);
    }

    [Fact]
    public async Task TheTenantProvisionerSaysWhyWithCodes()
    {
        var actor = OperatorSession.Principal();
        var provisioner = Service<ITenantProvisioner>();

        AssertFailure(await provisioner.CreateAsync(actor, "Bad Id!", "Name", CancellationToken.None), AdminStatus.Invalid, OutcomeCodes.TenantIdentifierInvalid);
        AssertFailure(await provisioner.CreateAsync(actor, "okid-" + Guid.NewGuid().ToString("N")[..8], " ", CancellationToken.None), AdminStatus.Invalid, OutcomeCodes.TenantNameRequired);
        var identifier = "dup-" + Guid.NewGuid().ToString("N")[..8];
        Assert.True((await provisioner.CreateAsync(actor, identifier, "First", CancellationToken.None)).IsOk);
        AssertFailure(await provisioner.CreateAsync(actor, identifier, "Second", CancellationToken.None), AdminStatus.Conflict, OutcomeCodes.TenantIdentifierTaken);
    }

    [Fact]
    public async Task APlanThatDoesNotExistNamesItAndTheDefinedOnes()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("oc-plan");
        var plans = Service<IPlanAdmin>();

        var unknown = await plans.SetAsync(OperatorSession.Principal(), tenant.Id, "no-such-plan", CancellationToken.None);

        AssertFailure(unknown, AdminStatus.Invalid, OutcomeCodes.PlanUnknown);
        Assert.Equal("no-such-plan", unknown.Args![0]);
        AssertFailure(await plans.SetAsync(OperatorSession.Principal(), "no-such-tenant-id", "standard", CancellationToken.None), AdminStatus.NotFound, OutcomeCodes.TenantNotFound);
    }

    [Fact]
    public async Task MemberAndOperatorCreationReportBadInputAndDuplicates()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("oc-mem");
        var members = Service<IMemberAdmin>();
        var operators = Service<IOperatorAdmin>();
        var actor = OperatorSession.Principal();

        AssertFailure(await members.CreateAsync(actor, tenant.Id, "not-an-email", null, CustomerRoles.Viewer, CancellationToken.None), AdminStatus.Invalid, OutcomeCodes.EmailInvalid);
        AssertFailure(await members.CreateAsync(actor, tenant.Id, NewEmail(), null, "NotARole", CancellationToken.None), AdminStatus.Invalid, OutcomeCodes.RoleInvalid);
        var email = NewEmail();
        Assert.True((await members.CreateAsync(actor, tenant.Id, email, null, CustomerRoles.Viewer, CancellationToken.None)).IsOk);
        AssertFailure(await members.CreateAsync(actor, tenant.Id, email, null, CustomerRoles.Viewer, CancellationToken.None), AdminStatus.Conflict, OutcomeCodes.MemberEmailTaken);
        AssertFailure(await members.ResetPasswordAsync(actor, tenant.Id, Guid.NewGuid().ToString(), CancellationToken.None), AdminStatus.NotFound, OutcomeCodes.MemberNotFound);
        AssertFailure(await operators.CreateAsync(actor, "bad", null, CancellationToken.None), AdminStatus.Invalid, OutcomeCodes.EmailInvalid);
        _ = await factory.EnsureOperatorAsync();
        AssertFailure(await operators.CreateAsync(actor, PlatformFactory.OperatorEmail, null, CancellationToken.None), AdminStatus.Conflict, OutcomeCodes.OperatorEmailTaken);
    }

    // API：英文的 detail 加穩定的 code，呼叫端不必讀文字。
    [Fact]
    public async Task TheAdminApiAnswersWithACodeAndAnEnglishDetail()
    {
        using var admin = factory.CreateAdminClient();

        using var response = await admin.PostAsJsonAsync(
            new Uri("/admin/tenants", UriKind.Relative), new { identifier = "Bad Id!", name = "Name" }, TestContext.Current.CancellationToken);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Equal(OutcomeCodes.TenantIdentifierInvalid, body.GetProperty("code").GetString());
        Assert.Equal(OutcomeCodes.English[OutcomeCodes.TenantIdentifierInvalid], body.GetProperty("errors").GetProperty("identifier")[0].GetString());
    }

    [Fact]
    public async Task AnUnknownPlanOverTheApiIsCodedToo()
    {
        var (tenant, _) = await factory.CreateTenantWithKeyAsync("oc-api");
        using var admin = factory.CreateAdminClient();

        using var response = await admin.PutAsJsonAsync(
            new Uri($"/admin/tenants/{tenant.Id}/plan", UriKind.Relative), new { plan = "no-such-plan" }, TestContext.Current.CancellationToken);
        var body = await response.Content.ReadFromJsonAsync<JsonElement>(TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Equal(OutcomeCodes.PlanUnknown, body.GetProperty("code").GetString());
        Assert.Contains("no-such-plan", body.GetProperty("errors").GetProperty("request")[0].GetString(), StringComparison.Ordinal);
    }
}