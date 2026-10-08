using System.Globalization;

namespace EnvLaw.Platform.Web.Presentation;

/// <summary>
/// 營運者在台灣：時間以台北時間（UTC+8，台灣沒有日光節約時間）顯示，完整的 ISO 時間放在 title。
/// 用固定位移而不是時區資料庫，所以不依賴容器裡有沒有 tzdata。
/// </summary>
public static class TaipeiTime
{
    private static readonly TimeSpan Offset = TimeSpan.FromHours(8);

    public static string Format(DateTimeOffset time) =>
        time.ToOffset(Offset).ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture);

    public static string Full(DateTimeOffset time) =>
        time.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'", CultureInfo.InvariantCulture);
}