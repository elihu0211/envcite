namespace EnvCite.Platform.Web.Components.Portal.Workspace;

/// <summary>使用者在某個回合裡點了一條法源：要開哪一條、問的是哪一天、同一回合還列了哪些。</summary>
public readonly record struct SourceRequest(AnswerSource Source, string? AsOfLabel, IReadOnlyList<AnswerSource> Sources);