using System.Collections.Concurrent;
using System.Net;

using EnvCite.Ontology.V2;

using Grpc.Core;

using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Server.Kestrel.Core;
using Microsoft.Extensions.DependencyInjection;

namespace EnvCite.Platform.Tests;

/// <summary>In-process h2c gRPC server standing in for envcite-ontology; records call metadata.</summary>
public sealed class FakeOntologyServer : IAsyncDisposable
{
    public const string KnownLawId = "O0020001";

    /// <summary>中央法規在對外 REST 的路徑前綴；之後接在地識別碼。</summary>
    public const string CentralLawsPath = "/v2/laws/TW-CENTRAL/moj_pcode/";
    public const string UnavailableLawId = "trigger-unavailable";

    /// <summary>GetLaw 回 RESOURCE_EXHAUSTED（讀取連線用盡）。</summary>
    public const string BusyLawId = "trigger-busy";

    /// <summary>每個新的請求頭一次呼叫回 UNAVAILABLE、之後成功的法規（驗證客戶端的重試）。</summary>
    public const string FlakyLawId = "trigger-flaky";

    /// <summary>GetLaw 被呼叫的次數，依法規代碼。</summary>
    public ConcurrentDictionary<string, int> GetLawCalls { get; } = new();

    /// <summary>假服務認為「這一天沒有有效版本」的日期（法規制定之前）。</summary>
    public const string NotInForceDate = "1900-01-01";

    private WebApplication? _app;

    public Uri Address { get; private set; } = null!;

    public ConcurrentQueue<Metadata> ReceivedMetadata { get; } = new();

    /// <summary>最近一次 SearchArticles 收到的請求（測試用，驗證參數有原樣傳到內部服務）。</summary>
    public SearchArticlesRequest? LastSearch { get; private set; }

    /// <summary>最近一次 Evaluate 收到的請求（測試用，驗證公開契約原樣對映到內部契約）。</summary>
    public EvaluateRequest? LastEvaluate { get; private set; }

    /// <summary>最近一次 GetLaw／ListArticles 收到的 as_of（測試用）。</summary>
    public string? LastAsOf { get; private set; }

    /// <summary>最近一次 GetLaw／ListArticles 收到的法規識別碼（測試用，驗證路徑三段原樣傳到內部服務）。</summary>
    public LawIdentifier? LastIdentifier { get; private set; }

    public async Task StartAsync()
    {
        var builder = WebApplication.CreateSlimBuilder();
        builder.WebHost.ConfigureKestrel(k => k.Listen(IPAddress.Loopback, 0, o => o.Protocols = HttpProtocols.Http2));
        builder.Services.AddGrpc();
        builder.Services.AddSingleton(this);
        _app = builder.Build();
        _app.MapGrpcService<Service>();
        _app.MapGrpcService<Reasoning>();
        await _app.StartAsync();
        Address = new Uri(_app.Urls.First());
    }

    public async ValueTask DisposeAsync()
    {
        if (_app is not null)
        {
            await _app.DisposeAsync();
        }
    }

    private sealed class Service(FakeOntologyServer server) : OntologyService.OntologyServiceBase
    {
        public override Task<GetLawResponse> GetLaw(GetLawRequest request, ServerCallContext context)
        {
            var id = request.Identifier?.LocalId ?? string.Empty;
            Authorize(context, id);
            server.LastAsOf = request.AsOf;
            server.LastIdentifier = request.Identifier;
            CheckAsOf(request.AsOf);
            var calls = server.GetLawCalls.AddOrUpdate(id, 1, (_, n) => n + 1);
            if (id == FlakyLawId && calls == 1)
            {
                throw new RpcException(new Status(StatusCode.Unavailable, "restarting"));
            }

            return Task.FromResult(new GetLawResponse
            {
                Law = new Law
                {
                    Identifier = request.Identifier,
                    Iri = $"https://envcite/tw/law/{id}",
                    Title = "空氣污染防制法",
                    Language = "zh-hant",
                    DateDocument = "2000-01-01",
                },
            });
        }

        public override Task<ListArticlesResponse> ListArticles(ListArticlesRequest request, ServerCallContext context)
        {
            var id = request.Identifier?.LocalId ?? string.Empty;
            Authorize(context, id);
            server.LastAsOf = request.AsOf;
            server.LastIdentifier = request.Identifier;
            CheckAsOf(request.AsOf);
            return Task.FromResult(new ListArticlesResponse
            {
                Articles = { new Article { Iri = "a1", Number = "1" }, new Article { Iri = "a5-1", Number = "5-1" } },
            });
        }

