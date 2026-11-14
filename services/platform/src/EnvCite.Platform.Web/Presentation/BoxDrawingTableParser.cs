using System.Text.RegularExpressions;

namespace EnvCite.Platform.Web.Presentation;

/// <summary>
/// Unicode 框線表格（┌┬┐├┼┤└┴┘─│）與 GFM 管線表格解析器。
/// <para>
/// 全國法規資料庫官方條文與行政規則附表常以全形／半形框線字元排版（包含多層分組表頭、
/// 對角角標籤堆疊與合併儲存格）。本解析器採雙路徑：
/// </para>
/// <list type="number">
/// <item><description>當框線呈現對齊顯示寬度的矩形網格時，直接依 2D 格線拓撲算出精確的 <c>colspan</c> 與 <c>rowspan</c>。</description></item>
/// <item><description>當原始資料因 PDF／排版寬度誤差導致虛線長度未對齊時，退回視覺列切分與 <c>┼／┬／┴</c> 分隔符號推算。</description></item>
/// </list>
/// 單欄裝飾性外框（僅包住條列文字而非表格）會回傳 <see langword="null"/>，交由呼叫端剝除框線後還原為一般條列。
/// </summary>
public static partial class BoxDrawingTableParser
{
    private const char GridPad = '\0';

    private static readonly HashSet<char> BoxChars =
    [
        '┌', '┬', '┐', '├', '┼', '┤', '└', '┴', '┘', '─', '│', '┃', '━',
    ];

    private static readonly HashSet<char> FormulaBracketChars = ['╭', '╮', '╰', '╯'];

    private static readonly HashSet<char> BorderRowChars =
    [
        '┌', '┬', '┐', '├', '┼', '┤', '└', '┴', '┘', '─', '━', ' ', '\u3000',
    ];

    private static readonly HashSet<char> JunctionChars =
    [
        '┌', '┬', '┐', '├', '┼', '┤', '└', '┴', '┘', '+',
    ];

    private static readonly HashSet<char> RightEdgeChars = ['┐', '┤', '┘'];

    /// <summary>判斷該行是否包含表格框線字元（刻意排除僅含公式大括號 <c>╭╮╰╯</c> 的行）。</summary>
    public static bool ContainsBoxDrawing(string? line)
    {
        if (string.IsNullOrEmpty(line))
            return false;
        foreach (var ch in line)
        {
            if (BoxChars.Contains(ch))
                return true;
        }
        return false;
    }

    /// <summary>補回被單行壓縮黏在一起的框線列換行。</summary>
    public static string RehydrateNewlines(string text)
    {
        if (string.IsNullOrEmpty(text))
            return string.Empty;
        var s = JoinedCornerRegex().Replace(text, "$1\n$2");
        s = JoinedBorderRegex().Replace(s, "$1\n$2");
        return s.Replace("││", "│\n│", StringComparison.Ordinal);
    }

    /// <summary>拆開同一行內的散文前綴與框線起點（例如 <c>二、┌──┐</c>）。</summary>
    public static (string Prefix, string Remaining) SplitLeadingProse(string line)
    {
        foreach (var origin in (ReadOnlySpan<char>)['┌', '├', '└'])
        {
            var idx = line.IndexOf(origin);
            if (idx > 0)
                return (line[..idx].TrimEnd(), line[idx..]);
            if (idx == 0)
                return (string.Empty, line);
        }
        return (string.Empty, line);
    }

    /// <summary>判斷是否為純框線分隔行（如 <c>├─┼─┤</c>）。</summary>
    public static bool IsBorderRow(string line)
    {
        var trimmed = line.Trim();
        if (trimmed.Length == 0 || (!trimmed.Contains('─') && !trimmed.Contains('━')))
            return false;
        foreach (var ch in trimmed)
        {
            if (!BorderRowChars.Contains(ch))
                return false;
        }
        return true;
    }

    /// <summary>剝除框線字元（用於裝飾性單欄外框還原成一般條文）。</summary>
    public static string StripBoxChars(string line) =>
        string.Concat(line.Where(ch => !BoxChars.Contains(ch) && !FormulaBracketChars.Contains(ch))).Trim();

