using EnvCite.Platform.Api;

namespace EnvCite.Platform.Tests;

/// <summary>命令列入口的判斷不碰資料庫，所以不放進需要資料庫的測試集合，本機沒有 Docker 也能跑。</summary>
public sealed class OperatorCliEntryTests
{
    [Theory]
    [InlineData(true, "operator", "create", "a@example.com")]
    [InlineData(true, "operator")]
    [InlineData(true, "mail", "test", "a@example.com")]
    [InlineData(true, "mail")]
    [InlineData(false, "mail", "preview", "out")]
    [InlineData(false, "mail", "preview")]
    [InlineData(false)]
    [InlineData(false, "--urls=http://localhost:5000")]
    [InlineData(false, "create", "a@example.com")]
    public void OnlyAFirstArgumentOfOperatorOrMailIsTheCommandLineEntry(bool expected, params string[] args) =>
        Assert.Equal(expected, OperatorCli.IsCommand(args));
}