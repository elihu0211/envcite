using EnvCite.Platform.Data;
using EnvCite.Platform.Modules.Members;
using EnvCite.Platform.SharedKernel;

using Microsoft.AspNetCore.Identity;
using Microsoft.Extensions.DependencyInjection;

namespace EnvCite.Platform.Tests;

/// <summary>
/// 密碼規則（<see cref="PasswordPolicy"/>）：夠長、不太容易被猜到、不含自己的 email，不強制大小寫與符號。
/// 用真的 Identity 組出同一份驗證器（<c>AddPasswordPolicy</c>）與真的選項（<c>ConfigureIdentity</c>），只把使用者儲存換成只認 email 的最小替身，
/// 所以不需要資料庫；頁面端到端的行為在 <c>InvitationTests</c> 與各登入測試。
/// </summary>
public sealed class PasswordPolicyTests
{
    private const string Email = "alice@example.com";

    private static async Task<IReadOnlyList<string>> ErrorsAsync(string password, string email = Email)
    {
        var services = new ServiceCollection();
        services.AddLogging();
        services.AddIdentityCore<ApplicationUser>(MembersModule.ConfigureIdentity)
            .AddPasswordPolicy<ApplicationUser>()
            .AddUserStore<EmailOnlyStore>();
        using var provider = services.BuildServiceProvider();
        using var scope = provider.CreateScope();
        var manager = scope.ServiceProvider.GetRequiredService<UserManager<ApplicationUser>>();
        var user = new ApplicationUser { Email = email };

        var errors = new List<string>();
        foreach (var validator in manager.PasswordValidators)
        {
            var result = await validator.ValidateAsync(manager, user, password);
            errors.AddRange(result.Errors.Select(e => e.Code));
        }

        return errors;
    }

    // 句子、中文、沒有大小寫與符號的長密碼都過：這就是「資安但不複雜」。
    [Theory]
    [InlineData("correct horse battery staple")]
    [InlineData("blue-bicycle-milk-tuesday")]
    [InlineData("blue bicycle milk")]
    [InlineData("我愛吃蘋果和香蕉每天都吃")]
    [InlineData("another long password!")]
    public async Task LongUnrelatedWordsPassWithoutAnyCompositionRule(string password) =>
        Assert.Empty(await ErrorsAsync(password));

    [Theory]
    [InlineData("password1234")]
    [InlineData("Password123!!")] // 套路：大寫加數字加驚嘆號
    [InlineData("qwertyuiop12")] // 鍵盤順序
    [InlineData("123456789012")] // 連續數字
    [InlineData("aaaaaaaaaaaa")] // 重複
    public async Task GuessablePasswordsAreRefusedEvenWhenTheyHaveEveryKindOfCharacter(string password) =>
        Assert.Equal([PasswordPolicyErrors.TooGuessable], await ErrorsAsync(password));

    [Theory]
    [InlineData("alice@example.com-purple-walrus")]
    [InlineData("ALICE@EXAMPLE.COM-purple-walrus")]
    [InlineData("purple-walrus-alice-lamp")] // email 的名稱部分
    public async Task ThePasswordMustNotContainTheAccountsEmail(string password) =>
        Assert.Equal([PasswordPolicyErrors.ContainsAccountInfo], await ErrorsAsync(password));

    // 名稱部分太短（ab@…）就不拿來比對，否則幾乎任何密碼都含有它。
    [Fact]
    public async Task AVeryShortNamePartIsNotUsedForTheComparison() =>
        Assert.Empty(await ErrorsAsync("tab-purple-walrus-lamp", "ab@example.com"));

    [Fact]
    public async Task TooShortIsReportedFirstByIdentityItself()
    {
        var errors = await ErrorsAsync("short");

        Assert.Equal(nameof(IdentityErrorDescriber.PasswordTooShort), errors[0]);
    }

    [Fact]
    public async Task TheLimitsAreInclusive()
    {
        // 決定性的偽隨機字串：夠亂，強度不是問題，只測長度的邊界。
        var random = new Random(7);
        string Of(int length) => new([.. Enumerable.Range(0, length).Select(_ => "abcdefghijkmnpqrstuvwxyz23456789"[random.Next(32)])]);

        Assert.Empty(await ErrorsAsync(Of(PasswordPolicy.MaximumLength)));
        Assert.Equal([PasswordPolicyErrors.TooLong], await ErrorsAsync(Of(PasswordPolicy.MaximumLength + 1)));
        Assert.DoesNotContain(nameof(IdentityErrorDescriber.PasswordTooShort), await ErrorsAsync(Of(PasswordPolicy.MinimumLength)));
        Assert.Contains(nameof(IdentityErrorDescriber.PasswordTooShort), await ErrorsAsync(Of(PasswordPolicy.MinimumLength - 1)));
    }

