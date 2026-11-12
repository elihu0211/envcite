using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 元件一律站在共用的基底與模板上，不各自寫死：掃原始碼守住（沒有任何東西會因為頁面自己寫了一份而報錯，
/// 啟用頁漏掉眼睛鈕就是這樣發生的）。基底在 <c>Components/Bases/</c>，版面模板在 <c>Components/Shared/</c>。
/// </summary>
public static partial class ComponentTemplateTests
{
    /// <summary>模板元件本身：它們就是那份標記的唯一出處。</summary>
    private static readonly HashSet<string> Templates = new(StringComparer.Ordinal)
    {
        "FormAlert", "PostForm", "FormField", "SubmitButton", "PageHeading", "DocumentTitle",
        "PageHeader", "StatusPage", "DialogError", "DialogSubmitActions",
        "SectionCard", "StatGrid", "StatTile", "DataTable", "SelectField", "OptionSelect", "LoadedView",
    };

    // 標記只在模板裡寫：頁面與對話框用 <FormAlert>、<PostForm>、<FormField>、<SubmitButton>、<PageHeading>、<DocumentTitle>、<DialogError>。
    [Theory]
    [InlineData("<PageTitle>", "<DocumentTitle>")]
    [InlineData("class=\"ep-alert", "<FormAlert>")]
    [InlineData("<EditForm", "<PostForm>（沒有欄位的隱藏表單例外：它只為了在連結失效後仍有處理常式，不套任何版面）")]
    [InlineData("class=\"ep-field-row\"", "<FormField>")]
    [InlineData("class=\"ep-label\"", "<FormField>")]
    [InlineData("<button type=\"submit\" class=\"ep-btn ep-btn-primary\"", "<SubmitButton>")]
    [InlineData("class=\"ep-page-title\"", "<PageHeading> 或 <PageHeader>")]
    [InlineData("<MudAlert Severity=\"Severity.Error\" Class=\"mt-3\" role=\"alert\"", "<DialogError>")]
    [InlineData("<section class=\"ep-card", "<SectionCard>")]
    [InlineData("<div class=\"ep-card", "<SectionCard>")]
    [InlineData("class=\"ep-stats\"", "<StatGrid>")]
    [InlineData("class=\"ep-stat\"", "<StatTile>（經 <StatGrid>）")]
    [InlineData("<table class=\"ep-plain-table\"", "<DataTable Plain=\"true\">")]
    [InlineData("<select ", "<OptionSelect>（表單欄位用 <SelectField>）")]
    [InlineData("<InputSelect", "<SelectField>")]
    public static void TemplateMarkupIsWrittenOnlyInTheTemplates(string markup, string useInstead)
    {
        var offenders = Razor()
            .Where(file => !Templates.Contains(Path.GetFileNameWithoutExtension(file)))
            .Where(file => ContainsOutsideAllowedForms(File.ReadAllText(file), markup))
            .Select(RelativePath)
            .ToList();

        Assert.True(offenders.Count == 0, $"這些檔案自己寫了 {markup}，要改用 {useInstead}：{string.Join("、", offenders)}");
    }

    // 基底管的東西不在元件裡再宣告一份：本地化與系統名稱、這個請求的 HttpContext、頁面的錯誤、單一表單的內容、路徑上的租戶、
    // 對話框的實體與送出中的旗標。
    [Theory]
    [InlineData(@"@inject IStringLocalizer<Ui>", "PlatformComponentBase")]
    [InlineData(@"@inject BrandText", "PlatformComponentBase")]
    [InlineData(@"\[CascadingParameter\][^\n]*\bHttpContext\b", "StaticPageBase")]
    [InlineData(@"\[SupplyParameterFromForm\][^\n]* Input\b", "FormPageBase<TForm>")]
    [InlineData(@"\bstring\? _error\b", "PageBase.Error")]
    [InlineData(@"\[Parameter\][^\n]*string Tenant\b", "PortalPageBase")]
    [InlineData(@"\[CascadingParameter\][^\n]*IMudDialogInstance", "DialogBase")]
    [InlineData(@"\bbool _busy\b", "FormDialogBase／AdminPageBase 的 Busy")]
    [InlineData(@"\bbool _loading\b", "PageBase.Loading 與 LoadingAsync")]
    [InlineData(@"\.ShowMessageBoxAsync\(", "AdminPageBase.ConfirmAsync")]
    public static void WhatABaseProvidesIsNotDeclaredAgain(string pattern, string provider)
    {
        var offenders = Razor().Concat(CodeBehind())
            .Where(file => !Templates.Contains(Path.GetFileNameWithoutExtension(file).Replace(".razor", string.Empty, StringComparison.Ordinal))
                && Path.GetFileNameWithoutExtension(file) != "DialogSubmitActions")
            .Where(file => Regex.IsMatch(File.ReadAllText(file), pattern, RegexOptions.None, TimeSpan.FromSeconds(2)))
            .Select(RelativePath)
            .ToList();

        Assert.True(offenders.Count == 0, $"這些檔案自己宣告了 {provider} 已提供的成員：{string.Join("、", offenders)}");
    }

