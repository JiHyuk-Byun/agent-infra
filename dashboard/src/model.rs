use std::collections::HashSet;

use ratatui::widgets::TableState;
use serde::Deserialize;

// --- /stats response ---

#[allow(dead_code)]
#[derive(Deserialize, Clone, Default, Debug)]
pub struct StatsResponse {
    #[serde(default)]
    pub uptime_seconds: f64,
    #[serde(default)]
    pub total_requests: u64,
    #[serde(default)]
    pub total_errors: u64,
    #[serde(default)]
    pub error_rate: f64,
    #[serde(default)]
    pub requests_per_minute: f64,
    #[serde(default)]
    pub strategy: String,
    #[serde(default)]
    pub models: Vec<String>,
    #[serde(default)]
    pub pools: Vec<PoolStats>,
}

#[derive(Deserialize, Clone, Debug)]
pub struct PoolStats {
    pub model: String,
    #[serde(default)]
    pub backends: Vec<BackendStats>,
}

#[allow(dead_code)]
#[derive(Deserialize, Clone, Debug)]
pub struct BackendStats {
    pub url: String,
    #[serde(default)]
    pub healthy: bool,
    #[serde(default)]
    pub requests: u64,
    #[serde(default)]
    pub errors: u64,
    #[serde(default)]
    pub avg_latency_ms: f64,
    #[serde(default)]
    pub inflight: u64,
    #[serde(default)]
    pub partition: String,
}

// --- /queue/status response ---

#[derive(Deserialize, Clone, Default, Debug)]
pub struct QueueResponse {
    #[serde(default)]
    pub summary: QueueSummary,
    #[serde(default)]
    pub pending: Vec<QueueRequest>,
    #[serde(default)]
    pub in_flight: Vec<QueueRequest>,
    #[serde(default)]
    pub backends: Vec<QueueBackend>,

    // Dual terminology support: sessions (new) and episodes (legacy)
    #[serde(default, alias = "episodes")]
    pub sessions: Vec<SessionGroup>,

    // Dual terminology support: clients (new) and processes (legacy)
    #[serde(default, alias = "processes")]
    pub clients: Vec<ClientGroup>,

    #[serde(default, alias = "orphan_episodes")]
    pub orphan_sessions: Vec<SessionGroup>,
}

#[allow(dead_code)]
#[derive(Deserialize, Clone, Debug)]
pub struct QueueRequest {
    #[serde(default)]
    pub request_id: String,
    #[serde(default)]
    pub source: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub backend: Option<String>,
    #[serde(default)]
    pub wait_time_ms: f64,
    #[serde(default)]
    pub processing_time_ms: Option<f64>,

    // Dual terminology support
    #[serde(default, alias = "episode_id")]
    pub session_id: Option<String>,
    #[serde(default, alias = "instruction_id")]
    pub task_id: Option<String>,
}

#[derive(Deserialize, Clone, Default, Debug)]
pub struct QueueSummary {
    #[serde(default)]
    pub pending: u64,
    #[serde(default)]
    pub in_flight: u64,
    #[serde(default)]
    pub completed_last_minute: u64,
    #[serde(default)]
    pub total_tracked: u64,
}

#[allow(dead_code)]
#[derive(Deserialize, Clone, Debug)]
pub struct QueueBackend {
    pub url: String,
    #[serde(default)]
    pub healthy: bool,
    #[serde(default)]
    pub gpu_load: u64,
    #[serde(default)]
    pub inflight: u64,
    #[serde(default)]
    pub avg_latency_ms: f64,
    #[serde(default)]
    pub partition: String,
}

// --- Client (Process) + Session (Episode) tracking ---

/// Client group - represents a process running multiple sessions.
/// Aliases: process_id, process_command, episodes (for backward compat)
#[derive(Deserialize, Clone, Default, Debug)]
pub struct ClientGroup {
    #[serde(default, alias = "process_id")]
    pub client_id: String,
    #[serde(default, alias = "process_command")]
    pub client_command: String,
    #[serde(default, alias = "episodes")]
    pub sessions: Vec<SessionGroup>,
}

