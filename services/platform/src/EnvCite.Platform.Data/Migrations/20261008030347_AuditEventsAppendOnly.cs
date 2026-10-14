using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace EnvCite.Platform.Data.Migrations
{
    /// <inheritdoc />
    public partial class AuditEventsAppendOnly : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            // 稽核只能新增。以觸發器在資料庫層強制（含 TRUNCATE），不靠程式自律。
            // 這是手寫 SQL（EF 不會從模型產生觸發器）；擁有者或超級使用者仍可停用觸發器，
            // 所以正式環境的應用程式帳號不該是這張表的擁有者。
            migrationBuilder.Sql(@"
CREATE FUNCTION platform.audit_events_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'platform.""AuditEvents"" is append-only' USING ERRCODE = 'integrity_constraint_violation';
END
$$;

CREATE TRIGGER audit_events_no_update_delete
    BEFORE UPDATE OR DELETE ON platform.""AuditEvents""
    FOR EACH ROW EXECUTE FUNCTION platform.audit_events_append_only();

CREATE TRIGGER audit_events_no_truncate
    BEFORE TRUNCATE ON platform.""AuditEvents""
    FOR EACH STATEMENT EXECUTE FUNCTION platform.audit_events_append_only();");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.Sql(@"
DROP TRIGGER audit_events_no_truncate ON platform.""AuditEvents"";
DROP TRIGGER audit_events_no_update_delete ON platform.""AuditEvents"";
DROP FUNCTION platform.audit_events_append_only();");
        }
    }
}
