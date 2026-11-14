using System.Text.RegularExpressions;

namespace EnvCite.Platform.Web.Presentation;

/// <summary>台灣法規條文之法定層級單位（項／款／目／細目）。</summary>
public enum ArticleClauseKind
{
    Paragraph,
    Subparagraph,
    Item,
    SubItem,
}

/// <summary>條文解析後之單一文字節點，保留原始法學縮排層級與前導標記。</summary>
public sealed record ArticleClauseNode(
    int Level,
    ArticleClauseKind Kind,
    string? Marker,
    string Body,
    bool StartsProviso);

/// <summary>條文內容區塊基底型別（文字條款區塊或結構化表格區塊）。</summary>
public abstract record ArticleContentBlock;

/// <summary>條文內之文字段落／款目區塊，含項次序號與註記標誌。</summary>
public sealed record ArticleClauseBlock(
    ArticleClauseNode Clause,
    int? ParagraphOrdinal = null,
    bool ShowParagraphBadge = false,
    bool IsNote = false) : ArticleContentBlock;

/// <summary>表格儲存格，保留合併欄數與列數。</summary>
public sealed record ArticleTableCell(string Text, int Colspan = 1, int Rowspan = 1);

/// <summary>結構化資料表區塊（含單層／多層表頭與資料列）。</summary>
public sealed record ArticleTableBlock(
    IReadOnlyList<IReadOnlyList<ArticleTableCell>> HeaderRows,
    IReadOnlyList<IReadOnlyList<ArticleTableCell>> BodyRows) : ArticleContentBlock;

/// <summary>
/// 條文原文之排版模板解析器：將資料庫回傳之純文字法條解析為結構化的項／款／目、但書、
/// 註記與框線／管線表格區塊，供前端渲染成具備項次徽章、懸掛縮排與合併儲存格的版面。
/// </summary>
public static partial class LegalArticleFormatter
{
    /// <summary>僅擷取文字條款節點（向下相容既有多數款目解析測試）。</summary>
    public static IReadOnlyList<ArticleClauseNode> Parse(string? content)
    {
        var blocks = ParseBlocks(content);
        if (blocks.Count == 0)
            return [];

        var result = new List<ArticleClauseNode>();
        foreach (var block in blocks)
        {
            if (block is ArticleClauseBlock clauseBlock)
                result.Add(clauseBlock.Clause);
        }
        return result;
    }

