use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Paragraph, Wrap};

use crate::model::ProxySnapshot;
use super::theme::ColorScheme;

pub fn draw(frame: &mut Frame, area: Rect, proxy: &ProxySnapshot, proxy_url: &str, colors: &ColorScheme) {
    let now = chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string();

    let status_color = if proxy.connected {
        colors.status_ok
    } else {
        colors.status_error
    };
    let status_text = if proxy.connected {
        "CONNECTED"
    } else {
        "OFFLINE"
    };

    let uptime = format_uptime(proxy.stats.uptime_seconds);

    let strategy_display = if proxy.stats.strategy.is_empty() {
        "unknown".to_string()
    } else {
        proxy.stats.strategy.clone()
    };

    let line = Line::from(vec![
        Span::styled("Proxy: ", Style::default().fg(colors.text_primary)),
        Span::styled(proxy_url, Style::default().fg(colors.accent)),
        Span::raw("  "),
        Span::styled(status_text, Style::default().fg(status_color)),
        Span::raw("  \u{2502}  "),
        Span::styled("LB: ", Style::default().fg(colors.text_primary)),
        Span::styled(strategy_display, Style::default().fg(colors.accent_id)),
        Span::raw("  \u{2502}  "),
        Span::styled(format!("Uptime: {}", uptime), Style::default().fg(colors.text_primary)),
        Span::raw("  \u{2502}  "),
        Span::styled(format!("Refreshed: {}", now), Style::default().fg(colors.text_secondary)),
    ]);

    let header = Paragraph::new(line)
        .wrap(Wrap { trim: false })
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(" Agent Infra Dashboard ")
                .border_style(Style::default().fg(colors.border_focused)),
        );

    frame.render_widget(header, area);
}

fn format_uptime(secs: f64) -> String {
    let total = secs as u64;
    let h = total / 3600;
    let m = (total % 3600) / 60;
    let s = total % 60;
    format!("{:02}:{:02}:{:02}", h, m, s)
}
