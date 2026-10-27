using EnvCite.Platform.Web.Resources;

namespace EnvCite.Platform.Web.Presentation;

/// <summary>
/// 租戶用量操作在畫面上的呈現：名稱、分類與計量單位，依目前的介面語言（見 Resources/Labels）。
/// 操作的鍵（<c>ontology.search</c> 這類）是契約，永遠不翻譯；未知的操作直接顯示它的鍵。
/// </summary>
public static class UsageLabels
{
    public static string Operation(string operation) => Text.Find($"Usage.Operation.{operation}") ?? operation;

    public static string Category(string operation) => Text.Find($"Usage.Category.{CategoryKey(operation)}")!;

    private static string CategoryKey(string operation) => operation switch
    {
        var op when op.StartsWith("ontology.", StringComparison.Ordinal) => "ontology",
        var op when op.StartsWith("consult.tokens.", StringComparison.Ordinal) => "tokens",
        var op when op.StartsWith("consult.", StringComparison.Ordinal) => "consult",
        _ => "other",
    };

    public static string UnitName(string operation) =>
        Text.Find(operation.Contains("tokens", StringComparison.OrdinalIgnoreCase) ? "Usage.Unit.tokens" : "Usage.Unit.calls")!;
}