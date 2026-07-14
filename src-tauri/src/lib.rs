use std::{
    fs,
    net::{IpAddr, Ipv4Addr, SocketAddr, TcpStream},
    path::{Path, PathBuf},
    sync::Mutex,
    time::{Duration, Instant},
};

use serde::Serialize;
use tauri::{AppHandle, Manager, RunEvent};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

const BACKEND_PORT: u16 = 8765;
const BACKEND_START_TIMEOUT: Duration = Duration::from_secs(20);
const BACKEND_START_GRACE: Duration = Duration::from_secs(5);
const BACKEND_CONNECT_TIMEOUT: Duration = Duration::from_millis(150);

#[derive(Default)]
struct BackendState {
    child: Mutex<Option<ManagedBackend>>,
}

struct ManagedBackend {
    child: CommandChild,
    started_at: Instant,
}

#[derive(Debug, Clone)]
struct BackendPaths {
    data_dir: PathBuf,
    config_path: PathBuf,
    database_path: PathBuf,
    workspace_path: PathBuf,
    log_path: PathBuf,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct BackendStatus {
    ready: bool,
    started: bool,
}

fn backend_address() -> SocketAddr {
    SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), BACKEND_PORT)
}

fn backend_paths(data_dir: &Path) -> BackendPaths {
    BackendPaths {
        data_dir: data_dir.to_path_buf(),
        config_path: data_dir.join("config.yaml"),
        database_path: data_dir.join("master.db"),
        workspace_path: data_dir.join("workspace"),
        log_path: data_dir.join("backend.log"),
    }
}

fn endpoint_is_open(address: SocketAddr) -> bool {
    TcpStream::connect_timeout(&address, BACKEND_CONNECT_TIMEOUT).is_ok()
}

fn wait_for_endpoint(address: SocketAddr, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    loop {
        if endpoint_is_open(address) {
            return true;
        }
        if Instant::now() >= deadline {
            return false;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn backend_can_be_replaced(uptime: Duration) -> bool {
    uptime >= BACKEND_START_GRACE
}

fn start_backend(app: &AppHandle) -> Result<bool, String> {
    if endpoint_is_open(backend_address()) {
        return Ok(false);
    }

    let state = app.state::<BackendState>();
    let mut child_slot = state
        .child
        .lock()
        .map_err(|_| "Project Master backend state is unavailable.".to_string())?;
    if let Some(managed_backend) = child_slot.as_ref() {
        if !backend_can_be_replaced(managed_backend.started_at.elapsed()) {
            return Ok(false);
        }

        let stale_backend = child_slot
            .take()
            .expect("managed backend was present while replacing it");
        drop(child_slot);
        terminate_process_tree(stale_backend.child);
        child_slot = state
            .child
            .lock()
            .map_err(|_| "Project Master backend state is unavailable.".to_string())?;
        if child_slot.is_some() {
            return Ok(false);
        }
    }

    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("Unable to locate the Project Master data directory: {error}"))?;
    let paths = backend_paths(&data_dir);
    fs::create_dir_all(&paths.workspace_path)
        .map_err(|error| format!("Unable to create the Project Master data directory: {error}"))?;

    let sidecar = app
        .shell()
        .sidecar("project-master-backend")
        .map_err(|error| format!("Unable to locate the packaged Project Master backend: {error}"))?
        .current_dir(&paths.data_dir)
        .env("MASTER_CONFIG", &paths.config_path)
        .env("MASTER_DB_PATH", &paths.database_path)
        .env("MASTER_WORKSPACE_ROOT", &paths.workspace_path)
        .env("MASTER_LOG_PATH", &paths.log_path)
        .env("MASTER_API_PORT", BACKEND_PORT.to_string());

    let (mut events, child) = sidecar
        .spawn()
        .map_err(|error| format!("Unable to start the packaged Project Master backend: {error}"))?;
    let pid = child.pid();
    *child_slot = Some(ManagedBackend {
        child,
        started_at: Instant::now(),
    });
    drop(child_slot);

    let app_handle = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = events.recv().await {
            if matches!(event, CommandEvent::Terminated(_)) {
                break;
            }
        }

        // A forcibly terminated sidecar may close the event channel without
        // delivering CommandEvent::Terminated. Always clear the matching child
        // after the monitor ends so Retry can launch a replacement.
        let state = app_handle.state::<BackendState>();
        if let Ok(mut child_slot) = state.child.lock() {
            clear_child_if_matching(&mut child_slot, pid);
        };
    });

    Ok(true)
}

