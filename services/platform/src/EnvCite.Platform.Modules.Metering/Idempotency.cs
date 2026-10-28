using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;
using EnvCite.ServiceDefaults;

using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Options;

using Npgsql;

namespace EnvCite.Platform.Modules.Metering;

/// <summary><c>Idempotency-Key</c> 的重送視窗（<c>Platform:Idempotency</c> 區段）。</summary>
public sealed class IdempotencyOptions : ISectionOptions
{
    public static string SectionName => "Platform:Idempotency";

    public static readonly TimeSpan MinWindow = TimeSpan.FromMinutes(1);

    public static readonly TimeSpan MaxWindow = TimeSpan.FromDays(7);

    /// <summary>第一次回應保留多久；視窗內同一租戶、同一 key 的重送回放第一次的回應。</summary>
    public TimeSpan Window { get; set; } = TimeSpan.FromHours(24);
}

internal sealed class IdempotencyOptionsValidator : IValidateOptions<IdempotencyOptions>
{
    public ValidateOptionsResult Validate(string? name, IdempotencyOptions options) =>
        options.Window < IdempotencyOptions.MinWindow || options.Window > IdempotencyOptions.MaxWindow
            ? ValidateOptionsResult.Fail("Platform:Idempotency:Window must be between 1 minute and 7 days.")
            : ValidateOptionsResult.Success;
}

internal sealed class IdempotencyStore(PlatformDbContext db, TimeProvider time, IOptions<IdempotencyOptions> options, IUsageRecorder usage)
    : IIdempotencyStore
{
    public async Task<IdempotencyResult> LookupAsync(
        string operation, string key, byte[] fingerprint, CancellationToken cancellationToken)
    {
        var existing = await db.IdempotencyRecords.AsNoTracking()
            .SingleOrDefaultAsync(r => r.Operation == operation && r.Key == key, cancellationToken);
        if (existing is null)
        {
            return new IdempotencyResult(IdempotencyState.Fresh);
        }

        if (existing.ExpiresAt <= time.GetUtcNow())
        {
            // 視窗已過、清理還沒輪到：當作沒有，並讓出唯一鍵給這次的請求。
            await db.IdempotencyRecords.Where(r => r.Id == existing.Id).ExecuteDeleteAsync(cancellationToken);
            return new IdempotencyResult(IdempotencyState.Fresh);
        }

        return System.Security.Cryptography.CryptographicOperations.FixedTimeEquals(existing.Fingerprint, fingerprint)
            ? new IdempotencyResult(IdempotencyState.Replay, existing.Response)
            : new IdempotencyResult(IdempotencyState.Mismatch);
    }

    public async Task<IdempotencyResult> CompleteAsync(
        string operation, string key, byte[] fingerprint, string response, string usageOperation, int units,
        CancellationToken cancellationToken)
    {
        var now = time.GetUtcNow();
        db.IdempotencyRecords.Add(new IdempotencyRecord
        {
            Operation = operation,
            Key = key,
            Fingerprint = fingerprint,
            Response = response,
            CreatedAt = now,
            ExpiresAt = now + options.Value.Window,
        });
        usage.Stage(usageOperation, units);
        try
        {
            await db.SaveChangesAsync(cancellationToken);
            return new IdempotencyResult(IdempotencyState.Fresh);
        }
        catch (DbUpdateException ex) when (ex.InnerException is PostgresException { SqlState: PostgresErrorCodes.UniqueViolation })
        {
            // 同 key 的另一個請求先存進去了；同一次儲存裡的用量也一起回滾，所以只計一次。
            db.ChangeTracker.Clear();
            var winner = await LookupAsync(operation, key, fingerprint, cancellationToken);
            if (winner.State == IdempotencyState.Fresh)
            {
                throw;
            }

            return winner;
        }
    }
}