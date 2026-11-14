using System.Text;

using Markdig.Extensions.Tables;
using Markdig.Extensions.TaskLists;
using Markdig.Syntax;
using Markdig.Syntax.Inlines;

namespace EnvCite.Platform.Web.Presentation.Output;

/// <summary>將 <c>```mermaid</c> 程式碼圍欄轉換為視覺圖表區塊。</summary>
internal sealed class MermaidDiagramBlockConverter : IOutputBlockConverter
{
    public int Order => 100;

    public bool CanConvert(Block block, OutputLayoutContext context) =>
        context.Options.EnableMermaidDiagrams
        && block is FencedCodeBlock fenced
        && string.Equals(fenced.Info?.Trim(), "mermaid", StringComparison.OrdinalIgnoreCase);

    public void Convert(Block block, OutputLayoutContext context, IList<OutputBlock> target)
    {
        var fenced = (FencedCodeBlock)block;
        var source = fenced.Lines.ToString().Trim();
        if (source.Length == 0)
            return;

        target.Add(new OutputDiagramBlock("mermaid", source));
    }
}

/// <summary>將一般圍欄與縮排程式碼區塊轉換為唯讀等寬程式碼區塊。</summary>
internal sealed class CodeBlockConverter : IOutputBlockConverter
{
    public int Order => 200;

    public bool CanConvert(Block block, OutputLayoutContext context) => block is CodeBlock;

    public void Convert(Block block, OutputLayoutContext context, IList<OutputBlock> target)
    {
        var codeBlock = (CodeBlock)block;
        var language = (block as FencedCodeBlock)?.Info?.Trim();
        var code = codeBlock.Lines.ToString().TrimEnd('\r', '\n');
        target.Add(new OutputCodeBlock(string.IsNullOrEmpty(language) ? null : language, code));
    }
}

/// <summary>將 Markdig 管線表格與格線表格轉換為支援合併與對齊的結構化表格。</summary>
internal sealed class MarkdigTableBlockConverter : IOutputBlockConverter
{
    public int Order => 300;

    public bool CanConvert(Block block, OutputLayoutContext context) =>
        context.Options.EnableTables && block is Table;

    public void Convert(Block block, OutputLayoutContext context, IList<OutputBlock> target)
    {
        var table = (Table)block;
        var headers = new List<IReadOnlyList<OutputTableCell>>();
        var body = new List<IReadOnlyList<OutputTableCell>>();

        foreach (var child in table)
        {
            if (child is not TableRow row)
                continue;

            var cells = new List<OutputTableCell>(row.Count);
            var runningColumn = 0;
            foreach (var rowChild in row)
            {
                if (rowChild is not TableCell cell)
                    continue;

                var colIndex = cell.ColumnIndex >= 0 ? cell.ColumnIndex : runningColumn;
                var colspan = Math.Max(1, cell.ColumnSpan);
                var alignment = ResolveAlignment(table, colIndex);
                var inlines = ExtractCellInlines(cell, context);
                cells.Add(new OutputTableCell(
                    inlines,
                    colspan,
                    Math.Max(1, cell.RowSpan),
                    alignment));
                runningColumn = colIndex + colspan;
            }

            if (cells.Count == 0)
                continue;

            if (row.IsHeader)
                headers.Add(cells);
            else
                body.Add(cells);
        }

        if (headers.Count > 0 || body.Count > 0)
            target.Add(new OutputTableBlock(headers, body));
    }

    private static OutputCellAlignment ResolveAlignment(Table table, int columnIndex)
    {
        if (columnIndex < 0 || columnIndex >= table.ColumnDefinitions.Count)
            return OutputCellAlignment.Left;

        return table.ColumnDefinitions[columnIndex].Alignment switch
        {
            TableColumnAlign.Center => OutputCellAlignment.Center,
            TableColumnAlign.Right => OutputCellAlignment.Right,
            _ => OutputCellAlignment.Left,
        };
    }

