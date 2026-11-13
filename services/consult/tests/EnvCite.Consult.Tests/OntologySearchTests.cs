namespace EnvCite.Consult.Tests;

/// <summary>consult 對本體要什麼檢索，全部由一個建構點決定；這裡守住它帶的每一個選項。</summary>
public sealed class OntologySearchTests
{
    [Fact]
    public void TheRequestCarriesTheQueryTheDateTheLimitAndEveryRetrievalOption()
    {
        var request = OntologySearch.BuildRequest("露天燃燒", "2026-01-01", rerank: false);

        Assert.Equal("露天燃燒", request.Query);
        Assert.Equal("2026-01-01", request.AsOf);
        Assert.Equal(OntologySearch.Limit, request.Limit);
        Assert.True(request.IncludeGuidance);
        Assert.False(request.Rerank); // 精排由設定決定，預設關
        Assert.Equal(string.Empty, request.Jurisdiction); // 不限管轄
    }

    [Fact]
    public void RerankIsAskedForOnlyWhenTheSettingSaysSo()
    {
        Assert.True(OntologySearch.BuildRequest("露天燃燒", "2026-01-01", rerank: true).Rerank);
        Assert.False(new OntologyOptions().Rerank);
    }
}