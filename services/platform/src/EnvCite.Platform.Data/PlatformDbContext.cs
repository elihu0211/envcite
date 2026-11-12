using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.EntityFrameworkCore;
using Finbuckle.MultiTenant.EntityFrameworkCore.Extensions;
using Finbuckle.MultiTenant.Identity.EntityFrameworkCore;

using Microsoft.AspNetCore.Identity;
using Microsoft.EntityFrameworkCore;

namespace EnvCite.Platform.Data;

/// <summary>
/// 租戶擁有的資料（API key、用量），由 Finbuckle 的具名 query filter 篩選至目前租戶。
/// 租戶在建構 context 時即被擷取，因此在請求中完成租戶解析之前，絕不可解析此
/// context（API key 驗證使用自己的 scope）。
/// </summary>
/// <remarks>
/// 租戶 store 不在這裡：Finbuckle 要求它的 context 衍生自 <c>EFCoreStoreDbContext</c>，
/// 故留在 Tenancy 模組（<c>TenancyDbContext</c>）。除此之外只有這一個 context。
/// </remarks>
public sealed class PlatformDbContext : MultiTenantIdentityDbContext<ApplicationUser>
{
    internal const string Schema = "platform";

    public PlatformDbContext(IMultiTenantContextAccessor accessor, DbContextOptions<PlatformDbContext> options)
        : base(accessor, options)
    {
    }

    public DbSet<ApiKey> ApiKeys => Set<ApiKey>();

    public DbSet<UsageRecord> UsageRecords => Set<UsageRecord>();

    public DbSet<IdempotencyRecord> IdempotencyRecords => Set<IdempotencyRecord>();

    public DbSet<Conversation> Conversations => Set<Conversation>();

    public DbSet<ConversationMessage> ConversationMessages => Set<ConversationMessage>();

    public DbSet<OperatorUser> Operators => Set<OperatorUser>();

    public DbSet<DirectoryToken> DirectoryTokens => Set<DirectoryToken>();

    public DbSet<DirectoryConnection> DirectoryConnections => Set<DirectoryConnection>();

    protected override void OnModelCreating(ModelBuilder builder)
    {
        base.OnModelCreating(builder);
        builder.HasDefaultSchema(Schema);

        builder.Entity<ApplicationUser>(user =>
        {
            user.Property(u => u.DisplayName).HasMaxLength(200);
            user.Property(u => u.Role).HasMaxLength(32).IsRequired();
            user.Property(u => u.ExternalId).HasMaxLength(200);
            user.Property(u => u.ManagedBy).HasMaxLength(32);
            // 同步以 ExternalId 比對：租戶內唯一，沒連結的（null）不受限。
            user.HasIndex("TenantId", nameof(ApplicationUser.ExternalId)).IsUnique().HasFilter("\"ExternalId\" IS NOT NULL");
        });

        builder.Entity<DirectoryToken>(token =>
        {
            token.IsMultiTenant();
            token.Property(t => t.Name).HasMaxLength(100);
            token.Property(t => t.Prefix).HasMaxLength(DirectoryToken.PrefixLength);
            token.HasIndex(t => t.Prefix).IsUnique();
            token.HasIndex("TenantId");
        });

        builder.Entity<DirectoryConnection>(connection =>
        {
            connection.IsMultiTenant();
            connection.Property(c => c.DefaultRole).HasMaxLength(32);
            connection.Property(c => c.LastFailureCode).HasMaxLength(64);
            connection.HasIndex("TenantId").IsUnique();
        });

        builder.Entity<ApiKey>(key =>
        {
            key.IsMultiTenant();
            key.Property(k => k.Name).HasMaxLength(100);
            key.Property(k => k.Prefix).HasMaxLength(ApiKey.PrefixLength);
            key.HasIndex(k => k.Prefix).IsUnique();
            key.HasIndex("TenantId");
        });

        // 沒有 IsMultiTenant()：稽核對營運者跨租戶，租戶是 SubjectTenantId 這個一般欄位。
        // 只能新增由遷移裡的觸發器強制。
        builder.Entity<AuditEvent>(audit =>
        {
            audit.ToTable("AuditEvents");
            audit.Property(a => a.ActorType).HasMaxLength(30);
            audit.Property(a => a.ActorId).HasMaxLength(320);
            audit.Property(a => a.Action).HasMaxLength(100);
            audit.Property(a => a.SubjectTenantId).HasMaxLength(100);
            audit.Property(a => a.TargetType).HasMaxLength(50);
            audit.Property(a => a.TargetId).HasMaxLength(100);
            audit.Property(a => a.TraceId).HasMaxLength(32);
            audit.Property(a => a.Detail).HasColumnType("jsonb");
            // 依 Id（UUIDv7，時間序）翻頁；各篩選條件都帶 Id 才能走索引。
            audit.HasIndex(a => new { a.SubjectTenantId, a.Id });
            audit.HasIndex(a => new { a.Action, a.Id });
        });

        // 沒有 IsMultiTenant()：理由同 AuditEvent（營運者跨租戶管理）。
        builder.Entity<TenantEntitlement>(entitlement =>
        {
            entitlement.ToTable("TenantEntitlements");
            entitlement.HasKey(e => e.TenantId);
            entitlement.Property(e => e.TenantId).HasMaxLength(100);
            entitlement.Property(e => e.TenantIdentifier).HasMaxLength(100);
            entitlement.Property(e => e.Plan).HasMaxLength(50);
            entitlement.HasIndex(e => e.TenantIdentifier).IsUnique();
        });

        builder.Entity<Conversation>(conversation =>
        {
            conversation.IsMultiTenant();
            conversation.Property(c => c.UserId).HasMaxLength(450);
            conversation.Property(c => c.Title).HasMaxLength(Conversation.TitleLength);
            // 清單：某位成員最近更新的在前。
            conversation.HasIndex(c => new { c.UserId, c.UpdatedAt });
            conversation.HasIndex(c => c.UpdatedAt);
            conversation.HasMany(c => c.Messages).WithOne().HasForeignKey(m => m.ConversationId).OnDelete(DeleteBehavior.Cascade);
        });

        builder.Entity<ConversationMessage>(message =>
        {
            message.IsMultiTenant();
            message.Property(m => m.Role).HasMaxLength(16);
            message.HasIndex(m => new { m.ConversationId, m.CreatedAt });
        });

        builder.Entity<UsageRecord>(record =>
        {
            record.IsMultiTenant();
            record.Property(r => r.Operation).HasMaxLength(100);
            record.HasIndex("TenantId", nameof(UsageRecord.OccurredAt));
        });

        builder.Entity<IdempotencyRecord>(record =>
        {
            record.IsMultiTenant();
            record.Property(r => r.Operation).HasMaxLength(100);
            record.Property(r => r.Key).HasMaxLength(EnvCite.Platform.SharedKernel.IIdempotencyStore.KeyLength);
            record.Property(r => r.Fingerprint).HasMaxLength(32);
            record.HasIndex("TenantId", nameof(IdempotencyRecord.Operation), nameof(IdempotencyRecord.Key)).IsUnique();
            // 過期清理依此掃描。
            record.HasIndex(r => r.ExpiresAt);
        });

        ConfigureOperators(builder);
    }

