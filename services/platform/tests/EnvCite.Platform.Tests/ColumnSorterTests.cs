using System.Globalization;

using EnvCite.Platform.Web.Presentation;

namespace EnvCite.Platform.Tests;

/// <summary>表格排序（<see cref="ColumnSorter"/>）：穩定、沒有鍵的列留在最後、字串依目前的語言。</summary>
public sealed class ColumnSorterTests
{
    private sealed record Row(string Name, long? Calls);

    private static readonly Row[] Rows =
    [
        new("b", 5),
        new("a", null),
        new("c", 20),
        new("d", 5),
        new("e", null),
    ];

    private static string Names(IEnumerable<Row> rows) => string.Concat(rows.Select(row => row.Name));

    [Fact]
    public void AscendingIsByKeyAndKeepsTheOriginalOrderForTies()
    {
        var ordered = ColumnSorter.Order(Rows, row => row.Calls, descending: false);

        Assert.Equal("bdcae", Names(ordered)); // 5（b、d 原順序）、20，然後沒有鍵的 a、e
    }

    [Fact]
    public void DescendingIsByKeyAndKeepsTheOriginalOrderForTies()
    {
        var ordered = ColumnSorter.Order(Rows, row => row.Calls, descending: true);

        Assert.Equal("cbdae", Names(ordered)); // 20、5（b、d 原順序），沒有鍵的仍在最後，不因降冪跑到最前面
    }

    [Fact]
    public void RowsWithoutAKeyStayLastInBothDirections()
    {
        Assert.Equal("ae", Names(ColumnSorter.Order(Rows, row => row.Calls, false).TakeLast(2)));
        Assert.Equal("ae", Names(ColumnSorter.Order(Rows, row => row.Calls, true).TakeLast(2)));
    }

    [Fact]
    public void AnEmptyOrAllNullListComesBackAsIs()
    {
        Assert.Empty(ColumnSorter.Order(Array.Empty<Row>(), row => row.Calls, false));
        Row[] allNull = [new("a", null), new("b", null)];

        Assert.Equal("ab", Names(ColumnSorter.Order(allNull, row => row.Calls, true)));
    }

    [Fact]
    public void StringsAreComparedByTheCurrentCulture()
    {
        var before = CultureInfo.CurrentCulture;
        try
        {
            CultureInfo.CurrentCulture = new CultureInfo("en-US");
            Row[] rows = [new("b", 1), new("A", 2), new("a", 3)];
            var ordered = ColumnSorter.Order(rows, row => row.Name, descending: false);

            Assert.Equal("aAb", Names(ordered)); // 文化比較：a 在 A 之前，而且都在 b 之前（不是序數比較的 A、a、b）
        }
        finally
        {
            CultureInfo.CurrentCulture = before;
        }
    }

    [Fact]
    public void DatesSortByTime()
    {
        var early = new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero);
        var late = early.AddDays(3);
        Row[] rows = [new("late", 0), new("early", 0)];

        var ordered = ColumnSorter.Order(rows, row => row.Name == "late" ? late : early, descending: false);

        Assert.Equal(["early", "late"], ordered.Select(row => row.Name));
    }
}