        public override Task<SearchArticlesResponse> SearchArticles(SearchArticlesRequest request, ServerCallContext context)
        {
            Authorize(context, KnownLawId);
            server.LastSearch = request;
            if (string.IsNullOrWhiteSpace(request.Query))
            {
                throw new RpcException(new Status(StatusCode.InvalidArgument, "query must not be empty"));
            }

            if (request.Query == "pool-busy")
            {
                throw new RpcException(new Status(StatusCode.ResourceExhausted, "no database connection is available"));
            }

            if (request.AsOf == "yesterday")
            {
                throw new RpcException(new Status(StatusCode.InvalidArgument, "as_of must be an ISO 8601 date"));
            }

            return Task.FromResult(new SearchArticlesResponse
            {
                Hits =
                {
                    new ArticleHit
                    {
                        Iri = "a5-1",
                        Identifier = new LawIdentifier { Jurisdiction = "TW-CENTRAL", IdScheme = "moj_pcode", LocalId = KnownLawId },
                        LawTitle = "空氣污染防制法",
                        Number = "5-1",
                        Content = "（條文內容）",
                        LexicalScore = 0.42f,
                    },
                },
            });
        }

        // 與真實服務同契約：不是日期 → INVALID_ARGUMENT；該日沒有有效版本 → NOT_FOUND（不退回現行）。
        private static void CheckAsOf(string asOf)
        {
            if (asOf == "yesterday")
            {
                throw new RpcException(new Status(StatusCode.InvalidArgument, "as_of must be an ISO 8601 date"));
            }

            if (asOf == NotInForceDate)
            {
                throw new RpcException(new Status(StatusCode.NotFound, "no version in force"));
            }
        }

        private void Authorize(ServerCallContext context, string lawId)
        {
            server.ReceivedMetadata.Enqueue(context.RequestHeaders);
            if (lawId == UnavailableLawId)
            {
                throw new RpcException(new Status(StatusCode.Unavailable, "down"));
            }

            if (lawId == BusyLawId)
            {
                throw new RpcException(new Status(StatusCode.ResourceExhausted, "no database connection is available"));
            }

            if (lawId is not (KnownLawId or FlakyLawId))
            {
                throw new RpcException(new Status(StatusCode.NotFound, "no such law"));
            }
        }
    }

    /// <summary>`conduct_on` 為此值時假服務回 RESOURCE_EXHAUSTED（推論容量用盡）。</summary>
    public const string BusyDate = "2000-01-02";

