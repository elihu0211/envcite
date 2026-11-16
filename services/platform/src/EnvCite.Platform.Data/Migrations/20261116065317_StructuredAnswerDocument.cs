using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace EnvCite.Platform.Data.Migrations
{
    /// <inheritdoc />
    public partial class StructuredAnswerDocument : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AlterColumn<string>(
                name: "Content",
                schema: "platform",
                table: "ConversationMessages",
                type: "text",
                nullable: true,
                oldClrType: typeof(string),
                oldType: "text");

            migrationBuilder.AddColumn<string>(
                name: "Document",
                schema: "platform",
                table: "ConversationMessages",
                type: "jsonb",
                nullable: true);

            migrationBuilder.AddColumn<short>(
                name: "SchemaVersion",
                schema: "platform",
                table: "ConversationMessages",
                type: "smallint",
                nullable: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "Document",
                schema: "platform",
                table: "ConversationMessages");

            migrationBuilder.DropColumn(
                name: "SchemaVersion",
                schema: "platform",
                table: "ConversationMessages");

            migrationBuilder.AlterColumn<string>(
                name: "Content",
                schema: "platform",
                table: "ConversationMessages",
                type: "text",
                nullable: false,
                defaultValue: "",
                oldClrType: typeof(string),
                oldType: "text",
                oldNullable: true);
        }
    }
}
