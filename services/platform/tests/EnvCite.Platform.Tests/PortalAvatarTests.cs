using System.Net;
using System.Text;
using System.Text.RegularExpressions;

using EnvCite.Platform.Modules.Members.Portal;

using SkiaSharp;

namespace EnvCite.Platform.Tests;

[Collection(PlatformTestGroup.Group1)]
public sealed partial class PortalAvatarTests(PlatformFactory factory)
{
    [GeneratedRegex("""src="(?<url>/portal/[^"]+/avatar/[^"]+\?v=\d+)" """, RegexOptions.None)]
    private static partial Regex AvatarSrcPattern();

    private static byte[] MakePng(int width, int height)
    {
        using var bitmap = new SKBitmap(width, height, SKColorType.Rgba8888, SKAlphaType.Premul);
        using var canvas = new SKCanvas(bitmap);
        canvas.Clear(new SKColor(0x00, 0x5F, 0xC4));
        using var image = SKImage.FromBitmap(bitmap);
        using var data = image.Encode(SKEncodedImageFormat.Png, 100);
        return data.ToArray();
    }

    [Fact]
    public async Task MemberCanUploadServeCacheAndRemoveAvatar()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "av-flow", CustomerRoles.Developer, "Avatar User");
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var settings = $"{Home(tenant.Identifier)}/settings";
        var uploadPath = $"{settings}/avatar";
        var removePath = $"{uploadPath}/remove";

        // 1. 上傳 400×200 非正方形 PNG：後端裁切縮放為 256×256 WebP 並更新 cookie。
        var uploaded = await browser.SubmitFileAsync(settings, uploadPath, "avatar", "photo.png", "image/png", MakePng(400, 200));
        Assert.Equal(HttpStatusCode.Redirect, uploaded.Status);
        Assert.Equal($"{settings}?avatar=saved", uploaded.Location);

        var afterUpload = await browser.GetAsync(uploaded.Location!);
        Assert.Contains("已更新大頭貼。", afterUpload.Html, StringComparison.Ordinal);

        var match = AvatarSrcPattern().Match(afterUpload.Html);
        Assert.True(match.Success, "settings page renders the versioned avatar image URL");
        var avatarUrl = match.Groups["url"].Value;

        // 2. 取得頭像圖片：200 OK、image/webp、256×256、immutable cache 與 ETag。
        using var getReq = new HttpRequestMessage(HttpMethod.Get, new Uri(avatarUrl, UriKind.Relative));
        using var getRes = await browser.SendAsync(getReq);
        Assert.Equal(HttpStatusCode.OK, getRes.StatusCode);
        Assert.Equal("image/webp", getRes.Content.Headers.ContentType?.MediaType);
        Assert.Contains("immutable", getRes.Headers.CacheControl?.ToString(), StringComparison.Ordinal);
        var etag = getRes.Headers.ETag?.Tag;
        Assert.NotNull(etag);

        var body = await getRes.Content.ReadAsByteArrayAsync(TestContext.Current.CancellationToken);
        using var decoded = SKBitmap.Decode(body);
        Assert.NotNull(decoded);
        Assert.Equal(256, decoded.Width);
        Assert.Equal(256, decoded.Height);

        // 3. 带 If-None-Match 應回 304 Not Modified。
        using var conditionalReq = new HttpRequestMessage(HttpMethod.Get, new Uri(avatarUrl, UriKind.Relative));
        conditionalReq.Headers.TryAddWithoutValidation("If-None-Match", etag);
        using var conditionalRes = await browser.SendAsync(conditionalReq);
        Assert.Equal(HttpStatusCode.NotModified, conditionalRes.StatusCode);

        // 4. 其他租戶的成員不能讀取這個租戶的頭像。
        var (otherTenant, otherEmail, otherInitial) = await MemberAsync(factory, "av-other", CustomerRoles.Viewer, "Other");
        using var otherBrowser = new PortalBrowser(factory);
        await SignInFullyAsync(otherBrowser, otherTenant.Identifier, otherEmail, otherInitial);
        Assert.NotEqual(HttpStatusCode.OK, await otherBrowser.StatusAsync(avatarUrl));

        // 5. 移除大頭貼：退回首字母徽章，舊 URL 回 404，稽核留紀錄。
        var removed = await browser.SubmitAsync(settings, [], removePath);
        Assert.Equal(HttpStatusCode.Redirect, removed.Status);
        Assert.Equal($"{settings}?avatar=removed", removed.Location);

        var afterRemove = await browser.GetAsync(removed.Location!);
        Assert.Contains("已移除大頭貼。", afterRemove.Html, StringComparison.Ordinal);
        Assert.DoesNotMatch(AvatarSrcPattern(), afterRemove.Html);
        Assert.Equal(HttpStatusCode.NotFound, await browser.StatusAsync(avatarUrl));

        var audit = factory.Services.GetRequiredService<IAuditQuery>();
        var updatedEvents = await audit.QueryAsync(new AuditFilter(Tenant: tenant.Id, Action: AuditActions.PortalAvatarUpdated), TestContext.Current.CancellationToken);
        var removedEvents = await audit.QueryAsync(new AuditFilter(Tenant: tenant.Id, Action: AuditActions.PortalAvatarRemoved), TestContext.Current.CancellationToken);
        Assert.NotEmpty(updatedEvents.Items);
        Assert.NotEmpty(removedEvents.Items);
    }

    [Fact]
    public async Task RejectsSvgAndOversizedImages()
    {
        var (tenant, email, initial) = await MemberAsync(factory, "av-sec", CustomerRoles.Developer);
        using var browser = new PortalBrowser(factory);
        await SignInFullyAsync(browser, tenant.Identifier, email, initial);

        var settings = $"{Home(tenant.Identifier)}/settings";
        var uploadPath = $"{settings}/avatar";

        // 假裝成 PNG 的 SVG payload 必須被拒。
        var svgBytes = Encoding.UTF8.GetBytes("<svg xmlns=\"http://www.w3.org/2000/svg\"><script>alert(1)</script></svg>");
        var svgAttempt = await browser.SubmitFileAsync(settings, uploadPath, "avatar", "evil.png", "image/png", svgBytes);
        Assert.Equal(HttpStatusCode.Redirect, svgAttempt.Status);
        Assert.Equal($"{settings}?avatar=invalid", svgAttempt.Location);

        var errorPage = await browser.GetAsync(svgAttempt.Location!);
        Assert.Contains("role=\"alert\"", errorPage.Html, StringComparison.Ordinal);

        // 超過 5 MB 的串流在處理器被拒。
        using var oversized = new MemoryStream(new byte[AvatarImageProcessor.MaxBytes + 1]);
        var outcome = await AvatarImageProcessor.NormalizeAsync(oversized, oversized.Length, TestContext.Current.CancellationToken);
        Assert.Equal(OutcomeStatus.Invalid, outcome.Status);
        Assert.Equal(OutcomeCodes.AvatarTooLarge, outcome.Code);
    }
}