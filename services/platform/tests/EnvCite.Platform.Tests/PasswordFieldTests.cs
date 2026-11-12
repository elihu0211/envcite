using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;

using EnvCite.Platform.SharedKernel;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 密碼欄一律走 <c>Shared/PasswordField</c>（眼睛鈕、大寫鎖定提醒、<c>autocomplete</c> 都由它管）。
/// 新頁面若自己寫 <c>type="password"</c>，使用者在那一頁就沒有「顯示密碼」，而且沒有任何東西會報錯——
/// 啟用頁就是這樣漏掉的。這裡掃原始碼守住：除了元件本身，沒有任何檔案可以宣告密碼輸入框。
/// </summary>
public static partial class PasswordFieldTests
{
    // 眼睛鈕的點擊切換在 design/forms.js（所有 PasswordField 共用，靜態渲染的頁面沒有電路）；
    // 這個專案沒有 JavaScript 測試環境，所以這裡守住兩端：頁面一定輸出元件的按鈕，元件本身的契約不變。
    /// <summary>啟用頁（營運者與客戶入口相同）的「新密碼」「再輸入一次」：各有眼睛鈕，且都標成新密碼。</summary>
    internal static void AssertActivationFields(string html)
    {
        AssertEyeToggle(html, "new");
        AssertEyeToggle(html, "confirm");
        Assert.Equal(2, NewPasswordHint().Count(html));
        Assert.Contains("minlength=\"12\"", html, StringComparison.Ordinal);
        AssertPasswordGuide(html, "new");
    }

    /// <summary>「新密碼」欄位的規則清單（<c>NewPasswordField</c>）：三條短句與一句提示，數字是規則的唯一來源。</summary>
    internal static void AssertPasswordGuide(string html, string id)
    {
        Assert.Contains($"id=\"{id}-guide\" class=\"ep-password-guide\" data-min=\"{PasswordPolicy.MinimumLength}\"", html, StringComparison.Ordinal);
        Assert.Contains($"maxlength=\"{PasswordPolicy.MaximumLength}\"", html, StringComparison.Ordinal);
        Assert.Contains($"aria-describedby=\"{id}-guide\"", html, StringComparison.Ordinal);
        Assert.Contains("data-text-short=\"還差 {0} 個\" data-text-ok=\"長度夠了\"", html, StringComparison.Ordinal);
        Assert.Contains("<li data-rule=\"length\">", html, StringComparison.Ordinal);
        Assert.Contains($"至少 {PasswordPolicy.MinimumLength} 個字元", html, StringComparison.Ordinal);
        Assert.Contains("不含你的 email", html, StringComparison.Ordinal);
        Assert.Contains("避開常見密碼與連續、重複的字元", html, StringComparison.Ordinal);
        var rules = Regex.Match(html, "<ul class=\"ep-hint ep-password-rules\">(.*?)</ul>", RegexOptions.Singleline, TimeSpan.FromSeconds(2));
        Assert.True(rules.Success);
        Assert.Equal(3, Regex.Count(rules.Groups[1].Value, "<li[ >]", RegexOptions.None, TimeSpan.FromSeconds(2)));
        Assert.Contains("role=\"status\"", html, StringComparison.Ordinal); // 只在長度剛夠時通知，不逐字念
    }

    internal static void AssertEyeToggle(string html, string id)
    {
        Assert.Contains(
            $"class=\"ep-password-toggle\" aria-controls=\"{id}\" aria-label=\"顯示密碼\" data-label-show=\"顯示密碼\" data-label-hide=\"隱藏密碼\"",
            html, StringComparison.Ordinal);
        Assert.Matches($"<input[^>]*\\bid=\"{id}\"[^>]*\\btype=\"password\"|<input[^>]*\\btype=\"password\"[^>]*\\bid=\"{id}\"", html);
    }

    [Fact]
    public static void OnlyPasswordFieldDeclaresAPasswordInput()
    {
        var files = SourceFiles().ToList();

        Assert.True(files.Count > 50, "掃不到原始碼，這個測試就沒有意義");
        var offenders = files
            .Where(file => !IsPasswordField(file))
            .Where(file => DeclaresPasswordInput().IsMatch(File.ReadAllText(file)))
            .Select(file => Path.GetRelativePath(WebPath(), file))
            .ToList();

        Assert.True(offenders.Count == 0,
            $"這些檔案自己宣告了密碼輸入框，要改用 <PasswordField>（眼睛鈕與大寫鎖定提醒）：{string.Join("、", offenders)}");
    }

