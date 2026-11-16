using System.ComponentModel;
using System.Globalization;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.Json.Serialization.Metadata;

namespace EnvCite.Platform.SharedKernel;

/// <summary>強型別回答文件與 LLM 結構化輸出的共用 JSON 序列化設定。</summary>
public static class AnswerJson
{
    public const short CurrentSchemaVersion = 1;

    public static JsonSerializerOptions Options { get; } = CreateOptions();

    private static JsonSerializerOptions CreateOptions()
    {
        var options = new JsonSerializerOptions(JsonSerializerDefaults.Web)
        {
            AllowOutOfOrderMetadataProperties = true,
            Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
            TypeInfoResolver = new DefaultJsonTypeInfoResolver(),
        };
        options.Converters.Add(new JsonStringEnumConverter(JsonNamingPolicy.SnakeCaseLower, allowIntegerValues: false));
        options.MakeReadOnly();
        return options;
    }
}

/// <summary>模型結構化輸出的頂層契約，經 <c>GetResponseAsync&lt;ModelAnswerOutput&gt;</c> 產出並由後端核驗。</summary>
public sealed record ModelAnswerOutput(
    [property: Description("若提供的證據不足以回答問題，設為 true 並回傳空的 blocks；否則設為 false。")]
    bool InsufficientEvidence,
    [property: Description("由領域積木組成的完整回答；證據不足時為空陣列。")]
    IReadOnlyList<AnswerBlock> Blocks);

/// <summary>通過後端引用與結構核驗的強型別回答文件（持久化於 DB 並直接驅動前端渲染）。</summary>
public sealed record AnswerDocument(IReadOnlyList<AnswerBlock> Blocks);

[JsonPolymorphic(TypeDiscriminatorPropertyName = "kind")]
[JsonDerivedType(typeof(CalloutBlock), "callout")]
[JsonDerivedType(typeof(ProseBlock), "prose")]
[JsonDerivedType(typeof(ChecklistBlock), "checklist")]
[JsonDerivedType(typeof(ComparisonTableBlock), "table")]
[JsonDerivedType(typeof(WorkflowBlock), "workflow")]
public abstract record AnswerBlock;

public enum CalloutTone
{
    Summary,
    Requirement,
    Caution,
}

/// <summary>重點摘要、法定要求或合規警示卡片。</summary>
public sealed record CalloutBlock(
    [property: Description("提示語氣：summary（結論摘要）、requirement（核心法定義務）、caution（罰則或合規注意）。")]
    CalloutTone Tone,
    [property: Description("簡短標題。")]
    string Title,
    [property: Description("精簡直接的說明內容，不含 [L1] 代號。")]
    string Body,
    [property: Description("支撐此區塊的證據代號陣列，例如 [\"L1\"]。")]
    IReadOnlyList<string> CitationCodes) : AnswerBlock;

/// <summary>一般法規說明段落（可帶小標）。</summary>
public sealed record ProseBlock(
    [property: Description("選用的段落小標；不需要時為 null。")]
    string? Heading,
    [property: Description("純文字說明內容，不含 Markdown 標記與 [L1] 代號。")]
    string Text,
    [property: Description("支撐此段落的證據代號陣列，例如 [\"L1\"]。")]
    IReadOnlyList<string> CitationCodes) : AnswerBlock;

/// <summary>清單中的單一項目。</summary>
public sealed record ChecklistItem(
    [property: Description("條列要點或檢核項目名稱。")]
    string Label,
    [property: Description("選用的補充說明；不需要時為 null。")]
    string? Detail,
    [property: Description("是否表示核取／強制要求狀態；一般條列為 null，檢核項可用 true/false。")]
    bool? Checked,
    [property: Description("支撐此項目的證據代號陣列，例如 [\"L1\"]。")]
    IReadOnlyList<string> CitationCodes);

/// <summary>法定義務、應備文件、條件要點或有序步驟清單。</summary>
public sealed record ChecklistBlock(
    [property: Description("選用的清單標題；不需要時為 null。")]
    string? Title,
    [property: Description("true 為有序編號清單，false 為項目符號或檢核清單。")]
    bool Ordered,
    [property: Description("清單項目陣列，至少一筆。")]
    IReadOnlyList<ChecklistItem> Items) : AnswerBlock;

/// <summary>對照表中的單一資料列。</summary>
public sealed record ComparisonRow(
    [property: Description("對應 columns 順序的儲存格純文字陣列，長度須與 columns 一致。")]
    IReadOnlyList<string> Cells,
    [property: Description("支撐此列的證據代號陣列，例如 [\"L1\"]。")]
    IReadOnlyList<string> CitationCodes);

