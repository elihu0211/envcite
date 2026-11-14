namespace EnvCite.Platform.Web.Components.Portal;

public sealed partial class LoginMfa(IPortalAccount account, IPortalTwoFactor twoFactor)
{
    private IPortalAccount Account => account;

    private IPortalTwoFactor TwoFactor => twoFactor;

    // 路徑上的租戶不存在：這個頁面就不存在。
    protected override void OnInitialized()
    {
        if (account.CurrentTenant is null)
        {
            Navigation.NotFound();
        }
    }
}