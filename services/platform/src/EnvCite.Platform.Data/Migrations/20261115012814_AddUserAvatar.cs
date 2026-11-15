using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace EnvCite.Platform.Data.Migrations
{
    /// <inheritdoc />
    public partial class AddUserAvatar : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<string>(
                name: "AvatarContentType",
                schema: "platform",
                table: "AspNetUsers",
                type: "character varying(64)",
                maxLength: 64,
                nullable: true);

            migrationBuilder.AddColumn<byte[]>(
                name: "AvatarData",
                schema: "platform",
                table: "AspNetUsers",
                type: "bytea",
                nullable: true);

            migrationBuilder.AddColumn<int>(
                name: "AvatarVersion",
                schema: "platform",
                table: "AspNetUsers",
                type: "integer",
                nullable: false,
                defaultValue: 0);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "AvatarContentType",
                schema: "platform",
                table: "AspNetUsers");

            migrationBuilder.DropColumn(
                name: "AvatarData",
                schema: "platform",
                table: "AspNetUsers");

            migrationBuilder.DropColumn(
                name: "AvatarVersion",
                schema: "platform",
                table: "AspNetUsers");
        }
    }
}