/// <summary>多條件對照、分級數值、管制標準或罰則比較表。</summary>
public sealed record ComparisonTableBlock(
    [property: Description("選用的表格標題；不需要時為 null。")]
    string? Title,
    [property: Description("欄位名稱陣列（不含法源依據欄，系統會依各列 citationCodes 自動呈現依據欄）。")]
    IReadOnlyList<string> Columns,
    [property: Description("資料列陣列，至少一筆。")]
    IReadOnlyList<ComparisonRow> Rows) : AnswerBlock;

public enum WorkflowDirection
{
    TopDown,
    LeftRight,
}

public enum WorkflowNodeKind
{
    Start,
    Step,
    Decision,
    End,
}

/// <summary>申報、許可、審查、改善或處分流程中的單一節點。</summary>
public sealed record WorkflowNode(
    [property: Description("流程圖內的唯一節點識別碼，例如 n1、n2。")]
    string Id,
    [property: Description("節點顯示文字。")]
    string Label,
    [property: Description("節點類型：start、step、decision、end。")]
    WorkflowNodeKind Kind,
    [property: Description("支撐此節點的證據代號陣列，例如 [\"L1\"]。")]
    IReadOnlyList<string> CitationCodes);

/// <summary>流程節點之間的有向連線。</summary>
public sealed record WorkflowEdge(
    [property: Description("起點節點 Id。")]
    string From,
    [property: Description("終點節點 Id。")]
    string To,
    [property: Description("選用的條件或連線標籤，例如「符合」「逾期」；不需要時為 null。")]
    string? Label = null);

/// <summary>結構化流程圖積木；後端核驗節點與連線後，確定性轉換為安全圖表與步驟說明。</summary>
public sealed record WorkflowBlock(
    [property: Description("選用的流程圖標題；不需要時為 null。")]
    string? Title,
    [property: Description("流程方向：top_down 或 left_right。")]
    WorkflowDirection Direction,
    [property: Description("流程節點陣列，至少兩個節點。")]
    IReadOnlyList<WorkflowNode> Nodes,
    [property: Description("流程連線陣列。")]
    IReadOnlyList<WorkflowEdge> Edges) : AnswerBlock;

/// <summary>將已核驗的 <see cref="AnswerDocument"/> 確定性轉為對話前文、複製文字與安全 Mermaid 原始碼。</summary>
public static class AnswerDocumentFormatter
{
    /// <summary>抽出文件內出現的所有不重複引用代號（保留首次出現順序）。</summary>
    public static IReadOnlyList<string> CollectCitationCodes(AnswerDocument document)
    {
        List<string> ordered = [];
        HashSet<string> seen = new(StringComparer.Ordinal);

        void AddRange(IEnumerable<string> codes)
        {
            foreach (var code in codes)
            {
                if (seen.Add(code))
                    ordered.Add(code);
            }
        }

        foreach (var block in document.Blocks)
        {
            switch (block)
            {
                case CalloutBlock callout:
                    AddRange(callout.CitationCodes);
                    break;
                case ProseBlock prose:
                    AddRange(prose.CitationCodes);
                    break;
                case ChecklistBlock checklist:
                    foreach (var item in checklist.Items)
                        AddRange(item.CitationCodes);
                    break;
                case ComparisonTableBlock table:
                    foreach (var row in table.Rows)
                        AddRange(row.CitationCodes);
                    break;
                case WorkflowBlock workflow:
                    foreach (var node in workflow.Nodes)
                        AddRange(node.CitationCodes);
                    break;
            }
        }

        return ordered;
    }

    /// <summary>轉為多輪追問前文用的純文字，將代號代換為正式法規名稱。</summary>
    public static string ToMemoryText(AnswerDocument document, IReadOnlyList<ConversationCitation> citations)
    {
        var map = citations.ToDictionary(c => c.Code, c => c.Label, StringComparer.OrdinalIgnoreCase);
        return Format(document, codes => FormatLabels(codes, map));
    }

    /// <summary>轉為使用者「複製回答」用的可讀純文字（保留 <c>[L#]</c> 代號）。</summary>
    public static string ToCopyText(AnswerDocument document) =>
        Format(document, FormatBracketedCodes);

    /// <summary>由已核驗的 <see cref="WorkflowBlock"/> 確定性組出安全的 Mermaid flowchart DSL，不允許任何指令注入。</summary>
    public static string ToMermaidSource(WorkflowBlock workflow)
    {
        var sb = new StringBuilder();
        sb.Append(workflow.Direction == WorkflowDirection.LeftRight ? "flowchart LR" : "flowchart TD").Append('\n');

        // 映射為連續安全識別碼 S0, S1…，避開 Mermaid 保留字（如 end、class、style）。
        Dictionary<string, string> idMap = new(StringComparer.Ordinal);
        for (var i = 0; i < workflow.Nodes.Count; i++)
        {
            var node = workflow.Nodes[i];
            var safeId = $"S{i}";
            idMap.TryAdd(node.Id, safeId);
            var label = EscapeMermaidLabel(node.Label);
            sb.Append("  ").Append(safeId).Append(node.Kind switch
            {
                WorkflowNodeKind.Start or WorkflowNodeKind.End => $"([\"{label}\"])",
                WorkflowNodeKind.Decision => $"{{\"{label}\"}}",
                _ => $"[\"{label}\"]",
            }).Append('\n');
        }

        foreach (var edge in workflow.Edges)
        {
            if (!idMap.TryGetValue(edge.From, out var from) || !idMap.TryGetValue(edge.To, out var to))
                continue;

            sb.Append("  ").Append(from);
            if (string.IsNullOrWhiteSpace(edge.Label))
                sb.Append(" --> ");
            else
                sb.Append(" -->|").Append(EscapeMermaidEdgeLabel(edge.Label)).Append("| ");
            sb.Append(to).Append('\n');
        }

        return sb.ToString().TrimEnd();
    }

