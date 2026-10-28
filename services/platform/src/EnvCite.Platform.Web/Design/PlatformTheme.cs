using System.Globalization;

using MudBlazor;

namespace EnvCite.Platform.Web.Design;

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
        palette.OverlayDark = c.Scrim;
        return palette;
    }

    private static Typography Typography()
    {
        static void Apply(BaseTypography target, string role)
        {
            var style = DesignTokens.Type[role];
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
            H5 = new H5Typography(),
            H6 = new H6Typography(),
            Subtitle1 = new Subtitle1Typography(),
            Subtitle2 = new Subtitle2Typography(),
            Body1 = new Body1Typography(),
            Body2 = new Body2Typography(),
            Caption = new CaptionTypography(),
            Button = new ButtonTypography(),
            Overline = new OverlineTypography(),
        };
        // MudBlazor 的排版名稱對到 M3 角色，元件不論用哪一個名稱，拿到的都是同一張字級表。
        Apply(typography.Default, "body-l");
        Apply(typography.H1, "display-l");
        Apply(typography.H2, "headline-l");
        Apply(typography.H3, "headline-m");
        Apply(typography.H4, "headline-s");
        Apply(typography.H5, "title-l");
        Apply(typography.H6, "title-m");
        Apply(typography.Subtitle1, "title-m");
        Apply(typography.Subtitle2, "title-s");
        Apply(typography.Body1, "body-l");
        Apply(typography.Body2, "body-m");
        Apply(typography.Caption, "body-s");
        Apply(typography.Button, "label-l");
        Apply(typography.Overline, "label-s");
        typography.Overline.TextTransform = "none";
        typography.Button.TextTransform = "none"; // MudBlazor 預設全大寫；中文沒有大小寫，英文全大寫也更難讀。
        return typography;
    }
}