    /// <summary>將連續框線行解析為結構化表格；若為單欄裝飾性外框或不足成表則回傳 <see langword="null"/>。</summary>
    public static ArticleTableBlock? Parse(IReadOnlyList<string> rawLines)
    {
        if (rawLines.Count == 0)
            return null;

        var rehydrated = RehydrateNewlines(string.Join('\n', rawLines));
        var (_, bodyStart) = SplitLeadingProse(rehydrated);
        var block = bodyStart.Split('\n').ToList();

        var (visualRows, borderBefore) = VisualRowsWithBorders(block);
        if (visualRows.Count == 0)
            return null;

        var groupNcols = visualRows.Select(GroupOwnNcol).ToList();
        if (groupNcols[0] == 0)
            return null;

        var maxNcol = groupNcols.Max();
        if (maxNcol <= 1)
            return null; // 裝飾性外框，非資料表格

        var gridTable = TryParseGridTable(block, maxNcol);
        if (gridTable is not null)
            return gridTable;

        return ParseFallbackTable(visualRows, borderBefore, groupNcols, maxNcol);
    }

    /// <summary>將 GFM 管線表格區塊解析為結構化表格。</summary>
    public static ArticleTableBlock? ParseMarkdownPipeTable(IReadOnlyList<string> lines)
    {
        if (lines.Count < 2)
            return null;
        var headerCells = SplitPipeRow(lines[0]);
        if (headerCells.Count == 0)
            return null;

        var headerRow = headerCells.Select(t => new ArticleTableCell(t)).ToList();
        var bodyRows = new List<IReadOnlyList<ArticleTableCell>>();
        for (var i = 2; i < lines.Count; i++)
        {
            var rowCells = SplitPipeRow(lines[i]);
            if (rowCells.Count == 0)
                continue;
            while (rowCells.Count < headerRow.Count)
                rowCells.Add(string.Empty);
            bodyRows.Add(rowCells.Select(t => new ArticleTableCell(t)).ToList());
        }
        return new ArticleTableBlock([headerRow], bodyRows);
    }

    internal static bool IsMarkdownTableSeparator(string line) => MarkdownSeparatorRegex().IsMatch(line);

    private static List<string> SplitPipeRow(string line)
    {
        var trimmed = line.Trim();
        if (trimmed.StartsWith('|'))
            trimmed = trimmed[1..];
        if (trimmed.EndsWith('|'))
            trimmed = trimmed[..^1];
        return [.. trimmed.Split('|').Select(c => c.Trim())];
    }

    // ── Path 1: Display-Width Rectangular 2D Grid Parser ───────────────────────────────────────

