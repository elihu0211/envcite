using System.Net;
using System.Text.RegularExpressions;

using Microsoft.AspNetCore.Mvc.Testing;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 一個「瀏覽器」：有自己的 cookie 罐，用 https 的基底位址（入口的 cookie 是 Secure），不自動跟隨轉址。
/// 表單用真的流程送出：先 GET 頁面拿到 antiforgery token 與隱藏欄位，再 POST。
/// </summary>
internal sealed partial class PortalBrowser : IDisposable
{
    private readonly HttpClient _client;

    public PortalBrowser(WebApplicationFactory<Program> app) =>
        _client = app.CreateClient(new WebApplicationFactoryClientOptions
        {
            BaseAddress = new Uri("https://localhost"),
            AllowAutoRedirect = false,
            HandleCookies = true,
        });

    public async Task<(HttpStatusCode Status, string Html, string? Location)> GetAsync(string path)
    {
        using var response = await _client.GetAsync(new Uri(path, UriKind.Relative));
        return (response.StatusCode, WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync()), Location(response));
    }

    /// <summary>GET <paramref name="formPath"/> 取隱藏欄位，再把 <paramref name="fields"/> POST 到 <paramref name="postPath"/>（預設同一頁）。</summary>
    public async Task<(HttpStatusCode Status, string Html, string? Location)> SubmitAsync(
        string formPath, Dictionary<string, string> fields, string? postPath = null)
    {
        var page = await GetAsync(formPath);
        var form = new Dictionary<string, string>(HiddenFields(page.Html));
        foreach (var (key, value) in fields)
        {
            form[key] = value;
        }

        using var response = await _client.PostAsync(new Uri(postPath ?? formPath, UriKind.Relative), new FormUrlEncodedContent(form));
        return (response.StatusCode, WebUtility.HtmlDecode(await response.Content.ReadAsStringAsync()), Location(response));
    }

    public async Task<HttpStatusCode> StatusAsync(string path)
    {
        using var response = await _client.GetAsync(new Uri(path, UriKind.Relative));
        return response.StatusCode;
    }

    public void Dispose() => _client.Dispose();

    /// <summary>轉址目標一律化成「路徑加查詢」：Blazor 的轉址是絕對網址，端點的轉址是相對路徑，測試只在意去哪一頁。</summary>
    private static string? Location(HttpResponseMessage response) =>
        response.Headers.Location is { } location
            ? location.IsAbsoluteUri ? location.PathAndQuery : location.OriginalString
            : null;

    private static Dictionary<string, string> HiddenFields(string html)
    {
        var fields = new Dictionary<string, string>();
        foreach (Match input in HiddenInput().Matches(html))
        {
            var name = Attribute(input.Value, "name");
            var value = Attribute(input.Value, "value");
            if (name is not null)
            {
                fields[name] = value ?? string.Empty;
            }
        }

        return fields;
    }

    private static string? Attribute(string tag, string name)
    {
        var match = Regex.Match(tag, $"""\b{name}="([^"]*)"|\b{name}='([^']*)'""", RegexOptions.None, TimeSpan.FromSeconds(1));
        return match.Success ? match.Groups[1].Success ? match.Groups[1].Value : match.Groups[2].Value : null;
    }

    [GeneratedRegex("""<input[^>]*type="hidden"[^>]*>""", RegexOptions.IgnoreCase, matchTimeoutMilliseconds: 1000)]
    private static partial Regex HiddenInput();
}