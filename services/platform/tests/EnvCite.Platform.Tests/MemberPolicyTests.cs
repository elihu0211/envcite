using EnvCite.Platform.Modules.Members;
using EnvCite.Platform.SharedKernel;

namespace EnvCite.Platform.Tests;

/// <summary>成員管理的階層矩陣：每一格都寫出來，改規則時看得到改了哪一格。</summary>
public sealed class MemberPolicyTests
{
    [Theory]
    // 擁有者管全部。
    [InlineData(CustomerRoles.Owner, CustomerRoles.Owner, true)]
    [InlineData(CustomerRoles.Owner, CustomerRoles.Admin, true)]
    [InlineData(CustomerRoles.Owner, CustomerRoles.Developer, true)]
    [InlineData(CustomerRoles.Owner, CustomerRoles.Billing, true)]
    [InlineData(CustomerRoles.Owner, CustomerRoles.Viewer, true)]
    // 管理員只管三個下層角色；不能碰擁有者，也不能碰另一位管理員。
    [InlineData(CustomerRoles.Admin, CustomerRoles.Owner, false)]
    [InlineData(CustomerRoles.Admin, CustomerRoles.Admin, false)]
    [InlineData(CustomerRoles.Admin, CustomerRoles.Developer, true)]
    [InlineData(CustomerRoles.Admin, CustomerRoles.Billing, true)]
    [InlineData(CustomerRoles.Admin, CustomerRoles.Viewer, true)]
    // 其他角色與沒有角色：什麼都不能管。
    [InlineData(CustomerRoles.Developer, CustomerRoles.Viewer, false)]
    [InlineData(CustomerRoles.Billing, CustomerRoles.Viewer, false)]
    [InlineData(CustomerRoles.Viewer, CustomerRoles.Viewer, false)]
    [InlineData("", CustomerRoles.Viewer, false)]
    [InlineData(null, CustomerRoles.Viewer, false)]
    [InlineData("SuperAdmin", CustomerRoles.Viewer, false)]
    public void WhoCanManageWhom(string? actor, string target, bool expected) =>
        Assert.Equal(expected, MemberPolicy.CanManage(actor, target));

    [Fact]
    public void WhatYouCanManageIsExactlyWhatYouCanGrantSoNobodyIsPromotedAboveTheirManager()
    {
        foreach (var actor in CustomerRoles.All)
        {
            foreach (var role in CustomerRoles.All)
            {
                Assert.Equal(MemberPolicy.CanManage(actor, role), MemberPolicy.Grantable(actor).Contains(role));
            }
        }
    }

    [Theory]
    [InlineData(CustomerRoles.Owner, true)]
    [InlineData(CustomerRoles.Admin, true)]
    [InlineData(CustomerRoles.Developer, false)]
    [InlineData(CustomerRoles.Billing, false)]
    [InlineData(CustomerRoles.Viewer, false)]
    [InlineData(null, false)]
    public void OnlyOwnersAndAdminsAreManagers(string? role, bool expected) =>
        Assert.Equal(expected, MemberPolicy.IsManager(role));

    [Fact]
    public void AnAdminCanNeverGrantAnAdminOrOwnerRole()
    {
        var grantable = MemberPolicy.Grantable(CustomerRoles.Admin);

        Assert.DoesNotContain(CustomerRoles.Owner, grantable);
        Assert.DoesNotContain(CustomerRoles.Admin, grantable);
    }
}