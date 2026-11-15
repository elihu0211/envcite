using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace EnvCite.Platform.Data.Migrations
{
    /// <inheritdoc />
    public partial class RenameOntologyKeysToCanon : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder) => Rename(migrationBuilder, "ontology", "canon");

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder) => Rename(migrationBuilder, "canon", "ontology");

        // 計量鍵與 API key 範圍以服務名為前綴（同 consult.*）；已寫入的值跟著改名。
        // 稽核事件是只增的（AuditEventsAppendOnly），不改。
        private static void Rename(MigrationBuilder migrationBuilder, string from, string to)
        {
            foreach (var table in new[] { "UsageRecords", "IdempotencyRecords" })
            {
                migrationBuilder.Sql(
                    $"""
                    UPDATE platform."{table}"
                       SET "Operation" = '{to}.' || substr("Operation", {from.Length + 2})
                     WHERE "Operation" LIKE '{from}.%';
                    """);
            }

            migrationBuilder.Sql(
                $"""
                UPDATE platform."ApiKeys"
                   SET "Scopes" = ARRAY(
                       SELECT CASE WHEN s.scope LIKE '{from}:%'
                                   THEN '{to}:' || substr(s.scope, {from.Length + 2})
                                   ELSE s.scope END
                         FROM unnest("Scopes") WITH ORDINALITY AS s(scope, position)
                        ORDER BY s.position)
                 WHERE EXISTS (SELECT 1 FROM unnest("Scopes") AS s(scope) WHERE s.scope LIKE '{from}:%');
                """);
        }
    }
}
