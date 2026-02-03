use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Paragraph, Wrap};

use crate::model::ProxySnapshot;
use super::analysis::collect_timing_stats;
use super::theme::ColorScheme;

fn format_latency(ms: f64) -> String {
    if ms < 1000.0 {
        format!("{:.0}ms", ms)
    } else {
        format!("{:.1}s", ms / 1000.0)
    }
}

pub fn draw(frame: &mut Frame, area: Rect, proxy: &ProxySnapshot, colors: &ColorScheme) {
    let content = if !proxy.connected {
        vec![Line::from(Span::styled(
            "  Proxy offline",
            Style::default().fg(colors.text_disabled),
        ))]
    } else {
        let s = &proxy.queue.summary;
        let mut lines = vec![
            Line::from(vec![
                Span::styled("  Pending:     ", Style::default().fg(colors.text_primary)),
                Span::styled(
                    s.pending.to_string(),
                    Style::default()
                        .fg(if s.pending > 0 { colors.status_warn } else { colors.status_ok })
                        .add_modifier(Modifier::BOLD),
                ),
            ]),
            Line::from(vec![
                Span::styled("  In-flight:   ", Style::default().fg(colors.text_primary)),
                Span::styled(
                    s.in_flight.to_string(),
                    Style::default().fg(colors.accent).add_modifier(Modifier::BOLD),
                ),
            ]),
            Line::from(vec![
                Span::styled("  Last minute: ", Style::default().fg(colors.text_primary)),
                Span::styled(
                    s.completed_last_minute.to_string(),
                    Style::default().fg(colors.status_ok),
                ),
            ]),
            Line::from(vec![
                Span::styled("  Tracked:     ", Style::default().fg(colors.text_primary)),
                Span::styled(s.total_tracked.to_string(), Style::default().fg(colors.text_primary)),
            ]),
        ];

        // Avg turn timing line
        let timing = collect_timing_stats(&proxy.queue);
        if timing.count > 0 {
            let avg_turn = timing.avg_total_ms + timing.avg_gap_ms;
            let detail = if timing.has_backend_time {
                format!("(inference={} agent={})", format_latency(timing.avg_inference_ms), format_latency(timing.avg_gap_ms))
            } else if timing.gap_count > 0 {
                format!("(proc={} agent={})", format_latency(timing.avg_inference_ms), format_latency(timing.avg_gap_ms))
            } else {
                format!("(proc={})", format_latency(timing.avg_inference_ms))
            };
            lines.push(Line::from(vec![
                Span::styled("  Avg turn:    ", Style::default().fg(colors.text_primary)),
                Span::styled(
                    format!("{} {}", format_latency(avg_turn), detail),
                    Style::default().fg(colors.text_primary),
                ),
            ]));
        }

        lines
    };

    let paragraph = Paragraph::new(content)
        .wrap(Wrap { trim: false })
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(" Queue Status ")
                .border_style(Style::default().fg(colors.border_focused)),
        );

    frame.render_widget(paragraph, area);
}
