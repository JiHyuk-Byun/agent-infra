use ratatui::Frame;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Paragraph, Wrap};

use crate::model::{SessionGroup, ProxySnapshot, QueueResponse};
use super::theme::ColorScheme;

/// Diagnosis of the current system state.
enum Diagnosis {
    GpuBound,
    AgentBound,
    Balanced,
    Idle,
    Unhealthy,
}

impl Diagnosis {
    fn label(&self) -> &'static str {
        match self {
            Diagnosis::GpuBound => "GPU-BOUND",
            Diagnosis::AgentBound => "AGENT-BOUND",
            Diagnosis::Balanced => "BALANCED",
            Diagnosis::Idle => "IDLE",
            Diagnosis::Unhealthy => "UNHEALTHY",
        }
    }

    fn color(&self, colors: &ColorScheme) -> Color {
        match self {
            Diagnosis::GpuBound => colors.status_error,
            Diagnosis::AgentBound => colors.status_warn,
            Diagnosis::Balanced => colors.status_ok,
            Diagnosis::Idle => colors.accent,
            Diagnosis::Unhealthy => colors.status_warn,
        }
    }

    fn advice(&self) -> &'static str {
        match self {
            Diagnosis::GpuBound => "All GPUs near capacity. Add replicas or reduce --num-parallel.",
            Diagnosis::AgentBound => "Agent overhead is high. Check obs/action timing in turn details.",
            Diagnosis::Balanced => "System running smoothly. Load well distributed.",
            Diagnosis::Idle => "GPUs underutilized. Can increase --num-parallel.",
            Diagnosis::Unhealthy => "Some GPUs offline. Check backend health.",
        }
    }
}

/// Aggregated pipeline timing stats across all turns.
pub struct TimingStats {
    pub count: u64,
    pub avg_inference_ms: f64,  // backend_time_ms or processing_time_ms
    pub avg_gap_ms: f64,        // inter-turn agent gap
    pub avg_wait_ms: f64,       // queue wait
    pub avg_proxy_ms: f64,      // processing - backend overhead
    pub avg_total_ms: f64,      // total_time_ms average
    pub has_backend_time: bool,  // whether backend_time_ms data is available
    pub gap_count: u64,          // how many gap measurements
}

/// Collect timing stats from all sessions across the queue.
pub fn collect_timing_stats(queue: &QueueResponse) -> TimingStats {
    let all_sessions = collect_all_sessions(queue);

    let mut count = 0_u64;
    let mut sum_inference = 0.0_f64;
    let mut sum_wait = 0.0_f64;
    let mut sum_proxy = 0.0_f64;
    let mut sum_total = 0.0_f64;
    let mut sum_gap = 0.0_f64;
    let mut gap_count = 0_u64;
    let mut has_backend = false;

    for sess in &all_sessions {
        for (i, turn) in sess.completed_turns.iter().enumerate() {
            count += 1;
            sum_wait += turn.wait_time_ms;
            sum_total += turn.total_time_ms;

            if let Some(backend_ms) = turn.backend_time_ms {
                has_backend = true;
                sum_inference += backend_ms;
                sum_proxy += (turn.processing_time_ms - backend_ms).max(0.0);
            } else {
                sum_inference += turn.processing_time_ms;
            }

            // Gap: time between prev turn completion and this turn submission (within same session)
            if i > 0 {
                let prev = &sess.completed_turns[i - 1];
                if prev.completed_at > 0.0 && turn.submitted_at > 0.0 {
                    let gap = (turn.submitted_at - prev.completed_at) * 1000.0;
                    if gap >= 0.0 {
                        sum_gap += gap;
                        gap_count += 1;
                    }
                }
            }
        }
    }

    let c = count.max(1) as f64;
    let g = gap_count.max(1) as f64;
    TimingStats {
        count,
        avg_inference_ms: sum_inference / c,
        avg_gap_ms: if gap_count > 0 { sum_gap / g } else { 0.0 },
        avg_wait_ms: sum_wait / c,
        avg_proxy_ms: if has_backend { sum_proxy / c } else { 0.0 },
        avg_total_ms: sum_total / c,
        has_backend_time: has_backend,
        gap_count,
    }
}

