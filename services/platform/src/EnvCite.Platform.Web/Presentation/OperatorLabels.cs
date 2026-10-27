using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Resources;

namespace EnvCite.Platform.Web.Presentation;

/// <summary>營運者帳號的狀態在畫面上的樣子。狀態寫成文字，色彩只是輔助；營運者清單與總覽共用。</summary>
public static class OperatorLabels
{
    public static string Status(OperatorInfo op) => Text.Find($"Operator.Status.{StatusKey(op)}")!;

    private static string StatusKey(OperatorInfo op) =>
        op.Disabled ? "disabled"
        : op.LockedOut ? "locked"
        : op.MustChangePassword ? "must_change_password"
        : op.TwoFactorEnabled ? "active"
        : "needs_enrollment";

    /// <summary><c>StatusBadge</c> 的 tone。</summary>
    public static string Tone(OperatorInfo op) =>
        op.Disabled || op.LockedOut ? "error"
        : op.MustChangePassword || !op.TwoFactorEnabled ? "warning"
        : "success";
}