    private static ArticleTableBlock? TryParseGridTable(List<string> lines, int expectedNcol)
    {
        var expanded = new List<string>(lines.Count);
        foreach (var line in lines)
        {
            if (string.IsNullOrWhiteSpace(line))
                continue;
            expanded.Add(ToGridLine(line));
        }
        if (expanded.Count < 3)
            return null;

        var width = 0;
        foreach (var ln in expanded)
        {
            var trimmedEnd = ln.TrimEnd();
            var rightJunction = trimmedEnd.LastIndexOf('+');
            var rightBar = trimmedEnd.LastIndexOf('|');
            var r = Math.Max(rightJunction, rightBar);
            if (r < 0)
                return null;
            width = Math.Max(width, r + 1);
        }
        if (width <= 2)
            return null;

        var grid = expanded.Select(ln => ln.Length >= width ? ln[..width] : ln.PadRight(width)).ToArray();

        // 收集所有邊界線的垂直線段位置
        var xSet = new SortedSet<int>();
        for (var y = 0; y < grid.Length; y++)
        {
            var ln = grid[y];
            for (var x = 0; x < ln.Length; x++)
            {
                if (ln[x] is '+' or '|')
                    xSet.Add(x);
            }
        }
        var colBounds = xSet.ToList();
        if (colBounds.Count - 1 != expectedNcol)
            return null;

        // 驗證外框與垂直邊緣一致：每列的最左／最右分隔符須貼齊 colBounds[0] 與 colBounds[^1]
        for (var y = 0; y < grid.Length; y++)
        {
            var left = grid[y][colBounds[0]];
            if (left is not ('+' or '|'))
                return null;
            var right = grid[y][colBounds[^1]];
            if (right is not ('+' or '|'))
                return null;
        }

        // 偵測水平分隔行（含全行分隔或行內後綴分隔），頂邊 y=0 與底邊 y=last 必須是分隔行
        if (!RowHasHorizontalBorder(grid[0], colBounds) || !RowHasHorizontalBorder(grid[^1], colBounds))
            return null;

        var borderYs = new List<int>();
        for (var y = 0; y < grid.Length; y++)
        {
            if (RowHasHorizontalBorder(grid[y], colBounds))
                borderYs.Add(y);
        }
        if (borderYs.Count < 2 || borderYs[0] != 0 || borderYs[^1] != grid.Length - 1)
            return null;

        // 建立 (rowBand x colStrip) 格網上的儲存格，計算 colspan / rowspan
        var nRowBands = borderYs.Count - 1;
        var nCols = colBounds.Count - 1;
        var visited = new bool[nRowBands, nCols];
        var rawGrid = new (int RowSpan, int ColSpan, List<string> Lines)?[nRowBands, nCols];

        for (var r = 0; r < nRowBands; r++)
        {
            for (var c = 0; c < nCols; c++)
            {
                if (visited[r, c])
                    continue;

                // 向右延伸 colspan：只要 r.. 垂直邊界上該欄右緣沒有貫穿分隔線
                var cSpan = 1;
                while (c + cSpan < nCols && !HasVerticalWallInBand(grid, borderYs[r], borderYs[r + 1], colBounds[c + cSpan]))
                    cSpan++;

                // 向下延伸 rowspan：只要底邊 borderYs[r + rSpan] 橫跨 colBounds[c]..colBounds[c+cSpan] 不是水平分隔線
                var rSpan = 1;
                while (r + rSpan < nRowBands &&
                       !HasHorizontalWallAtRow(grid[borderYs[r + rSpan]], colBounds[c], colBounds[c + cSpan]))
                {
                    rSpan++;
                }

                // 標記已涵蓋的儲存格並檢查矩形一致（無半截交錯邊界）
                for (var dr = 0; dr < rSpan; dr++)
                {
                    for (var dc = 0; dc < cSpan; dc++)
                    {
                        if (visited[r + dr, c + dc])
                            return null;
                        visited[r + dr, c + dc] = true;
                    }
                }

                var yStart = borderYs[r] + 1;
                var yEnd = borderYs[r + rSpan];
                var xStart = colBounds[c] + 1;
                var xEnd = colBounds[c + cSpan];
                var cellLines = new List<string>();

                // 若上邊界在該欄區段沒有畫橫線（例如混有文字的 inline border row），不該發生於此因為從 borderStart 往下；
                // 但若下邊界 borderYs[r + rSpan] 是混合行且左側該欄沒畫橫線，代表文字在 yStart..yEnd；
                // 反之若 borderYs[r] 是混合行，而此儲存格是因下層向右展開從上方延續或混合行自身起始：
                if (yStart == borderYs[r] + 1 && r > 0 && !HasHorizontalWallAtRow(grid[borderYs[r]], colBounds[c], colBounds[c + cSpan]))
                {
                    // 由上格 rowspan 已吃下；若未吃下代表形狀不規則
                    return null;
                }

                // 若某個內部 borderY[r + rSpan] 是混合行，該儲存格本身若於混合行停止（上側在該欄有橫線），已於下面停止。
                // 但若該欄在 borderYs[r] 上沒有畫橫線（不可達），不影響這裡；
                // 若最後一行 yEnd 本身在該欄位置「其實是空白＋文字」（i.e. inline border 混行：該欄沒有畫橫線而是右邊欄畫橫線），
                // 上方的 while 已讓 rSpan 向下合併，因此只有中間的 borderYs[r + k] 仍會有內容行！
                for (var y = yStart; y < yEnd; y++)
                {
                    var slice = ExtractGridSlice(grid[y], xStart, xEnd);
                    cellLines.Add(slice);
                }
                rawGrid[r, c] = (rSpan, cSpan, cellLines);
            }
        }

        // 若某列完全沒有非空細胞，格式異常則退回 fallback
        var headerDepth = ComputeGridHeaderDepth(rawGrid, nRowBands, nCols);
        var expandedHeader = TryExpandGridStubStack(rawGrid, headerDepth, nCols);
        var headerRows = expandedHeader ?? ExtractGridRows(rawGrid, 0, headerDepth, nCols);
        var bodyRows = ExtractGridRows(rawGrid, headerDepth, nRowBands, nCols);
        if (headerRows.Count == 0 && bodyRows.Count == 0)
            return null;

        return new ArticleTableBlock(headerRows, bodyRows);
    }

