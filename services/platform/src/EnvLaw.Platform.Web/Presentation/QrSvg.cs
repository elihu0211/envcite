using QrCodeGenerator = Net.Codecrete.QrCodeGenerator.QrCode;

namespace EnvLaw.Platform.Web.Presentation;

internal static class QrSvg
{
    /// <summary>
    /// 在伺服器上把 otpauth 網址畫成 SVG：QR 的內容含金鑰，不交給第三方的 QR 服務，也不經過瀏覽器以外的任何地方。
    /// 去掉 XML 宣告與 DOCTYPE，才能直接嵌進 HTML。內容只來自我們自己組的網址。
    /// </summary>
    public static string Render(string uri)
    {
        var svg = QrCodeGenerator.EncodeText(uri, QrCodeGenerator.Ecc.Medium).ToSvgString(border: 2);
        var start = svg.IndexOf("<svg", StringComparison.Ordinal);
        return start < 0 ? string.Empty : svg[start..];
    }
}