using EnvCite.ServiceDefaults;

using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace EnvCite.Consult.Tests;

/// <summary>
/// 字面與語意兩個下界只寫在 <c>relevance-floors.json</c> 一處，consult 與 platform 的搜尋端讀同一份。
/// 共用專案沒有自己的測試專案，所以這裡守它（<see cref="ServiceEnvironmentTests"/> 同理）。
/// </summary>
[Collection(ConsultTestGroup.Name)]
public sealed class RelevanceFloorsTests(ConsultFactory factory)
{
    private static (double Lexical, double Semantic) Shared()
    {
        var configuration = new ConfigurationManager().AddRelevanceFloors();
        var floors = configuration.GetSection(RelevanceFloorsOptions.SectionName).Get<RelevanceFloorsOptions>();
        Assert.NotNull(floors);
        return (floors.LexicalFloor, floors.SemanticFloor);
    }

    [Fact]
    public void TheSharedFloorsAreValid()
    {
        var (lexical, semantic) = Shared();

        Assert.True(lexical > 0);
        Assert.InRange(semantic, 0.0001, 0.9999);
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public void AnyOtherSourceOverridesTheSharedFloorsWhateverTheCallOrder(bool overrideAddedFirst)
    {
        var configuration = new ConfigurationManager();
        var overrides = new Dictionary<string, string?> { ["Relevance:LexicalFloor"] = "9" };
        if (overrideAddedFirst)
            configuration.AddInMemoryCollection(overrides);

        configuration.AddRelevanceFloors();
        if (!overrideAddedFirst)
            configuration.AddInMemoryCollection(overrides);

        Assert.Equal("9", configuration["Relevance:LexicalFloor"]);
        Assert.Equal(Shared().Semantic.ToString(System.Globalization.CultureInfo.InvariantCulture), configuration["Relevance:SemanticFloor"]);
    }

    [Fact]
    public void ConsultUsesTheSharedFloors()
    {
        var (lexical, semantic) = Shared();

        var consult = factory.Services.GetRequiredService<IOptions<RelevanceOptions>>().Value;

        Assert.Equal((lexical, semantic), (consult.LexicalFloor, consult.SemanticFloor));
    }

    [Fact]
    public void ConsultsOwnSettingsFileKeepsNoCopyOfTheFloors()
    {
        // 同值的第二份也不行：值改了一處、另一處沒跟著改，才是這筆債的起點。
        using var settings = System.Text.Json.JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "appsettings.json")));

        var relevance = settings.RootElement.GetProperty("Relevance");

        Assert.False(relevance.TryGetProperty("LexicalFloor", out _));
        Assert.False(relevance.TryGetProperty("SemanticFloor", out _));
    }
}