namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// 邀請／驗證連結的狀態。連結由 Identity 的資料保護權杖組成：單次、限時、綁帳號的安全戳記，
/// 所以用過、過期、或帳號被重設／重新邀請之後都失效。所有無效的原因對外都是同一個 <see cref="Invalid"/>，
/// 不透露帳號存在與否。
/// </summary>
public enum ActivationState
{
    /// <summary>連結無效、過期、已用過，或帳號不存在、被停用。</summary>
    Invalid,

    /// <summary>邀請連結：帳號還沒有密碼，開啟者要設定自己的密碼，同時完成信箱驗證。</summary>
    SetPassword,

    /// <summary>驗證連結：帳號已有密碼，開啟者只需確認這個信箱。</summary>
    ConfirmEmail,
}