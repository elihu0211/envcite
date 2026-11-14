using Markdig.Syntax;
using Markdig.Syntax.Inlines;

namespace EnvCite.Platform.Web.Presentation.Output;

/// <summary>AI 回答排版引擎的可調組態。</summary>
public sealed class OutputLayoutOptions
{
    /// <summary>是否將 <c>```mermaid</c> 程式碼區塊升格為結構化圖表區塊。</summary>
    public bool EnableMermaidDiagrams { get; set; } = true;

    /// <summary>是否解析 Markdown 管線／格線表格。</summary>
    public bool EnableTables { get; set; } = true;

    /// <summary>是否同時將段落中的傳統全形框線表格（┌┬┐─│）轉換為結構化表格。</summary>
    public bool EnableBoxDrawingTables { get; set; } = true;

    /// <summary>是否將正文及表格內出現的 <c>[L1]</c> 等代號轉換為可點擊的法源引用節點。</summary>
    public bool EnableInlineCitations { get; set; } = true;

    /// <summary>單篇輸入文本的最大解析字元上限，防止異常輸入耗盡計算資源。</summary>
    public int MaxDocumentLength { get; set; } = 128_000;
}

/// <summary>輸出排版引擎契約：將模型回答或歷史內容轉換為不可變、無原始 HTML 注入風險的版面區塊樹。</summary>
public interface IOutputLayoutEngine
{
    /// <summary>解析輸入內容並透過已註冊的轉換器管線建立結構化文件。</summary>
    OutputDocument Parse(string? content);
}

/// <summary>行內語法與 <c>[Ln]</c> 引用代號解析器契約。</summary>
public interface IOutputInlineParser
{
    /// <summary>將 Markdig 行內語法樹轉換為安全的行內節點序列。</summary>
    IReadOnlyList<OutputInline> ParseMarkdigInlines(ContainerInline? container, OutputLayoutOptions options);

    /// <summary>解析純文字（如框線表格儲存格）並抽出 <c>[L1]</c> 引用代號。</summary>
    IReadOnlyList<OutputInline> ParsePlainText(string? text, OutputLayoutOptions options);
}

/// <summary>可擴充的區塊轉換器介面（依 <see cref="Order"/> 由小到大依序比對 Markdig 區塊）。</summary>
public interface IOutputBlockConverter
{
    /// <summary>轉換器優先序（數值較小者優先判定）。</summary>
    int Order { get; }

    /// <summary>判斷此轉換器是否處理給定的 Markdig 區塊。</summary>
    bool CanConvert(Block block, OutputLayoutContext context);

    /// <summary>將 Markdig 區塊轉換為零至多個 <see cref="OutputBlock"/> 加入目標清單。</summary>
    void Convert(Block block, OutputLayoutContext context, IList<OutputBlock> target);
}

/// <summary>單次文件轉換期間傳遞給各轉換器的共用語境，支援容器區塊遞迴委派。</summary>
public sealed class OutputLayoutContext
{
    private readonly IReadOnlyList<IOutputBlockConverter> _converters;

    internal OutputLayoutContext(
        OutputLayoutOptions options,
        IOutputInlineParser inlines,
        IReadOnlyList<IOutputBlockConverter> converters)
    {
        Options = options;
        Inlines = inlines;
        _converters = converters;
    }

    public OutputLayoutOptions Options { get; }

    public IOutputInlineParser Inlines { get; }

    /// <summary>將容器內的子區塊依序送回完整轉換器管線處理。</summary>
    public IReadOnlyList<OutputBlock> ConvertChildren(ContainerBlock container)
    {
        var blocks = new List<OutputBlock>();
        foreach (var child in container)
            ConvertBlock(child, blocks);
        return blocks;
    }

    internal void ConvertBlock(Block block, IList<OutputBlock> target)
    {
        foreach (var converter in _converters)
        {
            if (converter.CanConvert(block, this))
            {
                converter.Convert(block, this, target);
                return;
            }
        }
    }
}