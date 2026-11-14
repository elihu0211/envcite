using EnvCite.Platform.Web.Presentation.Output;

using Microsoft.AspNetCore.Components;

namespace EnvCite.Platform.Web.Components.Shared;

public sealed partial class FormattedAnswerView(IOutputLayoutEngine layoutEngine)
{
    private IReadOnlyList<AnswerSource>? _cachedSources;
    private Dictionary<string, AnswerSource>? _lookup;

    [Parameter]
    public string? Content { get; set; }

    [Parameter]
    public IReadOnlyList<AnswerSource> Sources { get; set; } = [];

    [Parameter]
    public EventCallback<AnswerSource> OnCitationSelect { get; set; }

    private IOutputLayoutEngine LayoutEngine => layoutEngine;

    private IReadOnlyDictionary<string, AnswerSource> SourceLookup =>
        _cachedSources == Sources && _lookup is not null
            ? _lookup
            : BuildLookup();

    private Dictionary<string, AnswerSource> BuildLookup()
    {
        _cachedSources = Sources;
        var map = new Dictionary<string, AnswerSource>(StringComparer.OrdinalIgnoreCase);
        foreach (var source in Sources)
        {
            map.TryAdd(source.Code, source);
        }

        _lookup = map;
        return map;
    }

    private RenderFragment RenderHeading(OutputHeadingBlock heading) => builder =>
    {
        // 聊天泡泡內不搶頁面 <h1> 主標題，標題層級對應為 h2–h4。
        var tag = Math.Clamp(heading.Level + 1, 2, 4) switch
        {
            2 => "h2",
            3 => "h3",
            _ => "h4",
        };
        builder.OpenElement(0, tag);
        builder.AddAttribute(1, "class", "ep-md-heading");
        builder.AddContent(2, RenderInlines(heading.Inlines));
        builder.CloseElement();
    };

    private static string? AlignmentClass(OutputCellAlignment alignment) => alignment switch
    {
        OutputCellAlignment.Center => "ep-align-center",
        OutputCellAlignment.Right => "ep-align-right",
        _ => null,
    };
}