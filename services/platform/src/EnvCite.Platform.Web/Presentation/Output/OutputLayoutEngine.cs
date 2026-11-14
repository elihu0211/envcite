using Markdig;

using Microsoft.Extensions.DependencyInjection.Extensions;

namespace EnvCite.Platform.Web.Presentation.Output;

/// <summary>
/// 基於 Markdig AST 與可擴充 <see cref="IOutputBlockConverter"/> 管線的無狀態輸出排版引擎。
/// 透過 <c>DisableHtml()</c> 關閉原始 HTML 解析，確保 AI 輸出絕對安全。
/// </summary>
public sealed class OutputLayoutEngine : IOutputLayoutEngine
{
    private readonly IReadOnlyList<IOutputBlockConverter> _converters;
    private readonly IOutputInlineParser _inlineParser;
    private readonly OutputLayoutOptions _options;
    private readonly MarkdownPipeline _pipeline;

    public OutputLayoutEngine(
        IEnumerable<IOutputBlockConverter> converters,
        IOutputInlineParser inlineParser,
        IOptions<OutputLayoutOptions> options)
    {
        _converters = [.. converters.OrderBy(static c => c.Order)];
        _inlineParser = inlineParser;
        _options = options.Value;
        _pipeline = BuildPipeline(_options);
    }

    public OutputDocument Parse(string? content)
    {
        if (string.IsNullOrWhiteSpace(content))
            return OutputDocument.Empty;

        var normalized = content.Length > _options.MaxDocumentLength
            ? content[.._options.MaxDocumentLength]
            : content;
        normalized = normalized.Replace("\r\n", "\n", StringComparison.Ordinal).Replace('\r', '\n');

        var markdown = Markdown.Parse(normalized, _pipeline);
        var context = new OutputLayoutContext(_options, _inlineParser, _converters);
        var blocks = new List<OutputBlock>();
        foreach (var block in markdown)
            context.ConvertBlock(block, blocks);

        return blocks.Count == 0 ? OutputDocument.Empty : new OutputDocument(blocks);
    }

    private static MarkdownPipeline BuildPipeline(OutputLayoutOptions options)
    {
        var builder = new MarkdownPipelineBuilder()
            .DisableHtml()
            .UseTaskLists()
            .UseAutoLinks();

        if (options.EnableTables)
            builder.UsePipeTables().UseGridTables();

        return builder.Build();
    }
}

/// <summary>輸出排版引擎的依賴注入註冊擴充。</summary>
public static class OutputLayoutServiceCollectionExtensions
{
    /// <summary>
    /// 註冊 <see cref="IOutputLayoutEngine"/>、預設轉換器管線與 <see cref="OutputLayoutOptions"/>。
    /// 所有元件皆為無狀態單例，可在互動式 Blazor circuit 與靜態頁面安全共用。
    /// </summary>
    public static IServiceCollection AddOutputLayoutEngine(
        this IServiceCollection services,
        Action<OutputLayoutOptions>? configure = null)
    {
        services.AddOptions<OutputLayoutOptions>();
        if (configure is not null)
            services.Configure(configure);

        services.TryAddSingleton<IOutputInlineParser, OutputInlineParser>();
        services.TryAddEnumerable(ServiceDescriptor.Singleton<IOutputBlockConverter, MermaidDiagramBlockConverter>());
        services.TryAddEnumerable(ServiceDescriptor.Singleton<IOutputBlockConverter, CodeBlockConverter>());
        services.TryAddEnumerable(ServiceDescriptor.Singleton<IOutputBlockConverter, MarkdigTableBlockConverter>());
        services.TryAddEnumerable(ServiceDescriptor.Singleton<IOutputBlockConverter, HeadingBlockConverter>());
        services.TryAddEnumerable(ServiceDescriptor.Singleton<IOutputBlockConverter, ListBlockConverter>());
        services.TryAddEnumerable(ServiceDescriptor.Singleton<IOutputBlockConverter, QuoteBlockConverter>());
        services.TryAddEnumerable(ServiceDescriptor.Singleton<IOutputBlockConverter, ThematicBreakBlockConverter>());
        services.TryAddEnumerable(ServiceDescriptor.Singleton<IOutputBlockConverter, BoxDrawingParagraphBlockConverter>());
        services.TryAddEnumerable(ServiceDescriptor.Singleton<IOutputBlockConverter, ParagraphBlockConverter>());
        services.TryAddSingleton<IOutputLayoutEngine, OutputLayoutEngine>();
        return services;
    }
}