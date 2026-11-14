using EnvCite.Platform.Web.Auth;

using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Components;

namespace EnvCite.Platform.Web.Components.Portal;

public sealed partial class Login(
    IPortalAccount account,
    IAuthorizationService authorization,
    IGoogleSignInAvailability googleSignIn)
{
    [SupplyParameterFromQuery] private string? ReturnUrl { get; set; }

    private IPortalAccount Account => account;

    private IGoogleSignInAvailability GoogleSignIn => googleSignIn;

    protected override async Task OnInitializedAsync()
    {
        // 路徑上的租戶不存在：這個頁面就不存在。
        if (account.CurrentTenant is not { } tenant)
        {
            Navigation.NotFound();
            return;
        }

        // 已登入、可以正常使用的成員（同一份檢查：本租戶的成員、沒有待辦的強制步驟）不該再看到登入表單：
        // 外殼已經是已登入的樣子，登入網址被收藏或從信裡點開時，表單卻要人再登入一次。
        // 還有強制步驟的人不在這裡轉：他們的頁面由各自的授權導向，這裡留著表單讓他們能換帳號。
        if ((await authorization.AuthorizeAsync(User, PlatformAuth.PortalPolicy)).Succeeded)
        {
            Navigation.NavigateTo(SignInRoutes.Portal(tenant.Identifier).SafeReturnUrl(ReturnUrl));
        }
    }
}