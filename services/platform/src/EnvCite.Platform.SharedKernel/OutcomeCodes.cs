using System.Collections.Frozen;
using System.Globalization;

namespace EnvCite.Platform.SharedKernel;

/// <summary>
/// 營運操作失敗的穩定代碼（<see cref="AdminOutcome{T}.Code"/>）。代碼是契約：呼叫端（API 的使用者、介面）依代碼判斷，
/// 不依賴文字。<see cref="AdminOutcome{T}.Message"/> 永遠是這裡的英文，只供 API 的 <c>detail</c> 與日誌；
/// 介面依代碼取使用者的語言（<c>Labels</c> 資源檔的 <c>Outcome.{代碼}</c>）。
/// 新增代碼要同時加英文（這裡）與中英文資源檔；測試守住三者一致。
/// </summary>
public static class OutcomeCodes
{
    public const string TenantNotFound = "tenant.not_found";
    public const string MemberNotFound = "member.not_found";
    public const string OperatorNotFound = "operator.not_found";
    public const string ApiKeyNotFound = "api_key.not_found";
    public const string AccountNotFound = "account.not_found";
    public const string ApiKeyInvalid = "api_key.invalid";
    public const string PlanUnknown = "plan.unknown";
    public const string PlanRequiredForConsent = "plan.required_for_consent";
    public const string QuestionRequired = "question.required";
    public const string UpstreamInvalidRequest = "upstream.invalid_request";
    public const string EmailInvalid = "email.invalid";
    public const string ActivationLinkInvalid = "activation.link_invalid";
    public const string MailNotEnabled = "mail.not_enabled";
    public const string AccountAlreadyActive = "account.already_active";
    public const string AccountDisabled = "account.disabled";
    public const string RoleInvalid = "role.invalid";
    public const string DisplayNameTooLong = "display_name.too_long";
    public const string PasswordSameAsCurrent = "password.same_as_current";
    public const string PasswordCurrentIncorrect = "password.current_incorrect";
    public const string PasswordTooShort = "password.too_short";
    public const string PasswordTooLong = "password.too_long";
    public const string PasswordTooGuessable = "password.too_guessable";
    public const string PasswordContainsAccountInfo = "password.contains_account_info";
    public const string PasswordNotAcceptable = "password.not_acceptable";
    public const string IdentityRejected = "identity.rejected";
    public const string TwoFactorAlreadyEnabled = "two_factor.already_enabled";
    public const string TwoFactorNotEnabled = "two_factor.not_enabled";
    public const string TwoFactorRequiredForRole = "two_factor.required_for_role";
    public const string CredentialsIncorrect = "credentials.incorrect";
    public const string TenantIdentifierInvalid = "tenant.identifier_invalid";
    public const string TenantNameRequired = "tenant.name_required";
    public const string MemberEmailTaken = "member.email_taken";
    public const string OperatorEmailTaken = "operator.email_taken";
    public const string TenantIdentifierTaken = "tenant.identifier_taken";
    public const string MemberAddForbidden = "member.add_forbidden";
    public const string MemberAddRoleForbidden = "member.add_role_forbidden";
    public const string MemberAssignRoleForbidden = "member.assign_role_forbidden";
    public const string MemberManageForbidden = "member.manage_forbidden";
    public const string MemberManageSelfForbidden = "member.manage_self_forbidden";
    public const string MemberManageTargetForbidden = "member.manage_target_forbidden";
    public const string AnswerNotConfigured = "answer.not_configured";
    public const string ConsultUnavailable = "consult.unavailable";
    public const string ModelUnavailable = "model.unavailable";
    public const string OntologyUnavailable = "ontology.unavailable";
    public const string VerificationCodeIncorrect = "verification_code.incorrect";
    public const string VerificationCodeIncorrectHint = "verification_code.incorrect_hint";
    public const string DirectoryNotEnabled = "directory.not_enabled";
    public const string DirectoryOwnerOnly = "directory.owner_only";
    public const string DirectoryLastOwner = "directory.last_owner";
    public const string DirectoryMemberManaged = "directory.member_managed";
    public const string DirectoryNotManaged = "directory.not_managed";
    public const string DirectoryRoleNotMappable = "directory.role_not_mappable";
    public const string DirectoryEmailImmutable = "directory.email_immutable";
    public const string DirectoryExternalIdTaken = "directory.external_id_taken";
    public const string DirectoryExternalIdInvalid = "directory.external_id_invalid";
    public const string DirectoryAdoptRequired = "directory.adopt_required";
    public const string DirectoryTokenNotFound = "directory.token_not_found";
    public const string DirectoryTokenInvalid = "directory.token_invalid";
    public const string DirectoryGroupNotFound = "directory.group_not_found";
    public const string DirectoryGroupNameTaken = "directory.group_name_taken";
    public const string DirectoryGroupInvalid = "directory.group_invalid";
    public const string DirectoryGroupMemberInvalid = "directory.group_member_invalid";

