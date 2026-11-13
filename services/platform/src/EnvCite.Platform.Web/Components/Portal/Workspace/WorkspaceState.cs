namespace EnvCite.Platform.Web.Components.Portal.Workspace;

/// <summary>
/// 一個 circuit 裡工作區各部分之間的通知。目前只有一件事：問完一題、對話被記進歷史之後，
/// 版面（側欄的最近對話）要重新讀。scoped：同一個 circuit 的版面與頁面拿到同一個。
/// </summary>
public sealed class WorkspaceState
{
    public event Action? RecentChanged;

    public void NotifyRecentChanged() => RecentChanged?.Invoke();
}