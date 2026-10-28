using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace EnvCite.Platform.Modules.Tenancy.Migrations
{
    /// <inheritdoc />
    public partial class AppRoleGrants : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            // 執行期以群組 envcite_platform_app 的登入身分連線，不是資料庫擁有者：擁有者能停用
            // 稽核表的觸發器。手寫 SQL（EF 不產生授權）。角色是叢集層級的物件，部署環境的擁有者
            // 沒有建角色的權限，由部署庫先建好；只在不存在時（本機、測試）於此建立。
            // 預設權限只涵蓋本遷移的身分之後建的表，所以遷移必須以擁有者執行。
            migrationBuilder.Sql(@"
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'envcite_platform_app') THEN
        CREATE ROLE envcite_platform_app NOLOGIN;
    END IF;
END
$$;

GRANT USAGE ON SCHEMA tenancy TO envcite_platform_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA tenancy TO envcite_platform_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA tenancy TO envcite_platform_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA tenancy
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO envcite_platform_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA tenancy
    GRANT USAGE, SELECT ON SEQUENCES TO envcite_platform_app;");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.Sql(@"
ALTER DEFAULT PRIVILEGES IN SCHEMA tenancy
    REVOKE USAGE, SELECT ON SEQUENCES FROM envcite_platform_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA tenancy
    REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM envcite_platform_app;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA tenancy FROM envcite_platform_app;
REVOKE ALL ON ALL TABLES IN SCHEMA tenancy FROM envcite_platform_app;
REVOKE USAGE ON SCHEMA tenancy FROM envcite_platform_app;");
        }
    }
}
