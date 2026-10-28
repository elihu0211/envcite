using System.ComponentModel.DataAnnotations;

using EnvCite.ServiceDefaults;

using Microsoft.Extensions.Options;

namespace EnvCite.Platform.SharedKernel;

/// <summary>資料庫連線（<c>ConnectionStrings</c> 區段；Npgsql 另外讀 <c>PGUSER</c>／<c>PGPASSWORD</c>，密碼可不放在字串裡）。</summary>
public sealed class DatabaseOptions : ISectionOptions
{
    public static string SectionName => "ConnectionStrings";

    [Required(AllowEmptyStrings = false)]
    public string Platform { get; set; } = string.Empty;
}

[OptionsValidator]
public sealed partial class DatabaseOptionsValidator : IValidateOptions<DatabaseOptions>;