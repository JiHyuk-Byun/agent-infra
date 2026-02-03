use ratatui::style::Color;

#[derive(Clone, Copy, Debug)]
pub struct ColorScheme {
    // Text
    pub text_primary: Color,
    pub text_secondary: Color,
    pub text_disabled: Color,

    // Borders
    pub border_focused: Color,
    pub border_normal: Color,

    // Table
    pub table_header: Color,
    pub row_selected_bg: Color,
    pub row_alt_bg: Color,

    // Accents
    pub accent: Color,
    pub accent_id: Color,
    pub accent_count: Color,
    pub accent_latency: Color,

    // Status (semantic)
    pub status_ok: Color,
    pub status_warn: Color,
    pub status_error: Color,

    // Misc
    pub help_separator: Color,
}

impl ColorScheme {
    pub fn dark() -> Self {
        Self {
            text_primary: Color::White,
            text_secondary: Color::Gray,
            text_disabled: Color::DarkGray,
            border_focused: Color::LightBlue,
            border_normal: Color::DarkGray,
            table_header: Color::Yellow,
            row_selected_bg: Color::DarkGray,
            row_alt_bg: Color::Rgb(30, 30, 40),
            accent: Color::LightBlue,
            accent_id: Color::LightYellow,
            accent_count: Color::LightGreen,
            accent_latency: Color::LightMagenta,
            status_ok: Color::Green,
            status_warn: Color::Yellow,
            status_error: Color::Red,
            help_separator: Color::Rgb(60, 60, 60),
        }
    }

    pub fn light() -> Self {
        Self {
            text_primary: Color::Black,
            text_secondary: Color::DarkGray,
            text_disabled: Color::Gray,
            border_focused: Color::Blue,
            border_normal: Color::Gray,
            table_header: Color::Rgb(140, 100, 0),
            row_selected_bg: Color::Rgb(210, 220, 235),
            row_alt_bg: Color::Rgb(240, 240, 248),
            accent: Color::Blue,
            accent_id: Color::Rgb(160, 110, 0),
            accent_count: Color::Rgb(0, 130, 60),
            accent_latency: Color::Magenta,
            status_ok: Color::Rgb(0, 140, 50),
            status_warn: Color::Rgb(180, 120, 0),
            status_error: Color::Rgb(200, 30, 30),
            help_separator: Color::Rgb(180, 180, 180),
        }
    }
}