    private static List<OutputInline> ExtractCellInlines(TableCell cell, OutputLayoutContext context)
    {
        var inlines = new List<OutputInline>();
        foreach (var block in cell)
        {
            if (block is LeafBlock leaf && leaf.Inline is not null)
            {
                if (inlines.Count > 0)
                    inlines.Add(new OutputLineBreakInline());
                inlines.AddRange(context.Inlines.ParseMarkdigInlines(leaf.Inline, context.Options));
            }
        }
        return inlines;
    }
}

/// <summary>將 Markdown ATX／Setext 標題轉為語義標題區塊。</summary>
internal sealed class HeadingBlockConverter : IOutputBlockConverter
{
    public int Order => 400;

    public bool CanConvert(Block block, OutputLayoutContext context) => block is HeadingBlock;

    public void Convert(Block block, OutputLayoutContext context, IList<OutputBlock> target)
    {
        var heading = (HeadingBlock)block;
        var inlines = context.Inlines.ParseMarkdigInlines(heading.Inline, context.Options);
        if (inlines.Count > 0)
            target.Add(new OutputHeadingBlock(Math.Clamp(heading.Level, 1, 6), inlines));
    }
}

/// <summary>將有序、無序與任務核取清單遞迴轉換為結構化清單區塊。</summary>
internal sealed class ListBlockConverter : IOutputBlockConverter
{
    public int Order => 500;

    public bool CanConvert(Block block, OutputLayoutContext context) => block is ListBlock;

    public void Convert(Block block, OutputLayoutContext context, IList<OutputBlock> target)
    {
        var list = (ListBlock)block;
        var items = new List<OutputListItem>();
        foreach (var child in list)
        {
            if (child is not ListItemBlock item)
                continue;

            bool? isChecked = null;
            if (item.Count > 0 && item[0] is ParagraphBlock firstParagraph && firstParagraph.Inline is not null)
            {
                foreach (var inline in firstParagraph.Inline)
                {
                    if (inline is TaskList taskList)
                    {
                        isChecked = taskList.Checked;
                        break;
                    }
                }
            }

            items.Add(new OutputListItem(isChecked, context.ConvertChildren(item)));
        }

        var start = list.IsOrdered && int.TryParse(list.OrderedStart, out var parsedStart) ? parsedStart : 1;
        target.Add(new OutputListBlock(list.IsOrdered, Math.Max(1, start), items));
    }
}

/// <summary>將引用區塊遞迴轉換為容器引用節點。</summary>
internal sealed class QuoteBlockConverter : IOutputBlockConverter
{
    public int Order => 600;

    public bool CanConvert(Block block, OutputLayoutContext context) => block is QuoteBlock;

    public void Convert(Block block, OutputLayoutContext context, IList<OutputBlock> target)
    {
        var quote = (QuoteBlock)block;
        var children = context.ConvertChildren(quote);
        if (children.Count > 0)
            target.Add(new OutputQuoteBlock(children));
    }
}

/// <summary>將分隔線區塊轉換為水平分隔線節點。</summary>
internal sealed class ThematicBreakBlockConverter : IOutputBlockConverter
{
    public int Order => 700;

    public bool CanConvert(Block block, OutputLayoutContext context) => block is ThematicBreakBlock;

    public void Convert(Block block, OutputLayoutContext context, IList<OutputBlock> target) =>
        target.Add(new OutputThematicBreakBlock());
}

/// <summary>
/// 識別段落中混合的 Unicode 全形框線表格（┌┬┐─│），復用 <see cref="BoxDrawingTableParser"/> 解析為結構化表格與段落。
/// </summary>
internal sealed class BoxDrawingParagraphBlockConverter : IOutputBlockConverter
{
    public int Order => 800;

    public bool CanConvert(Block block, OutputLayoutContext context) =>
        context.Options.EnableTables
        && context.Options.EnableBoxDrawingTables
        && block is ParagraphBlock paragraph
        && BoxDrawingTableParser.ContainsBoxDrawing(ExtractRawText(paragraph));

