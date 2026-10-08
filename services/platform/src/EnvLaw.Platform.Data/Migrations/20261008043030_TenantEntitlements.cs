using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace EnvLaw.Platform.Data.Migrations
{
    /// <inheritdoc />
    public partial class TenantEntitlements : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "TenantEntitlements",
                schema: "platform",
                columns: table => new
                {
                    TenantId = table.Column<string>(type: "character varying(100)", maxLength: 100, nullable: false),
                    TenantIdentifier = table.Column<string>(type: "character varying(100)", maxLength: 100, nullable: false),
                    Plan = table.Column<string>(type: "character varying(50)", maxLength: 50, nullable: false),
                    UpdatedAt = table.Column<DateTimeOffset>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_TenantEntitlements", x => x.TenantId);
                });

            migrationBuilder.CreateIndex(
                name: "IX_TenantEntitlements_TenantIdentifier",
                schema: "platform",
                table: "TenantEntitlements",
                column: "TenantIdentifier",
                unique: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "TenantEntitlements",
                schema: "platform");
        }
    }
}
