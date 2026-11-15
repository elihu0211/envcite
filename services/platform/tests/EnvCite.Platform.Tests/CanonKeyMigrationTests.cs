using Finbuckle.MultiTenant.Abstractions;
using Finbuckle.MultiTenant.EntityFrameworkCore;

using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;

using Npgsql;

using Testcontainers.PostgreSql;

namespace EnvCite.Platform.Tests;

/// <summary>遷移 RenameOntologyKeysToCanon：已寫入的計量鍵與 API key 範圍換成 canon 前綴，其他值不動。</summary>
public sealed class CanonKeyMigrationTests
{
    private const string Before = "20261115012814_AddUserAvatar";

    [Fact]
    public async Task TheKeysAlreadyStoredTakeTheServicePrefix()
    {
        var ct = TestContext.Current.CancellationToken;
        await using var postgres = new PostgreSqlBuilder("postgres:18").Build();
        await postgres.StartAsync(ct);

        var options = new DbContextOptionsBuilder<PlatformDbContext>();
        // 歷史表的位置與應用程式相同（PlatformDataModule.Configure），否則會對著另一張表判斷已套用的遷移。
        options.UseNpgsql(postgres.GetConnectionString(), npgsql => npgsql.MigrationsHistoryTable("__EFMigrationsHistory", "platform"));
        await using var db = MultiTenantDbContext.Create<PlatformDbContext, TenantInfo>(
            new TenantInfo { Id = "migration", Identifier = "migration" }, options.Options);
        var migrator = db.GetService<IMigrator>();

        await migrator.MigrateAsync(Before, ct);
        await using var connection = new NpgsqlConnection(postgres.GetConnectionString());
        await connection.OpenAsync(ct);
        await ExecuteAsync(connection,
            """
            INSERT INTO platform."UsageRecords" ("Id", "OccurredAt", "Operation", "TenantId", "Units") VALUES
                (gen_random_uuid(), now(), 'ontology.search', 't1', 1),
                (gen_random_uuid(), now(), 'ontology.get_law', 't1', 2),
                (gen_random_uuid(), now(), 'consult.ground', 't1', 3);
            INSERT INTO platform."ApiKeys" ("Id", "CreatedAt", "Name", "Prefix", "Scopes", "SecretHash", "TenantId") VALUES
                (gen_random_uuid(), now(), 'a', 'sk_live_aaa', ARRAY['ontology:read', 'other:write'], '\x00', 't1'),
                (gen_random_uuid(), now(), 'b', 'sk_live_bbb', ARRAY['other:read'], '\x00', 't1');
            """, ct);

        await migrator.MigrateAsync(null, ct);

        Assert.Equal(["canon.get_law", "canon.search", "consult.ground"],
            await ColumnAsync(connection, """SELECT "Operation" FROM platform."UsageRecords" ORDER BY 1""", ct));
        Assert.Equal(["{canon:read,other:write}", "{other:read}"],
            await ColumnAsync(connection, """SELECT "Scopes"::text FROM platform."ApiKeys" ORDER BY "Name" """, ct));

        await migrator.MigrateAsync(Before, ct);

        Assert.Equal(["consult.ground", "ontology.get_law", "ontology.search"],
            await ColumnAsync(connection, """SELECT "Operation" FROM platform."UsageRecords" ORDER BY 1""", ct));
        Assert.Equal(["{ontology:read,other:write}", "{other:read}"],
            await ColumnAsync(connection, """SELECT "Scopes"::text FROM platform."ApiKeys" ORDER BY "Name" """, ct));
    }

    private static async Task ExecuteAsync(NpgsqlConnection connection, string sql, CancellationToken ct)
    {
        await using var command = new NpgsqlCommand(sql, connection);
        await command.ExecuteNonQueryAsync(ct);
    }

    private static async Task<string[]> ColumnAsync(NpgsqlConnection connection, string sql, CancellationToken ct)
    {
        await using var command = new NpgsqlCommand(sql, connection);
        await using var reader = await command.ExecuteReaderAsync(ct);
        var values = new List<string>();
        while (await reader.ReadAsync(ct))
            values.Add(reader.GetString(0));
        return [.. values];
    }
}