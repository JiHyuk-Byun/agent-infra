use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;

use super::theme::ColorScheme;

pub fn draw(frame: &mut Frame, area: Rect, colors: &ColorScheme) {
    let key_style = Style::default()
        .fg(colors.accent)
        .add_modifier(Modifier::BOLD);
    let desc_style = Style::default().fg(colors.text_secondary);
    let sep_style = Style::default().fg(colors.help_separator);

    let line = Line::from(vec![
        Span::styled(" Tab", key_style),
        Span::styled(" Switch panel ", desc_style),
        Span::styled("\u{2502}", sep_style),
        Span::styled(" \u{2191}/k", key_style),
        Span::styled(" Up ", desc_style),
        Span::styled("\u{2502}", sep_style),
        Span::styled(" \u{2193}/j", key_style),
        Span::styled(" Down ", desc_style),
        Span::styled("\u{2502}", sep_style),
        Span::styled(" Enter", key_style),
        Span::styled(" Expand/Collapse ", desc_style),
        Span::styled("\u{2502}", sep_style),
        Span::styled(" q/Esc", key_style),
        Span::styled(" Quit", desc_style),
    ]);

    let paragraph = Paragraph::new(line);
    frame.render_widget(paragraph, area);
}
