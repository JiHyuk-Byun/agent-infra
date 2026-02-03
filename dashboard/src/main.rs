mod collector;
mod model;
mod ui;

use std::io;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use clap::Parser;
use crossterm::event::{self, Event, KeyCode};
use crossterm::terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen};
use crossterm::execute;
use ratatui::backend::CrosstermBackend;
use ratatui::Terminal;

use model::{AppState, FocusedPanel, ProxySnapshot, SelectableItem, ThroughputSnapshot};
use ui::theme::ColorScheme;

#[derive(Clone, Copy, Debug, clap::ValueEnum)]
enum ThemeChoice {
    Dark,
    Light,
}

#[derive(Parser)]
#[command(name = "dashboard", about = "Real-time TUI monitoring dashboard for Agent Infra")]
struct Cli {
    /// Proxy server URL
    #[arg(long, default_value = "http://localhost:5800")]
    proxy: String,

    /// Artifact directory for throughput tracking
    #[arg(long)]
    artifacts: Option<PathBuf>,

    /// Screen refresh interval in seconds
    #[arg(long, default_value_t = 2)]
    interval: u64,

    /// Throughput sliding window in seconds
    #[arg(long, default_value_t = 300)]
    window: u64,

    /// Number of recent completions to display
    #[arg(long, default_value_t = 10)]
    recent: usize,

    /// Color theme
    #[arg(long, default_value = "dark", value_enum)]
    theme: ThemeChoice,
}

fn main() -> io::Result<()> {
    let cli = Cli::parse();
    let colors = match cli.theme {
        ThemeChoice::Dark => ColorScheme::dark(),
        ThemeChoice::Light => ColorScheme::light(),
    };

    // Shared state
    let proxy_snapshot = Arc::new(Mutex::new(ProxySnapshot::default()));
    let throughput_snapshot = Arc::new(Mutex::new(ThroughputSnapshot::default()));

    // Spawn proxy collector
    let _proxy_handle = collector::proxy::spawn_proxy_collector(
        cli.proxy.clone(),
        cli.interval,
        Arc::clone(&proxy_snapshot),
    );

    // Spawn throughput collector if artifacts dir specified
    if let Some(ref artifacts_dir) = cli.artifacts {
        let _tp_handle = collector::throughput::spawn_throughput_collector(
            artifacts_dir.clone(),
            cli.interval,
            cli.window,
            cli.recent,
            Arc::clone(&throughput_snapshot),
        );
    }

    // Dashboard UI state
    let mut app_state = AppState::default();

    // Setup terminal
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    // Main event loop
    let poll_timeout = Duration::from_millis(200);

    loop {
        // Draw
        let proxy_snap = proxy_snapshot.lock().unwrap().clone();
        let tp_snap = throughput_snapshot.lock().unwrap().clone();

        // Clamp selection indices
        let model_count = proxy_snap.stats.pools.len();
        if model_count > 0 && app_state.backend_selected >= model_count {
            app_state.backend_selected = model_count - 1;
        }
        let selectable_items = app_state.build_selectable_items(&proxy_snap.queue);
        let selectable_count = selectable_items.len();
        if selectable_count > 0 && app_state.session_selected >= selectable_count {
            app_state.session_selected = selectable_count - 1;
        }

        terminal.draw(|frame| {
            ui::draw(frame, &proxy_snap, &tp_snap, &cli.proxy, &mut app_state, &colors);
        })?;

        // Handle input
        if event::poll(poll_timeout)? {
            if let Event::Key(key) = event::read()? {
                match key.code {
                    KeyCode::Char('q') | KeyCode::Esc => break,
                    KeyCode::Tab | KeyCode::BackTab => {
                        app_state.focused_panel = match app_state.focused_panel {
                            FocusedPanel::Backends => FocusedPanel::Sessions,
                            FocusedPanel::Sessions => FocusedPanel::Backends,
                        };
                    }
                    KeyCode::Up | KeyCode::Char('k') => {
                        match app_state.focused_panel {
                            FocusedPanel::Backends => {
                                if app_state.backend_selected > 0 {
                                    app_state.backend_selected -= 1;
                                }
                            }
                            FocusedPanel::Sessions => {
                                if app_state.session_selected > 0 {
                                    app_state.session_selected -= 1;
                                }
                            }
                        }
                    }
                    KeyCode::Down | KeyCode::Char('j') => {
                        match app_state.focused_panel {
                            FocusedPanel::Backends => {
                                if model_count > 0 && app_state.backend_selected < model_count - 1 {
                                    app_state.backend_selected += 1;
                                }
                            }
                            FocusedPanel::Sessions => {
                                if selectable_count > 0 && app_state.session_selected < selectable_count - 1 {
                                    app_state.session_selected += 1;
                                }
                            }
                        }
                    }
                    KeyCode::Enter => {
                        match app_state.focused_panel {
                            FocusedPanel::Backends => {
                                if model_count > 0 {
                                    if let Some(pool) = proxy_snap.stats.pools.get(app_state.backend_selected) {
                                        let name = pool.model.clone();
                                        if app_state.backend_expanded.contains(&name) {
                                            app_state.backend_expanded.remove(&name);
                                        } else {
                                            app_state.backend_expanded.insert(name);
                                        }
                                    }
                                }
                            }
                            FocusedPanel::Sessions => {
                                if let Some(item) = selectable_items.get(app_state.session_selected) {
                                    match item {
                                        SelectableItem::Client(cid) => {
                                            if app_state.client_expanded.contains(cid) {
                                                app_state.client_expanded.remove(cid);
                                            } else {
                                                app_state.client_expanded.insert(cid.clone());
                                            }
                                        }
                                        SelectableItem::Session(sid) => {
                                            if app_state.session_expanded.contains(sid) {
                                                app_state.session_expanded.remove(sid);
                                            } else {
                                                app_state.session_expanded.insert(sid.clone());
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
    }

    // Restore terminal
    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;

    Ok(())
}
