use std::collections::{HashSet, VecDeque};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, SystemTime};

use chrono::{DateTime, Local};

use crate::model::{CompletionEntry, ThroughputSnapshot};

#[derive(serde::Deserialize)]
struct VerificationFile {
    #[serde(default)]
    overall_success: Option<bool>,
}

pub fn spawn_throughput_collector(
    artifacts_dir: PathBuf,
    interval_secs: u64,
    window_secs: u64,
    recent_count: usize,
    snapshot: Arc<Mutex<ThroughputSnapshot>>,
) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        let mut seen: HashSet<PathBuf> = HashSet::new();
        let mut timestamps: VecDeque<SystemTime> = VecDeque::new();
        let mut all_entries: Vec<(SystemTime, CompletionEntry)> = Vec::new();
        let mut total = 0usize;
        let mut success = 0usize;
        let mut failure = 0usize;

        loop {
            let artifact_dirs = find_artifact_dirs(&artifacts_dir);
            for path in artifact_dirs {
                if seen.contains(&path) {
                    continue;
                }

                seen.insert(path.clone());
                total += 1;

                let artifact_path = path.join("artifact.json");
                let modified = artifact_path
                    .metadata()
                    .and_then(|m| m.modified())
                    .unwrap_or_else(|_| SystemTime::now());

                timestamps.push_back(modified);

                let is_success = check_success(&path);
                if is_success {
                    success += 1;
                } else {
                    failure += 1;
                }

                let dt: DateTime<Local> = modified.into();
                let time_str = dt.format("%H:%M:%S").to_string();
                let spec_name = path
                    .file_name()
                    .map(|n| n.to_string_lossy().to_string())
                    .unwrap_or_else(|| "unknown".to_string());

                all_entries.push((
                    modified,
                    CompletionEntry {
                        time: time_str,
                        spec_name,
                        success: is_success,
                    },
                ));
            }

            // Prune timestamps outside window
            let window_duration = Duration::from_secs(window_secs);
            let now = SystemTime::now();
            while let Some(front) = timestamps.front() {
                if now.duration_since(*front).unwrap_or(Duration::ZERO) > window_duration {
                    timestamps.pop_front();
                } else {
                    break;
                }
            }

            // Calculate rate
            let rate_per_min = if timestamps.is_empty() {
                0.0
            } else if timestamps.len() == 1 {
                0.0
            } else {
                let oldest = *timestamps.front().unwrap();
                let elapsed = now.duration_since(oldest).unwrap_or(Duration::from_secs(1));
                let elapsed_min = elapsed.as_secs_f64() / 60.0;
                if elapsed_min > 0.0 {
                    timestamps.len() as f64 / elapsed_min
                } else {
                    0.0
                }
            };

            // Build recent list (sorted newest first)
            all_entries.sort_by(|a, b| b.0.cmp(&a.0));
            let recent: Vec<CompletionEntry> = all_entries
                .iter()
                .take(recent_count)
                .map(|(_, e)| e.clone())
                .collect();

            // Update snapshot
            let mut snap = snapshot.lock().unwrap();
            snap.enabled = true;
            snap.total = total;
            snap.success = success;
            snap.failure = failure;
            snap.rate_per_min = rate_per_min;
            snap.recent = recent;
            drop(snap);

            thread::sleep(Duration::from_secs(interval_secs));
        }
    })
}

/// Recursively find directories containing artifact.json.
fn find_artifact_dirs(root: &Path) -> Vec<PathBuf> {
    let mut result = Vec::new();
    walk_for_artifacts(root, &mut result);
    result
}

fn walk_for_artifacts(dir: &Path, result: &mut Vec<PathBuf>) {
    let entries = match fs::read_dir(dir) {
        Ok(e) => e,
        Err(_) => return,
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        if path.join("artifact.json").exists() {
            result.push(path);
        } else {
            walk_for_artifacts(&path, result);
        }
    }
}

fn check_success(dir: &Path) -> bool {
    let verification_path = dir.join("verification.json");
    if verification_path.exists() {
        if let Ok(content) = fs::read_to_string(&verification_path) {
            if let Ok(v) = serde_json::from_str::<VerificationFile>(&content) {
                return v.overall_success.unwrap_or(false);
            }
        }
    }
    false
}
