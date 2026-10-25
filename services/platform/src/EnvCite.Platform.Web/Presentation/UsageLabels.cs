using MudBlazor;

namespace EnvCite.Platform.Web.Presentation;

/// <summary>
/// 租戶用量操作在畫面上的呈現：繁體中文名稱、分類、計量單位與圖示。
/// </summary>
public static class UsageLabels
{
    public static string Operation(string operation) => operation switch
    {
        "ontology.search" => "本體條文檢索",
        "ontology.get_law" => "取得單部法規",
        "ontology.list_articles" => "條文清單",
        "ontology.evaluate" => "法規論理推論",
        "consult.ground" => "諮詢接地證據集",
        "consult.answer" => "模型生成回答",
        "consult.tokens.input" => "模型輸入 Tokens",
        "consult.tokens.output" => "模型輸出 Tokens",
        _ => operation,
    };

    public static string Category(string operation) => operation switch
    {
        var op when op.StartsWith("ontology.", StringComparison.Ordinal) => "法規本體",
        var op when op.StartsWith("consult.tokens.", StringComparison.Ordinal) => "Token 計量",
        var op when op.StartsWith("consult.", StringComparison.Ordinal) => "諮詢接地",
        _ => "其他操作",
    };

    public static Color CategoryColor(string operation) => operation switch
    {
        var op when op.StartsWith("ontology.", StringComparison.Ordinal) => Color.Primary,
        var op when op.StartsWith("consult.tokens.", StringComparison.Ordinal) => Color.Tertiary,
        var op when op.StartsWith("consult.", StringComparison.Ordinal) => Color.Info,
        _ => Color.Default,
    };

    public static string UnitName(string operation) => operation switch
    {
        var op when op.Contains("tokens", StringComparison.OrdinalIgnoreCase) => "tokens",
        _ => "次",
    };

    public static string Icon(string operation) => operation switch
    {
        "ontology.search" => Icons.Material.Filled.Search,
        "ontology.get_law" => Icons.Material.Filled.MenuBook,
        "ontology.list_articles" => Icons.Material.Filled.FormatListBulleted,
        "ontology.evaluate" => Icons.Material.Filled.Psychology,
        "consult.ground" => Icons.Material.Filled.Gavel,
        "consult.answer" => Icons.Material.Filled.AutoAwesome,
        var op when op.Contains("tokens", StringComparison.OrdinalIgnoreCase) => Icons.Material.Filled.DataUsage,
        _ => Icons.Material.Filled.BarChart,
    };
}