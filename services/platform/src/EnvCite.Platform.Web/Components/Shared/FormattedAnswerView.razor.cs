using Microsoft.AspNetCore.Components;

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

    private string CalloutToneLabel(CalloutTone tone) => tone switch
    {
        CalloutTone.Requirement => L["核心義務"],
        CalloutTone.Caution => L["合規提醒"],
        _ => L["結論摘要"],
    };
}