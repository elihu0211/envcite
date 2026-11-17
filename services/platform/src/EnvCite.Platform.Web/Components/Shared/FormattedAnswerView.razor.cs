using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;

using MudBlazor;

namespace EnvCite.Platform.Web.Components.Shared;

public sealed partial class FormattedAnswerView
{
    private IReadOnlyList<AnswerSource>? _cachedSources;
    private Dictionary<string, AnswerSource>? _lookup;

    [Parameter]
    public AnswerDocument? Document { get; set; }

    [Parameter]
    public IReadOnlyList<AnswerSource> Sources { get; set; } = [];

    [Parameter]
    public EventCallback<AnswerSource> OnCitationSelect { get; set; }

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

    private static string CalloutToneClass(CalloutTone tone) => tone switch
    {
        CalloutTone.Requirement => "ep-ans-callout-requirement",
        CalloutTone.Caution => "ep-ans-callout-caution",
        _ => "ep-ans-callout-summary",
    };

    // 欄位由資料宣告：標題與儲存格怎麼畫交給共用的 DataTable；第一欄是列首，最後一欄是依據。
    private List<ColumnDef<ComparisonRow>> ColumnsOf(ComparisonTableBlock table)
    {
        var columns = table.Columns
            .Select((title, index) => new ColumnDef<ComparisonRow>
            {
                Title = title,
                Labeled = index != 0,
                Class = index == 0 ? "ep-keep" : null,
                Cell = row => builder => builder.AddContent(0, index < row.Cells.Count ? row.Cells[index] : null),
            })
            .ToList();
        columns.Add(new ColumnDef<ComparisonRow>
        {
            Title = L["依據"],
            Cell = row => RenderCitations(row.CitationCodes),
        });
        return columns;
    }

    private static string CalloutToneIcon(CalloutTone tone) => tone switch
    {
        CalloutTone.Requirement => Icons.Material.Outlined.Gavel,
        CalloutTone.Caution => Icons.Material.Outlined.WarningAmber,
        _ => Icons.Material.Outlined.AutoAwesome,
    };

    private string CalloutToneLabel(CalloutTone tone) => tone switch
    {
        CalloutTone.Requirement => L["核心義務"],
        CalloutTone.Caution => L["合規提醒"],
        _ => L["結論摘要"],
    };
}