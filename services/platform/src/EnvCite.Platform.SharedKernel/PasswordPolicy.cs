namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// 密碼規則的<b>唯一來源</b>：伺服器端的驗證（<c>PasswordPolicyValidator</c>）與設定、修改密碼頁的提示（<c>NewPasswordField</c>）都讀這裡，
/// 數字不在頁面或文案裡另寫一份。客戶入口的成員與後台的營運者適用同一套。
/// 依 NIST SP 800-63B：<b>長度比組成規則重要</b>，所以只要求夠長、不太容易被猜到，不強制大小寫、數字或符號，不要求定期換密碼；
/// 允許任何字元（含空白與中文）。
/// </summary>
public static class PasswordPolicy
{
    /// <summary>最短長度（UTF-16 字元數，與瀏覽器的 <c>length</c> 同一種算法）。</summary>
    public const int MinimumLength = 12;

    /// <summary>最長長度。夠長的句子都放得下；上限是為了不讓超長的輸入拖慢雜湊與強度估算。</summary>
    public const int MaximumLength = 128;

    /// <summary>
    /// 強度估算（zxcvbn，0 到 4）的最低分數。3 擋下常見密碼、鍵盤順序、連續或重複的字元、生日與「大寫加數字加驚嘆號」這類套路，
    /// 同時讓幾個不相干的詞連起來的句子過關，所以不必靠符號與大小寫。
    /// </summary>
    public const int MinimumScore = 3;
}