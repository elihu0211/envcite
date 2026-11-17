using EnvCite.Platform.Web.Presentation;

using Microsoft.AspNetCore.Components;

namespace EnvCite.Platform.Web.Components.Shared;

public sealed partial class FormattedAnswerView
{
    private CitationContext _citations = new([], default);

    [Parameter]
    public AnswerDocument? Document { get; set; }

    [Parameter]
    public IReadOnlyList<AnswerSource> Sources { get; set; } = [];

    [Parameter]
    public EventCallback<AnswerSource> OnCitationSelect { get; set; }

    protected override void OnParametersSet() => _citations = new CitationContext(Sources, OnCitationSelect);
}