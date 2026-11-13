using Microsoft.AspNetCore.Components;

namespace EnvCite.Platform.Web.Presentation;

/// <summary>
/// <c>DataTable</c> 元件的一欄：標題與儲存格怎麼畫。頁面以清單宣告欄位，表格的標記（thead、data-label、窄螢幕卡片）只在模板裡寫。
/// </summary>
/// <typeparam name="TItem">一列的資料。</typeparam>
public sealed class ColumnDef<TItem>
{
    public required string Title { get; init; }

    public required RenderFragment<TItem> Cell { get; init; }

    /// <summary>窄螢幕（卡片）時顯示在值前面的標籤；沒給就用 <see cref="Title"/>。</summary>
    public string? Label { get; init; }

    /// <summary>儲存格的樣式類別，例如列首的 <c>ep-keep</c>、動作欄的 <c>ep-row-actions</c>。數字欄用 <see cref="Numeric"/>，不自己寫 <c>ep-num</c>。</summary>
    public string? Class { get; init; }

    /// <summary>
    /// 數字欄：標題與儲存格都靠右、等寬數字，上下列與標題才對得齊。標題也要靠右：數字靠右、標題靠左，
    /// 欄一寬就會讓標題離它的數字很遠。
    /// </summary>
    public bool Numeric { get; init; }

    /// <summary>標題只給螢幕閱讀器（動作欄）。</summary>
    public bool HideTitle { get; init; }

    /// <summary>窄螢幕時要不要帶標籤；列首（不需要標籤）與動作欄設 false。</summary>
    public bool Labeled { get; init; } = true;

    /// <summary>
    /// 排序鍵。有它的欄位，標題是一顆可以點的排序鈕（升冪、降冪、回到原順序），標題上帶 <c>aria-sort</c>。
    /// 只有後台（互動式）的表格會排；客戶入口的純表格是靜態的，沒有排序鈕。鍵為 <c>null</c> 的列永遠排在最後，
    /// 不論方向。字串依目前的語言排序。
    /// </summary>
    public Func<TItem, IComparable?>? SortBy { get; init; }

    /// <summary>表格一出現就依這一欄排序（同一張表只有一欄設）。</summary>
    public ColumnSort InitialSort { get; init; }
}

/// <summary>欄位的排序狀態。</summary>
public enum ColumnSort
{
    None,
    Ascending,
    Descending,
}