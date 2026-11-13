using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace EnvCite.Platform.Data.Migrations
{
    /// <inheritdoc />
    public partial class TenantScopedUserLogins : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropPrimaryKey(
                name: "PK_AspNetUserLogins",
                schema: "platform",
                table: "AspNetUserLogins");

            migrationBuilder.AddPrimaryKey(
                name: "PK_AspNetUserLogins",
                schema: "platform",
                table: "AspNetUserLogins",
                columns: new[] { "LoginProvider", "ProviderKey", "TenantId" });
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropPrimaryKey(
                name: "PK_AspNetUserLogins",
                schema: "platform",
                table: "AspNetUserLogins");

            migrationBuilder.AddPrimaryKey(
                name: "PK_AspNetUserLogins",
                schema: "platform",
                table: "AspNetUserLogins",
                columns: new[] { "LoginProvider", "ProviderKey" });
        }
    }
}
