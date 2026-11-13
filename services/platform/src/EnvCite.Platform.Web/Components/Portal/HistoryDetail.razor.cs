using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Http;

namespace EnvCite.Platform.Web.Components.Portal;

public partial class HistoryDetail(IConversationHistory store)
{
    [Parameter] public Guid Id { get; set; }

    private ConversationDetail? _conversation;

    protected override async Task OnInitializedAsync() =>
        _conversation = await store.GetAsync(HttpContext.User, Id, HttpContext.RequestAborted);
}