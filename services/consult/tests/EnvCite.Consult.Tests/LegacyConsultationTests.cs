using Grpc.Core;

using Legacy = EnvCite.Consult.V2;

namespace EnvCite.Consult.Tests;

/// <summary>過渡：舊套件名 <c>envcite.consult.v2</c> 的呼叫端在升級前得到同樣的證據與狀態碼。</summary>
[Collection(ConsultTestGroup.Name)]
public sealed class LegacyConsultationTests(ConsultFactory factory)
{
    private static CancellationToken Ct => TestContext.Current.CancellationToken;

    private Legacy.ConsultationService.ConsultationServiceClient Client => new(ConsultFactory.ChannelFor(factory));

    [Fact]
    public async Task TheOldPackageNameStillCarriesEvidence()
    {
        var response = await Client.GroundAsync(
            new Legacy.GroundRequest { Question = FakeCanonServer.AnswerableQuery }, ConsultFactory.CallMetadata, cancellationToken: Ct);

        Assert.Equal(Legacy.GroundResponse.OutcomeOneofCase.Evidence, response.OutcomeCase);
        Assert.Equal(["L1", "L2"], response.Evidence.Citations.Select(c => c.Code));
        Assert.Equal(Legacy.Confidence.High, response.Evidence.Confidence);
    }

    [Fact]
    public async Task TheOldPackageNameKeepsTheStatusCodes()
    {
        var error = await Assert.ThrowsAsync<RpcException>(async () =>
            await Client.GroundAsync(new Legacy.GroundRequest { Question = FakeCanonServer.AnswerableQuery }, cancellationToken: Ct));

        Assert.Equal(StatusCode.InvalidArgument, error.StatusCode);
    }
}