    [Theory]
    [InlineData("<InputText id=\"x\" type=\"password\" class=\"ep-input\" />")]
    [InlineData("<input id='x' type='password'>")]
    [InlineData("<input TYPE=\"Password\">")]
    [InlineData("<MudTextField InputType=\"InputType.Password\" />")]
    [InlineData("<MudTextField InputType=\"@InputType.Password\" />")]
    [InlineData("<MudTextField InputType=\"Password\" />")]
    [InlineData("var t = InputType.Password;")]
    public static void TheScanRecognisesEveryWayToDeclareAPasswordInput(string snippet) =>
        Assert.Matches(DeclaresPasswordInput(), snippet);

    [Theory]
    [InlineData("<PasswordField Id=\"new\" @bind-Value=\"Input.New\" Autocomplete=\"new-password\" />")]
    [InlineData("<InputText id=\"email\" type=\"email\" autocomplete=\"username\" />")]
    [InlineData("<MudTextField Label=\"@L[\"一次性密碼\"]\" ReadOnly=\"true\" />")]
    public static void TheScanLeavesOrdinaryFieldsAlone(string snippet) =>
        Assert.DoesNotMatch(DeclaresPasswordInput(), snippet);

    // 兩個啟用頁是被漏掉過的；指名守住，不靠「掃到就好」。
    [Theory]
    [InlineData("Components/Account/Activate.razor")]
    [InlineData("Components/Portal/Activate.razor")]
    [InlineData("Components/Account/ChangePassword.razor")]
    [InlineData("Components/Portal/ChangePassword.razor")]
    [InlineData("Components/Account/Login.razor")]
    [InlineData("Components/Portal/Login.razor")]
    [InlineData("Components/Portal/Security.razor")]
    public static void PagesThatTakeAPasswordUsePasswordField(string page) =>
        Assert.Contains("<PasswordField ", PageWithItsFlows(page), StringComparison.Ordinal);

    // 元件本身的契約：按鈕有名稱、指向它控制的輸入框、兩種狀態的名稱交給 forms.js 換；
    // 不寫死 autocomplete="off"（密碼管理員靠 current-password／new-password 辨識欄位）。
    [Fact]
    public static void PasswordFieldKeepsItsAccessibleToggleAndPasswordManagerHints()
    {
        var source = File.ReadAllText(Path.Combine(WebPath(), "Components", "Shared", "PasswordField.razor"));

        Assert.Contains("type=\"password\"", source, StringComparison.Ordinal);
        Assert.Contains("autocomplete=\"@Autocomplete\"", source, StringComparison.Ordinal);
        Assert.DoesNotContain("autocomplete=\"off\"", source, StringComparison.Ordinal);
        Assert.Contains("<button type=\"button\" class=\"ep-password-toggle\" aria-controls=\"@Id\" aria-label=\"@L[\"顯示密碼\"]\"", source, StringComparison.Ordinal);
        Assert.Contains("data-label-show=\"@L[\"顯示密碼\"]\" data-label-hide=\"@L[\"隱藏密碼\"]\"", source, StringComparison.Ordinal);
    }

    // 設定或修改密碼的頁面一律走 NewPasswordField 模板：規則的數字與提示文字只有一處（PasswordPolicy 與模板），
    // 頁面不自己寫 minlength 或「至少 N 個字元」，改規則時就不會有一頁漏改。
    [Theory]
    [InlineData("Components/Account/Activate.razor")]
    [InlineData("Components/Portal/Activate.razor")]
    [InlineData("Components/Account/ChangePassword.razor")]
    [InlineData("Components/Portal/ChangePassword.razor")]
    public static void PagesThatSetANewPasswordUseTheNewPasswordFieldTemplate(string page) =>
        Assert.Contains("<NewPasswordField ", PageWithItsFlows(page), StringComparison.Ordinal);

    [Fact]
    public static void NoPageHardCodesThePasswordRule()
    {
        var offenders = SourceFiles()
            .Where(file => !string.Equals(Path.GetFileName(file), "NewPasswordField.razor", StringComparison.Ordinal))
            .Where(file => HardCodedRule().IsMatch(File.ReadAllText(file)))
            .Select(file => Path.GetRelativePath(WebPath(), file))
            .ToList();

        Assert.True(offenders.Count == 0, $"這些檔案自己寫了密碼規則的數字或提示，要改用 <NewPasswordField> 與 PasswordPolicy：{string.Join("、", offenders)}");
    }

