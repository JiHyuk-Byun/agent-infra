use ratatui::Frame;
use ratatui::layout::{Constraint, Rect};
use ratatui::style::{Modifier, Style};
use ratatui::text::Text;
use ratatui::widgets::{Block, Borders, Cell, Row, Table};

use crate::model::{AppState, FocusedPanel, ProxySnapshot};
use super::theme::ColorScheme;

pub fn draw(frame: &mut Frame, area: Rect, proxy: &ProxySnapshot, state: &AppState, colors: &ColorScheme) {
    let is_focused = state.focused_panel == FocusedPanel::Backends;
    let border_color = if is_focused { colors.border_focused } else { colors.border_normal };

    if !proxy.connected {
        let block = Block::default()
            .borders(Borders::ALL)
            .title(" GPU Backends ")
            .border_style(Style::default().fg(colors.border_normal));
        let msg = ratatui::widgets::Paragraph::new(Text::styled(
            "  Proxy offline \u{2014} no backend data",
            Style::default().fg(colors.text_disabled),
        ))
        .block(block);
        frame.render_widget(msg, area);
        return;
    }

    // Build lookup maps from queue backends
    let gpu_load_map: std::collections::HashMap<&str, u64> = proxy
        .queue
        .backends
        .iter()
        .map(|b| (b.url.as_str(), b.gpu_load))
        .collect();
    let inflight_map: std::collections::HashMap<&str, u64> = proxy
        .queue
        .backends
        .iter()
        .map(|b| (b.url.as_str(), b.inflight))
        .collect();

    let header = Row::new(vec![
        Cell::from("Model / Backend"),
        Cell::from("Status"),
        Cell::from("Partition"),
        Cell::from("GPU Load"),
        Cell::from("Proxy Inflt"),
        Cell::from("Requests"),
        Cell::from("Errors"),
        Cell::from("Avg Latency"),
    ])
    .style(
        Style::default()
            .fg(colors.table_header)
            .add_modifier(Modifier::BOLD),
    );

    let mut rows: Vec<Row> = Vec::new();
    let mut total_backends: usize = 0;

    for (pool_idx, pool) in proxy.stats.pools.iter().enumerate() {
        let is_selected = is_focused && pool_idx == state.backend_selected;
        let is_expanded = state.backend_expanded.contains(&pool.model);

        let arrow = if is_expanded { "\u{25be}" } else { "\u{25b8}" };

        // Aggregate pool-level stats
        let pool_healthy = pool.backends.iter().filter(|b| b.healthy).count();
        let pool_total = pool.backends.len();
        let pool_requests: u64 = pool.backends.iter().map(|b| b.requests).sum();
        let pool_errors: u64 = pool.backends.iter().map(|b| b.errors).sum();
        let pool_gpu_load: u64 = pool
            .backends
            .iter()
            .map(|b| gpu_load_map.get(b.url.as_str()).copied().unwrap_or(0))
            .sum();
        let pool_inflight: u64 = pool
            .backends
            .iter()
            .map(|b| inflight_map.get(b.url.as_str()).copied().unwrap_or(0))
            .sum();

        // Model group header row
        let health_summary = format!("{}/{} healthy", pool_healthy, pool_total);
        let health_color = if pool_healthy == pool_total {
            colors.status_ok
        } else if pool_healthy > 0 {
            colors.status_warn
        } else {
            colors.status_error
        };

        let row_style = if is_selected {
            Style::default().bg(colors.row_selected_bg)
        } else {
            Style::default().bg(colors.row_alt_bg)
        };

        let model_row = Row::new(vec![
            Cell::from(format!("{} {}", arrow, &pool.model))
                .style(Style::default().fg(colors.accent).add_modifier(Modifier::BOLD)),
            Cell::from(health_summary).style(Style::default().fg(health_color)),
            Cell::from(""),
            Cell::from(pool_gpu_load.to_string()).style(Style::default().fg(colors.accent_id)),
            Cell::from(pool_inflight.to_string()).style(Style::default().fg(
                if pool_inflight > 0 { colors.accent } else { colors.text_primary },
            )),
            Cell::from(pool_requests.to_string()).style(Style::default().fg(colors.text_primary)),
            Cell::from(pool_errors.to_string()).style(if pool_errors > 0 {
                Style::default().fg(colors.status_error)
            } else {
                Style::default().fg(colors.text_primary)
            }),
            Cell::from(""),
        ])
        .style(row_style);
        rows.push(model_row);

        // Individual backend rows (only if expanded)
        if is_expanded {
            for backend in &pool.backends {
                let status_style = if backend.healthy {
                    Style::default().fg(colors.status_ok)
                } else {
                    Style::default().fg(colors.status_error)
                };
                let status_text = if backend.healthy { "healthy" } else { "down" };

                let gpu_load = gpu_load_map
                    .get(backend.url.as_str())
                    .copied()
                    .map(|v| v.to_string())
                    .unwrap_or_else(|| "-".to_string());

                let inflight = inflight_map
                    .get(backend.url.as_str())
                    .copied()
                    .unwrap_or(0);

                let partition_str = if backend.partition.is_empty() {
                    "-".to_string()
                } else {
                    backend.partition.clone()
                };

                let row = Row::new(vec![
                    Cell::from(format!("  {}", shorten_url(&backend.url)))
                        .style(Style::default().fg(colors.text_primary)),
                    Cell::from(status_text).style(status_style),
                    Cell::from(partition_str).style(Style::default().fg(colors.text_secondary)),
                    Cell::from(gpu_load).style(Style::default().fg(colors.accent_id)),
                    Cell::from(inflight.to_string()).style(Style::default().fg(
                        if inflight > 0 { colors.accent } else { colors.text_primary },
                    )),
                    Cell::from(backend.requests.to_string()).style(Style::default().fg(colors.text_primary)),
                    Cell::from(backend.errors.to_string()).style(if backend.errors > 0 {
                        Style::default().fg(colors.status_error)
                    } else {
                        Style::default().fg(colors.text_primary)
                    }),
                    Cell::from(format!("{:.0}ms", backend.avg_latency_ms)).style(Style::default().fg(colors.accent_latency)),
                ]);
                rows.push(row);
                total_backends += 1;
            }
        } else {
            total_backends += pool.backends.len();
        }
    }

    if rows.is_empty() {
        rows.push(Row::new(vec![Cell::from("  No backends registered")]));
    }

    let title = format!(
        " GPU Backends ({} models, {} backends) ",
        proxy.stats.pools.len(),
        total_backends,
    );

    let table = Table::new(
        rows,
        [
            Constraint::Percentage(22), // model / backend
            Constraint::Percentage(12), // status
            Constraint::Percentage(10), // partition
            Constraint::Percentage(8),  // gpu load
            Constraint::Percentage(9),  // inflight
            Constraint::Percentage(10), // requests
            Constraint::Percentage(8),  // errors
            Constraint::Percentage(11), // avg latency
        ],
    )
    .header(header)
    .block(
        Block::default()
            .borders(Borders::ALL)
            .title(title)
            .border_style(Style::default().fg(border_color)),
    );

    frame.render_widget(table, area);
}

fn shorten_url(url: &str) -> String {
    url.replace("http://", "").replace("https://", "")
}
