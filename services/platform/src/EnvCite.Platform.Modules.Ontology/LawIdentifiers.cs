using EnvCite.Ontology.V2;
using EnvCite.Platform.SharedKernel;

namespace EnvCite.Platform.Modules.Ontology;

internal static class LawIdentifiers
{
    /// <summary>
    /// 內部服務的每個法規都帶完整的自然鍵；缺了是契約之外的回應，不替它補預設的管轄或方案。
    /// </summary>
    public static LawIdentifierDto ToDto(LawIdentifier? id) =>
        id is null
            ? throw new InvalidOperationException("The ontology service returned a law without its identifier.")
            : new(id.Jurisdiction, id.IdScheme, id.LocalId);
}