    private static int DisplayWidth(char ch)
    {
        if (BoxChars.Contains(ch) || FormulaBracketChars.Contains(ch))
            return 2;
        if (ch is >= '\u2E80' and <= '\u9FFF'
            or >= '\uF900' and <= '\uFAFF'
            or >= '\uFF00' and <= '\uFFEF'
            or '℃' or '㎡' or '±' or '×' or '÷' or '≤' or '≥' or '°' or '％' or '　')
        {
            return 2;
        }
        return 1;
    }

    private static string ToGridLine(string line)
    {
        var sb = new System.Text.StringBuilder(line.Length * 2);
        foreach (var ch in line)
        {
            var pad = DisplayWidth(ch) - 1;
            if (ch is '│' or '┃' or '|')
            {
                sb.Append('|');
                if (pad > 0)
                    sb.Append(GridPad, pad);
            }
            else if (ch is '─' or '━' or '-')
            {
                sb.Append('-', pad + 1);
            }
            else if (JunctionChars.Contains(ch))
            {
                sb.Append('+');
                if (pad > 0)
                    sb.Append(RightEdgeChars.Contains(ch) ? ' ' : '-', pad);
            }
            else
            {
                sb.Append(ch);
                if (pad > 0)
                    sb.Append(GridPad, pad);
            }
        }
        return sb.ToString();
    }

    private static bool RowHasHorizontalBorder(string row, List<int> colBounds)
    {
        for (var c = 0; c < colBounds.Count - 1; c++)
        {
            if (HasHorizontalWallAtRow(row, colBounds[c], colBounds[c + 1]))
                return true;
        }
        return false;
    }

    private static bool HasHorizontalWallAtRow(string row, int x0, int x1)
    {
        if (x1 >= row.Length)
            return false;
        if (row[x0] != '+' || row[x1] != '+')
            return false;
        for (var x = x0 + 1; x < x1; x++)
        {
            if (row[x] is not ('-' or '+'))
                return false;
        }
        return x1 > x0 + 1;
    }

    private static bool HasVerticalWallInBand(string[] grid, int yBorderTop, int yBorderBottom, int x)
    {
        if (yBorderBottom - yBorderTop <= 1)
            return grid[yBorderTop][x] is '|' or '+';
        for (var y = yBorderTop + 1; y < yBorderBottom; y++)
        {
            if (grid[y][x] is '|' or '+')
                return true;
        }
        return false;
    }

    private static string ExtractGridSlice(string gridLine, int xStart, int xEnd)
    {
        if (xStart >= gridLine.Length)
            return string.Empty;
        var safeEnd = Math.Min(xEnd, gridLine.Length);
        if (safeEnd <= xStart)
            return string.Empty;
        return gridLine[xStart..safeEnd].Replace(GridPad.ToString(), string.Empty, StringComparison.Ordinal);
    }

    private static int ComputeGridHeaderDepth(
        (int RowSpan, int ColSpan, List<string> Lines)?[,] rawGrid,
        int nRows,
        int nCols)
    {
        var depth = 0;
        for (var r = 0; r < nRows; r++)
        {
            depth++;
            var hasRightGroup = false;
            for (var c = 1; c < nCols; c++)
            {
                if (rawGrid[r, c] is { ColSpan: > 1 })
                {
                    hasRightGroup = true;
                    break;
                }
            }
            if (!hasRightGroup)
                break;
        }
        return depth >= nRows ? 1 : depth;
    }

