using System.Text.RegularExpressions;

namespace EnvCite.Platform.Web.Presentation;

public enum ArticleClauseKind
{
    Paragraph = 0,
    Subparagraph = 1,
    Item = 2,
    SubItem = 3,
}

public sealed record ArticleClauseNode(
    int Level,
    ArticleClauseKind Kind,
    string? Marker,
    string Body,
    bool StartsProviso);

/// <summary>
/// 法規條文結構化排版模板引擎：將純文字條文依台灣法制體例（項／款／目／細目與但書）
/// 解析為具階層語義的節點序列，供抽屜與條文檢視模板元件以懸掛縮排呈現。
/// </summary>
public static partial class LegalArticleFormatter
{
    [GeneratedRegex(@"^([一二三四五六七八九十百千]+、)\s*(.+)$")]
    private static partial Regex SubparagraphRegex();

    [GeneratedRegex(@"^([（(][一二三四五六七八九十百千]+[）)])\s*(.+)$")]
    private static partial Regex ItemRegex();

    [GeneratedRegex(@"^(\d+\.|[（(]\d+[）)])\s*(.+)$")]
    private static partial Regex SubItemRegex();

    public static IReadOnlyList<ArticleClauseNode> Parse(string? content)
    {
        if (string.IsNullOrWhiteSpace(content))
            return [];

        var lines = content.Replace("\r\n", "\n", StringComparison.Ordinal)
            .Replace('\r', '\n')
            .Split('\n', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);

        var nodes = new List<ArticleClauseNode>(lines.Length);
        foreach (var line in lines)
        {
            if (SubparagraphRegex().Match(line) is { Success: true } sub)
            {
                nodes.Add(new ArticleClauseNode(1, ArticleClauseKind.Subparagraph, sub.Groups[1].Value, sub.Groups[2].Value, false));
            }
            else if (ItemRegex().Match(line) is { Success: true } item)
            {
                nodes.Add(new ArticleClauseNode(2, ArticleClauseKind.Item, item.Groups[1].Value, item.Groups[2].Value, false));
            }
            else if (SubItemRegex().Match(line) is { Success: true } subItem)
            {
                nodes.Add(new ArticleClauseNode(3, ArticleClauseKind.SubItem, subItem.Groups[1].Value, subItem.Groups[2].Value, false));
            }
            else
            {
                var startsProviso = line.StartsWith('但');
                nodes.Add(new ArticleClauseNode(0, ArticleClauseKind.Paragraph, null, line, startsProviso));
            }
        }

        return nodes;
    }
}