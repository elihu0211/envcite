using System.Security.Claims;

using EnvCite.Platform.Data;
using EnvCite.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.DependencyInjection;

namespace EnvCite.Platform.Modules.ApiAccess;

/// <summary>
/// 指派方案：權益那一列與稽核事件在同一個交易裡寫；寫成功之後本機的快照立即更新，
/// 其他副本在下一次重新載入內看到（見 <see cref="PlanCatalog"/>）。
/// </summary>
internal sealed class PlanAdmin(IServiceScopeFactory scopes, PlanCatalog catalog, TimeProvider time) : IPlanAdmin
{
    public IReadOnlyList<PlanInfo> Plans => catalog.Plans
        .OrderBy(p => p.Key, StringComparer.Ordinal)
        .Select(p => new PlanInfo(p.Key, p.Value.PermitLimit, p.Value.Window))
        .ToList();

    public async Task<AdminOutcome<PlanChange>> SetAsync(
        ClaimsPrincipal actor, string tenantId, string? plan, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var store = scope.ServiceProvider.GetRequiredService<IMultiTenantStore<TenantInfo>>();
        if (await store.GetAsync(tenantId) is not { Identifier: { } identifier } tenant)
        {
            return AdminOutcome.NotFound<PlanChange>("Tenant not found.");
        }

        if (plan is not null && !catalog.Plans.ContainsKey(plan))
        {
            return AdminOutcome.Invalid<PlanChange>(
                $"Unknown plan '{plan}'. Defined plans: {string.Join(", ", catalog.Plans.Keys.Order(StringComparer.Ordinal))}.");
        }

        var db = scope.ServiceProvider.GetRequiredService<PlatformDbContext>();
        var existing = await db.Set<TenantEntitlement>().SingleOrDefaultAsync(e => e.TenantId == tenant.Id, cancellationToken);
        var previous = catalog.NameFor(identifier);

        if (plan is null)
        {
            if (existing is not null)
            {
                db.Remove(existing);
                db.StageAudit(actor, Audit(AuditActions.TenantPlanRemoved, tenant, previous, null), time);
                await db.SaveChangesAsync(cancellationToken);
                catalog.Set(identifier, null);
            }
        }
        else if (existing?.Plan != plan)
        {
            // 同一個方案再指派一次：不改資料也不留紀錄（冪等）。
            if (existing is null)
            {
                db.Add(new TenantEntitlement
                {
                    TenantId = tenant.Id!,
                    TenantIdentifier = identifier,
                    Plan = plan,
                    UpdatedAt = time.GetUtcNow(),
                });
            }
            else
            {
                existing.Plan = plan;
                existing.UpdatedAt = time.GetUtcNow();
            }

            db.StageAudit(actor, Audit(AuditActions.TenantPlanAssigned, tenant, previous, plan), time);
            await db.SaveChangesAsync(cancellationToken);
            catalog.Set(identifier, plan);
        }

        return AdminOutcome.Ok(new PlanChange(tenant.Id!, previous, catalog.NameFor(identifier)));
    }

    public async Task<AdminOutcome<bool>> SetAiAnswerAsync(
        ClaimsPrincipal actor, string tenantId, bool enabled, CancellationToken cancellationToken)
    {
        await using var scope = scopes.CreateAsyncScope();
        var store = scope.ServiceProvider.GetRequiredService<IMultiTenantStore<TenantInfo>>();
        if (await store.GetAsync(tenantId) is not { Identifier: { } identifier } tenant)
        {
            return AdminOutcome.NotFound<bool>("Tenant not found.");
        }

        var db = scope.ServiceProvider.GetRequiredService<PlatformDbContext>();
        var existing = await db.Set<TenantEntitlement>().SingleOrDefaultAsync(e => e.TenantId == tenant.Id, cancellationToken);
        if (existing is null)
        {
            return AdminOutcome.Invalid<bool>("The tenant has no plan assigned in the database; assign one first (consent follows the plan).");
        }

        if (existing.AiAnswerEnabled != enabled)
        {
            existing.AiAnswerEnabled = enabled;
            existing.UpdatedAt = time.GetUtcNow();
            db.StageAudit(
                actor,
                new AuditEntry(
                    enabled ? AuditActions.TenantAiAnswerEnabled : AuditActions.TenantAiAnswerDisabled,
                    tenant.Id, "tenant", tenant.Id, new Dictionary<string, object?> { ["identifier"] = identifier }),
                time);
            await db.SaveChangesAsync(cancellationToken);
        }

        return AdminOutcome.Ok(enabled);
    }

    private static AuditEntry Audit(string action, TenantInfo tenant, string? previous, string? current) =>
        new(action, tenant.Id, "tenant", tenant.Id,
            new Dictionary<string, object?> { ["identifier"] = tenant.Identifier, ["previous"] = previous, ["plan"] = current });
}