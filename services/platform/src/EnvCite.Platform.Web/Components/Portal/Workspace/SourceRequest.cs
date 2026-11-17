namespace EnvCite.Platform.Web.Components.Portal.Workspace;

/// <summary>使用者在某個回合裡點了一條法源：要開哪一條、問的是哪一天。抽屜只顯示這一條，不能切到同一回合的其他法源。</summary>
public readonly record struct SourceRequest(AnswerSource Source, string? AsOfLabel);