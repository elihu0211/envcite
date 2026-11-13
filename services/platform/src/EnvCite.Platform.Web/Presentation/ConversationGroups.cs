namespace EnvCite.Platform.Web.Presentation;

/// <summary>對話離「現在」多久，側欄依它分組。</summary>
public enum ConversationAge
{
    Today,
    Yesterday,
    PastWeek,
    Older,
}

/// <summary>
/// 側欄的最近對話依日期分組（今天、昨天、過去 7 天、更早）。日期以台北時間（UTC+8）的日曆日算，不是 24 小時：
/// 晚上十一點問的，隔天早上就是「昨天」。純函式，傳入的順序（新的在前）保留，沒有項目的組不出現。
/// </summary>
public static class ConversationGroups
{
    private static readonly TimeSpan Offset = TimeSpan.FromHours(8);

    public static ConversationAge AgeOf(DateTimeOffset updated, DateTimeOffset now)
    {
        var days = (now.ToOffset(Offset).Date - updated.ToOffset(Offset).Date).Days;
        return days switch
        {
            <= 0 => ConversationAge.Today, // 時鐘稍微不一致造成的「未來」也算今天
            1 => ConversationAge.Yesterday,
            <= 7 => ConversationAge.PastWeek,
            _ => ConversationAge.Older,
        };
    }

    public static IReadOnlyList<(ConversationAge Age, IReadOnlyList<T> Items)> Group<T>(
        IEnumerable<T> items, Func<T, DateTimeOffset> updatedOf, DateTimeOffset now) =>
        [.. items
            .GroupBy(item => AgeOf(updatedOf(item), now))
            .OrderBy(group => group.Key)
            .Select(group => (group.Key, (IReadOnlyList<T>)[.. group]))];
}