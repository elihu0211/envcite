using System.Text;
using System.Text.RegularExpressions;

namespace EnvCite.Platform.Modules.Consult;

/// <summary>
/// 把推論的結果組成結論區塊，並核對模型的說明沒有越界。結論只由系統用固定格式輸出：每則結論所依的條文都在
/// 本次證據內（所以有證據代號，經得起同一套引用核驗），不在證據內的不列，只計數。
/// </summary>
internal static partial class ConclusionAssembly
{
    /// <summary>
    /// 結論區塊；沒有任何結論（事實沒觸及證據內的條文、或都不適用）時為 null。「不適用」不列：它只說這條規定
    /// 不及於本案，對讀者是雜訊，與沒有列出的結論一起計入 <see cref="ConclusionBlock.OmittedCount"/>。
    /// </summary>
    public static ConclusionBlock? Assemble(
        CaseConclusions conclusions, IReadOnlyList<CaseFact> facts, IReadOnlyList<AnswerSource> sources, string asOf, bool asOfAssumed)
    {
        var order = sources.Select((s, i) => (s.Code, Index: i)).ToDictionary(x => x.Code, x => x.Index, StringComparer.Ordinal);
        List<(StatementConclusion Conclusion, List<string> Codes)> kept = [];
        var omitted = 0;
        foreach (var conclusion in conclusions.Items)
        {
            List<string> codes = [.. sources.Where(s => Covers(s.Uri, conclusion.SourceUri)).Select(s => s.Code)];
            if (conclusion.Verdict == ConclusionVerdict.NotApplicable || codes.Count == 0)
            {
                omitted++;
                continue;
            }

            kept.Add((conclusion, codes));
        }

        if (kept.Count == 0)
            return null;

        // 依證據的次序列：讀者先看到第一條證據相關的結論；同一條證據內維持推論給的次序。
        var items = kept
            .Select((k, i) => (k.Conclusion, k.Codes, Position: i))
            .OrderBy(k => order[k.Codes[0]])
            .ThenBy(k => k.Position)
            .Select((k, i) => new Conclusion(i + 1, k.Conclusion.Verdict, k.Codes, k.Conclusion.MissingConcepts, k.Conclusion.NeedsAssessmentConcepts, k.Conclusion.Penalties))
            .ToList();
        IReadOnlyList<UsedFact> used = [.. facts.Select(f => new UsedFact(f.Concept, f.Name, f.Flag, f.Number, f.Quote))];
        return new ConclusionBlock(items, used, asOf, asOfAssumed, omitted);
    }

    /// <summary>證據是條（<c>.../art_7</c>），陳述可能在條底下的項款（<c>.../art_7/para_1</c>）：同一條或其下都算它涵蓋。</summary>
    internal static bool Covers(string evidenceIri, string statementIri) =>
        string.Equals(evidenceIri, statementIri, StringComparison.Ordinal)
        || statementIri.StartsWith(evidenceIri + "/", StringComparison.Ordinal);

    /// <summary>
    /// 給模型的結論段：只有編號、依據與欠缺的事實，不含任何結論用語。模型能解釋依據的條文與欠缺什麼，
    /// 但看不到「該當」「不該當」，也就沒有東西可以改寫。
    /// </summary>
    public static string PromptSection(ConclusionBlock block)
    {
        var sb = new StringBuilder("系統已用規則推論出下列結論，結論本身與用語由系統另行呈現：\n");
        foreach (var item in block.Items)
        {
            sb.Append("結論 ").Append(item.No).Append("：依據 ").Append(string.Concat(item.CitationCodes.Select(c => $"[{c}]")));
            if (item.MissingFacts.Count > 0)
                sb.Append("；欠缺事實 ").Append(string.Join('、', item.MissingFacts));
            if (item.NeedsAssessment.Count > 0)
                sb.Append("；有待主管機關判斷的要件 ").Append(string.Join('、', item.NeedsAssessment));
            if (item.Penalties.Count > 0)
                sb.Append("；另有候選罰則（額度由系統列出）");
            sb.Append('\n');
        }

        return sb.ToString();
    }

    /// <summary>模型說明裡的每一段文字，核對用。</summary>
    internal static IEnumerable<string> TextOf(AnswerBlock block)
    {
        switch (block)
        {
            case CalloutBlock callout:
                yield return callout.Title;
                yield return callout.Body;
                break;
            case ProseBlock prose:
                if (prose.Heading is not null)
                    yield return prose.Heading;
                yield return prose.Text;
                break;
            case ChecklistBlock checklist:
                if (checklist.Title is not null)
                    yield return checklist.Title;
                foreach (var item in checklist.Items)
                {
                    yield return item.Label;
                    if (item.Detail is not null)
                        yield return item.Detail;
                }

                break;
            case ComparisonTableBlock table:
                if (table.Title is not null)
                    yield return table.Title;
                foreach (var column in table.Columns)
                    yield return column;
                foreach (var cell in table.Rows.SelectMany(r => r.Cells))
                    yield return cell;
                break;
            case WorkflowBlock workflow:
                if (workflow.Title is not null)
                    yield return workflow.Title;
                foreach (var node in workflow.Nodes)
                    yield return node.Label;
                foreach (var label in workflow.Edges.Select(e => e.Label).OfType<string>())
                    yield return label;
                break;
        }
    }

    /// <summary>
    /// 說明有沒有越界：重述或改寫結論（用了結論用語），或引用不存在的結論編號。
    /// 「違反」常出現在法條自己的行文（「違反第七條規定者」），所以後面接條文、本法、規定的不算；
    /// 其餘（「已違反」「構成違反」）是對本案下結論，越界。
    /// </summary>
    public static bool Oversteps(IEnumerable<string> texts, ConclusionBlock block)
    {
        var numbers = block.Items.Select(i => i.No).ToHashSet();
        foreach (var text in texts)
        {
            if (ConclusionTerms.Forbidden.Where(w => w != "違反").Any(w => text.Contains(w, StringComparison.Ordinal))
                || CaseViolation().IsMatch(text))
            {
                return true;
            }

            foreach (Match match in ConclusionRef().Matches(text))
            {
                if (!int.TryParse(match.Groups[1].Value, out var no) || !numbers.Contains(no))
                    return true;
            }
        }

        return false;
    }

    [GeneratedRegex(@"違反(?![第本前各規依])")]
    private static partial Regex CaseViolation();

    [GeneratedRegex(@"結論\s*(\d+)")]
    private static partial Regex ConclusionRef();
}