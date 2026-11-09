namespace EnvCite.Consult.Tests;

/// <summary>地方標記的判定規則：哪些問句算「點名縣市」「點名自治法規」，兩者並存時取哪一類。</summary>
public sealed class LocalMarkerTests
{
    public static TheoryData<string> AllJurisdictions => [.. LocalMarkers.Jurisdictions];

    public static TheoryData<string> AllOrdinanceTerms => [.. LocalMarkers.OrdinanceTerms];

    [Fact]
    public void TheListCoversTheTwentyTwoCountiesAndCitiesOnce()
    {
        Assert.Equal(22, LocalMarkers.Jurisdictions.Distinct().Count());
        // 比對前問句的「台」先換成「臺」，詞表若寫「台」就永遠對不上。
        Assert.All(LocalMarkers.Jurisdictions, name => Assert.DoesNotContain('台', name));
    }

    [Theory]
    [MemberData(nameof(AllJurisdictions))]
    public void EveryJurisdictionIsRecognisedInEitherSpelling(string name)
    {
        Assert.Equal(LocalMarker.Jurisdiction, LocalMarkers.Classify($"{name}的工廠排放要符合什麼標準"));
        Assert.Equal(LocalMarker.Jurisdiction, LocalMarkers.Classify($"{name.Replace('臺', '台')}的工廠排放要符合什麼標準"));
    }

    [Theory]
    [MemberData(nameof(AllOrdinanceTerms))]
    public void EveryOrdinanceTermIsRecognised(string term)
    {
        Assert.Equal(LocalMarker.Ordinance, LocalMarkers.Classify($"請問{term}有沒有規定"));
    }

    [Theory]
    [InlineData("臺北市有哪些空氣污染自治條例")]
    [InlineData("台北市的自治法規")]
    [InlineData("自治規則對高雄市工廠有沒有加嚴")]
    public void OrdinanceWinsWhenBothKindsAreMentioned(string question)
    {
        Assert.Equal(LocalMarker.Ordinance, LocalMarkers.Classify(question));
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData("空氣污染防制法第 20 條的罰則")]
    [InlineData("台灣的固定污染源排放標準")]
    [InlineData("台北的排放標準")]
    [InlineData("縣市政府的稽查權限")]
    public void WithoutAMarkerTheQuestionIsNone(string question)
    {
        Assert.Equal(LocalMarker.None, LocalMarkers.Classify(question));
    }
}