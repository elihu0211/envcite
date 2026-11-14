using System.Globalization;
using System.Text.RegularExpressions;

namespace EnvCite.Platform.Web.Presentation;

/// <summary>
/// 法規條號、司法院公文標準引述與官方資料庫出處連結的確定性格式化工具。
/// </summary>
public static partial class LegalCitationFormat
{
    private static readonly string[] LegalDigits = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九"];

    private const string MojOrigin = "https://law.moj.gov.tw";

    /// <summary>將條號（如 <c>5</c>、<c>5-1</c>）格式化為顯示用條號（如 <c>第 5 條</c>、<c>第 5 條之 1</c>）。</summary>
    public static string FormatArticleNumber(string? number)
    {
        if (string.IsNullOrWhiteSpace(number))
            return string.Empty;

        var trimmed = number.Trim();
        var hyphenMatch = HyphenArticleRegex().Match(trimmed);
        if (hyphenMatch.Success)
        {
            return string.IsNullOrEmpty(hyphenMatch.Groups[2].Value)
                ? $"第 {hyphenMatch.Groups[1].Value} 條"
                : $"第 {hyphenMatch.Groups[1].Value} 條之 {hyphenMatch.Groups[2].Value}";
        }
        return trimmed;
    }

    /// <summary>
    /// 產出符合司法院與行政院公文體例的標準引述字串，例如
    /// <c>《噪音管制標準》第五條</c>、<c>《水污染防治法》第七條之一</c>。
    /// </summary>
    public static string FormatOfficialCitation(string lawTitle, string? articleNumber)
    {
        var title = lawTitle.Trim();
        var compact = WhitespaceRegex().Replace(articleNumber ?? string.Empty, string.Empty);

        var arabic = ArabicArticleRegex().Match(compact);
        if (arabic.Success && int.TryParse(arabic.Groups[1].Value, CultureInfo.InvariantCulture, out var mainNum))
        {
            var mainChinese = $"第{NumberToLegalChinese(mainNum)}條";
            var subGroup = arabic.Groups[2].Value;
            var articlePart = subGroup.Length > 0 && int.TryParse(subGroup, CultureInfo.InvariantCulture, out var subNum)
                ? $"{mainChinese}之{NumberToLegalChinese(subNum)}"
                : mainChinese;
            return $"《{title}》{articlePart}";
        }

        return string.IsNullOrEmpty(compact) ? $"《{title}》" : $"《{title}》{compact}";
    }

    /// <summary>將正整數轉為法制國字數字（例如 10 → 十、16 → 十六、108 → 一百零八）。</summary>
    public static string NumberToLegalChinese(int number)
    {
        if (number is < 0 or >= 1000)
            return number.ToString(CultureInfo.InvariantCulture);
        if (number < 10)
            return LegalDigits[number];
        if (number < 20)
            return number == 10 ? "十" : $"十{LegalDigits[number % 10]}";
        if (number < 100)
        {
            var tens = number / 10;
            var rem = number % 10;
            return rem > 0 ? $"{LegalDigits[tens]}十{LegalDigits[rem]}" : $"{LegalDigits[tens]}十";
        }
        var hundreds = number / 100;
        var rem100 = number % 100;
        if (rem100 == 0)
            return $"{LegalDigits[hundreds]}百";
        if (rem100 < 10)
            return $"{LegalDigits[hundreds]}百零{LegalDigits[rem100]}";
        var hTens = rem100 / 10;
        var hRem = rem100 % 10;
        return hRem > 0
            ? $"{LegalDigits[hundreds]}百{LegalDigits[hTens]}十{LegalDigits[hRem]}"
            : $"{LegalDigits[hundreds]}百{LegalDigits[hTens]}十";
    }

    /// <summary>依識別碼方案與在地識別碼組出官方單條深連結與來源標籤鍵。</summary>
    public static (string SourceLabelKey, string? Url) BuildOfficialSource(PortalLawKey identifier, string? articleNumber)
    {
        if (string.Equals(identifier.IdScheme, "moj_pcode", StringComparison.Ordinal))
        {
            var pcode = identifier.LocalId.Trim();
            if (pcode.Length == 0)
                return ("全國法規資料庫", null);

            var flno = MojFlno(articleNumber);
            var url = flno.Length > 0
                ? $"{MojOrigin}/LawClass/LawSingle.aspx?pcode={Uri.EscapeDataString(pcode)}&flno={Uri.EscapeDataString(flno)}"
                : $"{MojOrigin}/LawClass/LawAll.aspx?pcode={Uri.EscapeDataString(pcode)}";
            return ("全國法規資料庫", url);
        }

        return (identifier.IdScheme, null);
    }

    /// <summary>計算全國法規資料庫 <c>flno</c> 參數：去空白與「第／條」，「之」換成連字號。</summary>
    public static string MojFlno(string? articleNumber)
    {
        if (string.IsNullOrWhiteSpace(articleNumber))
            return string.Empty;
        var compact = WhitespaceRegex().Replace(articleNumber, string.Empty)
            .Replace("第", string.Empty, StringComparison.Ordinal)
            .Replace("條", string.Empty, StringComparison.Ordinal)
            .Replace("之", "-", StringComparison.Ordinal);
        return compact;
    }

    [GeneratedRegex(@"^(\d+)(?:-(\d+))?$")]
    private static partial Regex HyphenArticleRegex();

    private static Regex ArabicArticleRegex() => ArabicArticleCompactRegex();

    [GeneratedRegex(@"^第?(\d+)(?:(?:條之|之|-)(\d+))?條?$")]
    private static partial Regex ArabicArticleCompactRegex();

    [GeneratedRegex(@"\s+")]
    private static partial Regex WhitespaceRegex();
}