    private static List<IReadOnlyList<ArticleTableCell>>? TryExpandGridStubStack(
        (int RowSpan, int ColSpan, List<string> Lines)?[,] rawGrid,
        int headerDepth,
        int nCols)
    {
        if (headerDepth < 2)
            return null;

        var labels = new List<string>();
        for (var r = 0; r < headerDepth; r++)
        {
            if (rawGrid[r, 0] is { Lines: var lines })
            {
                foreach (var line in lines)
                {
                    var cleaned = StripInnerBorderNoise(line);
                    if (!string.IsNullOrEmpty(cleaned))
                        labels.Add(cleaned);
                }
            }
        }
        if (labels.Count <= headerDepth)
            return null;

        var groups = new SortedDictionary<int, ArticleTableCell>();
        for (var r = 0; r < headerDepth - 1; r++)
        {
            for (var c = 1; c < nCols; c++)
            {
                if (rawGrid[r, c] is not { ColSpan: var colSpan, Lines: var cellLines })
                    continue;
                var text = StripInnerBorderNoise(MergeCell(cellLines));
                if (!string.IsNullOrEmpty(text) && !groups.ContainsKey(c))
                    groups[c] = new ArticleTableCell(text, colSpan, labels.Count - 1);
            }
        }

        var finestStub = labels[^1];
        var headerRows = new List<IReadOnlyList<ArticleTableCell>>();
        var firstRow = new List<ArticleTableCell> { new(labels[0]) };
        firstRow.AddRange(groups.Values);
        headerRows.Add(firstRow);

        for (var i = 1; i < labels.Count - 1; i++)
            headerRows.Add([new ArticleTableCell(labels[i])]);

        var finestRow = new List<ArticleTableCell> { new(finestStub) };
        for (var c = 1; c < nCols; c++)
        {
            if (rawGrid[headerDepth - 1, c] is { ColSpan: var colSpan, RowSpan: var rowSpan, Lines: var cellLines })
            {
                finestRow.Add(new ArticleTableCell(
                    StripInnerBorderNoise(MergeCell(cellLines)),
                    colSpan,
                    rowSpan));
            }
        }
        headerRows.Add(finestRow);
        return headerRows;
    }

    private static List<IReadOnlyList<ArticleTableCell>> ExtractGridRows(
        (int RowSpan, int ColSpan, List<string> Lines)?[,] rawGrid,
        int rStart,
        int rEnd,
        int nCols)
    {
        var result = new List<IReadOnlyList<ArticleTableCell>>();
        for (var r = rStart; r < rEnd; r++)
        {
            var row = new List<ArticleTableCell>();
            for (var c = 0; c < nCols; c++)
            {
                if (rawGrid[r, c] is { RowSpan: var rSpan, ColSpan: var cSpan, Lines: var cellLines })
                {
                    row.Add(new ArticleTableCell(
                        StripInnerBorderNoise(MergeCell(cellLines)),
                        cSpan,
                        rSpan));
                }
            }
            if (row.Count > 0)
                result.Add(row);
        }
        return result;
    }

    // ── Path 2: Visual-Row & Border-Char Fallback Parser ───────────────────────────────────────

