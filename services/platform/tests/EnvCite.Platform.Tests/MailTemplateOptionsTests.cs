using EnvCite.Platform.SharedKernel;

using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

using static EnvCite.Platform.Tests.MailTestServices;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 版型選項（<c>Platform:Mail:Template</c>）：值會進內嵌樣式與樣式表，所以格式與對比度在啟動就驗證，
/// 錯了停下並指名鍵（與其他設定群組同一個規則）。預設值本身要通過，且與寄信有沒有啟用無關。
/// </summary>
public sealed class MailTemplateOptionsTests
{
    private static OptionsValidationException Rejected(string key, string value)
    {
        using var provider = Build(With(($"Platform:Mail:Template:{key}", value)));
        return Assert.Throws<OptionsValidationException>(() => provider.GetRequiredService<IOptions<MailTemplateOptions>>().Value);
    }

    [Fact]
    public void TheDefaultsAreValidWhetherOrNotMailIsEnabled()
    {
        foreach (var enabled in new[] { "false", "true" })
        {
            using var provider = Build(With(("Platform:Mail:Enabled", enabled)));

            var options = provider.GetRequiredService<IOptions<MailTemplateOptions>>().Value;

            Assert.Equal(("zh-TW", 600, 28, 32), (options.Culture, options.Width, options.PaddingVertical, options.PaddingHorizontal));
            Assert.Equal(("#185fa5", "#85b7eb"), (options.Light.Button, options.Dark.Button));
        }
    }

    [Theory]
    [InlineData("Culture", "fr", "Platform:Mail:Template:Culture")]
    [InlineData("Culture", "", "Platform:Mail:Template:Culture")]
    [InlineData("Width", "300", "Platform:Mail:Template:Width")]
    [InlineData("Width", "900", "Platform:Mail:Template:Width")]
    [InlineData("PaddingVertical", "-1", "Platform:Mail:Template:PaddingVertical")]
    [InlineData("PaddingHorizontal", "200", "Platform:Mail:Template:PaddingHorizontal")]
    [InlineData("CompactBreakpoint", "1000", "Platform:Mail:Template:CompactBreakpoint")]
    [InlineData("CompactPaddingHorizontal", "99", "Platform:Mail:Template:CompactPaddingHorizontal")]
    [InlineData("FontFamily", "Arial;}</style><script>", "Platform:Mail:Template:FontFamily")]
    [InlineData("FontFamily", "", "Platform:Mail:Template:FontFamily")]
    [InlineData("MonospaceFontFamily", "mono\"\"", "Platform:Mail:Template:MonospaceFontFamily")]
    [InlineData("Light:Button", "red", "Platform:Mail:Template:Light:Button")]
    [InlineData("Light:Page", "#fff", "Platform:Mail:Template:Light:Page")]
    [InlineData("Dark:Text", "#12345g", "Platform:Mail:Template:Dark:Text")]
    [InlineData("Dark:Card", "url(http://x)", "Platform:Mail:Template:Dark:Card")]
    public void ABadValueStopsTheStartupAndNamesTheKey(string key, string value, string named)
    {
        var failure = Rejected(key, value);

        Assert.Contains(failure.Failures, f => f.Contains(named, StringComparison.Ordinal));
    }

    // 調色票不能把信調成看不清：文字對比度低於 4.5:1 的覆寫被擋下，並指名是哪個色票的哪個顏色。
    [Theory]
    [InlineData("Light:Muted", "#c0c0c0", "Light:Muted")] // 次要文字太淡
    [InlineData("Light:Button", "#9ec5f2", "Light:ButtonText")] // 白字在淡藍按鈕上
    [InlineData("Dark:Text", "#3a3a3a", "Dark:Text")] // 深色底上的深色字
    public void ALowContrastOverrideIsRejectedWithTheColorNamed(string key, string value, string named)
    {
        var failure = Rejected(key, value);

        var messages = failure.Failures.Where(f => f.Contains($"Platform:Mail:Template:{named}", StringComparison.Ordinal)).ToList();
        Assert.NotEmpty(messages);
        Assert.All(messages, m => Assert.Contains("contrast", m, StringComparison.Ordinal));
    }

    [Fact]
    public void AWidthTooNarrowForTheButtonIsRejected()
    {
        using var provider = Build(With(("Platform:Mail:Template:Width", "320"), ("Platform:Mail:Template:PaddingHorizontal", "64")));

        var failure = Assert.Throws<OptionsValidationException>(() => provider.GetRequiredService<IOptions<MailTemplateOptions>>().Value);

        Assert.Contains(failure.Failures, f => f.Contains("PaddingHorizontal", StringComparison.Ordinal));
    }

    [Fact]
    public void ValidOverridesAreAccepted()
    {
        using var provider = Build(With(
            ("Platform:Mail:Template:Culture", "en"),
            ("Platform:Mail:Template:Width", "640"),
            ("Platform:Mail:Template:Light:Brand", "#0b5cad"),
            ("Platform:Mail:Template:FontFamily", "'Noto Sans TC',Arial,sans-serif")));

        var options = provider.GetRequiredService<IOptions<MailTemplateOptions>>().Value;

        Assert.Equal(("en", 640, "#0b5cad"), (options.Culture, options.Width, options.Light.Brand));
        Assert.Equal("#185fa5", options.Light.Button); // 沒覆寫的鍵維持預設
    }

    // ValidateOnStart：主機啟動時就驗證（這裡以啟動驗證器模擬），不是第一封信才失敗。
    [Fact]
    public async Task TheStartupValidationCatchesABadValueBeforeAnyMailIsSent()
    {
        using var good = Build();
        await good.GetRequiredService<IAsyncStartupValidator>().ValidateAsync(TestContext.Current.CancellationToken);

        using var bad = Build(With(("Platform:Mail:Template:Light:Button", "blue")));
        await Assert.ThrowsAnyAsync<Exception>(() => bad.GetRequiredService<IAsyncStartupValidator>().ValidateAsync(TestContext.Current.CancellationToken));
    }
}