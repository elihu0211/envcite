using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace EnvCite.Platform.Data.Migrations
{
    /// <inheritdoc />
    public partial class ConversationArchive : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<DateTimeOffset>(
                name: "ArchivedAt",
                schema: "platform",
                table: "Conversations",
                type: "timestamp with time zone",
                nullable: true);

            migrationBuilder.CreateIndex(
                name: "IX_Conversations_UserId_ArchivedAt",
                schema: "platform",
                table: "Conversations",
                columns: new[] { "UserId", "ArchivedAt" },
                filter: "\"ArchivedAt\" IS NOT NULL");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropIndex(
                name: "IX_Conversations_UserId_ArchivedAt",
                schema: "platform",
                table: "Conversations");

            migrationBuilder.DropColumn(
                name: "ArchivedAt",
                schema: "platform",
                table: "Conversations");
        }
    }
}
