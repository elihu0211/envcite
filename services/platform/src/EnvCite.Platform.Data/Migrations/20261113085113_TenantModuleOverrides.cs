using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace EnvCite.Platform.Data.Migrations
{
    /// <inheritdoc />
    public partial class TenantModuleOverrides : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "TenantModuleOverrides",
                schema: "platform",
                columns: table => new
                {
                    TenantId = table.Column<string>(type: "character varying(100)", maxLength: 100, nullable: false),
                    ModuleKey = table.Column<string>(type: "character varying(64)", maxLength: 64, nullable: false),
                    Enabled = table.Column<bool>(type: "boolean", nullable: false),
                    UpdatedAt = table.Column<DateTimeOffset>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_TenantModuleOverrides", x => new { x.TenantId, x.ModuleKey });
                    table.ForeignKey(
                        name: "FK_TenantModuleOverrides_TenantEntitlements_TenantId",
                        column: x => x.TenantId,
                        principalSchema: "platform",
                        principalTable: "TenantEntitlements",
                        principalColumn: "TenantId",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.Sql("""
                INSERT INTO platform."TenantModuleOverrides" ("TenantId", "ModuleKey", "Enabled", "UpdatedAt")
                SELECT "TenantId", 'grounded-answer', TRUE, "UpdatedAt"
                FROM platform."TenantEntitlements"
                WHERE "AiAnswerEnabled" = TRUE
                ON CONFLICT ("TenantId", "ModuleKey") DO NOTHING;
                """);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "TenantModuleOverrides",
                schema: "platform");
        }
    }
}
