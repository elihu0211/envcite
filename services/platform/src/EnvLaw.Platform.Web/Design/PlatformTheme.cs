using System.Globalization;

using MudBlazor;

namespace EnvLaw.Platform.Web.Design;

/// <summary>由 <see cref="DesignTokens"/> 建出 MudBlazor 主題；這裡不寫任何色碼或字級。</summary>
public static class PlatformTheme
{
    public static MudTheme Create() => new()
    {
        PaletteLight = Palette(DesignTokens.Light),
        Typography = Typography(),
        LayoutProperties = new LayoutProperties
        {
            DefaultBorderRadius = DesignTokens.Radius["control"],
            DrawerWidthLeft = DesignTokens.DrawerWidth,
            AppbarHeight = DesignTokens.AppBarHeight,
        },
    };

    private static PaletteLight Palette(ColorTokens c)
    {
        var palette = new PaletteLight();
        palette.Background = c.Background;
        palette.Surface = c.Surface;
        palette.AppbarBackground = c.Surface;
        palette.AppbarText = c.Text;
        palette.DrawerBackground = c.Surface;
        palette.DrawerText = c.Text;
        palette.DrawerIcon = c.TextSecondary;
        palette.TextPrimary = c.Text;
        palette.TextSecondary = c.TextSecondary;
        palette.Primary = c.Primary;
        palette.PrimaryContrastText = c.OnPrimary;
        palette.Success = c.Success;
        palette.Warning = c.Warning;
        palette.Error = c.Error;
        palette.Info = c.Info;
        palette.LinesDefault = c.BorderSubtle;
        palette.LinesInputs = c.BorderControl;
        palette.Divider = c.BorderSubtle;
        palette.TableLines = c.BorderSubtle;
        return palette;
    }

    private static Typography Typography()
    {
        static void Apply(BaseTypography target, DesignTokens.TypeStyle style)
        {
            target.FontFamily = DesignTokens.FontFamily;
            target.FontSize = style.Size;
            target.LineHeight = style.LineHeight;
            target.LetterSpacing = style.LetterSpacing;
            target.FontWeight = style.Weight.ToString(CultureInfo.InvariantCulture);
        }

        var typography = new Typography
        {
            Default = new DefaultTypography(),
            H1 = new H1Typography(),
            H2 = new H2Typography(),
            H3 = new H3Typography(),
            H4 = new H4Typography(),
            Body1 = new Body1Typography(),
            Body2 = new Body2Typography(),
            Caption = new CaptionTypography(),
            Button = new ButtonTypography(),
        };
        Apply(typography.Default, DesignTokens.Body);
        Apply(typography.H1, DesignTokens.Display);
        Apply(typography.H2, DesignTokens.H1);
        Apply(typography.H3, DesignTokens.H2);
        Apply(typography.H4, DesignTokens.H3);
        Apply(typography.Body1, DesignTokens.Body);
        Apply(typography.Body2, DesignTokens.BodySmall);
        Apply(typography.Caption, DesignTokens.Caption);
        Apply(typography.Button, DesignTokens.Button);
        typography.Button.TextTransform = "none"; // MudBlazor 預設全大寫；中文沒有大小寫，英文全大寫也更難讀。
        return typography;
    }
}