    private static ArticleTableBlock? ParseFallbackTable(
        List<List<string>> visualRows,
        List<string> borderBefore,
        List<int> groupNcols,
        int maxNcol)
    {
        if (visualRows.Count == 1)
        {
            var single = MergeGroupToRow(visualRows[0], maxNcol)
                .Select(t => new ArticleTableCell(t))
                .ToList();
            return new ArticleTableBlock([single], []);
        }

        var headerDepth = 0;
        foreach (var n in groupNcols)
        {
            headerDepth++;
            if (n == maxNcol)
                break;
        }
        if (headerDepth >= visualRows.Count)
            headerDepth = 1;

        var bodyRows = new List<IReadOnlyList<ArticleTableCell>>();
        for (var i = headerDepth; i < visualRows.Count; i++)
        {
            foreach (var sub in SplitBodyGroupIntoRows(visualRows[i]))
            {
                var merged = MergeGroupToRow(sub, maxNcol);
                bodyRows.Add(merged.Select(t => new ArticleTableCell(t)).ToList());
            }
        }

        var finestHeader = MergeGroupToRow(visualRows[headerDepth - 1], maxNcol)
            .Select(t => new ArticleTableCell(t))
            .ToList();

        if (headerDepth == 1)
            return new ArticleTableBlock([finestHeader], bodyRows);

        var (stubs, groups) = CollectStubStackAndGroups(visualRows.Take(headerDepth - 1));
        var groupingCount = (stubs.Count > 0 ? 1 : 0) + groups.Count;
        var border = borderBefore[headerDepth - 1];
        var spans = ColspansFromBorder(border, maxNcol, groupingCount);
        if (spans is null && groupingCount > 0 && maxNcol % groupingCount == 0)
            spans = Enumerable.Repeat(maxNcol / groupingCount, groupingCount).ToList();

        if (spans is null || stubs.Count == 0)
        {
            var groupingRow = MergeGroupToRow(visualRows[headerDepth - 2], maxNcol);
            var fallbackSpans = ColspansFromBorder(border, maxNcol, groupingRow.Count);
            if (fallbackSpans is null && groupingRow.Count > 0 && maxNcol % groupingRow.Count == 0)
                fallbackSpans = Enumerable.Repeat(maxNcol / groupingRow.Count, groupingRow.Count).ToList();

            if (fallbackSpans is null)
                return new ArticleTableBlock([finestHeader], bodyRows);

            var groupingHeader = groupingRow
                .Zip(fallbackSpans, (text, span) => new ArticleTableCell(text, span))
                .ToList();
            return new ArticleTableBlock([groupingHeader, finestHeader], bodyRows);
        }

        var rowspan = stubs.Count;
        var firstRow = new List<ArticleTableCell> { new(stubs[0], spans[0]) };
        var groupSpans = spans.Count > 1 ? spans.Skip(1).ToList() : Enumerable.Repeat(1, groups.Count).ToList();
        for (var i = 0; i < groups.Count; i++)
        {
            var cSpan = i < groupSpans.Count ? groupSpans[i] : 1;
            firstRow.Add(new ArticleTableCell(groups[i], cSpan, rowspan > 1 ? rowspan : 1));
        }

        var headerRows = new List<IReadOnlyList<ArticleTableCell>> { firstRow };
        for (var i = 1; i < stubs.Count; i++)
            headerRows.Add([new ArticleTableCell(stubs[i])]);
        headerRows.Add(finestHeader);

        return new ArticleTableBlock(headerRows, bodyRows);
    }

    private static (string Line, string? InlineBorder) SplitInlineBorderSuffix(string line)
    {
        var i = line.Length;
        while (i > 0 && BorderRowChars.Contains(line[i - 1]))
            i--;
        var suffix = line[i..];
        if (suffix.Contains('┬') || suffix.Contains('┼') || suffix.Contains('┴'))
            return (line[..i].TrimEnd(), suffix);
        return (line, null);
    }

    private static (List<List<string>> VisualRows, List<string> BorderBefore) VisualRowsWithBorders(List<string> block)
    {
        var visualRows = new List<List<string>>();
        var borderBefore = new List<string>();
        var cur = new List<string>();
        var lastBorder = string.Empty;

        foreach (var rawLine in block)
        {
            if (IsBorderRow(rawLine))
            {
                if (cur.Count > 0)
                {
                    visualRows.Add(cur);
                    borderBefore.Add(lastBorder);
                    cur = [];
                }
                lastBorder = rawLine;
                continue;
            }

            var (line, inlineBorder) = SplitInlineBorderSuffix(rawLine);
            if (inlineBorder is not null)
            {
                if (!string.IsNullOrWhiteSpace(line) && (line.Contains('│') || line.Contains('|')))
                    cur.Add(line);
                if (cur.Count > 0)
                {
                    visualRows.Add(cur);
                    borderBefore.Add(lastBorder);
                    cur = [];
                }
                lastBorder = inlineBorder;
            }
            else if (line.Contains('│') || line.Contains('|'))
            {
                cur.Add(line);
            }
        }

        if (cur.Count > 0)
        {
            visualRows.Add(cur);
            borderBefore.Add(lastBorder);
        }

        return (visualRows, borderBefore);
    }

    private static List<string> SplitRowCells(string line)
    {
        var parts = ColSepRegex().Split(line).ToList();
        if (parts.Count > 0 && string.IsNullOrWhiteSpace(parts[0]))
            parts.RemoveAt(0);
        if (parts.Count > 0 && string.IsNullOrWhiteSpace(parts[^1]))
            parts.RemoveAt(parts.Count - 1);
        return parts;
    }

    private static int GroupOwnNcol(List<string> group)
    {
        var max = 0;
        foreach (var ln in group)
        {
            var cnt = SplitRowCells(ln).Count;
            if (cnt > max)
                max = cnt;
        }
        return max;
    }