    /// <summary>
    /// 營運者的 Identity 資料表（<c>Operators</c>、<c>OperatorClaims</c>、<c>OperatorLogins</c>、<c>OperatorTokens</c>）。
    /// 照 <c>IdentityUserContext</c> 的對應來，只換型別與表名，所以 Identity 的 store 不必知道有兩套使用者。
    /// 沒有 <c>IsMultiTenant()</c>：營運者不屬於任何租戶（理由同 <see cref="AuditEvent"/>）。
    /// </summary>
    private static void ConfigureOperators(ModelBuilder builder)
    {
        builder.Entity<OperatorUser>(user =>
        {
            user.ToTable("Operators");
            user.HasKey(u => u.Id);
            user.HasIndex(u => u.NormalizedUserName).HasDatabaseName("OperatorUserNameIndex").IsUnique();
            user.HasIndex(u => u.NormalizedEmail).HasDatabaseName("OperatorEmailIndex").IsUnique();
            user.Property(u => u.ConcurrencyStamp).IsConcurrencyToken();
            user.Property(u => u.UserName).HasMaxLength(256);
            user.Property(u => u.NormalizedUserName).HasMaxLength(256);
            user.Property(u => u.Email).HasMaxLength(256);
            user.Property(u => u.NormalizedEmail).HasMaxLength(256);
            user.Property(u => u.DisplayName).HasMaxLength(200);
            user.HasMany<IdentityUserClaim<Guid>>().WithOne().HasForeignKey(c => c.UserId).IsRequired();
            user.HasMany<IdentityUserLogin<Guid>>().WithOne().HasForeignKey(l => l.UserId).IsRequired();
            user.HasMany<IdentityUserToken<Guid>>().WithOne().HasForeignKey(t => t.UserId).IsRequired();
        });

        builder.Entity<IdentityUserClaim<Guid>>(claim =>
        {
            claim.ToTable("OperatorClaims");
            claim.HasKey(c => c.Id);
        });

        builder.Entity<IdentityUserLogin<Guid>>(login =>
        {
            login.ToTable("OperatorLogins");
            login.HasKey(l => new { l.LoginProvider, l.ProviderKey });
        });

        builder.Entity<IdentityUserToken<Guid>>(token =>
        {
            token.ToTable("OperatorTokens");
            token.HasKey(t => new { t.UserId, t.LoginProvider, t.Name });
        });
    }
}