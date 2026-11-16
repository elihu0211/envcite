namespace EnvCite.Platform.SharedKernel;

public enum OutcomeStatus
{
    Ok,
    NotFound,

    /// <summary>輸入不合法（例如名稱空白、方案不存在）；<see cref="Outcome{T}.Message"/> 說明原因。</summary>
    Invalid,

    /// <summary>與既有資料衝突（例如識別碼已被使用）。</summary>
    Conflict,

    /// <summary>依賴的內部服務暫時不可用（可稍後重試）。</summary>
    Unavailable,

    /// <summary>身分是對的，但這個人沒有權限做這件事（例如管理員想動擁有者）。</summary>
    Forbidden,
}

/// <summary>
/// 一次操作的結果。入口、營運後台與對外 API 共用。輸入錯誤與找不到是預期的結果，不是例外。
/// 失敗時 <see cref="Code"/> 是穩定的代碼（見 <see cref="OutcomeCodes"/>），
/// <see cref="Args"/> 是代碼的參數；<see cref="Message"/> 是英文說明，只供 API 與日誌。
/// </summary>
public sealed record Outcome<T>(
    OutcomeStatus Status, T? Value = default, string? Message = null, string? Code = null, IReadOnlyList<string>? Args = null)
{
    public bool IsOk => Status == OutcomeStatus.Ok;

    /// <summary>將失敗狀態原樣轉為另一種結果型別，不重複展開欄位。</summary>
    public Outcome<TOut> ForwardError<TOut>() => new(Status, default, Message, Code, Args);
}

/// <summary><see cref="Outcome{T}"/> 的工廠（泛型型別上不放靜態成員）。失敗一律帶代碼，文字由代碼決定。</summary>
public static class Outcomes
{
    public static Outcome<T> Ok<T>(T value) => new(OutcomeStatus.Ok, value);

    public static Outcome<T> NotFound<T>(string code, params object[] args) => Fail<T>(OutcomeStatus.NotFound, code, args);

    public static Outcome<T> Invalid<T>(string code, params object[] args) => Fail<T>(OutcomeStatus.Invalid, code, args);

    public static Outcome<T> Conflict<T>(string code, params object[] args) => Fail<T>(OutcomeStatus.Conflict, code, args);

    public static Outcome<T> Unavailable<T>(string code, params object[] args) => Fail<T>(OutcomeStatus.Unavailable, code, args);

    public static Outcome<T> Forbidden<T>(string code, params object[] args) => Fail<T>(OutcomeStatus.Forbidden, code, args);

    private static Outcome<T> Fail<T>(OutcomeStatus status, string code, object[] args)
    {
        string[] text = [.. args.Select(a => Convert.ToString(a, System.Globalization.CultureInfo.InvariantCulture) ?? string.Empty)];
        return new(status, default, OutcomeCodes.Describe(code, text), code, text);
    }
}