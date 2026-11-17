using System.Diagnostics.CodeAnalysis;

using Microsoft.AspNetCore.Components;

namespace EnvCite.Platform.Web.Presentation;

/// <summary>
/// 一份回答的引用查找：代號對應到哪一條法源，以及點下代號時要做什麼（開條文抽屜）。
/// 由 <c>FormattedAnswerView</c> 以 CascadingValue 提供，各積木只認這個型別，不認上層元件與它的參數。
/// 沒有選取回呼時（只讀的呈現）代號不可點。
/// </summary>
public sealed class CitationContext(IReadOnlyList<AnswerSource> sources, EventCallback<AnswerSource> onSelect)
{
    private readonly Dictionary<string, AnswerSource> _byCode = Index(sources);

    public bool TryResolve(string code, [NotNullWhen(true)] out AnswerSource? source)
    {
        source = null;
        return onSelect.HasDelegate && _byCode.TryGetValue(code, out source);
    }

    public Task SelectAsync(AnswerSource source) => onSelect.InvokeAsync(source);

    private static Dictionary<string, AnswerSource> Index(IReadOnlyList<AnswerSource> sources)
    {
        var map = new Dictionary<string, AnswerSource>(StringComparer.OrdinalIgnoreCase);
        foreach (var source in sources)
        {
            map.TryAdd(source.Code, source);
        }

        return map;
    }
}