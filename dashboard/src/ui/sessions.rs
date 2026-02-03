use chrono::{Local, TimeZone};
use ratatui::Frame;
use ratatui::layout::{Constraint, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::Text;
use ratatui::widgets::{Block, Borders, Cell, Row, Table};

use crate::model::{AppState, SessionGroup, ClientGroup, FocusedPanel, ProxySnapshot};
use super::theme::ColorScheme;

pub fn draw(frame: &mut Frame, area: Rect, proxy: &ProxySnapshot, state: &mut AppState, colors: &ColorScheme) {
    let is_focused = state.focused_panel == FocusedPanel::Sessions;
    let border_color = if is_focused { colors.border_focused } else { colors.border_normal };

    if !proxy.connected {
        let block = Block::default()
            .borders(Borders::ALL)
            .title(" Sessions ")
            .border_style(Style::default().fg(colors.border_normal));
        let msg = ratatui::widgets::Paragraph::new(Text::styled(
            "  Proxy offline",
            Style::default().fg(colors.text_disabled),
        ))
        .block(block);
        frame.render_widget(msg, area);
        return;
    }

    let queue = &proxy.queue;

    let clients = &queue.clients;
    let orphans = &queue.orphan_sessions;

    let use_hierarchy = !clients.is_empty() || !orphans.is_empty();

    let header = Row::new(vec![
        Cell::from(""),
        Cell::from("ID / Label"),
        Cell::from("Detail"),
        Cell::from("Latency"),
        Cell::from("Time"),
        Cell::from("Port"),
    ])
    .style(
        Style::default()
            .fg(colors.table_header)
            .add_modifier(Modifier::BOLD),
    );

    let mut rows: Vec<Row> = Vec::new();
    let mut selected_visual_row: Option<usize> = None;
    let mut visual_idx: usize = 0;
    let mut selectable_idx: usize = 0;

    if use_hierarchy {
        // === Hierarchical: Client -> Session -> Turn ===
        for (client_idx, client) in clients.iter().enumerate() {
            // Client row
            let is_selected = is_focused && selectable_idx == state.session_selected;
            if selectable_idx == state.session_selected {
                selected_visual_row = Some(visual_idx);
            }

            let is_expanded = state.client_expanded.contains(&client.client_id);
            let arrow = if is_expanded { "v" } else { ">" };

            let (status_text, status_color) = client_aggregate_status(client, colors);
            let total_turns: u64 = client.sessions.iter().map(|s| s.total_requests).sum();
            let sess_count = format!("{} sess ({}t)", client.sessions.len(), total_turns);
            let cmd_short = shorten_command(&client.client_command);

            // Find the most recent backend port used by this client
            let last_port: String = client.sessions.iter()
                .flat_map(|s| s.completed_turns.iter())
                .max_by(|a, b| a.completed_at.partial_cmp(&b.completed_at).unwrap_or(std::cmp::Ordering::Equal))
                .and_then(|turn| turn.backend.as_deref())
                .and_then(|b: &str| b.rsplit(':').next())
                .map(|p| format!(":{}", p))
                .unwrap_or_default();

            let row_style = if is_selected {
                Style::default().bg(colors.row_selected_bg)
            } else {
                Style::default()
            };

            // Format client ID as client-{n}/{pid}
            let client_label = format!("client-{}/{}", client_idx + 1, client.client_id.rsplit(':').next().unwrap_or(&client.client_id));

            rows.push(Row::new(vec![
                Cell::from(arrow),
                Cell::from(client_label).style(Style::default().fg(colors.accent).add_modifier(Modifier::BOLD)),
                Cell::from(cmd_short).style(Style::default().fg(colors.text_primary)),
                Cell::from(sess_count),
                Cell::from(status_text).style(Style::default().fg(status_color).add_modifier(Modifier::BOLD)),
                Cell::from(last_port).style(Style::default().fg(colors.accent_latency)),
            ]).style(row_style));
            visual_idx += 1;
            selectable_idx += 1;

            // Expanded: show sessions under this client
            if is_expanded {
                for sess in &client.sessions {
                    let sess_rows = render_session(sess, state, is_focused, &mut selectable_idx, &mut selected_visual_row, visual_idx, true, colors);
                    for r in sess_rows {
                        rows.push(r);
                        visual_idx += 1;
                    }
                }
            }
        }

        // Orphan sessions
        if !orphans.is_empty() {
            // Separator
            rows.push(Row::new(vec![
                Cell::from("\u{2500}\u{2500}").style(Style::default().fg(colors.border_normal)),
                Cell::from("orphan").style(Style::default().fg(colors.border_normal)),
                Cell::from("").style(Style::default().fg(colors.border_normal)),
                Cell::from(""),
                Cell::from(""),
                Cell::from(""),
            ]));
            visual_idx += 1;

            for sess in orphans {
                let sess_rows = render_session(sess, state, is_focused, &mut selectable_idx, &mut selected_visual_row, visual_idx, false, colors);
                for r in sess_rows {
                    rows.push(r);
                    visual_idx += 1;
                }
            }
        }
    } else {
        // === Flat fallback (old proxy) ===
        for sess in &queue.sessions {
            let sess_rows = render_session(sess, state, is_focused, &mut selectable_idx, &mut selected_visual_row, visual_idx, false, colors);
            for r in sess_rows {
                rows.push(r);
                visual_idx += 1;
            }
        }
    }

    // Ungrouped requests (no session_id at all)
    let ungrouped_in_flight: Vec<_> = queue
        .in_flight
        .iter()
        .filter(|r| r.session_id.is_none())
        .collect();
    let ungrouped_pending: Vec<_> = queue
        .pending
        .iter()
        .filter(|r| r.session_id.is_none())
        .collect();

    if !ungrouped_in_flight.is_empty() || !ungrouped_pending.is_empty() {
        rows.push(Row::new(vec![
            Cell::from("\u{2500}").style(Style::default().fg(colors.border_normal)),
            Cell::from(""),
            Cell::from(""),
            Cell::from(""),
            Cell::from(""),
            Cell::from(""),
        ]));

        for req in &ungrouped_in_flight {
            let processing_str = req
                .processing_time_ms
                .map(format_duration_ms)
                .unwrap_or_else(|| "-".to_string());

            rows.push(Row::new(vec![
                Cell::from(""),
                Cell::from(req.request_id.clone()),
                Cell::from(format!("(ungrouped) {}", shorten_model(&req.model))),
                Cell::from(format_duration_ms(req.wait_time_ms)),
                Cell::from(processing_str).style(Style::default().fg(colors.accent)),
                Cell::from(""),
            ]));
        }

        for req in &ungrouped_pending {
            rows.push(Row::new(vec![
                Cell::from(""),
                Cell::from(req.request_id.clone()),
                Cell::from(format!("(ungrouped) {}", shorten_model(&req.model))),
                Cell::from(format_duration_ms(req.wait_time_ms)).style(Style::default().fg(colors.status_warn)),
                Cell::from("PENDING").style(Style::default().fg(colors.status_warn)),
                Cell::from(""),
            ]));
        }
    }

    if rows.is_empty() {
        rows.push(Row::new(vec![
            Cell::from(""),
            Cell::from("  No sessions tracked"),
            Cell::from(""),
            Cell::from(""),
            Cell::from(""),
            Cell::from(""),
        ]));
    }

    // Build title
    let title = if use_hierarchy {
        let client_count = clients.len();
        let sess_count: usize = clients.iter().map(|c| c.sessions.len()).sum();
        let orphan_count = orphans.len();
        let ungrouped_count = ungrouped_in_flight.len() + ungrouped_pending.len();
        let mut t = format!(" Clients ({}) / Sessions ({})", client_count, sess_count);
        if orphan_count > 0 {
            t.push_str(&format!(" + {} orphan", orphan_count));
        }
        if ungrouped_count > 0 {
            t.push_str(&format!(" + {} ungrouped", ungrouped_count));
        }
        t.push_str(" \u{2502} turns: last 60s ");
        t
    } else {
        let sess_count = queue.sessions.len();
        let ungrouped_count = ungrouped_in_flight.len() + ungrouped_pending.len();
        let mut t = if ungrouped_count > 0 {
            format!(" Sessions ({}) + {} ungrouped", sess_count, ungrouped_count)
        } else {
            format!(" Sessions ({})", sess_count)
        };
        t.push_str(" \u{2502} turns: last 60s ");
        t
    };

    state.session_table_state.select(selected_visual_row);

    let table = Table::new(
        rows,
        [
            Constraint::Length(2),      // arrow
            Constraint::Percentage(14), // ID/label/turn#
            Constraint::Percentage(38), // detail/response
            Constraint::Percentage(24), // latency
            Constraint::Percentage(16), // time/status
            Constraint::Length(6),      // port
        ],
    )
    .header(header)
    .block(
        Block::default()
            .borders(Borders::ALL)
            .title(title)
            .border_style(Style::default().fg(border_color)),
    )
    .row_highlight_style(Style::default());

    frame.render_stateful_widget(table, area, &mut state.session_table_state);
}

/// Render a session (and its turns if expanded) as Row(s).
#[allow(clippy::too_many_arguments)]
fn render_session<'a>(
    sess: &SessionGroup,
    state: &AppState,
    is_focused: bool,
    selectable_idx: &mut usize,
    selected_visual_row: &mut Option<usize>,
    current_visual_idx: usize,
    indented: bool,
    colors: &ColorScheme,
) -> Vec<Row<'a>> {
    let mut rows = Vec::new();

    let is_selected = is_focused && *selectable_idx == state.session_selected;
    if *selectable_idx == state.session_selected {
        *selected_visual_row = Some(current_visual_idx);
    }
    let is_expanded = state.session_expanded.contains(&sess.session_id);
    let arrow = if is_expanded { "v" } else { ">" };

    let (status_text, status_color) = session_status(sess, colors);
    // Use total_turns (survives cleanup) when available, fallback to total_requests
    let total = if sess.total_turns > 0 { sess.total_turns } else { sess.total_requests };
    let turns_text = format!("{}/{}", sess.completed_requests, total);

    let task_short = if sess.task_id.len() > 50 {
        format!("{}..", &sess.task_id[..48])
    } else {
        sess.task_id.clone()
    };

    let prefix = if indented { "  " } else { "" };

    let row_style = if is_selected {
        Style::default().bg(colors.row_selected_bg)
    } else {
        Style::default()
    };

    let sess_id_short = if sess.session_id.len() > 7 {
        sess.session_id[..7].to_string()
    } else {
        sess.session_id.clone()
    };

    let (id_label, detail_text) = if indented {
        // Under a client: show task name in ID column, session ID as detail
        (task_short.clone(), sess_id_short)
    } else {
        (sess_id_short, task_short.clone())
    };

    // Compute session elapsed time from first submitted to last completed
    let elapsed_str = if !sess.completed_turns.is_empty() {
        let first_submitted = sess.completed_turns.first().map(|t| t.submitted_at).unwrap_or(0.0);
        let last_completed = sess.completed_turns.last().map(|t| t.completed_at).unwrap_or(0.0);
        if first_submitted > 0.0 && last_completed > first_submitted {
            let elapsed_ms = (last_completed - first_submitted) * 1000.0;
            format!(" {}", format_elapsed(elapsed_ms))
        } else {
            String::new()
        }
    } else {
        String::new()
    };

    rows.push(Row::new(vec![
        Cell::from(format!("{}{}", prefix, arrow)),
        Cell::from(format!("{}{}", prefix, id_label)).style(Style::default().fg(colors.accent_id)),
        Cell::from(detail_text).style(Style::default().fg(colors.text_primary)),
        Cell::from(turns_text).style(Style::default().fg(colors.accent_count)),
        Cell::from(format!("{}{}", status_text, elapsed_str)).style(Style::default().fg(status_color).add_modifier(Modifier::BOLD)),
        Cell::from(""),  // port column (empty for session)
    ]).style(row_style));

    *selectable_idx += 1;

    // Expanded turns - one line per turn, spread across all columns
    if is_expanded {
        for (i, turn) in sess.completed_turns.iter().enumerate() {
            let total_str = format_duration_ms(turn.total_time_ms);
            let wait_str = format_duration_ms(turn.wait_time_ms);

            // Layer 2: if backend_time_ms available, split into infer/proxy; else fallback to proc=
            let timing_str = if let Some(backend_ms) = turn.backend_time_ms {
                let proxy_ms = turn.processing_time_ms - backend_ms;
                format!("infer={} proxy={}", format_duration_ms(backend_ms), format_duration_ms(proxy_ms.max(0.0)))
            } else {
                format!("proc={}", format_duration_ms(turn.processing_time_ms))
            };

            let backend_short = turn
                .backend
                .as_deref()
                .and_then(|b| b.rsplit(':').next())
                .map(|p| format!(":{}", p))
                .unwrap_or_default();

            let turn_prefix = if indented { "    " } else { "  " };

            // Timestamps (HH:MM:SS)
            let sent_time = format_epoch(turn.submitted_at);
            let recv_time = format_epoch(turn.completed_at);

            // Agent gap = time between previous turn completion and this turn submission
            let gap_ms: Option<f64> = if i > 0 {
                let prev = &sess.completed_turns[i - 1];
                if prev.completed_at > 0.0 && turn.submitted_at > 0.0 {
                    Some((turn.submitted_at - prev.completed_at) * 1000.0)
                } else {
                    None
                }
            } else {
                None
            };

            // Cell 3: agent gap or timestamps
            let (cell3_text, cell3_style) = if let Some(gap) = gap_ms {
                // Show obs/act breakdown if available
                let detail = match (turn.agent_obs_ms, turn.agent_act_ms) {
                    (Some(obs), Some(act)) => format!(
                        "agent={}(obs={} act={})",
                        format_duration_ms(gap),
                        format_duration_ms(obs),
                        format_duration_ms(act),
                    ),
                    _ => format!("agent={}", format_duration_ms(gap)),
                };
                let style = if gap > 5000.0 {
                    Style::default().fg(colors.status_warn)
                } else {
                    Style::default().fg(colors.text_secondary)
                };
                (detail, style)
            } else {
                (format!("{}\u{2192}{}", sent_time, recv_time), Style::default().fg(colors.text_primary))
            };

            // Response snippet - gets the widest column (Detail)
            let resp_snippet = turn.response_summary.as_deref()
                .filter(|s| !s.is_empty())
                .map(|s| {
                    if s.len() > 120 { format!("\u{2190} {}..", &s[..118]) }
                    else { format!("\u{2190} {}", s) }
                })
                .unwrap_or_default();

            // Spread across cells:
            // [0] empty  [1] T#/total  [2] response  [3] latency  [4] time/agent  [5] port
            let turn_label = format!("{}T{}/{}", turn_prefix, turn.turn_number.unwrap_or(i as u64 + 1), total);
            let latency_detail = format!("{} wait={} {}", total_str, wait_str, timing_str);
            rows.push(Row::new(vec![
                Cell::from(""),
                Cell::from(turn_label).style(Style::default().fg(colors.text_primary)),
                Cell::from(resp_snippet).style(Style::default().fg(colors.accent)),
                Cell::from(latency_detail).style(Style::default().fg(colors.text_primary)),
                Cell::from(cell3_text).style(cell3_style),
                Cell::from(backend_short).style(Style::default().fg(colors.accent_latency)),
            ]));
        }
    }

    rows
}