fn collect_all_sessions(queue: &QueueResponse) -> Vec<&SessionGroup> {
    let mut sessions = Vec::new();

    // Collect from clients
    for client in &queue.clients {
        for sess in &client.sessions {
            sessions.push(sess);
        }
    }

    // Orphan sessions
    for sess in &queue.orphan_sessions {
        sessions.push(sess);
    }

    // Fallback to flat sessions
    if queue.clients.is_empty() && queue.orphan_sessions.is_empty() {
        for sess in &queue.sessions {
            sessions.push(sess);
        }
    }

    sessions
}

/// Draw the GPU Performance panel.
pub fn draw_gpu_performance(frame: &mut Frame, area: Rect, proxy: &ProxySnapshot, colors: &ColorScheme) {
    if !proxy.connected {
        let block = Block::default()
            .borders(Borders::ALL)
            .title(" GPU Performance ")
            .border_style(Style::default().fg(colors.border_normal));
        let msg = Paragraph::new(Line::from(Span::styled(
            "  Proxy offline",
            Style::default().fg(colors.text_disabled),
        )))
        .block(block);
        frame.render_widget(msg, area);
        return;
    }

    // Collect per-backend stats from /stats pools
    struct BackendInfo {
        port: String,
        healthy: bool,
        gpu_load: u64,
        inflight: u64,
        requests: u64,
        avg_latency_ms: f64,
    }

    let mut backends: Vec<BackendInfo> = Vec::new();

    // Use stats pools for requests/errors/latency
    for pool in &proxy.stats.pools {
        for b in &pool.backends {
            let port = b
                .url
                .rsplit(':')
                .next()
                .unwrap_or(&b.url)
                .to_string();

            // Find matching queue backend for gpu_load
            let queue_b = proxy.queue.backends.iter().find(|qb| qb.url == b.url);
            let gpu_load = queue_b.map_or(0, |qb| qb.gpu_load);

            backends.push(BackendInfo {
                port,
                healthy: b.healthy,
                gpu_load,
                inflight: b.inflight,
                requests: b.requests,
                avg_latency_ms: b.avg_latency_ms,
            });
        }
    }

    let mut lines: Vec<Line> = Vec::new();

    // Header
    lines.push(Line::from(vec![
        Span::styled(
            format!(
                "  {:<10} {:>6} {:>9} {:>9} {:>8} {:>8}",
                "Backend", "Health", "GPU Load", "Inflight", "Reqs", "Avg Lat"
            ),
            Style::default()
                .fg(colors.table_header)
                .add_modifier(Modifier::BOLD),
        ),
    ]));

    // Find slowest for highlighting
    let valid_latencies: Vec<f64> = backends
        .iter()
        .filter(|b| b.healthy && b.requests > 0)
        .map(|b| b.avg_latency_ms)
        .collect();
    let max_latency = valid_latencies.iter().cloned().fold(0.0_f64, f64::max);
    let min_latency = valid_latencies
        .iter()
        .cloned()
        .fold(f64::MAX, f64::min);

    for b in &backends {
        let health = if b.healthy { "\u{2713}" } else { "\u{2717}" };
        let health_color = if b.healthy { colors.status_ok } else { colors.status_error };
        let is_slowest =
            b.healthy && b.requests > 0 && b.avg_latency_ms == max_latency && backends.len() > 1;

        let lat_str = if b.healthy && b.requests > 0 {
            format_latency(b.avg_latency_ms)
        } else {
            "-".to_string()
        };
        let load_str = if b.healthy {
            b.gpu_load.to_string()
        } else {
            "-".to_string()
        };
        let inflight_str = if b.healthy {
            b.inflight.to_string()
        } else {
            "-".to_string()
        };
        let req_str = if b.healthy {
            b.requests.to_string()
        } else {
            "-".to_string()
        };

        let row_text = format!(
            "  :{:<9} {:>6} {:>9} {:>9} {:>8} {:>8}",
            b.port, health, load_str, inflight_str, req_str, lat_str
        );

        let row_style = if is_slowest {
            Style::default().fg(health_color)
        } else {
            Style::default().fg(colors.text_primary)
        };

        let mut spans = vec![Span::styled(row_text, row_style)];
        if is_slowest {
            spans.push(Span::styled(
                " \u{2190} slowest",
                Style::default().fg(colors.status_error),
            ));
        }
        lines.push(Line::from(spans));
    }

    // Summary line
    if !valid_latencies.is_empty() && backends.len() > 1 && min_latency > 0.0 {
        let diff_pct = ((max_latency - min_latency) / min_latency * 100.0).round();
        let gpu_loads: Vec<u64> = backends
            .iter()
            .filter(|b| b.healthy)
            .map(|b| b.gpu_load)
            .collect();
        let load_min = gpu_loads.iter().copied().min().unwrap_or(0);
        let load_max = gpu_loads.iter().copied().max().unwrap_or(0);
        let spread_label = if load_max - load_min <= 1 {
            "even"
        } else {
            "uneven"
        };

        lines.push(Line::from(""));
        lines.push(Line::from(vec![
            Span::styled("  Spread: ", Style::default().fg(colors.text_primary)),
            Span::styled(
                format!(
                    "{:.0}% latency diff, load {}-{} ({})",
                    diff_pct, load_min, load_max, spread_label
                ),
                Style::default().fg(colors.text_secondary),
            ),
        ]));
    }

    let paragraph = Paragraph::new(lines)
        .wrap(Wrap { trim: false })
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(" GPU Performance ")
                .border_style(Style::default().fg(colors.border_focused)),
        );

    frame.render_widget(paragraph, area);
}