    [Theory]
    [InlineData("<PasswordField Id=\"new\" MinLength=\"12\" />")]
    [InlineData("<PasswordField MaxLength=\"128\" />")]
    [InlineData("L[\"至少 12 個字元\"]")]
    public static void TheRuleScanRecognisesAHardCodedRule(string snippet) =>
        Assert.Matches(HardCodedRule(), snippet);

    [Fact]
    public static void TheTemplateTakesItsNumbersFromThePolicy()
    {
        var source = File.ReadAllText(Path.Combine(WebPath(), "Components", "Shared", "NewPasswordField.razor"));

        Assert.Contains("MinLength=\"@PasswordPolicy.MinimumLength\"", source, StringComparison.Ordinal);
        Assert.Contains("MaxLength=\"@PasswordPolicy.MaximumLength\"", source, StringComparison.Ordinal);
        Assert.Contains("data-min=\"@PasswordPolicy.MinimumLength\"", source, StringComparison.Ordinal);
        Assert.DoesNotMatch(@"\b(?:12|128)\b", source.Replace("PasswordPolicy.", string.Empty, StringComparison.Ordinal));
    }

    // 進度與「還差幾個字元」是漸進增強：長度的最短值與兩段文字都由伺服器寫在 data-*，forms.js 不寫死任何一個。
    [Fact]
    public static void TheLengthFeedbackScriptHardCodesNeitherTheRuleNorTheWording()
    {
        var script = File.ReadAllText(Path.Combine(WebPath(), "wwwroot", "design", "forms.js"));
        var section = script[script.IndexOf("// ---- 新密碼的長度回饋", StringComparison.Ordinal)..script.IndexOf("// ---- 送出中的回饋", StringComparison.Ordinal)];

        Assert.Contains("dataset.min", section, StringComparison.Ordinal);
        Assert.Contains("dataset.textShort", section, StringComparison.Ordinal);
        Assert.Contains("dataset.textOk", section, StringComparison.Ordinal);
        var code = string.Join('\n', section.Split('\n').Where(line => !line.TrimStart().StartsWith("//", StringComparison.Ordinal)));
        Assert.DoesNotMatch(@"\b(?:12|128)\b|還差|長度夠了", code);
    }

    /// <summary>
    /// 頁面的原始碼，加上它用到的「流程」元件（<c>&lt;XxxFlow …&gt;</c>，在 <c>Shared/</c>）的原始碼：
    /// 營運者與客戶入口共用的流程（改密碼、啟用）把欄位收在流程裡，頁面只剩外框。
    /// </summary>
    private static string PageWithItsFlows(string page)
    {
        var source = File.ReadAllText(Path.Combine(WebPath(), page));
        var flows = FlowComponent().Matches(source).Select(m => m.Groups[1].Value).Distinct();
        return source + string.Concat(flows.Select(flow => File.ReadAllText(Path.Combine(WebPath(), "Components", "Shared", flow + ".razor"))));
    }

    private static bool IsPasswordField(string file) =>
        string.Equals(Path.GetFileName(file), "PasswordField.razor", StringComparison.Ordinal);

    private static IEnumerable<string> SourceFiles() =>
        Directory.EnumerateFiles(WebPath(), "*.*", SearchOption.AllDirectories)
            .Where(file => file.EndsWith(".razor", StringComparison.Ordinal) || file.EndsWith(".cs", StringComparison.Ordinal))
            .Where(file => !file.Contains($"{Path.DirectorySeparatorChar}obj{Path.DirectorySeparatorChar}", StringComparison.Ordinal)
                && !file.Contains($"{Path.DirectorySeparatorChar}bin{Path.DirectorySeparatorChar}", StringComparison.Ordinal));

    private static string WebPath([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.Web"));

    [GeneratedRegex(@"<(\w+Flow)\b", RegexOptions.None, matchTimeoutMilliseconds: 2000)]
    private static partial Regex FlowComponent();

    [GeneratedRegex("autocomplete=\"new-password\"", RegexOptions.None, matchTimeoutMilliseconds: 2000)]
    private static partial Regex NewPasswordHint();

    [GeneratedRegex("""<PasswordField\b[^>]*\b(?:Min|Max)Length\s*=\s*"\d+"|至少\s*\d+\s*個字元""", RegexOptions.None, matchTimeoutMilliseconds: 2000)]
    private static partial Regex HardCodedRule();

    [GeneratedRegex(
        """type\s*=\s*["']password["']|InputType\s*\.\s*Password|InputType\s*=\s*"Password\b""",
        RegexOptions.IgnoreCase, matchTimeoutMilliseconds: 2000)]
    private static partial Regex DeclaresPasswordInput();
}