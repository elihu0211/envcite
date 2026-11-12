using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 對話框的必填欄位：開啟時不能一開始就是紅字。MudBlazor 在欄位失焦時把它標成「碰過」並驗證必填；
/// 對話框一開就把焦點從自動對焦的欄位移走，空欄位就在使用者還沒打字前顯示「必填」。
/// <c>OnlyValidateIfDirty</c> 讓驗證等到使用者真的改過值才開始。
/// </summary>
public static partial class DialogFieldTests
{
    [Fact]
    public static void RequiredFieldsInDialogsOnlyValidateAfterTheUserChangedThem()
    {
        var required = Directory.EnumerateFiles(DialogsPath(), "*.razor")
            .SelectMany(file => TextField().Matches(File.ReadAllText(file)).Select(m => (File: Path.GetFileName(file), Tag: m.Value)))
            .Where(field => field.Tag.Contains("Required=\"true\"", StringComparison.Ordinal))
            .ToList();

        Assert.NotEmpty(required); // 掃得到欄位，這個測試才有意義
        Assert.All(required, field => Assert.True(
            field.Tag.Contains("OnlyValidateIfDirty=\"true\"", StringComparison.Ordinal),
            $"{field.File}：必填欄位要加 OnlyValidateIfDirty，否則對話框一開就顯示「必填」"));
    }

    // MudDialog 預設把焦點放在焦點陷阱自己的容器（DefaultFocus.Element），晚於欄位自己的 AutoFocus，
    // 把焦點搶走：打開表單對話框得再點一下欄位才能打字。有輸入欄位的對話框要把焦點給第一個欄位。
    [Fact]
    public static void DialogsWithInputsFocusTheirFirstFieldWhenTheyOpen()
    {
        var forms = Directory.EnumerateFiles(DialogsPath(), "*.razor")
            .Select(file => (File: Path.GetFileName(file), Source: File.ReadAllText(file)))
            .Where(dialog => AutoFocusedField().IsMatch(dialog.Source))
            .ToList();

        Assert.NotEmpty(forms);
        Assert.All(forms, dialog => Assert.Contains(
            "<MudDialog DefaultFocus=\"DefaultFocus.FirstChild\">", dialog.Source, StringComparison.Ordinal));
    }

    // MudBlazor 預設不處理 Esc：沒在 provider 開啟，所有對話框都只能按按鈕或點背景關閉。
    // 只顯示一次的密碼與金鑰關掉 Esc，而且一併關掉點背景與右上角的關閉鈕，三者一致才擋得住誤關。
    [Fact]
    public static void EscapeClosesDialogsExceptTheOnesThatShowASecretOnce()
    {
        var providers = File.ReadAllText(Path.Combine(DialogsPath(), "..", "Shared", "PlatformProviders.razor"));
        var secret = Directory.EnumerateFiles(DialogsPath(), "*.razor")
            .Select(file => (File: Path.GetFileName(file), Source: File.ReadAllText(file)))
            .Where(dialog => dialog.Source.Contains("CloseOnEscapeKey = false", StringComparison.Ordinal))
            .ToList();

        Assert.Contains("<MudDialogProvider CloseOnEscapeKey=\"true\" />", providers, StringComparison.Ordinal);
        Assert.NotEmpty(secret);
        Assert.All(secret, dialog =>
        {
            Assert.Contains("BackdropClick = false", dialog.Source, StringComparison.Ordinal);
            Assert.Contains("CloseButton = false", dialog.Source, StringComparison.Ordinal);
        });
    }

    private static string DialogsPath([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(
            Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.Web", "Components", "Dialogs"));

    [GeneratedRegex(@"<MudTextField\b.*?/>", RegexOptions.Singleline, matchTimeoutMilliseconds: 2000)]
    private static partial Regex TextField();

    [GeneratedRegex(@"<MudTextField\b[^>]*AutoFocus=""true""", RegexOptions.Singleline, matchTimeoutMilliseconds: 2000)]
    private static partial Regex AutoFocusedField();
}