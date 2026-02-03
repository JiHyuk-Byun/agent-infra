use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use crate::model::{ProxySnapshot, QueueResponse, StatsResponse};

pub fn spawn_proxy_collector(
    proxy_url: String,
    interval_secs: u64,
    snapshot: Arc<Mutex<ProxySnapshot>>,
) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        let client = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(2))
            .build()
            .expect("failed to build HTTP client");

        loop {
            let stats_url = format!("{}/stats", proxy_url);
            let queue_url = format!("{}/queue/status", proxy_url);

            let stats_result = client.get(&stats_url).send().and_then(|r| r.json::<StatsResponse>());
            let queue_result = client.get(&queue_url).send().and_then(|r| r.json::<QueueResponse>());

            let mut snap = snapshot.lock().unwrap();

            match (stats_result, queue_result) {
                (Ok(stats), Ok(queue)) => {
                    snap.connected = true;
                    snap.stats = stats;
                    snap.queue = queue;
                }
                (Ok(stats), Err(_)) => {
                    snap.connected = true;
                    snap.stats = stats;
                    // keep previous queue data
                }
                (Err(_), Ok(queue)) => {
                    snap.connected = true;
                    snap.queue = queue;
                    // keep previous stats data
                }
                (Err(_), Err(_)) => {
                    snap.connected = false;
                    // keep all previous data
                }
            }

            drop(snap);
            thread::sleep(Duration::from_secs(interval_secs));
        }
    })
}
