namespace EnvCite.Platform.Web.Components.Portal;

public sealed class NormativeReasoningForm
{
    public string? ConductOn { get; set; }
    public string? DecidedOn { get; set; }
    public string? ConductEndedOn { get; set; }
    public string CriminalOffense { get; set; } = string.Empty;
    public string CourtConfiscated { get; set; } = string.Empty;
    public string? CriminalPayment { get; set; }
    public string? GainedBenefit { get; set; }
}