    private static string Format(AnswerDocument document, Func<IReadOnlyList<string>, string> formatCitations)
    {
        var sb = new StringBuilder();
        foreach (var block in document.Blocks)
        {
            if (sb.Length > 0)
                sb.Append("\n\n");

            switch (block)
            {
                case CalloutBlock callout:
                    sb.Append(callout.Title).Append('：').Append(callout.Body);
                    AppendSuffix(sb, formatCitations(callout.CitationCodes));
                    break;
                case ProseBlock prose:
                    if (!string.IsNullOrWhiteSpace(prose.Heading))
                        sb.Append(prose.Heading).Append('\n');
                    sb.Append(prose.Text);
                    AppendSuffix(sb, formatCitations(prose.CitationCodes));
                    break;
                case ChecklistBlock checklist:
                    if (!string.IsNullOrWhiteSpace(checklist.Title))
                        sb.Append(checklist.Title).Append('\n');
                    for (var i = 0; i < checklist.Items.Count; i++)
                    {
                        if (i > 0)
                            sb.Append('\n');
                        var item = checklist.Items[i];
                        var prefix = checklist.Ordered
                            ? $"{i + 1}. "
                            : item.Checked switch { true => "[x] ", false => "[ ] ", null => "- " };
                        sb.Append(prefix).Append(item.Label);
                        if (!string.IsNullOrWhiteSpace(item.Detail))
                            sb.Append('：').Append(item.Detail);
                        AppendSuffix(sb, formatCitations(item.CitationCodes));
                    }

                    break;
                case ComparisonTableBlock table:
                    if (!string.IsNullOrWhiteSpace(table.Title))
                        sb.Append(table.Title).Append('\n');
                    sb.Append("| ").Append(string.Join(" | ", table.Columns)).Append(" | 依據 |\n");
                    sb.Append("| ").Append(string.Join(" | ", table.Columns.Select(_ => "---"))).Append(" | --- |");
                    foreach (var row in table.Rows)
                    {
                        sb.Append('\n').Append("| ").Append(string.Join(" | ", row.Cells))
                            .Append(" | ").Append(formatCitations(row.CitationCodes)).Append(" |");
                    }

                    break;
                case WorkflowBlock workflow:
                    if (!string.IsNullOrWhiteSpace(workflow.Title))
                        sb.Append(workflow.Title).Append('\n');
                    for (var i = 0; i < workflow.Nodes.Count; i++)
                    {
                        if (i > 0)
                            sb.Append('\n');
                        var node = workflow.Nodes[i];
                        sb.Append(CultureInfo.InvariantCulture, $"{i + 1}. ").Append(node.Label);
                        AppendSuffix(sb, formatCitations(node.CitationCodes));
                    }

                    break;
            }
        }

        return sb.ToString();
    }

    private static void AppendSuffix(StringBuilder sb, string suffix)
    {
        if (suffix.Length > 0)
            sb.Append(' ').Append(suffix);
    }

    private static string FormatBracketedCodes(IReadOnlyList<string> codes) =>
        codes.Count == 0 ? string.Empty : string.Concat(codes.Select(c => $"[{c}]"));

    private static string FormatLabels(IReadOnlyList<string> codes, Dictionary<string, string> map)
    {
        List<string> labels = [];
        foreach (var code in codes)
        {
            if (map.TryGetValue(code, out var label))
                labels.Add($"（{label}）");
        }

        return string.Concat(labels);
    }

    private static string EscapeMermaidLabel(string value) =>
        value
            .Replace("\r\n", " ", StringComparison.Ordinal)
            .Replace('\r', ' ')
            .Replace('\n', ' ')
            .Replace('\\', '＼')
            .Replace('"', '\'')
            .Replace('<', '＜')
            .Replace('>', '＞')
            .Replace('#', '＃')
            .Replace(';', '；')
            .Trim();

    private static string EscapeMermaidEdgeLabel(string value) =>
        EscapeMermaidLabel(value)
            .Replace('|', '/')
            .Replace('[', '［')
            .Replace(']', '］')
            .Replace('{', '｛')
            .Replace('}', '｝');
}