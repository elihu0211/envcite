using System.Net;
using System.Net.Http.Headers;
using System.Net.Mime;
using System.Text.Json;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;

namespace EnvCite.Platform.Tests;

/// <summary>未捕捉的例外：非 Development 回帶穩定代碼的 problem+json，例外的訊息不出現在回應裡。</summary>
[Collection(PlatformTestGroup.Group2)]
public sealed class UnhandledExceptionTests(PlatformFactory factory)
{
    private const string Secret = "Host=db.internal;Password=hunter2";

    // 接在整條管線的最後：沒有端點接手的請求才會走到這裡，等於一個丟例外的端點。
    private sealed class ThrowingAtTheEnd : IStartupFilter
    {
        public Action<IApplicationBuilder> Configure(Action<IApplicationBuilder> next) => app =>
        {
            next(app);
            app.Run(_ => throw new InvalidOperationException(Secret));
        };
    }

    private WebApplicationFactory<Program> Throwing() =>
        factory.WithWebHostBuilder(builder => builder
            .UseEnvironment("Production")
            .ConfigureServices(services => services.AddSingleton<IStartupFilter, ThrowingAtTheEnd>()));

    [Fact]
    public async Task AJsonCallerGetsAProblemWithAStableCodeAndNoExceptionText()
    {
        await using var host = Throwing();
        using var client = host.CreateClient();
        client.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue(MediaTypeNames.Application.Json));

        using var response = await client.GetAsync(new Uri("/anything", UriKind.Relative), TestContext.Current.CancellationToken);
        var body = await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.InternalServerError, response.StatusCode);
        Assert.Equal("application/problem+json", response.Content.Headers.ContentType?.MediaType);
        using var json = JsonDocument.Parse(body);
        Assert.Equal(OutcomeCodes.InternalError, json.RootElement.GetProperty("code").GetString());
        Assert.Equal(OutcomeCodes.English[OutcomeCodes.InternalError], json.RootElement.GetProperty("detail").GetString());
        Assert.Equal(500, json.RootElement.GetProperty("status").GetInt32());
        Assert.True(json.RootElement.TryGetProperty("traceId", out _));
        Assert.DoesNotContain(Secret, body, StringComparison.Ordinal);
        Assert.DoesNotContain(nameof(InvalidOperationException), body, StringComparison.Ordinal);
    }

    // 只接受 HTML 的呼叫端（瀏覽器的頁面）維持空本文的 500，同樣不洩漏例外。
    [Fact]
    public async Task AHtmlOnlyCallerKeepsAnEmpty500()
    {
        await using var host = Throwing();
        using var client = host.CreateClient();
        client.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue(MediaTypeNames.Text.Html));

        using var response = await client.GetAsync(new Uri("/anything", UriKind.Relative), TestContext.Current.CancellationToken);
        var body = await response.Content.ReadAsStringAsync(TestContext.Current.CancellationToken);

        Assert.Equal(HttpStatusCode.InternalServerError, response.StatusCode);
        Assert.DoesNotContain(Secret, body, StringComparison.Ordinal);
    }
}