using EnvCite.Platform.Api;
using EnvCite.Platform.Modules.Canon;
using EnvCite.Platform.Modules.Consult;

using Microsoft.AspNetCore.Http.Timeouts;
using Microsoft.AspNetCore.Routing;

namespace EnvCite.Platform.Tests;

/// <summary>
/// <c>/v2</c> 的兜底請求逾時（見 <see cref="PlatformRequestTimeouts"/>）：比任何上游的期限都長，
/// 上游的逾時才會先發生並對映成 503；而且 <c>/v2</c> 下每個端點都掛著它。
/// </summary>
[Collection(PlatformTestGroup.Group2)]
public sealed class RequestTimeoutTests(PlatformFactory factory)
{
    [Fact]
    public void TheApiTimeoutOutlastsEveryUpstreamDeadline()
    {
        var policy = factory.Services.GetRequiredService<IOptions<RequestTimeoutOptions>>().Value.Policies[PlatformRequestTimeouts.ApiPolicy];
        var canon = factory.Services.GetRequiredService<IOptions<CanonOptions>>().Value;
        var consult = factory.Services.GetRequiredService<IOptions<ConsultOptions>>().Value;

        TimeSpan[] deadlines = [canon.Timeout, canon.ReasoningTimeout, canon.ExtractTimeout, consult.Timeout];

        Assert.All(deadlines, deadline => Assert.True(policy.Timeout > deadline, $"{policy.Timeout} must outlast {deadline}"));
    }

    [Fact]
    public void EveryApiEndpointCarriesTheTimeout()
    {
        var api = factory.Services.GetRequiredService<EndpointDataSource>().Endpoints
            .OfType<RouteEndpoint>()
            .Where(e => e.RoutePattern.RawText?.StartsWith("/v2", StringComparison.Ordinal) == true)
            .ToList();

        Assert.NotEmpty(api);
        Assert.All(api, endpoint =>
            Assert.Equal(PlatformRequestTimeouts.ApiPolicy, endpoint.Metadata.GetMetadata<RequestTimeoutAttribute>()?.PolicyName));
    }
}