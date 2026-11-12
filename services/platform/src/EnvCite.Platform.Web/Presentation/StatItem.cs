namespace EnvCite.Platform.Web.Presentation;

/// <summary>摘要磚的一格：標籤、數值與一行說明；有 <paramref name="Href"/> 時整格是連結。數值與說明已是要顯示的文字（頁面負責本地化與格式）。</summary>
public sealed record StatItem(string Label, string Value, string? Note = null, string? Href = null);