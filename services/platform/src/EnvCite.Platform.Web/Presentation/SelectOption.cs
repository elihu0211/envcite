namespace EnvCite.Platform.Web.Presentation;

/// <summary>下拉選單的一個選項：送出的值與顯示的文字。選項由資料給，模板不認得「角色」之類的領域。</summary>
public sealed record SelectOption(string Value, string Text);