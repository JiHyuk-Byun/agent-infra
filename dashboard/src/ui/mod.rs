pub mod analysis;
pub mod backends;
pub mod sessions;
pub mod header;
pub mod help;
pub mod queue;
pub mod recent;
pub mod theme;

use ratatui::Frame;
use ratatui::layout::{Constraint, Direction, Layout};

use crate::model::{AppState, ProxySnapshot, ThroughputSnapshot};
use theme::ColorScheme;

pub fn draw(
    frame: &mut Frame,
    proxy: &ProxySnapshot,
    throughput: &ThroughputSnapshot,
    proxy_url: &str,
    state: &mut AppState,
    colors: &ColorScheme,
) {
    let outer = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),   // header
            Constraint::Min(5),     // backends table
            Constraint::Length(10), // GPU performance + queue status
            Constraint::Length(14), // bottleneck analysis (expanded)
            Constraint::Min(10),    // sessions
            Constraint::Length(5),  // recent completions
            Constraint::Length(1),  // help bar
        ])
        .split(frame.area());

    header::draw(frame, outer[0], proxy, proxy_url, colors);
    backends::draw(frame, outer[1], proxy, state, colors);

    // GPU Performance + Queue Status
    let top_row = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
        .split(outer[2]);

    analysis::draw_gpu_performance(frame, top_row[0], proxy, colors);
    queue::draw(frame, top_row[1], proxy, colors);

    // Bottleneck Analysis (full width, expanded height)
    analysis::draw_bottleneck(frame, outer[3], proxy, colors);

    sessions::draw(frame, outer[4], proxy, state, colors);
    recent::draw(frame, outer[5], throughput, colors);
    help::draw(frame, outer[6], colors);
}