    // 版面、頁面與對話框各自從自己的基底繼承；一般元件從 _Imports 的 @inherits 拿到 PlatformComponentBase。
    [Fact]
    public static void EveryComponentHasABase()
    {
        var imports = File.ReadAllText(Path.Combine(ComponentsPath(), "_Imports.razor"));
        Assert.Contains("@inherits PlatformComponentBase", imports, StringComparison.Ordinal);

        var layouts = Directory.EnumerateFiles(Path.Combine(ComponentsPath(), "Layout"), "*Layout.razor").ToList();
        Assert.NotEmpty(layouts);
        Assert.All(layouts, layout => Assert.Contains("@inherits PlatformLayoutBase", File.ReadAllText(layout), StringComparison.Ordinal));

        var dialogs = Directory.EnumerateFiles(Path.Combine(ComponentsPath(), "Dialogs"), "*.razor").ToList();
        Assert.NotEmpty(dialogs);
        Assert.All(dialogs, dialog => Assert.Matches(@"@inherits (FormDialogBase|SecretDialogBase)\b", File.ReadAllText(dialog)));

        // 後台的互動式頁面（Pages）與客戶入口的靜態頁面（Portal）各自繼承頁面的基底。
        var pages = Directory.EnumerateFiles(Path.Combine(ComponentsPath(), "Pages"), "*.razor").Where(file => !file.EndsWith("_Imports.razor", StringComparison.Ordinal));
        Assert.All(pages, page => Assert.Matches(@"@inherits (AdminPageBase|PageBase)\b", File.ReadAllText(page)));
        var portal = Directory.EnumerateFiles(Path.Combine(ComponentsPath(), "Portal"), "*.razor");
        Assert.All(portal, page => Assert.Matches(@"@inherits (PortalPageBase|PortalFormPageBase<\w+>)(?!\w)", File.ReadAllText(page)));
    }

    // 營運者與客戶入口共用的流程（改密碼、啟用）只寫一份，兩邊的頁面只剩外框。
    [Theory]
    [InlineData("Account/ChangePassword.razor", "<ChangePasswordFlow ")]
    [InlineData("Portal/ChangePassword.razor", "<ChangePasswordFlow ")]
    [InlineData("Account/Activate.razor", "<ActivationFlow ")]
    [InlineData("Portal/Activate.razor", "<ActivationFlow ")]
    public static void TheSharedFlowsAreUsedByBothSides(string page, string flow) =>
        Assert.Contains(flow, File.ReadAllText(Path.Combine(ComponentsPath(), page)), StringComparison.Ordinal);

    [Theory]
    [InlineData("Account/ChangePassword.razor.cs")]
    [InlineData("Portal/ChangePassword.razor.cs")]
    [InlineData("Account/Activate.razor.cs")]
    [InlineData("Portal/Activate.razor.cs")]
    public static void ThePagesOfASharedFlowHaveNoCodeOfTheirOwn(string codeBehind) =>
        Assert.False(File.Exists(Path.Combine(ComponentsPath(), codeBehind)), $"{codeBehind} 的邏輯在共用的流程元件裡，頁面不再留一份");

    // 兩邊的帳號服務共用 IAccountLifecycle（登出、啟用、改密碼）：流程元件只依賴它。
    [Fact]
    public static void BothAccountServicesShareTheLifecycleInterface()
    {
        var kernel = Path.GetFullPath(Path.Combine(ComponentsPath(), "..", "..", "EnvCite.Platform.SharedKernel"));

        Assert.Contains("IOperatorAccount : IAccountLifecycle", File.ReadAllText(Path.Combine(kernel, "OperatorAccounts.cs")), StringComparison.Ordinal);
        Assert.Contains("IPortalAccount : IAccountLifecycle", File.ReadAllText(Path.Combine(kernel, "PortalAccount.cs")), StringComparison.Ordinal);
    }

    /// <summary>不套 ep-form 的隱藏表單（連結失效後仍要有處理常式）不算違規。</summary>
    private static bool ContainsOutsideAllowedForms(string source, string markup)
    {
        if (markup != "<EditForm")
        {
            return source.Contains(markup, StringComparison.Ordinal);
        }

        return EditFormTag().Matches(source).Any(tag => !tag.Value.Contains("hidden=\"hidden\"", StringComparison.Ordinal));
    }

    private static IEnumerable<string> Razor() =>
        Directory.EnumerateFiles(ComponentsPath(), "*.razor", SearchOption.AllDirectories)
            .Where(file => !file.EndsWith("_Imports.razor", StringComparison.Ordinal));

    private static IEnumerable<string> CodeBehind() =>
        Directory.EnumerateFiles(ComponentsPath(), "*.razor.cs", SearchOption.AllDirectories);

    private static string RelativePath(string file) => Path.GetRelativePath(ComponentsPath(), file);

    private static string ComponentsPath([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.Web", "Components"));

    [GeneratedRegex(@"<EditForm\b[^>]*>", RegexOptions.None, matchTimeoutMilliseconds: 2000)]
    private static partial Regex EditFormTag();
}