using System.ComponentModel.DataAnnotations;

using EnvCite.ServiceDefaults;

namespace EnvCite.Platform.SharedKernel;

/// <summary>資料庫連線（<c>ConnectionStrings</c> 區段；Npgsql 另外讀 <c>PGUSER</c>／<c>PGPASSWORD</c>，密碼可不放在字串裡）。</summary>
public sealed class DatabaseOptions : ISectionOptions
{
    public static string SectionName => "ConnectionStrings";

    // 訊息指名設定鍵：產生的驗證器預設只說「The Platform field is required.」，k8s 的 CrashLoopBackOff 裡看不出是哪個區段。
    [Required(AllowEmptyStrings = false, ErrorMessage = "ConnectionStrings:Platform is required.")]
    public string Platform { get; set; } = string.Empty;
}

[OptionsValidator]
public sealed partial class DatabaseOptionsValidator : IValidateOptions<DatabaseOptions>;