using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace EnvLaw.Platform.Data.Migrations
{
    /// <inheritdoc />
    public partial class MemberDisabled : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<bool>(
                name: "Disabled",
                schema: "platform",
                table: "AspNetUsers",
                type: "boolean",
                nullable: false,
                defaultValue: false);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "Disabled",
                schema: "platform",
                table: "AspNetUsers");
        }
    }
}