/// Aggregate status for a client group.
fn client_aggregate_status(client: &ClientGroup, colors: &ColorScheme) -> (&'static str, Color) {
    let mut has_inflight = false;
    let mut has_pending = false;
    let mut has_failed = false;
    for sess in &client.sessions {
        if sess.in_flight_requests > 0 { has_inflight = true; }
        if sess.pending_requests > 0 { has_pending = true; }
        if sess.failed_requests > 0 { has_failed = true; }
    }
    if has_inflight {
        ("IN-FLGT", colors.accent)
    } else if has_pending {
        ("PENDING", colors.status_warn)
    } else if has_failed {
        ("FAILED", colors.status_error)
    } else {
        ("IDLE", colors.status_ok)
    }
}

/// Status for a single session.
fn session_status(sess: &SessionGroup, colors: &ColorScheme) -> (&'static str, Color) {
    if sess.in_flight_requests > 0 {
        ("IN-FLGT", colors.accent)
    } else if sess.pending_requests > 0 {
        ("PENDING", colors.status_warn)
    } else if sess.failed_requests > 0 {
        ("FAILED", colors.status_error)
    } else {
        ("IDLE", colors.status_ok)
    }
}

/// Shorten a command line for display.
fn shorten_command(cmd: &str) -> String {
    let trimmed = cmd
        .strip_prefix("python -m ")
        .or_else(|| cmd.strip_prefix("python3 -m "))
        .or_else(|| cmd.strip_prefix("python "))
        .or_else(|| cmd.strip_prefix("python3 "))
        .unwrap_or(cmd);
    if trimmed.len() > 60 {
        format!("{}..", &trimmed[..58])
    } else {
        trimmed.to_string()
    }
}