    private sealed class Reasoning(FakeOntologyServer server) : ReasoningService.ReasoningServiceBase
    {
        public override Task<EvaluateResponse> Evaluate(EvaluateRequest request, ServerCallContext context)
        {
            server.ReceivedMetadata.Enqueue(context.RequestHeaders);
            server.LastEvaluate = request;
            if (request.ConductOn == "yesterday")
            {
                throw new RpcException(new Status(StatusCode.InvalidArgument, "conduct_on must be an ISO 8601 date"));
            }

            if (request.ConductOn == BusyDate)
            {
                throw new RpcException(new Status(StatusCode.ResourceExhausted, "evaluation capacity is in use"));
            }

            var outcome = new Outcome
            {
                Verdicts = { new StatementVerdict { StatementId = 7, Verdict = Verdict.Violation } },
                Penalties =
                {
                    new PenaltyCandidate
                    {
                        StatementId = 8, Punishes = 7, Effect = LegalEffect.Fine, MinAmount = 60000, MaxAmount = 1000000,
                        Conditional = true, Discretionary = false,
                        SubjectBases = { LiabilityBasis.State },
                    },
                    new PenaltyCandidate { StatementId = 9, Punishes = 7, Effect = LegalEffect.Order },
                    new PenaltyCandidate
                    {
                        StatementId = 11, Punishes = 7, Effect = LegalEffect.Warning,
                        TriggeredBy = { new SanctionTrigger { StatementId = 8, Kind = TriggerKind.SanctionImposed } },
                    },
                    new PenaltyCandidate { StatementId = 12, Punishes = 7, Effect = LegalEffect.Disgorgement },
                },
                SubjectMismatch = { 9 },
                MissingPermitLimits = { new PermitGap { StatementId = 7, Permit = "operating_permit", Concept = "effluent_cod" } },
                OneAct = new FineInterval { MinAmount = 60000, MaxAmount = 1000000 },
                Missing = { new Gap { StatementId = 7, Concept = "effluent_cod" } },
                Problems =
                {
                    new Problem { Kind = ProblemKind.ContradictoryFact, Concept = "ph" },
                    new Problem { Kind = ProblemKind.UnresolvedDelegation, ConditionId = 31 },
                    new Problem { Kind = ProblemKind.MeasurementMismatch, ConditionId = 32 },
                    new Problem { Kind = ProblemKind.InvalidatedDelegation, ConditionId = 33 },
                },
                Defeated = { new Defeat { Loser = 1, Winner = 2, Basis = OverrideBasis.LexSpecialis } },
                Trace =
                {
                    new TraceEntry
                    {
                        ConditionId = 31, StatementId = 7, Concept = "effluent_cod", Role = ConditionRole.Duty,
                        Fact = new EnvCite.Ontology.V2.Decimal { Value = "120" },
                        Threshold = new EnvCite.Ontology.V2.Decimal { Value = "100" },
                    },
                },
                LimitSources = { { 31, 52 } },
                General = new GeneralPart
                {
                    Unpunished =
                    {
                        new PenaltyExcuse { PenaltyStatementId = 10, Excuse = Excuse.UnderFourteen },
                        new PenaltyExcuse { PenaltyStatementId = 13, Excuse = Excuse.CriminalPriority },
                    },
                    Mitigation = { new PenaltyMitigation { PenaltyStatementId = 8, Mitigation = Mitigation.Youth } },
                    PresumedAgentFault = { 8 },
                    TimeBarred = { 8 },
                    Reduced =
                    {
                        new ReducedFine
                        {
                            PenaltyStatementId = 8, Mitigation = Mitigation.Youth, Share = ReductionShare.Half,
                            MinAmount = 30000, MaxAmount = 500000,
                        },
                        new ReducedFine { PenaltyStatementId = 14, Mitigation = Mitigation.ExcessiveDefense, Share = ReductionShare.Third },
                    },
                    RaisedCaps = { new RaisedCap { PenaltyStatementId = 8, MaxAmount = new EnvCite.Ontology.V2.Decimal { Value = "2500000" } } },
                    CriminalDeductions = { new CriminalDeduction { PenaltyStatementId = 8, Amount = new EnvCite.Ontology.V2.Decimal { Value = "41760" } } },
                },
                Invalidated =
                {
                    new InvalidatedStatement
                    {
                        StatementId = 15, VersionIri = "https://envcite/tw/law/O0020001/2018-08-01/zho/art_40",
                        RulingNo = "112年憲判字第1號", InvalidFrom = "2023-03-01", Partial = true,
                    },
                },
                DelegationBases =
                {
                    new DelegationBasis
                    {
                        StatementId = 52, Rooted = true,
                        Links =
                        {
                            new DelegationLink
                            {
                                CommandIdentifier = new LawIdentifier { Jurisdiction = "TW-CENTRAL", IdScheme = "moj_pcode", LocalId = "O0020010" },
                                CitedLawName = "水污染防治法",
                                CitedPath = "art_7/para_2",
                                Identifier = new LawIdentifier { Jurisdiction = "TW-CENTRAL", IdScheme = "moj_pcode", LocalId = "O0020001" },
                                VersionIri = "https://envcite/tw/law/O0020001/2018-08-01/zho/art_7/para_2",
                            },
                        },
                    },
                    new DelegationBasis
                    {
                        StatementId = 53,
                        Links = { new DelegationLink { CommandIdentifier = new LawIdentifier { Jurisdiction = "TW-CENTRAL", IdScheme = "moj_pcode", LocalId = "O0020011" }, Gap = DelegationGap.NoCitation } },
                    },
                },
                Sources = { new StatementSource { StatementId = 7, VersionIri = "https://envcite/tw/law/O0020001/2018-08-01/zho/art_32" } },
                UncoveredLawIdentifiers = { new LawIdentifier { Jurisdiction = "TW-CENTRAL", IdScheme = "moj_pcode", LocalId = "O0040001" } },
                DefinitionsUsed =
                {
                    new DefinitionUse
                    {
                        ConditionId = 31,
                        DefinitionId = 5,
                        VersionIri = "https://envcite/tw/law/O0020001/2018-08-01/zho/art_2/para_1/pt_1",
                        Term = "事業",
                        Members = { "工廠", "礦場" },
                    },
                },
                Guidance =
                {
                    new Guidance
                    {
                        Issuer = "環境部",
                        DocumentNo = "環署水字第1090000001號",
                        IssuedOn = "2020-02-01",
                        Kind = GuidanceKind.Interpretation,
                        Subject = "排放之認定",
                        StatementIds = { 7 },
                        PublishedIn = "行政院公報第26卷",
                    },
                },
            };
            return Task.FromResult(new EvaluateResponse
            {
                AtDecision = outcome,
                Earlier = { new EarlierLaw { FirstDay = request.ConductOn, Outcome = new Outcome() } },
            });
        }
    }
}