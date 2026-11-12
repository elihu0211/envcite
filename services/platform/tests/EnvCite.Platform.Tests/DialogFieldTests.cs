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

    private static string DialogsPath([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(
            Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.Web", "Components", "Dialogs"));

    [GeneratedRegex(@"<MudTextField\b.*?/>", RegexOptions.Singleline, matchTimeoutMilliseconds: 2000)]
    private static partial Regex TextField();
}