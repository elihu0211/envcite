using System.Runtime.CompilerServices;

namespace EnvCite.Platform.Tests;

/// <summary>載入中一律用骨架屏（畫出即將出現的版面），不用整頁的進度條：骨架屏說得出「要來的是什麼」，內容到了也不會整頁跳動。</summary>
public static class LoadingStateTests
{
    [Fact]
    public static void PagesShowSkeletonsWhileLoading()
    {
        var pages = Directory.EnumerateFiles(PagesPath(), "*.razor").ToList();

        Assert.NotEmpty(pages);
        Assert.All(pages, page => Assert.DoesNotContain("MudProgressLinear", File.ReadAllText(page), StringComparison.Ordinal));
        Assert.Contains(pages, page => File.ReadAllText(page).Contains("<Skeleton ", StringComparison.Ordinal));
    }

    private static string PagesPath([CallerFilePath] string here = "") =>
        Path.GetFullPath(Path.Combine(Path.GetDirectoryName(here)!, "..", "..", "src", "EnvCite.Platform.Web", "Components", "Pages"));
}