fn shorten_model(model: &str) -> String {
    if let Some(pos) = model.rfind('/') {
        model[pos + 1..].to_string()
    } else {
        model.to_string()
    }
}

fn format_epoch(epoch: f64) -> String {
    if epoch <= 0.0 {
        return "-".to_string();
    }
    let secs = epoch as i64;
    let nanos = ((epoch - secs as f64) * 1_000_000_000.0) as u32;
    match Local.timestamp_opt(secs, nanos) {
        chrono::LocalResult::Single(dt) => dt.format("%H:%M:%S").to_string(),
        _ => "-".to_string(),
    }
}

fn format_duration_ms(ms: f64) -> String {
    if ms < 1000.0 {
        format!("{:.0}ms", ms)
    } else {
        format!("{:.1}s", ms / 1000.0)
    }
}

/// Format a longer duration (session-level) as e.g. "45s", "2m30s", "1h05m".
fn format_elapsed(ms: f64) -> String {
    let total_secs = (ms / 1000.0).round() as u64;
    if total_secs < 60 {
        format!("{}s", total_secs)
    } else if total_secs < 3600 {
        let mins = total_secs / 60;
        let secs = total_secs % 60;
        format!("{}m{:02}s", mins, secs)
    } else {
        let hours = total_secs / 3600;
        let mins = (total_secs % 3600) / 60;
        format!("{}h{:02}m", hours, mins)
    }
}
