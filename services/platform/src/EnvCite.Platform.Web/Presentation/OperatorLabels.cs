using EnvCite.Platform.SharedKernel;

namespace EnvCite.Platform.Web.Presentation;

/// <summary>營運者帳號的狀態在畫面上的樣子。狀態寫成文字，色彩只是輔助；營運者清單與總覽共用。</summary>
public static class OperatorLabels
{
    public static string Status(OperatorInfo op) =>
        op.Disabled ? "已停用"
        : op.LockedOut ? "已鎖定"
        : op.MustChangePassword ? "須改密碼"
        : op.TwoFactorEnabled ? "使用中"
        : "須綁定驗證器";

    /// <summary><c>StatusBadge</c> 的 tone。</summary>
    public static string Tone(OperatorInfo op) =>
        op.Disabled || op.LockedOut ? "error"
        : op.MustChangePassword || !op.TwoFactorEnabled ? "warning"
        : "success";
}