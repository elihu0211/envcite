namespace EnvCite.Platform.Web.Presentation;

/// <summary>
/// 表格依一個排序鍵排列：<see cref="ColumnDef{TItem}.SortBy"/> 的實作。獨立出來是為了能直接測：
/// 排序是穩定的（鍵相同的列維持原順序）；鍵為 <c>null</c> 的列留在最後，升冪或降冪都一樣
/// （沒有資料的欄位不該在降冪時跑到最前面）；字串依目前的語言比較。
/// </summary>
public static class ColumnSorter
{
    private static readonly Comparer<IComparable> KeyOrder = Comparer<IComparable>.Create((a, b) => a.CompareTo(b));

    public static IEnumerable<TItem> Order<TItem>(IEnumerable<TItem> items, Func<TItem, IComparable?> sortBy, bool descending)
    {
        var keyed = items.Select(item => (Item: item, Key: sortBy(item))).ToList();
        var present = keyed.Where(row => row.Key is not null).Select(row => (row.Item, Key: row.Key!));
        var ordered = descending ? present.OrderByDescending(row => row.Key, KeyOrder) : present.OrderBy(row => row.Key, KeyOrder);
        return ordered.Select(row => row.Item).Concat(keyed.Where(row => row.Key is null).Select(row => row.Item));
    }
}