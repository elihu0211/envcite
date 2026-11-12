using Microsoft.AspNetCore.Components;

namespace EnvCite.Platform.Web.Presentation;

/// <summary>
/// <see cref="DataTable{TItem}"/> 的一欄：標題與儲存格怎麼畫。頁面以清單宣告欄位，表格的標記（thead、data-label、窄螢幕卡片）只在模板裡寫。
/// </summary>
/// <typeparam name="TItem">一列的資料。</typeparam>
public sealed class ColumnDef<TItem>
{
    public required string Title { get; init; }

    public required RenderFragment<TItem> Cell { get; init; }

    /// <summary>窄螢幕（卡片）時顯示在值前面的標籤；沒給就用 <see cref="Title"/>。</summary>
    public string? Label { get; init; }

    /// <summary>儲存格的樣式類別，例如列首的 <c>ep-keep</c>、數字的 <c>ep-num</c>、動作欄的 <c>ep-row-actions</c>。</summary>
    public string? Class { get; init; }

    /// <summary>標題只給螢幕閱讀器（動作欄）。</summary>
    public bool HideTitle { get; init; }

    /// <summary>窄螢幕時要不要帶標籤；列首（不需要標籤）與動作欄設 false。</summary>
    public bool Labeled { get; init; } = true;
}