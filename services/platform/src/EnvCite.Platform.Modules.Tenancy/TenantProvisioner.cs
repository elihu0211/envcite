using System.Security.Claims;
using System.Text.RegularExpressions;

using EnvCite.Platform.SharedKernel;

using Finbuckle.MultiTenant.Abstractions;

using Microsoft.Extensions.DependencyInjection;

namespace EnvCite.Platform.Modules.Tenancy;

/// <summary>每次呼叫自己開 scope：營運後台的 Blazor 電路很長壽，不能把 DbContext 綁在電路上。</summary>
internal sealed partial class TenantProvisioner(IServiceScopeFactory scopes) : ITenantProvisioner
{
    [GeneratedRegex("^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$")]
    private static partial Regex IdentifierPattern();

    public async Task<AdminOutcome<CreatedTenant>> CreateAsync(
        ClaimsPrincipal actor, string identifier, string name, CancellationToken cancellationToken)
    {
        // JSON 繫結可能給 null（請求缺欄位），型別系統擋不住。
        if (string.IsNullOrEmpty(identifier) || !IdentifierPattern().IsMatch(identifier))
        {
            return AdminOutcome.Invalid<CreatedTenant>(OutcomeCodes.TenantIdentifierInvalid);
        }

        if (string.IsNullOrWhiteSpace(name))
        {
            return AdminOutcome.Invalid<CreatedTenant>(OutcomeCodes.TenantNameRequired);
        }

        await using var scope = scopes.CreateAsyncScope();
        var store = scope.ServiceProvider.GetRequiredService<IMultiTenantStore<TenantInfo>>();
        var audit = scope.ServiceProvider.GetRequiredService<IAuditLog>();

        if (await store.GetByIdentifierAsync(identifier) is not null)
        {
            return AdminOutcome.Conflict<CreatedTenant>(OutcomeCodes.TenantIdentifierTaken);
        }

        var tenant = new TenantInfo
        {
            Id = Guid.CreateVersion7().ToString(),
            Identifier = identifier,
            Name = name.Trim(),
        };

        if (!await store.AddAsync(tenant))
        {
            return AdminOutcome.Conflict<CreatedTenant>(OutcomeCodes.TenantIdentifierTaken);
        }

        // 租戶 store 是另一個 DbContext，無法與稽核同交易；建立成功後立刻記錄。
        // 記錄失敗會讓這個呼叫失敗（不靜默吞掉）：租戶已建立，重送會得到衝突，營運者需查稽核是否缺這筆。
        await audit.AppendAsync(actor, new AuditEntry(
            AuditActions.TenantCreated, tenant.Id, "tenant", tenant.Id,
            new Dictionary<string, object?> { ["identifier"] = tenant.Identifier, ["name"] = tenant.Name }), cancellationToken);

        return AdminOutcome.Ok(new CreatedTenant(tenant.Id, tenant.Identifier, tenant.Name));
    }
}