    /// <summary>將法條原文解析為交錯排列的條款區塊與結構化表格區塊，並計算法定「項」次。</summary>
    public static IReadOnlyList<ArticleContentBlock> ParseBlocks(string? content)
    {
        if (string.IsNullOrWhiteSpace(content))
            return [];

        var normalized = BoxDrawingTableParser.RehydrateNewlines(
            content.Replace("\r\n", "\n", StringComparison.Ordinal).Replace('\r', '\n'));
        var lines = normalized.Split('\n');

        var blocks = new List<ArticleContentBlock>();
        var paragraphOrdinal = 0;
        var hasSubItems = false;
        var hasTables = false;

        void AppendProseLine(string raw)
        {
            var line = raw.Trim();
            if (line.Length == 0)
                return;

            var sub = SubparagraphRegex().Match(line);
            if (sub.Success)
            {
                hasSubItems = true;
                blocks.Add(new ArticleClauseBlock(
                    new ArticleClauseNode(1, ArticleClauseKind.Subparagraph, sub.Groups[1].Value, sub.Groups[2].Value, false)));
                return;
            }

            var item = ItemRegex().Match(line);
            if (item.Success)
            {
                hasSubItems = true;
                blocks.Add(new ArticleClauseBlock(
                    new ArticleClauseNode(2, ArticleClauseKind.Item, item.Groups[1].Value, item.Groups[2].Value, false)));
                return;
            }

            var subItem = SubItemRegex().Match(line);
            if (subItem.Success)
            {
                hasSubItems = true;
                blocks.Add(new ArticleClauseBlock(
                    new ArticleClauseNode(3, ArticleClauseKind.SubItem, subItem.Groups[1].Value, subItem.Groups[2].Value, false)));
                return;
            }

            if (NoteLineRegex().IsMatch(line))
            {
                blocks.Add(new ArticleClauseBlock(
                    new ArticleClauseNode(0, ArticleClauseKind.Paragraph, null, line, false),
                    IsNote: true));
                return;
            }

            var startsProviso = line.StartsWith('但');
            if (startsProviso)
            {
                blocks.Add(new ArticleClauseBlock(
                    new ArticleClauseNode(0, ArticleClauseKind.Paragraph, null, line, true)));
                return;
            }

            paragraphOrdinal++;
            blocks.Add(new ArticleClauseBlock(
                new ArticleClauseNode(0, ArticleClauseKind.Paragraph, null, line, false),
                ParagraphOrdinal: paragraphOrdinal));
        }

        for (var i = 0; i < lines.Length; i++)
        {
            var rawLine = lines[i];

            if (BoxDrawingTableParser.ContainsBoxDrawing(rawLine))
            {
                var (prefix, rest) = BoxDrawingTableParser.SplitLeadingProse(rawLine);
                if (!string.IsNullOrWhiteSpace(prefix))
                    AppendProseLine(prefix);

                var boxLines = new List<string> { rest.Length > 0 ? rest : rawLine };
                while (i + 1 < lines.Length && BoxDrawingTableParser.ContainsBoxDrawing(lines[i + 1]))
                {
                    i++;
                    boxLines.Add(lines[i]);
                }

                var parsedTable = BoxDrawingTableParser.Parse(boxLines);
                if (parsedTable is not null)
                {
                    hasTables = true;
                    blocks.Add(parsedTable);
                }
                else
                {
                    foreach (var decorativeLine in boxLines)
                    {
                        var stripped = BoxDrawingTableParser.StripBoxChars(decorativeLine);
                        if (stripped.Length > 0)
                            AppendProseLine(stripped);
                    }
                }
                continue;
            }

            if (i + 1 < lines.Length
                && rawLine.Contains('|', StringComparison.Ordinal)
                && BoxDrawingTableParser.IsMarkdownTableSeparator(lines[i + 1]))
            {
                var mdLines = new List<string> { rawLine, lines[i + 1] };
                i += 2;
                while (i < lines.Length && lines[i].Contains('|', StringComparison.Ordinal) && !string.IsNullOrWhiteSpace(lines[i]))
                {
                    mdLines.Add(lines[i]);
                    i++;
                }
                i--;

                var mdTable = BoxDrawingTableParser.ParseMarkdownPipeTable(mdLines);
                if (mdTable is not null)
                {
                    hasTables = true;
                    blocks.Add(mdTable);
                    continue;
                }
            }

            AppendProseLine(rawLine);
        }

        var showBadges = paragraphOrdinal >= 2 || hasSubItems || hasTables;
        if (!showBadges)
            return blocks;

        for (var idx = 0; idx < blocks.Count; idx++)
        {
            if (blocks[idx] is ArticleClauseBlock { ParagraphOrdinal: not null } cb)
                blocks[idx] = cb with { ShowParagraphBadge = true };
        }

        return blocks;
    }

    [GeneratedRegex(@"^([一二三四五六七八九十百]+、)\s*(.+)$")]
    private static partial Regex SubparagraphRegex();

    [GeneratedRegex(@"^([（(][一二三四五六七八九十百]+[）)])\s*(.+)$")]
    private static partial Regex ItemRegex();

    [GeneratedRegex(@"^(\d+\.|[（(]\d+[）)])\s*(.+)$")]
    private static partial Regex SubItemRegex();

    [GeneratedRegex(@"^(單位|註|備註|說明|附表|附圖)[：:]")]
    private static partial Regex NoteLineRegex();
}