/// Draw the Bottleneck Analysis panel.
pub fn draw_bottleneck(frame: &mut Frame, area: Rect, proxy: &ProxySnapshot, colors: &ColorScheme) {
    if !proxy.connected {
        let block = Block::default()
            .borders(Borders::ALL)
            .title(" Bottleneck Analysis ")
            .border_style(Style::default().fg(colors.border_normal));
        let msg = Paragraph::new(Line::from(Span::styled(
            "  Proxy offline",
            Style::default().fg(colors.text_disabled),
        )))
        .block(block);
        frame.render_widget(msg, area);
        return;
    }

    let summary = &proxy.queue.summary;
    let qbackends = &proxy.queue.backends;

    let total_backends = qbackends.len() as u64;
    let healthy_backends = qbackends.iter().filter(|b| b.healthy).count() as u64;

    // Count active sessions across all sources
    let all_sessions = collect_all_sessions(&proxy.queue);
    let active_sessions = all_sessions
        .iter()
        .filter(|s| s.in_flight_requests > 0 || s.pending_requests > 0)
        .count();

    // Collect pipeline timing stats
    let timing = collect_timing_stats(&proxy.queue);

    // GPU metrics
    let healthy_loads: Vec<u64> = qbackends
        .iter()
        .filter(|b| b.healthy)
        .map(|b| b.gpu_load)
        .collect();
    let avg_gpu_load = if !healthy_loads.is_empty() {
        healthy_loads.iter().sum::<u64>() as f64 / healthy_loads.len() as f64
    } else {
        0.0
    };
    let total_inflight: u64 = qbackends
        .iter()
        .filter(|b| b.healthy)
        .map(|b| b.inflight)
        .sum();
    let gpu_util = if healthy_backends > 0 {
        (total_inflight as f64 / healthy_backends as f64 * 100.0).min(100.0)
    } else {
        0.0
    };

    // Diagnosis — add AgentBound check
    let diagnosis = if healthy_backends < total_backends && total_backends > 0 {
        Diagnosis::Unhealthy
    } else if timing.gap_count > 0 && timing.avg_gap_ms > timing.avg_inference_ms * 0.5 && timing.avg_inference_ms > 0.0 {
        Diagnosis::AgentBound
    } else if summary.pending > 0 && avg_gpu_load >= healthy_backends as f64 * 0.8 {
        Diagnosis::GpuBound
    } else if avg_gpu_load < 0.5 && summary.pending == 0 {
        Diagnosis::Idle
    } else {
        Diagnosis::Balanced
    };

    let mut lines: Vec<Line> = Vec::new();

    lines.push(Line::from(vec![
        Span::styled("  Sessions:  ", Style::default().fg(colors.text_primary)),
        Span::styled(
            format!("{} active", active_sessions),
            Style::default()
                .fg(colors.accent)
                .add_modifier(Modifier::BOLD),
        ),
    ]));

    lines.push(Line::from(vec![
        Span::styled("  GPUs:      ", Style::default().fg(colors.text_primary)),
        Span::styled(
            format!("{} healthy / {} total", healthy_backends, total_backends),
            Style::default().fg(if healthy_backends == total_backends {
                colors.status_ok
            } else {
                colors.status_warn
            }),
        ),
    ]));

    lines.push(Line::from(""));

    // Pipeline timing line
    if timing.count > 0 {
        let pipeline_str = if timing.has_backend_time {
            format!(
                "agent={}  inference={}  proxy={}  wait={}",
                format_latency(timing.avg_gap_ms),
                format_latency(timing.avg_inference_ms),
                format_latency(timing.avg_proxy_ms),
                format_latency(timing.avg_wait_ms),
            )
        } else if timing.gap_count > 0 {
            format!(
                "agent={}  proc={}  wait={}",
                format_latency(timing.avg_gap_ms),
                format_latency(timing.avg_inference_ms),
                format_latency(timing.avg_wait_ms),
            )
        } else {
            format!(
                "proc={}  wait={}",
                format_latency(timing.avg_inference_ms),
                format_latency(timing.avg_wait_ms),
            )
        };
        lines.push(Line::from(vec![
            Span::styled("  Pipeline: ", Style::default().fg(colors.text_primary)),
            Span::styled(pipeline_str, Style::default().fg(colors.text_primary)),
        ]));
    } else {
        lines.push(Line::from(vec![
            Span::styled("  Pipeline: ", Style::default().fg(colors.text_primary)),
            Span::styled("no data", Style::default().fg(colors.text_disabled)),
        ]));
    }

    lines.push(Line::from(vec![
        Span::styled("  GPU:      ", Style::default().fg(colors.text_primary)),
        Span::styled(
            format!(
                "load_avg={:.1} util={:.0}%",
                avg_gpu_load, gpu_util
            ),
            Style::default().fg(colors.text_primary),
        ),
    ]));

    lines.push(Line::from(""));

    lines.push(Line::from(vec![
        Span::styled("  Diagnosis: ", Style::default().fg(colors.text_primary)),
        Span::styled(
            diagnosis.label(),
            Style::default()
                .fg(diagnosis.color(colors))
                .add_modifier(Modifier::BOLD),
        ),
    ]));

    lines.push(Line::from(vec![Span::styled(
        format!("  \u{2192} {}", diagnosis.advice()),
        Style::default().fg(colors.text_secondary),
    )]));

    // Breakdown percentage line
    if timing.count > 0 && timing.gap_count > 0 {
        let total = timing.avg_gap_ms + timing.avg_inference_ms + timing.avg_proxy_ms;
        if total > 0.0 {
            let infer_pct = (timing.avg_inference_ms / total * 100.0).round() as u64;
            let agent_pct = (timing.avg_gap_ms / total * 100.0).round() as u64;
            let proxy_pct = 100_u64.saturating_sub(infer_pct).saturating_sub(agent_pct);
            let infer_label = if timing.has_backend_time { "inference" } else { "proc" };
            lines.push(Line::from(vec![Span::styled(
                format!(
                    "  Breakdown: {} {}%, agent {}%, proxy {}%",
                    infer_label, infer_pct, agent_pct, proxy_pct,
                ),
                Style::default().fg(colors.text_secondary),
            )]));
        }
    }

    // Golden point: suggested --num-parallel
    if timing.count > 0 && timing.avg_inference_ms > 0.0 && healthy_backends > 0 {
        let per_gpu = (timing.avg_inference_ms + timing.avg_gap_ms) / timing.avg_inference_ms;
        let optimal = (healthy_backends as f64 * per_gpu).ceil() as u64;
        lines.push(Line::from(vec![
            Span::styled("  Suggested: ", Style::default().fg(colors.text_primary)),
            Span::styled(
                format!("--num-parallel {}",  optimal),
                Style::default()
                    .fg(colors.accent)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                format!("  ({:.1}/gpu \u{00d7} {} gpus)", per_gpu, healthy_backends),
                Style::default().fg(colors.text_secondary),
            ),
        ]));
    }

    let paragraph = Paragraph::new(lines)
        .wrap(Wrap { trim: false })
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(" Bottleneck Analysis ")
                .border_style(Style::default().fg(colors.border_focused)),
        );

    frame.render_widget(paragraph, area);
}

fn format_latency(ms: f64) -> String {
    if ms < 1000.0 {
        format!("{:.0}ms", ms)
    } else {
        format!("{:.1}s", ms / 1000.0)
    }
}
