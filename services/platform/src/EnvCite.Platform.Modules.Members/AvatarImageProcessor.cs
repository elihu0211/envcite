using SkiaSharp;

namespace EnvCite.Platform.Modules.Members;

/// <summary>
/// 大頭貼影像正規化（以 <see cref="SkiaSharp"/> 實作）：只接受 PNG／JPEG／WebP、阻擋解壓炸彈、
/// 置中裁切並縮放為 256×256 WebP，藉此剝除所有 EXIF／中繼資料與潛在惡意 payload。
/// </summary>
internal static class AvatarImageProcessor
{
    public const int MaxBytes = 5 * 1024 * 1024;
    public const int OutputSize = 256;
    public const string OutputContentType = "image/webp";

    private const int MaxDimension = 8192;
    private const int WebpQuality = 85;

    public static async Task<AdminOutcome<byte[]>> NormalizeAsync(
        Stream source, long contentLength, CancellationToken cancellationToken)
    {
        if (contentLength > MaxBytes)
            return AdminOutcome.Invalid<byte[]>(OutcomeCodes.AvatarTooLarge);

        using var buffer = new MemoryStream();
        var chunk = new byte[81920];
        int read;
        while ((read = await source.ReadAsync(chunk, cancellationToken)) > 0)
        {
            if (buffer.Length + read > MaxBytes)
                return AdminOutcome.Invalid<byte[]>(OutcomeCodes.AvatarTooLarge);

            buffer.Write(chunk, 0, read);
        }

        if (buffer.Length == 0)
            return AdminOutcome.Invalid<byte[]>(OutcomeCodes.AvatarInvalidFormat);

        buffer.Position = 0;
        using var codec = SKCodec.Create(buffer);
        if (codec is null)
            return AdminOutcome.Invalid<byte[]>(OutcomeCodes.AvatarInvalidFormat);

        if (codec.EncodedFormat is not (SKEncodedImageFormat.Png or SKEncodedImageFormat.Jpeg or SKEncodedImageFormat.Webp))
            return AdminOutcome.Invalid<byte[]>(OutcomeCodes.AvatarInvalidFormat);

        var info = codec.Info;
        if (info.Width <= 0 || info.Height <= 0 || info.Width > MaxDimension || info.Height > MaxDimension)
            return AdminOutcome.Invalid<byte[]>(OutcomeCodes.AvatarInvalidFormat);

        using var decoded = SKBitmap.Decode(codec);
        if (decoded is null)
            return AdminOutcome.Invalid<byte[]>(OutcomeCodes.AvatarInvalidFormat);

        var side = Math.Min(decoded.Width, decoded.Height);
        var cropX = (decoded.Width - side) / 2f;
        var cropY = (decoded.Height - side) / 2f;

        var targetInfo = new SKImageInfo(OutputSize, OutputSize, SKColorType.Rgba8888, SKAlphaType.Premul);
        using var surface = SKSurface.Create(targetInfo);
        if (surface is null)
            return AdminOutcome.Invalid<byte[]>(OutcomeCodes.AvatarInvalidFormat);

        var canvas = surface.Canvas;
        canvas.Clear(SKColors.Transparent);
        using var image = SKImage.FromBitmap(decoded);
        var srcRect = new SKRect(cropX, cropY, cropX + side, cropY + side);
        var dstRect = new SKRect(0, 0, OutputSize, OutputSize);
        canvas.DrawImage(image, srcRect, dstRect, new SKSamplingOptions(SKFilterMode.Linear, SKMipmapMode.Linear));
        canvas.Flush();

        using var snapshot = surface.Snapshot();
        using var encoded = snapshot.Encode(SKEncodedImageFormat.Webp, WebpQuality);
        if (encoded is null)
            return AdminOutcome.Invalid<byte[]>(OutcomeCodes.AvatarInvalidFormat);

        return AdminOutcome.Ok(encoded.ToArray());
    }
}