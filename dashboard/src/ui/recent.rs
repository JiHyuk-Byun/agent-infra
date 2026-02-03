use ratatui::Frame;
use ratatui::layout::{Constraint, Rect};
use ratatui::style::{Modifier, Style};
use ratatui::text::Text;
use ratatui::widgets::{Block, Borders, Cell, Row, Table};

use crate::model::ThroughputSnapshot;
use super::theme::ColorScheme;

pub fn draw(frame: &mut Frame, area: Rect, tp: &ThroughputSnapshot, colors: &ColorScheme) {
    if !tp.enabled {
        let block = Block::default()
            .borders(Borders::ALL)
            .title(" Recent Completions ")
            .border_style(Style::default().fg(colors.border_normal));
        let msg = ratatui::widgets::Paragraph::new(Text::styled(
            "  N/A (--artifacts not specified)",
            Style::default().fg(colors.text_disabled),
        ))
        .block(block);
        frame.render_widget(msg, area);
        return;
    }

    let header = Row::new(vec![
        Cell::from("Time"),
        Cell::from("Spec"),
        Cell::from("Result"),
    ])
    .style(
        Style::default()
            .fg(colors.table_header)
            .add_modifier(Modifier::BOLD),
    );

    let rows: Vec<Row> = tp
        .recent
        .iter()
        .map(|entry| {
            let result_style = if entry.success {
                Style::default().fg(colors.status_ok)
            } else {
                Style::default().fg(colors.status_error)
            };
            let result_text = if entry.success { "PASS" } else { "FAIL" };

            Row::new(vec![
                Cell::from(entry.time.clone()),
                Cell::from(entry.spec_name.clone()),
                Cell::from(result_text).style(result_style),
            ])
        })
        .collect();

    let table = Table::new(
        rows,
        [
            Constraint::Length(10),
            Constraint::Min(30),
            Constraint::Length(8),
        ],
    )
    .header(header)
    .block(
        Block::default()
            .borders(Borders::ALL)
            .title(" Recent Completions ")
            .border_style(Style::default().fg(colors.border_focused)),
    );

    frame.render_widget(table, area);
}