/// Session group - represents a multi-turn conversation.
/// Aliases: episode_id, instruction_id (for backward compat)
#[allow(dead_code)]
#[derive(Deserialize, Clone, Default, Debug)]
pub struct SessionGroup {
    #[serde(default, alias = "episode_id")]
    pub session_id: String,
    #[serde(default, alias = "instruction_id")]
    pub task_id: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub source: String,
    #[serde(default)]
    pub total_requests: u64,
    #[serde(default)]
    pub completed_requests: u64,
    #[serde(default)]
    pub pending_requests: u64,
    #[serde(default)]
    pub in_flight_requests: u64,
    #[serde(default)]
    pub failed_requests: u64,
    #[serde(default)]
    pub completed_turns: Vec<CompletedTurn>,
    /// Total turns ever assigned to this session (survives request cleanup).
    #[serde(default)]
    pub total_turns: u64,
}

#[allow(dead_code)]
#[derive(Deserialize, Clone, Default, Debug)]
pub struct CompletedTurn {
    #[serde(default)]
    pub request_id: String,
    #[serde(default)]
    pub backend: Option<String>,
    #[serde(default)]
    pub request_summary: Option<String>,
    #[serde(default)]
    pub response_summary: Option<String>,
    #[serde(default)]
    pub submitted_at: f64,
    #[serde(default)]
    pub completed_at: f64,
    #[serde(default)]
    pub total_time_ms: f64,
    #[serde(default)]
    pub wait_time_ms: f64,
    #[serde(default)]
    pub processing_time_ms: f64,
    #[serde(default)]
    pub backend_time_ms: Option<f64>,
    #[serde(default, alias = "agent_pre_ms")]
    pub agent_obs_ms: Option<f64>,
    #[serde(default, alias = "agent_post_ms")]
    pub agent_act_ms: Option<f64>,
    /// Sequential turn number within session (1-indexed, assigned by proxy).
    #[serde(default)]
    pub turn_number: Option<u64>,
}

// --- Dashboard UI state ---

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FocusedPanel {
    Backends,
    Sessions,
}

/// Selectable item in the hierarchical sessions panel.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum SelectableItem {
    Client(String),   // client_id
    Session(String),  // session_id
}

#[derive(Debug)]
pub struct AppState {
    pub focused_panel: FocusedPanel,
    // Sessions panel -- hierarchical (Client -> Session -> Turn)
    pub session_selected: usize,              // index into flattened selectable items
    pub client_expanded: HashSet<String>,     // expanded client_ids
    pub session_expanded: HashSet<String>,    // expanded session_ids
    pub session_table_state: TableState,
    // Backends panel
    pub backend_selected: usize,
    pub backend_expanded: HashSet<String>,  // expanded model names
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            focused_panel: FocusedPanel::Backends,
            session_selected: 0,
            client_expanded: HashSet::new(),
            session_expanded: HashSet::new(),
            session_table_state: TableState::default(),
            backend_selected: 0,
            backend_expanded: HashSet::new(),
        }
    }
}

impl AppState {
    /// Build the flat list of selectable items from the current queue data.
    pub fn build_selectable_items(&self, queue: &QueueResponse) -> Vec<SelectableItem> {
        let mut items = Vec::new();

        let clients = &queue.clients;
        let orphans = &queue.orphan_sessions;

        if !clients.is_empty() || !orphans.is_empty() {
            // Hierarchical mode
            for client in clients {
                items.push(SelectableItem::Client(client.client_id.clone()));
                if self.client_expanded.contains(&client.client_id) {
                    for sess in &client.sessions {
                        items.push(SelectableItem::Session(sess.session_id.clone()));
                    }
                }
            }
            // Orphan sessions (no client)
            for sess in orphans {
                items.push(SelectableItem::Session(sess.session_id.clone()));
            }
        } else {
            // Flat fallback (old proxy without client grouping)
            for sess in &queue.sessions {
                items.push(SelectableItem::Session(sess.session_id.clone()));
            }
        }

        items
    }

    /// Resolve the currently selected item.
    #[allow(dead_code)]
    pub fn resolve_selected(&self, queue: &QueueResponse) -> Option<SelectableItem> {
        let items = self.build_selectable_items(queue);
        items.get(self.session_selected).cloned()
    }
}

// --- Internal snapshots ---

#[derive(Clone, Default, Debug)]
pub struct ProxySnapshot {
    pub connected: bool,
    pub stats: StatsResponse,
    pub queue: QueueResponse,
}

#[derive(Clone, Default, Debug)]
pub struct ThroughputSnapshot {
    pub enabled: bool,
    pub total: usize,
    pub success: usize,
    pub failure: usize,
    pub rate_per_min: f64,
    pub recent: Vec<CompletionEntry>,
}

#[derive(Clone, Debug)]
pub struct CompletionEntry {
    pub time: String,
    pub spec_name: String,
    pub success: bool,
}