    // 一次性密碼（營運者經手）也要過同一套驗證器，否則偶爾建不起帳號。
    [Fact]
    public async Task EveryGeneratedOneTimePasswordIsAcceptable()
    {
        for (var i = 0; i < 200; i++)
        {
            Assert.Empty(await ErrorsAsync(OneTimePassword.New()));
        }
    }

    // 每個驗證器錯誤都有穩定的結果代碼與參數；介面依代碼取使用者的語言。
    [Theory]
    [InlineData(PasswordPolicyErrors.TooLong, OutcomeCodes.PasswordTooLong, PasswordPolicy.MaximumLength)]
    [InlineData(PasswordPolicyErrors.TooGuessable, OutcomeCodes.PasswordTooGuessable, null)]
    [InlineData(PasswordPolicyErrors.ContainsAccountInfo, OutcomeCodes.PasswordContainsAccountInfo, null)]
    [InlineData("PasswordTooShort", OutcomeCodes.PasswordTooShort, PasswordPolicy.MinimumLength)]
    public void RejectionsMapToStableOutcomeCodes(string identityCode, string outcomeCode, int? argument)
    {
        var outcome = IdentityOutcomes.PasswordRejected<object>(new IdentityError { Code = identityCode, Description = "ignored" });

        Assert.Equal(outcomeCode, outcome.Code);
        Assert.Equal(argument is null ? [] : [argument.Value.ToString(System.Globalization.CultureInfo.InvariantCulture)], outcome.Args);
    }

    /// <summary>只認 email 的使用者儲存：驗證器只讀 email，其餘都不該被呼叫。</summary>
    private sealed class EmailOnlyStore : IUserEmailStore<ApplicationUser>
    {
        public Task<string?> GetEmailAsync(ApplicationUser user, CancellationToken cancellationToken) => Task.FromResult(user.Email);

        public void Dispose()
        {
        }

        public Task SetEmailAsync(ApplicationUser user, string? email, CancellationToken cancellationToken) => throw new NotSupportedException();

        public Task<bool> GetEmailConfirmedAsync(ApplicationUser user, CancellationToken cancellationToken) => throw new NotSupportedException();

        public Task SetEmailConfirmedAsync(ApplicationUser user, bool confirmed, CancellationToken cancellationToken) => throw new NotSupportedException();

        public Task<ApplicationUser?> FindByEmailAsync(string normalizedEmail, CancellationToken cancellationToken) => throw new NotSupportedException();

        public Task<string?> GetNormalizedEmailAsync(ApplicationUser user, CancellationToken cancellationToken) => throw new NotSupportedException();

        public Task SetNormalizedEmailAsync(ApplicationUser user, string? normalizedEmail, CancellationToken cancellationToken) => throw new NotSupportedException();

        public Task<string> GetUserIdAsync(ApplicationUser user, CancellationToken cancellationToken) => throw new NotSupportedException();

        public Task<string?> GetUserNameAsync(ApplicationUser user, CancellationToken cancellationToken) => throw new NotSupportedException();

        public Task SetUserNameAsync(ApplicationUser user, string? userName, CancellationToken cancellationToken) => throw new NotSupportedException();

        public Task<string?> GetNormalizedUserNameAsync(ApplicationUser user, CancellationToken cancellationToken) => throw new NotSupportedException();

        public Task SetNormalizedUserNameAsync(ApplicationUser user, string? normalizedName, CancellationToken cancellationToken) => throw new NotSupportedException();

        public Task<IdentityResult> CreateAsync(ApplicationUser user, CancellationToken cancellationToken) => throw new NotSupportedException();

        public Task<IdentityResult> UpdateAsync(ApplicationUser user, CancellationToken cancellationToken) => throw new NotSupportedException();

        public Task<IdentityResult> DeleteAsync(ApplicationUser user, CancellationToken cancellationToken) => throw new NotSupportedException();

        public Task<ApplicationUser?> FindByIdAsync(string userId, CancellationToken cancellationToken) => throw new NotSupportedException();

        public Task<ApplicationUser?> FindByNameAsync(string normalizedUserName, CancellationToken cancellationToken) => throw new NotSupportedException();
    }
}