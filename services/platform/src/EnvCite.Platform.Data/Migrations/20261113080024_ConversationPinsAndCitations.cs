using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace EnvCite.Platform.Data.Migrations
{
    /// <inheritdoc />
    public partial class ConversationPinsAndCitations : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<DateTimeOffset>(
                name: "PinnedAt",
                schema: "platform",
                table: "Conversations",
                type: "timestamp with time zone",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "AsOf",
                schema: "platform",
                table: "ConversationMessages",
                type: "character varying(10)",
                maxLength: 10,
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "Citations",
                schema: "platform",
                table: "ConversationMessages",
                type: "jsonb",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "Outcome",
                schema: "platform",
                table: "ConversationMessages",
                type: "character varying(16)",
                maxLength: 16,
                nullable: true);

            migrationBuilder.CreateIndex(
                name: "IX_Conversations_UserId_PinnedAt",
                schema: "platform",
                table: "Conversations",
                columns: new[] { "UserId", "PinnedAt" },
                filter: "\"PinnedAt\" IS NOT NULL");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropIndex(
                name: "IX_Conversations_UserId_PinnedAt",
                schema: "platform",
                table: "Conversations");

            migrationBuilder.DropColumn(
                name: "PinnedAt",
                schema: "platform",
                table: "Conversations");

            migrationBuilder.DropColumn(
                name: "AsOf",
                schema: "platform",
                table: "ConversationMessages");

            migrationBuilder.DropColumn(
                name: "Citations",
                schema: "platform",
                table: "ConversationMessages");

            migrationBuilder.DropColumn(
                name: "Outcome",
                schema: "platform",
                table: "ConversationMessages");
        }
    }
}
