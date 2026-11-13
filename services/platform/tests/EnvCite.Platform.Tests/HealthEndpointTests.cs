using System.Net;

namespace EnvCite.Platform.Tests;

[Collection(PlatformTestGroup.Group4)]
public sealed class HealthEndpointTests(PlatformFactory factory)
{
    [Fact]
    public async Task HealthReturnsOk()
    {
        using var client = factory.CreateClient();

        using var response = await client.GetAsync(new Uri("/health", UriKind.Relative), TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }
}