using EnvCite.Platform.Web.Presentation;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 側欄最近對話的分組：今天、昨天、過去 7 天、更早。日期以台北時間（UTC+8）的日曆日算，不是 24 小時，
/// 也不是 UTC 的日期（UTC 的日期在台灣早上 8 點才換）。
/// </summary>
public sealed class ConversationGroupsTests
{
    // 台北時間 2026-11-13 10:00。
    private static readonly DateTimeOffset Now = new(2026, 11, 13, 2, 0, 0, TimeSpan.Zero);

    private static DateTimeOffset Taipei(int month, int day, int hour, int minute = 0) =>
        new(2026, month, day, hour, minute, 0, TimeSpan.FromHours(8));

    [Theory]
    [InlineData(11, 13, 0, 0, ConversationAge.Today)] // 剛過午夜
    [InlineData(11, 13, 9, 59, ConversationAge.Today)]
    [InlineData(11, 12, 23, 59, ConversationAge.Yesterday)] // 不到 24 小時，但已是前一個日曆日
    [InlineData(11, 12, 0, 0, ConversationAge.Yesterday)]
    [InlineData(11, 11, 23, 59, ConversationAge.PastWeek)]
    [InlineData(11, 6, 0, 0, ConversationAge.PastWeek)] // 7 天前
    [InlineData(11, 5, 23, 59, ConversationAge.Older)] // 8 天前
    [InlineData(1, 2, 12, 0, ConversationAge.Older)]
    public void AConversationIsGroupedByTheTaipeiCalendarDay(int month, int day, int hour, int minute, ConversationAge expected) =>
        Assert.Equal(expected, ConversationGroups.AgeOf(Taipei(month, day, hour, minute), Now));

    // UTC 的日期是 12 日，台灣已經是 13 日凌晨：算今天。
    [Fact]
    public void TheDayChangesAtTaipeiMidnightNotAtUtcMidnight() =>
        Assert.Equal(ConversationAge.Today, ConversationGroups.AgeOf(new DateTimeOffset(2026, 11, 12, 16, 30, 0, TimeSpan.Zero), Now));

    // 時鐘稍微不一致造成「未來」的時間，不能掉到奇怪的組。
    [Fact]
    public void AnUpdateFromTheFutureCountsAsToday()
    {
        Assert.Equal(ConversationAge.Today, ConversationGroups.AgeOf(Now.AddMinutes(5), Now));
        Assert.Equal(ConversationAge.Today, ConversationGroups.AgeOf(Now.AddDays(1), Now)); // 另一個日曆日的「未來」
        Assert.Equal(ConversationAge.Today, ConversationGroups.AgeOf(Now.AddDays(30), Now));
    }

    [Fact]
    public void GroupsKeepTheInputOrderAndOmitEmptyOnes()
    {
        (string Title, DateTimeOffset Updated)[] items =
        [
            ("a", Taipei(11, 13, 9)),
            ("b", Taipei(11, 13, 8)),
            ("c", Taipei(11, 1, 8)),
            ("d", Taipei(10, 1, 8)),
        ];

        var groups = ConversationGroups.Group(items, i => i.Updated, Now);

        Assert.Equal([ConversationAge.Today, ConversationAge.Older], groups.Select(g => g.Age));
        Assert.Equal(["a", "b"], groups[0].Items.Select(i => i.Title));
        Assert.Equal(["c", "d"], groups[1].Items.Select(i => i.Title));
    }

    [Fact]
    public void NothingGroupsToNothing() =>
        Assert.Empty(ConversationGroups.Group(Array.Empty<DateTimeOffset>(), t => t, Now));
}