    private static List<string> MergeGroupToRow(List<string> group, int ncol)
    {
        var perLine = group.Select(SplitRowCells).ToList();
        var row = new List<string>(ncol);
        for (var j = 0; j < ncol; j++)
        {
            var parts = perLine.Select(lc => j < lc.Count ? lc[j] : string.Empty);
            row.Add(StripInnerBorderNoise(MergeCell(parts)));
        }
        return row;
    }

    private static List<List<string>> SplitBodyGroupIntoRows(List<string> group)
    {
        var rows = new List<List<string>>();
        foreach (var line in group)
        {
            var cells = SplitRowCells(line);
            var firstBlank = cells.Count == 0 || string.IsNullOrWhiteSpace(cells[0]);
            if (rows.Count > 0 && firstBlank)
                rows[^1].Add(line);
            else
                rows.Add([line]);
        }
        return rows;
    }

    private static List<int>? ColspansFromBorder(string border, int expectedTotal, int expectedCells)
    {
        var chars = border.Where(c => BorderRowChars.Contains(c) && c is not (' ' or '\u3000')).ToList();
        if (chars.Count < 2)
            return null;

        var segments = new List<List<char>> { new() };
        for (var i = 1; i < chars.Count - 1; i++)
        {
            var ch = chars[i];
            if (ch == '┼')
                segments.Add([]);
            else
                segments[^1].Add(ch);
        }

        var spans = segments.Select(seg => seg.Count(c => c == '┬') + 1).ToList();
        if (spans.Count == expectedCells && spans.Sum() == expectedTotal)
            return spans;

        var missing = expectedCells - spans.Count;
        if (missing > 0)
        {
            var padded = Enumerable.Repeat(1, missing).Concat(spans).ToList();
            if (padded.Count == expectedCells && padded.Sum() == expectedTotal)
                return padded;
        }

        return null;
    }

    private static (List<string> Stubs, List<string> Groups) CollectStubStackAndGroups(
        IEnumerable<List<string>> prefixVisualRows)
    {
        var stubs = new List<string>();
        var groups = new List<string>();

        foreach (var visualRow in prefixVisualRows)
        {
            foreach (var line in visualRow)
            {
                var cells = SplitRowCells(line).Select(StripInnerBorderNoise).ToList();
                if (cells.Count == 0)
                    continue;
                var rest = cells.Skip(1).ToList();
                if (rest.Any(r => r.Length > 0))
                {
                    if (cells[0].Length > 0)
                        stubs.Add(cells[0]);
                    if (groups.Count == 0)
                    {
                        groups.AddRange(rest);
                    }
                    else
                    {
                        for (var j = 0; j < rest.Count; j++)
                        {
                            var extra = rest[j];
                            if (extra.Length == 0)
                                continue;
                            if (j < groups.Count)
                                groups[j] = MergeCell([groups[j], extra]);
                            else
                                groups.Add(extra);
                        }
                    }
                }
                else if (cells[0].Length > 0)
                {
                    stubs.Add(cells[0]);
                }
            }
        }

        return (stubs, groups);
    }

    private static string MergeCell(IEnumerable<string> parts) =>
        WhitespaceRunRegex().Replace(string.Concat(parts), " ").Trim();

    private static string StripInnerBorderNoise(string text)
    {
        var cleaned = string.Concat(text.Where(ch => ch == '│' || !BoxChars.Contains(ch)));
        return WhitespaceRunRegex().Replace(cleaned, " ").Trim();
    }

    [GeneratedRegex(@"([┐┤┘])([│├└┌])")]
    private static partial Regex JoinedCornerRegex();

    [GeneratedRegex(@"([│┐┤┘])([├└┌])")]
    private static partial Regex JoinedBorderRegex();

    [GeneratedRegex(@"[│|]")]
    private static partial Regex ColSepRegex();

    [GeneratedRegex(@"[ \u3000\t]+")]
    private static partial Regex WhitespaceRunRegex();

    [GeneratedRegex(@"^\s*\|?(?:\s*:?-{2,}:?\s*\|)+\s*:?-{2,}:?\s*\|?\s*$")]
    private static partial Regex MarkdownSeparatorRegex();
}