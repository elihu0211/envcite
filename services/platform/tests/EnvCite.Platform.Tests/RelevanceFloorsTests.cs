using EnvCite.ServiceDefaults;

using Microsoft.Extensions.Configuration;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 搜尋端的字面與語意下界和 consult 是同一組，值只寫在共用專案的 <c>relevance-floors.json</c> 一處；
/// 平台在這裡守它沒有自己的第二份。
/// </summary>
[Collection(PlatformTestGroup.Group4)]
public sealed class RelevanceFloorsTests(PlatformFactory factory)
{
    [Fact]
    public void TheSearchUsesTheSharedFloors()
    {
        var shared = new ConfigurationManager().AddRelevanceFloors()
            .GetSection(RelevanceFloorsOptions.SectionName).Get<RelevanceFloorsOptions>();
        Assert.NotNull(shared);

        var used = factory.Services.GetRequiredService<IOptions<RelevanceFloorsOptions>>().Value;

        Assert.Equal((shared.LexicalFloor, shared.SemanticFloor), (used.LexicalFloor, used.SemanticFloor));
    }

    [Fact]
    public void ThePlatformsOwnSettingsFileKeepsNoCopyOfTheFloors()
    {
        // 同值的第二份也不行：值改了一處、另一處沒跟著改，才是重複的代價。
        using var settings = System.Text.Json.JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "appsettings.json")));

        Assert.False(settings.RootElement.TryGetProperty("Relevance", out _));
        if (settings.RootElement.TryGetProperty("Platform", out var platform) && platform.TryGetProperty("Search", out var search))
        {
            Assert.False(search.TryGetProperty("LexicalFloor", out _));
            Assert.False(search.TryGetProperty("SemanticFloor", out _));
        }
    }
}