    public void Convert(Block block, OutputLayoutContext context, IList<OutputBlock> target)
    {
        var raw = ExtractRawText((ParagraphBlock)block);
        var normalized = BoxDrawingTableParser.RehydrateNewlines(
            raw.Replace("\r\n", "\n", StringComparison.Ordinal).Replace('\r', '\n'));
        var lines = normalized.Split('\n');
        var i = 0;
        var proseBuffer = new List<string>();

        void FlushProse()
        {
            if (proseBuffer.Count == 0)
                return;
            var text = string.Join("\n", proseBuffer).Trim();
            proseBuffer.Clear();
            if (text.Length > 0)
                target.Add(new OutputParagraphBlock(context.Inlines.ParsePlainText(text, context.Options)));
        }

        while (i < lines.Length)
        {
            if (BoxDrawingTableParser.ContainsBoxDrawing(lines[i]))
            {
                var (prefix, firstTableLine) = BoxDrawingTableParser.SplitLeadingProse(lines[i]);
                if (prefix.Length > 0)
                    proseBuffer.Add(prefix);

                FlushProse();

                var tableLines = new List<string> { firstTableLine };
                i++;
                while (i < lines.Length && BoxDrawingTableParser.ContainsBoxDrawing(lines[i]))
                {
                    tableLines.Add(lines[i]);
                    i++;
                }

                var parsed = BoxDrawingTableParser.Parse(tableLines);
                if (parsed is not null)
                {
                    target.Add(ToOutputTable(parsed, context));
                }
                else
                {
                    foreach (var line in tableLines)
                    {
                        var stripped = BoxDrawingTableParser.StripBoxChars(line);
                        if (stripped.Length > 0)
                            proseBuffer.Add(stripped);
                    }
                    FlushProse();
                }
                continue;
            }

            if (!string.IsNullOrWhiteSpace(lines[i]))
                proseBuffer.Add(lines[i].Trim());
            i++;
        }

        FlushProse();
    }

    private static OutputTableBlock ToOutputTable(ArticleTableBlock source, OutputLayoutContext context) =>
        new(
            ConvertRows(source.HeaderRows, context),
            ConvertRows(source.BodyRows, context));

    private static List<IReadOnlyList<OutputTableCell>> ConvertRows(
        IReadOnlyList<IReadOnlyList<ArticleTableCell>> rows,
        OutputLayoutContext context) =>
        rows.Select(row => (IReadOnlyList<OutputTableCell>)row.Select(cell =>
                new OutputTableCell(
                    context.Inlines.ParsePlainText(cell.Text, context.Options),
                    cell.Colspan,
                    cell.Rowspan,
                    OutputCellAlignment.Center))
            .ToList())
            .ToList();

    private static string ExtractRawText(ParagraphBlock paragraph)
    {
        var raw = paragraph.Lines.ToString();
        if (!string.IsNullOrEmpty(raw))
            return raw;

        if (paragraph.Inline is null)
            return string.Empty;

        var sb = new StringBuilder();
        AppendRawInlines(paragraph.Inline, sb);
        return sb.ToString();
    }

    private static void AppendRawInlines(ContainerInline container, StringBuilder sb)
    {
        foreach (var inline in container)
        {
            switch (inline)
            {
                case LiteralInline literal:
                    sb.Append(literal.Content.AsSpan());
                    break;
                case LineBreakInline:
                    sb.Append('\n');
                    break;
                case CodeInline code:
                    sb.Append('`').Append(code.Content).Append('`');
                    break;
                case HtmlInline html:
                    sb.Append(html.Tag);
                    break;
                case ContainerInline nested:
                    AppendRawInlines(nested, sb);
                    break;
            }
        }
    }
}

/// <summary>一般段落區塊轉換器（作為末端兜底處理所有標準文字與行內 <c>[L1]</c> 引用）。</summary>
internal sealed class ParagraphBlockConverter : IOutputBlockConverter
{
    public int Order => 900;

    public bool CanConvert(Block block, OutputLayoutContext context) => block is ParagraphBlock;

    public void Convert(Block block, OutputLayoutContext context, IList<OutputBlock> target)
    {
        var paragraph = (ParagraphBlock)block;
        var inlines = context.Inlines.ParseMarkdigInlines(paragraph.Inline, context.Options);
        if (inlines.Count > 0)
            target.Add(new OutputParagraphBlock(inlines));
    }
}