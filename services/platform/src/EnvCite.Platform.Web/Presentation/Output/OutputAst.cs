namespace EnvCite.Platform.Web.Presentation.Output;

/// <summary>經 <see cref="IOutputLayoutEngine"/> 解析後之安全結構化輸出文件（不可變抽象語法樹）。</summary>
public sealed record OutputDocument(IReadOnlyList<OutputBlock> Blocks)
{
    public static OutputDocument Empty { get; } = new([]);
}

/// <summary>輸出文件的區塊節點基底型別。</summary>
public abstract record OutputBlock;

/// <summary>標題區塊（對應 H1–H6，由前端元件映射至適當層級以免干擾頁面主標題）。</summary>
public sealed record OutputHeadingBlock(int Level, IReadOnlyList<OutputInline> Inlines) : OutputBlock;

/// <summary>一般段落區塊。</summary>
public sealed record OutputParagraphBlock(IReadOnlyList<OutputInline> Inlines) : OutputBlock;

/// <summary>項目符號、編號或核取任務清單區塊。</summary>
public sealed record OutputListBlock(bool Ordered, int Start, IReadOnlyList<OutputListItem> Items) : OutputBlock;

/// <summary>清單項目；若為 GFM 任務清單則 <see cref="IsChecked"/> 帶勾選狀態。</summary>
public sealed record OutputListItem(bool? IsChecked, IReadOnlyList<OutputBlock> Blocks);

/// <summary>引用區塊（法規摘錄、要旨提示）。</summary>
public sealed record OutputQuoteBlock(IReadOnlyList<OutputBlock> Blocks) : OutputBlock;

/// <summary>等寬程式碼或原文片段區塊。</summary>
public sealed record OutputCodeBlock(string? Language, string Code) : OutputBlock;

/// <summary>結構化視覺圖表區塊（如 Mermaid 流程圖、時序圖、時程圖或心智圖）。</summary>
public sealed record OutputDiagramBlock(string Language, string Source) : OutputBlock;

/// <summary>儲存格對齊方式。</summary>
public enum OutputCellAlignment
{
    Left,
    Center,
    Right,
}

/// <summary>表格儲存格（支援合併欄列、對齊與內嵌引用徽章）。</summary>
public sealed record OutputTableCell(
    IReadOnlyList<OutputInline> Inlines,
    int Colspan = 1,
    int Rowspan = 1,
    OutputCellAlignment Alignment = OutputCellAlignment.Left);

/// <summary>結構化資料表區塊（來自 GFM Pipe/Grid Table 或傳統全形框線表格）。</summary>
public sealed record OutputTableBlock(
    IReadOnlyList<IReadOnlyList<OutputTableCell>> HeaderRows,
    IReadOnlyList<IReadOnlyList<OutputTableCell>> BodyRows) : OutputBlock;

/// <summary>水平分隔線區塊。</summary>
public sealed record OutputThematicBreakBlock : OutputBlock;

/// <summary>段落或儲存格內的行內節點基底型別。</summary>
public abstract record OutputInline;

/// <summary>純文字片段。</summary>
public sealed record OutputTextInline(string Text) : OutputInline;

/// <summary>粗體文字。</summary>
public sealed record OutputStrongInline(IReadOnlyList<OutputInline> Children) : OutputInline;

/// <summary>斜體文字。</summary>
public sealed record OutputEmphasisInline(IReadOnlyList<OutputInline> Children) : OutputInline;

/// <summary>行內程式碼。</summary>
public sealed record OutputCodeInline(string Code) : OutputInline;

/// <summary>經過 URL 安全驗證的外部或站內連結（僅允許 http、https 或單斜線站內路徑）。</summary>
public sealed record OutputLinkInline(string Href, IReadOnlyList<OutputInline> Children) : OutputInline;

/// <summary>可點擊開啟法源條文抽屜的行內引用代號（例如 <c>L1</c>）。</summary>
public sealed record OutputCitationInline(string Code) : OutputInline;

/// <summary>明確換行符號。</summary>
public sealed record OutputLineBreakInline : OutputInline;