fn clear_child_if_matching(child_slot: &mut Option<ManagedBackend>, ended_pid: u32) {
    if backend_pid_matches(
        child_slot
            .as_ref()
            .map(|managed_backend| managed_backend.child.pid()),
        ended_pid,
    ) {
        child_slot.take();
    }
}

fn backend_pid_matches(current_pid: Option<u32>, ended_pid: u32) -> bool {
    current_pid == Some(ended_pid)
}

fn stop_backend(app: &AppHandle) {
    let state = app.state::<BackendState>();
    let child = state
        .child
        .lock()
        .ok()
        .and_then(|mut child_slot| child_slot.take());
    if let Some(managed_backend) = child {
        terminate_process_tree(managed_backend.child);
    }
}

#[cfg(target_os = "windows")]
fn terminate_process_tree(child: CommandChild) {
    use std::os::windows::process::CommandExt;

    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let pid = child.pid().to_string();
    let result = std::process::Command::new("taskkill")
        .args(["/PID", &pid, "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .status();
    if !matches!(result, Ok(status) if status.success()) {
        let _ = child.kill();
    }
}

#[cfg(not(target_os = "windows"))]
fn terminate_process_tree(child: CommandChild) {
    let _ = child.kill();
}

#[tauri::command]
async fn ensure_backend(app: AppHandle) -> Result<BackendStatus, String> {
    let started = start_backend(&app)?;
    let ready = tauri::async_runtime::spawn_blocking(|| {
        wait_for_endpoint(backend_address(), BACKEND_START_TIMEOUT)
    })
    .await
    .map_err(|error| format!("Backend readiness check failed: {error}"))?;

    if !ready {
        let data_dir = app
            .path()
            .app_data_dir()
            .map_err(|error| format!("Unable to locate backend logs: {error}"))?;
        return Err(format!(
            "Project Master backend did not become ready. Check {} for details.",
            backend_paths(&data_dir).log_path.display()
        ));
    }

    Ok(BackendStatus {
        ready: true,
        started,
    })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(BackendState::default())
        .plugin(tauri_plugin_http::init())
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![ensure_backend])
        .setup(|app| {
            if let Err(error) = start_backend(app.handle()) {
                eprintln!("{error}");
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building MASTER");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
            stop_backend(app_handle);
        }
    });
}

#[cfg(test)]
mod tests {
    use std::net::TcpListener;

    use super::*;

    #[test]
    fn backend_files_stay_inside_the_application_data_directory() {
        let base = PathBuf::from("test-data");
        let paths = backend_paths(&base);

        assert_eq!(paths.config_path, base.join("config.yaml"));
        assert_eq!(paths.database_path, base.join("master.db"));
        assert_eq!(paths.workspace_path, base.join("workspace"));
        assert_eq!(paths.log_path, base.join("backend.log"));
    }

    #[test]
    fn endpoint_probe_distinguishes_listening_and_closed_ports() {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).expect("bind test listener");
        let address = listener.local_addr().expect("read test listener address");

        assert!(endpoint_is_open(address));
        drop(listener);
        assert!(!endpoint_is_open(address));
    }

    #[test]
    fn backend_pid_matching_rejects_stale_exit_events() {
        assert!(backend_pid_matches(Some(100), 100));
        assert!(!backend_pid_matches(Some(100), 200));
        assert!(!backend_pid_matches(None, 100));
    }

    #[test]
    fn backend_replacement_waits_for_the_startup_grace_period() {
        assert!(!backend_can_be_replaced(Duration::from_secs(4)));
        assert!(backend_can_be_replaced(Duration::from_secs(5)));
        assert!(backend_can_be_replaced(Duration::from_secs(30)));
    }
}
