namespace EnvCite.Platform.Web.Presentation;

/// <summary>分頁列的一格：<paramref name="Key"/> 是網址 <c>?tab=</c> 的值與面板的識別，<paramref name="Label"/> 已是要顯示的文字；<paramref name="Count"/> 有值時在標籤旁顯示數量。</summary>
public sealed record TabItem(string Key, string Label, int? Count = null);