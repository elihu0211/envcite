using Npgsql;

namespace EnvCite.Platform.Tests;

// 部署時執行期以群組 envcite_platform_app 的登入身分連線，不是資料庫擁有者；權限由遷移授予。
[Collection(PlatformTestGroup.Name)]
public sealed class DatabaseRoleTests(PlatformFactory factory)
{
    private const string AppRole = "envcite_platform_app";

    private async Task<string> OwnerConnectionStringAsync()
    {
        await using var scope = factory.Services.CreateAsyncScope();
        return scope.ServiceProvider.GetRequiredService<PlatformDbContext>().Database.GetConnectionString()!;
    }

    [Fact]
    public async Task TheAppRoleCanUseEveryTableButOnlyAppendAuditEvents()
    {
        await using var connection = new NpgsqlConnection(await OwnerConnectionStringAsync());
        await connection.OpenAsync(TestContext.Current.CancellationToken);
        await using var command = new NpgsqlCommand(
            """
            SELECT n.nspname || '.' || c.relname,
                   has_table_privilege(@role, c.oid, 'SELECT'),
                   has_table_privilege(@role, c.oid, 'INSERT'),
                   has_table_privilege(@role, c.oid, 'UPDATE'),
                   has_table_privilege(@role, c.oid, 'DELETE'),
                   pg_get_userbyid(c.relowner) = @role
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname IN ('platform', 'tenancy') AND c.relkind IN ('r', 'p')
            """, connection);
        command.Parameters.AddWithValue("role", AppRole);

        var tables = new Dictionary<string, (bool Select, bool Insert, bool Update, bool Delete, bool Owns)>();
        await using (var reader = await command.ExecuteReaderAsync(TestContext.Current.CancellationToken))
        {
            while (await reader.ReadAsync(TestContext.Current.CancellationToken))
                tables[reader.GetString(0)] = (reader.GetBoolean(1), reader.GetBoolean(2), reader.GetBoolean(3), reader.GetBoolean(4), reader.GetBoolean(5));
        }

        Assert.Contains("tenancy.TenantInfo", tables.Keys);
        foreach (var (table, privileges) in tables)
        {
            var appendOnly = table == "platform.AuditEvents";
            Assert.False(privileges.Owns, table);
            Assert.True(privileges.Select && privileges.Insert, table);
            Assert.Equal(!appendOnly, privileges.Update);
            Assert.Equal(!appendOnly, privileges.Delete);
        }
    }

    [Fact]
    public async Task TheAppRoleCannotDisableTheAuditTrigger()
    {
        var owner = new NpgsqlConnectionStringBuilder(await OwnerConnectionStringAsync());
        var login = $"app_{Guid.NewGuid():N}"[..20];
        const string password = "test";
        await using (var connection = new NpgsqlConnection(owner.ConnectionString))
        {
            await connection.OpenAsync(TestContext.Current.CancellationToken);
            await using var create = new NpgsqlCommand($"CREATE ROLE {login} LOGIN PASSWORD '{password}' IN ROLE {AppRole}", connection);
            await create.ExecuteNonQueryAsync(TestContext.Current.CancellationToken);
        }

        await using var app = new NpgsqlConnection(new NpgsqlConnectionStringBuilder(owner.ConnectionString) { Username = login, Password = password }.ConnectionString);
        await app.OpenAsync(TestContext.Current.CancellationToken);
        await using var disable = new NpgsqlCommand("ALTER TABLE platform.\"AuditEvents\" DISABLE TRIGGER audit_events_no_update_delete", app);

        var failure = await Assert.ThrowsAsync<PostgresException>(() => disable.ExecuteNonQueryAsync(TestContext.Current.CancellationToken));

        Assert.Equal(PostgresErrorCodes.InsufficientPrivilege, failure.SqlState);
    }
}