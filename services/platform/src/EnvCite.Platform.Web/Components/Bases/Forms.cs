namespace EnvCite.Platform.Web.Components.Bases;

// 營運者後台（/ui）與客戶入口（/portal）的表單內容：兩邊的欄位一樣，所以共用，不各複製一份。
// 欄位的名稱就是 POST 的欄位名稱（Input.Email…），改名會讓既有頁面的送出對不上。

/// <summary>登入：電子郵件與密碼。</summary>
public sealed class LoginForm
{
    public string Email { get; set; } = string.Empty;

    public string Password { get; set; } = string.Empty;
}

/// <summary>一組驗證碼或復原碼（第二步驗證、註冊與重新產生復原碼）。</summary>
public sealed class CodeForm
{
    public string Code { get; set; } = string.Empty;
}

/// <summary>改密碼：目前的密碼、新密碼、再輸入一次。</summary>
public sealed class PasswordForm
{
    public string Current { get; set; } = string.Empty;

    public string New { get; set; } = string.Empty;

    public string Confirm { get; set; } = string.Empty;
}

/// <summary>啟用帳號：新密碼與再輸入一次。</summary>
public sealed class ActivateForm
{
    public string New { get; set; } = string.Empty;

    public string Confirm { get; set; } = string.Empty;
}