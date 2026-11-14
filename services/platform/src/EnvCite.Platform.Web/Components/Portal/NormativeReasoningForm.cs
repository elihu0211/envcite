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

    public string? PollutantCode { get; set; }
    public string? PollutantValue { get; set; }
    public string? PollutantUnit { get; set; }
    public string? IndustryClass { get; set; }

    public string Intent { get; set; } = string.Empty;
    public string Negligence { get; set; } = string.Empty;
    public string FactMistake { get; set; } = string.Empty;
    public string SelfDefense { get; set; } = string.Empty;
    public string DefenseExcessive { get; set; } = string.Empty;
    public string Necessity { get; set; } = string.Empty;
    public string NecessityExcessive { get; set; } = string.Empty;
    public string IgnoranceUnavoidable { get; set; } = string.Empty;
    public string PettyMinor { get; set; } = string.Empty;
    public string SelfInduced { get; set; } = string.Empty;
    public string CapacityReduced { get; set; } = string.Empty;

    public string? ScoringMatrixCode { get; set; }
    public string? DemeritPoints { get; set; }
    public string? CureDeadline { get; set; }
    public string? ReinspectionOn { get; set; }

    public PortalReasoningInput ToInput() =>
        new(
            ConductOn,
            DecidedOn,
            ConductEndedOn,
            ParseTriState(CriminalOffense),
            ParseTriState(CourtConfiscated),
            CriminalPayment,
            GainedBenefit,
            PollutantCode,
            PollutantValue,
            PollutantUnit,
            IndustryClass,
            ParseTriState(Intent),
            ParseTriState(Negligence),
            ParseTriState(FactMistake),
            ParseTriState(SelfDefense),
            ParseTriState(DefenseExcessive),
            ParseTriState(Necessity),
            ParseTriState(NecessityExcessive),
            ParseTriState(IgnoranceUnavoidable),
            ParseTriState(PettyMinor),
            ParseTriState(SelfInduced),
            ParseTriState(CapacityReduced),
            ScoringMatrixCode,
            DemeritPoints,
            CureDeadline,
            ReinspectionOn);

    private static bool? ParseTriState(string? value) => value switch
    {
        "true" => true,
        "false" => false,
        _ => null,
    };
}