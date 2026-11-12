using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;

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
        Assert.Contains("<PasswordField ", File.ReadAllText(Path.Combine(WebPath(), page)), StringComparison.Ordinal);

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

    private static bool IsPasswordField(string file) =>
        string.Equals(Path.GetFileName(file), "PasswordField.razor", StringComparison.Ordinal);

    private static IEnumerable<string> SourceFiles() =>
        Directory.EnumerateFiles(WebPath(), "*.*", SearchOption.AllDirectories)
            .Where(file => file.EndsWith(".razor", StringComparison.Ordinal) || file.EndsWith(".cs", StringComparison.Ordinal))
            .Where(file => !file.Contains($"{Path.DirectorySeparatorChar}obj{Path.DirectorySeparatorChar}", StringComparison.Ordinal)
                && !file.Contains($"{Path.DirectorySeparatorChar}bin{Path.DirectorySeparatorChar}", StringComparison.Ordinal));

    private static string WebPath([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.Web"));

    [GeneratedRegex("autocomplete=\"new-password\"", RegexOptions.None, matchTimeoutMilliseconds: 2000)]
    private static partial Regex NewPasswordHint();

    [GeneratedRegex(
        """type\s*=\s*["']password["']|InputType\s*\.\s*Password|InputType\s*=\s*"Password\b""",
        RegexOptions.IgnoreCase, matchTimeoutMilliseconds: 2000)]
    private static partial Regex DeclaresPasswordInput();
}