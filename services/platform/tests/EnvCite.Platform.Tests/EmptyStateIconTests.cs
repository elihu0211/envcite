using System.Runtime.CompilerServices;
using System.Text.RegularExpressions;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 空狀態一律有圖示磚：沒有資料的畫面是使用者最常看到「這頁是空的」的地方，圖示讓它一眼看得出是狀態、不是壞掉。
/// 圖示只是輔助（aria-hidden），標題與說明才是意思，所以新增空狀態時漏了圖示也不影響功能，這裡讓它在 CI 就被看見。
/// </summary>
public static partial class EmptyStateIconTests
{
    [Fact]
    public static void EveryEmptyStateCarriesAnIcon()
    {
        var states = Directory.EnumerateFiles(ComponentsPath(), "*.razor", SearchOption.AllDirectories)
            .SelectMany(file => EmptyState().Matches(File.ReadAllText(file)).Select(m => (File: Path.GetFileName(file), Tag: m.Value)))
            .ToList();

        Assert.NotEmpty(states);
        Assert.All(states, state => Assert.True(
            state.Tag.Contains("Icon=", StringComparison.Ordinal),
            $"{state.File}：<EmptyState> 要有 Icon"));
    }

    private static string ComponentsPath([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.Web", "Components"));

    [GeneratedRegex(@"<EmptyState\b[^>]*>", RegexOptions.Singleline, matchTimeoutMilliseconds: 2000)]
    private static partial Regex EmptyState();
}