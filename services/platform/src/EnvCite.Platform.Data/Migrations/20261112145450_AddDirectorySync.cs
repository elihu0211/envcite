using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace EnvCite.Platform.Data.Migrations
{
    /// <inheritdoc />
    public partial class AddDirectorySync : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<string>(
                name: "ExternalId",
                schema: "platform",
                table: "AspNetUsers",
                type: "character varying(200)",
                maxLength: 200,
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "ManagedBy",
                schema: "platform",
                table: "AspNetUsers",
                type: "character varying(32)",
                maxLength: 32,
                nullable: true);

            migrationBuilder.AddColumn<DateTimeOffset>(
                name: "SyncedAt",
                schema: "platform",
                table: "AspNetUsers",
                type: "timestamp with time zone",
                nullable: true);

            migrationBuilder.CreateTable(
                name: "DirectoryConnections",
                schema: "platform",
                columns: table => new
                {
                    Id = table.Column<Guid>(type: "uuid", nullable: false),
                    Enabled = table.Column<bool>(type: "boolean", nullable: false),
                    DefaultRole = table.Column<string>(type: "character varying(32)", maxLength: 32, nullable: false),
                    AdoptExistingMembers = table.Column<bool>(type: "boolean", nullable: false),
                    LastRequestAt = table.Column<DateTimeOffset>(type: "timestamp with time zone", nullable: true),
                    LastFailureCode = table.Column<string>(type: "character varying(64)", maxLength: 64, nullable: true),
                    CreatedCount = table.Column<long>(type: "bigint", nullable: false),
                    UpdatedCount = table.Column<long>(type: "bigint", nullable: false),
                    DeactivatedCount = table.Column<long>(type: "bigint", nullable: false),
                    RejectedCount = table.Column<long>(type: "bigint", nullable: false),
                    UpdatedAt = table.Column<DateTimeOffset>(type: "timestamp with time zone", nullable: false),
                    TenantId = table.Column<string>(type: "text", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_DirectoryConnections", x => x.Id);
                });

            migrationBuilder.CreateTable(
                name: "DirectoryTokens",
                schema: "platform",
                columns: table => new
                {
                    Id = table.Column<Guid>(type: "uuid", nullable: false),
                    Name = table.Column<string>(type: "character varying(100)", maxLength: 100, nullable: false),
                    Prefix = table.Column<string>(type: "character varying(12)", maxLength: 12, nullable: false),
                    SecretHash = table.Column<byte[]>(type: "bytea", nullable: false),
                    CreatedAt = table.Column<DateTimeOffset>(type: "timestamp with time zone", nullable: false),
                    ExpiresAt = table.Column<DateTimeOffset>(type: "timestamp with time zone", nullable: true),
                    RevokedAt = table.Column<DateTimeOffset>(type: "timestamp with time zone", nullable: true),
                    LastUsedAt = table.Column<DateTimeOffset>(type: "timestamp with time zone", nullable: true),
                    TenantId = table.Column<string>(type: "text", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_DirectoryTokens", x => x.Id);
                });

            migrationBuilder.CreateIndex(
                name: "IX_AspNetUsers_TenantId_ExternalId",
                schema: "platform",
                table: "AspNetUsers",
                columns: new[] { "TenantId", "ExternalId" },
                unique: true,
                filter: "\"ExternalId\" IS NOT NULL");

            migrationBuilder.CreateIndex(
                name: "IX_DirectoryConnections_TenantId",
                schema: "platform",
                table: "DirectoryConnections",
                column: "TenantId",
                unique: true);

            migrationBuilder.CreateIndex(
                name: "IX_DirectoryTokens_Prefix",
                schema: "platform",
                table: "DirectoryTokens",
                column: "Prefix",
                unique: true);

            migrationBuilder.CreateIndex(
                name: "IX_DirectoryTokens_TenantId",
                schema: "platform",
                table: "DirectoryTokens",
                column: "TenantId");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "DirectoryConnections",
                schema: "platform");

            migrationBuilder.DropTable(
                name: "DirectoryTokens",
                schema: "platform");

            migrationBuilder.DropIndex(
                name: "IX_AspNetUsers_TenantId_ExternalId",
                schema: "platform",
                table: "AspNetUsers");

            migrationBuilder.DropColumn(
                name: "ExternalId",
                schema: "platform",
                table: "AspNetUsers");

            migrationBuilder.DropColumn(
                name: "ManagedBy",
                schema: "platform",
                table: "AspNetUsers");

            migrationBuilder.DropColumn(
                name: "SyncedAt",
                schema: "platform",
                table: "AspNetUsers");
        }
    }
}
