using EnvCite.Platform.SharedKernel;
using EnvCite.Platform.Web.Resources;

namespace EnvCite.Platform.Web.Presentation;

/// <summary>
/// 營運操作的失敗，在畫面上用使用者語言說的話：依 <see cref="AdminOutcome{T}.Code"/> 查資源檔（<c>Outcome.{代碼}</c>）填入參數。
/// 沒有代碼或查不到時退回英文的 <see cref="AdminOutcome{T}.Message"/>；兩者都沒有回 null，由呼叫端決定通用的說法。
/// </summary>
public static class OutcomeText
{
    public static string? Localized<T>(this AdminOutcome<T> outcome)
    {
        if (outcome.Code is { } code && Text.Find($"Outcome.{code}") is { } template)
        {
            return string.Format(System.Globalization.CultureInfo.CurrentCulture, template, [.. outcome.Args ?? []]);
        }

        return outcome.Message;
    }
}