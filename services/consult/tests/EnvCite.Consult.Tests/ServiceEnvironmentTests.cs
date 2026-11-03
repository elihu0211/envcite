using System.Collections;

using EnvCite.ServiceDefaults;

using Microsoft.Extensions.Configuration;

namespace EnvCite.Consult.Tests;

public sealed class ServiceEnvironmentTests
{
    private static ConfigurationManager Configured(params (string Name, string Value)[] variables)
    {
        var environment = new Hashtable();
        foreach (var (name, value) in variables)
        {
            environment[name] = value;
        }

        var configuration = new ConfigurationManager();
        configuration.AddEnvironmentVariables(); // WebApplication.CreateBuilder 預設加的不帶前綴來源
        // 預設來源讀的是真實環境，測試用注入的環境重建一個等價的，確保被取代的是它。
        configuration.AddServiceEnvironment(environment);
        return configuration;
    }

    [Fact]
    public void AVariableWithThePrefixIsReadByItsConfigurationPath()
    {
        var configuration = Configured(("ENVCITE_Ontology__Address", "http://ontology:50051"));

        Assert.Equal("http://ontology:50051", configuration["Ontology:Address"]);
    }

    [Fact]
    public void AnUnprefixedVariableIsNotReadAsConfiguration()
    {
        var configuration = Configured(("Ontology__Address", "http://ignored"));

        Assert.Null(configuration["Ontology:Address"]);
    }

    [Fact]
    public void OtelVariablesKeepTheirNameForOpenTelemetry()
    {
        var configuration = Configured(("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317"));

        Assert.Equal("http://collector:4317", configuration["OTEL_EXPORTER_OTLP_ENDPOINT"]);
    }
}