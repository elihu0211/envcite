using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace EnvLaw.Platform.Data.Migrations
{
    /// <inheritdoc />
    public partial class TotpReplayGuard : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<long>(
                name: "LastTotpStep",
                schema: "platform",
                table: "AspNetUsers",
                type: "bigint",
                nullable: false,
                defaultValue: 0L);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "LastTotpStep",
                schema: "platform",
                table: "AspNetUsers");
        }
    }
}
