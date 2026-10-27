using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;

using EnvCite.Platform.Modules.Tenancy;

using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 金鑰環的靜態加密（<c>Platform:DataProtection:CertificatePath／CertificatePassword</c>）：兩者都設才加密、都不設維持明文、
/// 只設一個啟動就失敗；加密之前產生的明文金鑰仍可讀取。每個測試用全新的資料庫，才看得到「新產生」的金鑰。
/// </summary>
[Collection(PlatformTestGroup.Name)]
public sealed class DataProtectionCertificateTests(PlatformFactory factory) : IDisposable
{
    private const string Password = "test-only-pfx-password";

    private readonly string _directory = Directory.CreateTempSubdirectory("envcite-dp-").FullName;

    public void Dispose() => Directory.Delete(_directory, recursive: true);

    private string NewCertificateFile()
    {
        using var rsa = RSA.Create(2048);
        var request = new CertificateRequest("CN=envcite-test", rsa, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        using var certificate = request.CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddDays(30));
        var path = Path.Combine(_directory, $"{Guid.NewGuid():N}.pfx");
        File.WriteAllBytes(path, certificate.Export(X509ContentType.Pfx, Password));
        return path;
    }

    private async Task<WebApplicationFactory<Program>> HostAsync(string? database = null, string? certificatePath = null, string? password = null)
    {
        database ??= await factory.CreateDatabaseAsync();
        return factory.WithWebHostBuilder(b =>
        {
            b.UseSetting("ConnectionStrings:Platform", database);
            if (certificatePath is not null)
            {
                b.UseSetting("Platform:DataProtection:CertificatePath", certificatePath);
            }

            if (password is not null)
            {
                b.UseSetting("Platform:DataProtection:CertificatePassword", password);
            }
        });
    }

    /// <summary>用主機的 Data Protection 保護一段資料（逼出金鑰環建立第一把金鑰），回傳密文。</summary>
    private static string Protect(WebApplicationFactory<Program> host, string plaintext) =>
        host.Services.GetRequiredService<IDataProtectionProvider>().CreateProtector("test").Protect(plaintext);

    private static string? Unprotect(WebApplicationFactory<Program> host, string protectedText) =>
        host.Services.GetRequiredService<IDataProtectionProvider>().CreateProtector("test").Unprotect(protectedText);

    private static async Task<List<string>> KeyXmlAsync(WebApplicationFactory<Program> host)
    {
        await using var scope = host.Services.CreateAsyncScope();
        var db = scope.ServiceProvider.GetRequiredService<TenancyDbContext>();
        return await db.DataProtectionKeys.AsNoTracking().OrderBy(k => k.Id).Select(k => k.Xml!).ToListAsync(TestContext.Current.CancellationToken);
    }

    [Fact]
    public async Task WithACertificateNewKeysAreStoredEncrypted()
    {
        await using var host = await HostAsync(certificatePath: NewCertificateFile(), password: Password);

        var cipher = Protect(host, "secret");

        var xml = Assert.Single(await KeyXmlAsync(host));
        Assert.Contains("EncryptedData", xml, StringComparison.Ordinal);
        Assert.DoesNotContain("masterKey", xml, StringComparison.Ordinal);
        Assert.Equal("secret", Unprotect(host, cipher));
    }

    [Fact]
    public async Task WithoutACertificateKeysStayInPlainXmlAsBefore()
    {
        await using var host = await HostAsync();

        Protect(host, "secret");

        var xml = Assert.Single(await KeyXmlAsync(host));
        Assert.Contains("masterKey", xml, StringComparison.Ordinal);
        Assert.DoesNotContain("EncryptedData", xml, StringComparison.Ordinal);
    }

    // 上線加密時，資料庫裡已有明文金鑰、已簽出去的 cookie 不能因此失效。
    [Fact]
    public async Task KeysWrittenBeforeTheCertificateWasConfiguredStillWork()
    {
        var database = await factory.CreateDatabaseAsync();
        string cipher;
        await using (var before = await HostAsync(database))
        {
            cipher = Protect(before, "secret");
        }

        await using var after = await HostAsync(database, NewCertificateFile(), Password);

        Assert.Equal("secret", Unprotect(after, cipher));
        Assert.DoesNotContain("EncryptedData", Assert.Single(await KeyXmlAsync(after)), StringComparison.Ordinal); // 舊金鑰不被改寫
    }

    // 另一個副本（同一張憑證、同一個資料庫）解得開加密金鑰環裡的資料。
    [Fact]
    public async Task AnotherReplicaWithTheSameCertificateReadsTheEncryptedKeyRing()
    {
        var database = await factory.CreateDatabaseAsync();
        var path = NewCertificateFile();
        await using var one = await HostAsync(database, path, Password);
        var cipher = Protect(one, "secret");

        await using var two = await HostAsync(database, path, Password);

        Assert.Equal("secret", Unprotect(two, cipher));
    }

    [Theory]
    [InlineData(true, false)]
    [InlineData(false, true)]
    public async Task SettingOnlyOneOfThePairFailsAtStartup(bool pathOnly, bool passwordOnly)
    {
        Assert.NotEqual(pathOnly, passwordOnly);
        await using var host = await HostAsync(
            certificatePath: pathOnly ? NewCertificateFile() : null,
            password: passwordOnly ? Password : null);

        var failure = Assert.ThrowsAny<Exception>(() => host.CreateClient());

        Assert.Contains("Platform:DataProtection:CertificatePath and Platform:DataProtection:CertificatePassword must be set together", failure.ToString(), StringComparison.Ordinal);
    }

    [Fact]
    public async Task ACertificateFileThatDoesNotExistFailsAtStartupAndNamesTheKey()
    {
        await using var host = await HostAsync(certificatePath: Path.Combine(_directory, "missing.pfx"), password: Password);

        var failure = Assert.ThrowsAny<Exception>(() => host.CreateClient());

        Assert.Contains("Platform:DataProtection:CertificatePath", failure.ToString(), StringComparison.Ordinal);
    }
}