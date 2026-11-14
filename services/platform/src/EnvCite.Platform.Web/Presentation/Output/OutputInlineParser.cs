using System.Text.RegularExpressions;

using Markdig.Extensions.TaskLists;
using Markdig.Syntax.Inlines;

namespace EnvCite.Platform.Web.Presentation.Output;

/// <summary>預設行內語法解析器：支援 Markdown 行內格式、連結白名單過濾與 <c>[L1]</c> 引用代號萃取。</summary>
public sealed partial class OutputInlineParser : IOutputInlineParser
{
    public IReadOnlyList<OutputInline> ParseMarkdigInlines(ContainerInline? container, OutputLayoutOptions options)
    {
        if (container is null)
            return [];

        var result = new List<OutputInline>();
        AppendContainer(container, options, result);
        return result;
    }

    public IReadOnlyList<OutputInline> ParsePlainText(string? text, OutputLayoutOptions options)
    {
        if (string.IsNullOrEmpty(text))
            return [];

        var result = new List<OutputInline>();
        AppendTextWithCitations(text, options, result);
        return result;
    }

    private static void AppendContainer(ContainerInline container, OutputLayoutOptions options, IList<OutputInline> target)
    {
        // Markdig 會將獨立的 `[L1]` 拆成 `[` 與 `L1]` 兩個相鄰 LiteralInline（未閉合的連結前綴），
        // 合併相鄰純文字後再萃取 [Ln] 引用代號才能完整命中。
        var textBuffer = new System.Text.StringBuilder();

        void FlushText()
        {
            if (textBuffer.Length == 0)
                return;
            AppendTextWithCitations(textBuffer.ToString(), options, target);
            textBuffer.Clear();
        }

        foreach (var inline in container)
        {
            if (inline is LiteralInline literal)
            {
                textBuffer.Append(literal.Content.AsSpan());
                continue;
            }

            FlushText();
            AppendInline(inline, options, target);
        }

        FlushText();
    }

    private static void AppendInline(Inline inline, OutputLayoutOptions options, IList<OutputInline> target)
    {
        switch (inline)
        {
            case TaskList:
                // 核取方塊狀態已由 ListBlockConverter 提升為 OutputListItem.IsChecked。
                break;

            case LiteralInline literal:
                AppendTextWithCitations(literal.Content.ToString(), options, target);
                break;

            case LineBreakInline:
                target.Add(new OutputLineBreakInline());
                break;

            case CodeInline code:
                target.Add(new OutputCodeInline(code.Content));
                break;

            case EmphasisInline emphasis:
                {
                    var children = new List<OutputInline>();
                    AppendContainer(emphasis, options, children);
                    if (children.Count == 0)
                        break;

                    target.Add(emphasis.DelimiterCount >= 2
                        ? new OutputStrongInline(children)
                        : new OutputEmphasisInline(children));
                    break;
                }

            case LinkInline link:
                {
                    var children = new List<OutputInline>();
                    AppendContainer(link, options, children);
                    if (children.Count == 0 && !string.IsNullOrEmpty(link.Url))
                        AppendTextWithCitations(link.Url, options, children);

                    var safeUrl = TrySanitizeUrl(link.Url);
                    if (safeUrl is not null && !link.IsImage)
                    {
                        target.Add(new OutputLinkInline(safeUrl, children));
                    }
                    else
                    {
                        foreach (var child in children)
                            target.Add(child);
                    }
                    break;
                }

            case AutolinkInline autolink:
                {
                    var safeUrl = TrySanitizeUrl(autolink.Url);
                    var label = new OutputInline[] { new OutputTextInline(autolink.Url) };
                    if (safeUrl is not null)
                        target.Add(new OutputLinkInline(safeUrl, label));
                    else
                        target.Add(label[0]);
                    break;
                }

            case HtmlInline html:
                // .DisableHtml() 下理論上不產生 HtmlInline，防禦性轉為純文字以杜絕 XSS。
                AppendTextWithCitations(html.Tag, options, target);
                break;

            case ContainerInline nested:
                AppendContainer(nested, options, target);
                break;

            case LeafInline leaf:
                AppendTextWithCitations(leaf.ToString() ?? string.Empty, options, target);
                break;
        }
    }

    private static void AppendTextWithCitations(string text, OutputLayoutOptions options, IList<OutputInline> target)
    {
        if (text.Length == 0)
            return;

        if (!options.EnableInlineCitations)
        {
            target.Add(new OutputTextInline(text));
            return;
        }

        var cursor = 0;
        foreach (Match match in CitationCodeRegex().Matches(text))
        {
            if (match.Index > cursor)
                target.Add(new OutputTextInline(text[cursor..match.Index]));

            target.Add(new OutputCitationInline(match.Groups[1].Value));
            cursor = match.Index + match.Length;
        }

        if (cursor < text.Length)
            target.Add(new OutputTextInline(text[cursor..]));
    }

    internal static string? TrySanitizeUrl(string? raw)
    {
        if (string.IsNullOrWhiteSpace(raw))
            return null;

        var trimmed = raw.Trim();
        if (trimmed.StartsWith('/') && !trimmed.StartsWith("//", StringComparison.Ordinal))
            return trimmed;

        if (Uri.TryCreate(trimmed, UriKind.Absolute, out var uri)
            && (uri.Scheme.Equals(Uri.UriSchemeHttp, StringComparison.OrdinalIgnoreCase)
                || uri.Scheme.Equals(Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase)))
        {
            return uri.AbsoluteUri;
        }

        return null;
    }

    [GeneratedRegex(@"\[(L\d+)\]", RegexOptions.None, matchTimeoutMilliseconds: 1000)]
    private static partial Regex CitationCodeRegex();
}