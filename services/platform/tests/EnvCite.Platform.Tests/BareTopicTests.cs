using EnvCite.Platform.Modules.Consult;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 「只打了主題詞、沒有問句」的判準：只看形態（長度、句末標點、疑問詞），四項全中才算。
/// 判斷錯的兩個方向代價不同——誤認只是模型多寫一份有出處的條文說明，漏認是使用者什麼都拿不到——所以邊界案例要釘死。
/// </summary>
public sealed class BareTopicTests
{
    [Theory]
    [InlineData("露天燃燒")]
    [InlineData("  露天燃燒  ")]
    [InlineData("噪音")]
    [InlineData("噪音 罰則")] // 入口的說明教使用者用空白分隔關鍵字
    [InlineData("未經許可設置固定污染源")] // 11 字
    [InlineData("一二三四五六七八九十一二")] // 剛好 12 字
    public void AKeywordWithoutAQuestionIsABareTopic(string text) => Assert.True(BareTopic.Is(text));

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData("一二三四五六七八九十一二三")] // 13 字：開始像一句敘述
    [InlineData("露天燃燒如何處罰")] // 疑問詞
    [InlineData("露天燃燒會罰嗎")]
    [InlineData("露天燃燒罰多少")]
    [InlineData("是否可以露天燃燒")]
    [InlineData("露天燃燒。")] // 句末標點（全形與半形）
    [InlineData("露天燃燒?")]
    [InlineData("露天燃燒？")]
    [InlineData("a;b")]
    public void AQuestionOrASentenceIsNot(string? text) => Assert.False(BareTopic.Is(text));
}