    /// <summary>所有代碼的英文說明；{0}、{1} 是 <see cref="AdminOutcome{T}.Args"/>。</summary>
    public static IReadOnlyDictionary<string, string> English { get; } = new Dictionary<string, string>
    {
        [TenantNotFound] = "Tenant not found.",
        [MemberNotFound] = "Member not found.",
        [OperatorNotFound] = "Operator not found.",
        [ApiKeyNotFound] = "API key not found.",
        [AccountNotFound] = "Account not found.",
        [ApiKeyInvalid] = "Name is required (max 100 chars); ExpiresAt must be in the future.",
        [PlanUnknown] = "Unknown plan '{0}'. Defined plans: {1}.",
        [PlanRequiredForConsent] = "The tenant has no plan assigned in the database; assign one first (consent follows the plan).",
        [QuestionRequired] = "question is required",
        [UpstreamInvalidRequest] = "{0}",
        [EmailInvalid] = "Email address is not valid.",
        [ActivationLinkInvalid] = "This link is invalid or has expired. Ask an administrator to send a new invitation.",
        [MailNotEnabled] = "Sending mail is not enabled in this environment.",
        [AccountAlreadyActive] = "This account is already active and its email is verified.",
        [AccountDisabled] = "This account is disabled; enable it first.",
        [RoleInvalid] = "Role is not allowed.",
        [DisplayNameTooLong] = "Display name is limited to 200 characters.",
        [PasswordSameAsCurrent] = "The new password must differ from the current one.",
        [PasswordCurrentIncorrect] = "The current password is incorrect.",
        [PasswordTooShort] = "The new password must be at least {0} characters.",
        [PasswordTooLong] = "The new password must be at most {0} characters.",
        [PasswordTooGuessable] = "This password is too easy to guess. Try a few unrelated words strung together.",
        [PasswordContainsAccountInfo] = "The new password must not contain your email.",
        [PasswordNotAcceptable] = "The new password does not meet the rules.",
        [IdentityRejected] = "{0}",
        [TwoFactorAlreadyEnabled] = "Two-step verification is already enabled.",
        [TwoFactorNotEnabled] = "Two-step verification is not enabled.",
        [TwoFactorRequiredForRole] = "Your role must use two-step verification, so it cannot be turned off.",
        [CredentialsIncorrect] = "The password or verification code is incorrect.",
        [TenantIdentifierInvalid] = "Identifier must be 3–63 characters: lowercase letters, digits and hyphens, starting and ending with a letter or digit.",
        [TenantNameRequired] = "Name must not be empty.",
        [MemberEmailTaken] = "This tenant already has a member with this email.",
        [OperatorEmailTaken] = "An operator with this email already exists.",
        [TenantIdentifierTaken] = "This identifier is already in use.",
        [MemberAddForbidden] = "Only owners and admins can add members.",
        [MemberAddRoleForbidden] = "You cannot add members with this role.",
        [MemberAssignRoleForbidden] = "You cannot assign this role.",
        [MemberManageForbidden] = "Only owners and admins can manage members.",
        [MemberManageSelfForbidden] = "You cannot do this to yourself. To change your own password, use “Change password”.",
        [MemberManageTargetForbidden] = "Your role cannot manage this member.",
        [AnswerNotConfigured] = "Answer service is not configured.",
        [ConsultUnavailable] = "Consult service unavailable.",
        [ModelUnavailable] = "Model unavailable.",
        [OntologyUnavailable] = "Ontology service unavailable.",
        [VerificationCodeIncorrect] = "The verification code is incorrect.",
        [VerificationCodeIncorrectHint] = "The verification code is incorrect. Make sure your phone's time is set automatically, then enter the six digits the authenticator shows now.",
        [DirectoryNotEnabled] = "Directory sync is not enabled.",
        [DirectoryOwnerOnly] = "Only owners can manage directory sync.",
        [DirectoryLastOwner] = "The last active owner cannot be deactivated or demoted by directory sync.",
        [DirectoryMemberManaged] = "This member is managed by directory sync. Change it in the identity provider, or release it from directory sync first.",
        [DirectoryNotManaged] = "This member is not managed by directory sync.",
        [DirectoryRoleNotMappable] = "Directory sync cannot assign this role. The highest role it can assign is admin; owners are assigned by people.",
        [DirectoryEmailImmutable] = "Directory sync cannot change a member's email.",
        [DirectoryExternalIdTaken] = "Another member of this tenant already has this external id.",
        [DirectoryExternalIdInvalid] = "External id must be 1 to 200 characters.",
        [DirectoryAdoptRequired] = "A member with this email already exists and was not created by directory sync. Enable adopting existing members to link it.",
        [DirectoryTokenNotFound] = "Directory token not found.",
        [DirectoryTokenInvalid] = "Name is required (max 100 chars); ExpiresAt must be in the future.",
        [DirectoryGroupNotFound] = "Group not found.",
        [DirectoryGroupNameTaken] = "Another group of this tenant already has this name.",
        [DirectoryGroupInvalid] = "Group name is required (max 200 chars).",
        [DirectoryGroupMemberInvalid] = "Group members must be members of this tenant that directory sync manages.",
    }.ToFrozenDictionary();

    /// <summary>代碼的英文說明（填入參數）；不認得的代碼原樣回傳。</summary>
    public static string Describe(string code, IReadOnlyList<string>? args = null) =>
        English.TryGetValue(code, out var template)
            ? string.Format(CultureInfo.InvariantCulture, template, [.. args ?? []])
            : code;
}