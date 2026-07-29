use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Seek, Write},
    net::{IpAddr, Ipv4Addr, SocketAddr, TcpStream},
    path::{Path, PathBuf},
    sync::Mutex,
    time::{Duration, Instant},
};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, RunEvent};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

const BACKEND_PORT: u16 = 8765;
const BACKEND_START_TIMEOUT: Duration = Duration::from_secs(20);
const BACKEND_START_GRACE: Duration = Duration::from_secs(5);
const BACKEND_CONNECT_TIMEOUT: Duration = Duration::from_millis(150);
const BACKEND_RESPONSE_TIMEOUT: Duration = Duration::from_secs(2);
const BACKEND_OWNER_SCHEMA_VERSION: u8 = 1;

struct BackendState {
    child: Mutex<Option<ManagedBackend>>,
    owner: Mutex<Option<BackendOwnerLease>>,
    session_token: String,
}

impl BackendState {
    fn new() -> Result<Self, String> {
        Ok(Self {
            child: Mutex::new(None),
            owner: Mutex::new(None),
            session_token: generate_session_token()?,
        })
    }
}

struct ManagedBackend {
    child: CommandChild,
    started_at: Instant,
}

#[derive(Debug)]
struct BackendOwnerLease {
    file: File,
    previous: Option<BackendOwnerMetadata>,
    current: BackendOwnerMetadata,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
struct BackendOwnerMetadata {
    schema_version: u8,
    gui_pid: u32,
    launcher_pid: Option<u32>,
    backend_pid: Option<u32>,
    session_token: String,
}

impl BackendOwnerMetadata {
    fn new(session_token: &str) -> Self {
        Self {
            schema_version: BACKEND_OWNER_SCHEMA_VERSION,
            gui_pid: std::process::id(),
            launcher_pid: None,
            backend_pid: None,
            session_token: session_token.to_owned(),
        }
    }

    fn is_valid(&self) -> bool {
        self.schema_version == BACKEND_OWNER_SCHEMA_VERSION
            && self.gui_pid != 0
            && self.launcher_pid != Some(0)
            && self.backend_pid != Some(0)
            && self.session_token.len() == 64
            && self
                .session_token
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit())
    }
}

#[derive(Debug, Clone)]
struct BackendPaths {
    data_dir: PathBuf,
    config_path: PathBuf,
    database_path: PathBuf,
    workspace_path: PathBuf,
    log_path: PathBuf,
    pid_path: PathBuf,
    owner_path: PathBuf,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct BackendStatus {
    ready: bool,
    started: bool,
    session_token: String,
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
        pid_path: data_dir.join("backend.pid"),
        owner_path: data_dir.join("backend-owner.json"),
    }
}

fn endpoint_is_open(address: SocketAddr) -> bool {
    TcpStream::connect_timeout(&address, BACKEND_CONNECT_TIMEOUT).is_ok()
}

fn generate_session_token() -> Result<String, String> {
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes)
        .map_err(|error| format!("Unable to generate a backend session token: {error}"))?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

fn read_backend_owner_metadata(file: &mut File) -> Option<BackendOwnerMetadata> {
    file.rewind().ok()?;
    let mut raw = String::new();
    file.read_to_string(&mut raw).ok()?;
    let metadata: BackendOwnerMetadata = serde_json::from_str(&raw).ok()?;
    metadata.is_valid().then_some(metadata)
}

fn write_backend_owner_metadata(
    file: &mut File,
    metadata: &BackendOwnerMetadata,
) -> Result<(), String> {
    let encoded = serde_json::to_vec(metadata)
        .map_err(|error| format!("Unable to encode backend ownership data: {error}"))?;
    file.set_len(0)
        .map_err(|error| format!("Unable to reset backend ownership data: {error}"))?;
    file.rewind()
        .map_err(|error| format!("Unable to seek backend ownership data: {error}"))?;
    file.write_all(&encoded)
        .map_err(|error| format!("Unable to write backend ownership data: {error}"))?;
    file.sync_data()
        .map_err(|error| format!("Unable to persist backend ownership data: {error}"))
}

fn try_acquire_backend_owner(
    path: &Path,
    session_token: &str,
) -> Result<Option<BackendOwnerLease>, String> {
    let mut options = OpenOptions::new();
    options.create(true).read(true).write(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }

    let mut file = options
        .open(path)
        .map_err(|error| format!("Unable to open backend ownership data: {error}"))?;
    match file.try_lock() {
        Ok(()) => {}
        Err(fs::TryLockError::WouldBlock) => return Ok(None),
        Err(fs::TryLockError::Error(error)) => {
            return Err(format!(
                "Unable to lock the backend ownership data: {error}"
            ));
        }
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        file.set_permissions(fs::Permissions::from_mode(0o600))
            .map_err(|error| format!("Unable to secure backend ownership data: {error}"))?;
    }

    let previous = read_backend_owner_metadata(&mut file);
    Ok(Some(BackendOwnerLease {
        file,
        previous,
        current: BackendOwnerMetadata::new(session_token),
    }))
}

fn ensure_backend_owner(
    state: &BackendState,
    owner_path: &Path,
) -> Result<Option<BackendOwnerMetadata>, String> {
    let mut owner_slot = state
        .owner
        .lock()
        .map_err(|_| "Project Master backend ownership state is unavailable.".to_string())?;
    if let Some(owner) = owner_slot.as_ref() {
        return Ok(owner.previous.clone());
    }

    let Some(owner) = try_acquire_backend_owner(owner_path, &state.session_token)? else {
        return Err(
            "Another live Project Master window owns the local backend. \
             Use that window, or close it before choosing Retry."
                .to_string(),
        );
    };
    let previous = owner.previous.clone();
    *owner_slot = Some(owner);
    Ok(previous)
}

fn initialize_backend_owner(state: &BackendState) -> Result<(), String> {
    let mut owner_slot = state
        .owner
        .lock()
        .map_err(|_| "Project Master backend ownership state is unavailable.".to_string())?;
    let owner = owner_slot
        .as_mut()
        .ok_or_else(|| "Project Master does not hold the backend ownership lease.".to_string())?;
    owner.current.launcher_pid = None;
    owner.current.backend_pid = None;
    let current = owner.current.clone();
    write_backend_owner_metadata(&mut owner.file, &current)?;
    owner.previous = None;
    Ok(())
}

fn record_backend_launcher(state: &BackendState, launcher_pid: u32) -> Result<(), String> {
    let mut owner_slot = state
        .owner
        .lock()
        .map_err(|_| "Project Master backend ownership state is unavailable.".to_string())?;
    let owner = owner_slot
        .as_mut()
        .ok_or_else(|| "Project Master does not hold the backend ownership lease.".to_string())?;
    owner.current.launcher_pid = Some(launcher_pid);
    let current = owner.current.clone();
    write_backend_owner_metadata(&mut owner.file, &current)
}

fn record_backend_pid(state: &BackendState, launcher_pid: u32, backend_pid: u32) {
    let Ok(mut owner_slot) = state.owner.lock() else {
        return;
    };
    let Some(owner) = owner_slot.as_mut() else {
        return;
    };
    if owner.current.launcher_pid != Some(launcher_pid) {
        return;
    }
    owner.current.backend_pid = Some(backend_pid);
    let current = owner.current.clone();
    if let Err(error) = write_backend_owner_metadata(&mut owner.file, &current) {
        eprintln!("{error}");
    }
}

fn active_backend_version(
    address: SocketAddr,
    session_token: &str,
) -> Result<Option<String>, String> {
    let mut stream = TcpStream::connect_timeout(&address, BACKEND_CONNECT_TIMEOUT)
        .map_err(|error| format!("Unable to inspect the local backend: {error}"))?;
    stream
        .set_read_timeout(Some(BACKEND_RESPONSE_TIMEOUT))
        .map_err(|error| format!("Unable to set the backend read timeout: {error}"))?;
    stream
        .set_write_timeout(Some(BACKEND_RESPONSE_TIMEOUT))
        .map_err(|error| format!("Unable to set the backend write timeout: {error}"))?;
    let request = format!(
        "GET /api/v1/ready HTTP/1.1\r\nHost: 127.0.0.1\r\n\
         X-Project-Master-Token: {session_token}\r\nConnection: close\r\n\r\n"
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|error| format!("Unable to query the local backend: {error}"))?;

    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|error| format!("Unable to read the local backend response: {error}"))?;
    let (_, body) = response
        .split_once("\r\n\r\n")
        .ok_or_else(|| "The local backend returned an invalid health response.".to_string())?;
    let payload: serde_json::Value = serde_json::from_str(body)
        .map_err(|error| format!("The local backend returned invalid health data: {error}"))?;
    Ok(payload
        .get("version")
        .and_then(serde_json::Value::as_str)
        .map(str::to_owned))
}

fn backend_version_matches_current(version: Option<&str>) -> bool {
    version == Some(env!("CARGO_PKG_VERSION"))
}

fn wait_for_backend_ready(address: SocketAddr, session_token: &str, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    loop {
        if active_backend_version(address, session_token)
            .ok()
            .flatten()
            .as_deref()
            .is_some_and(|version| backend_version_matches_current(Some(version)))
        {
            return true;
        }
        if Instant::now() >= deadline {
            return false;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn wait_for_endpoint_closed(address: SocketAddr, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    loop {
        if !endpoint_is_open(address) {
            return true;
        }
        if Instant::now() >= deadline {
            return false;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn backend_can_be_replaced(uptime: Duration) -> bool {
    uptime >= BACKEND_START_GRACE
}

fn start_backend(app: &AppHandle) -> Result<bool, String> {
    let state = app.state::<BackendState>();
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| format!("Unable to locate the Project Master data directory: {error}"))?;
    let paths = backend_paths(&data_dir);
    fs::create_dir_all(&paths.data_dir)
        .map_err(|error| format!("Unable to create the Project Master data directory: {error}"))?;
    let previous_owner = ensure_backend_owner(&state, &paths.owner_path)?;

    let mut child_slot = state
        .child
        .lock()
        .map_err(|_| "Project Master backend state is unavailable.".to_string())?;
    if let Some(managed_backend) = child_slot.as_ref() {
        let uptime = managed_backend.started_at.elapsed();
        if !backend_can_be_replaced(uptime) || uptime < BACKEND_START_TIMEOUT {
            return Ok(false);
        }
        if endpoint_is_open(backend_address()) {
            let version = active_backend_version(backend_address(), &state.session_token);
            if matches!(
                version,
                Ok(Some(ref current))
                    if backend_version_matches_current(Some(current.as_str()))
            ) {
                return Ok(false);
            }
        }

        let stale_backend = child_slot
            .take()
            .expect("managed backend was present while replacing it");
        drop(child_slot);
        terminate_process_tree(stale_backend.child);
        if !wait_for_endpoint_closed(backend_address(), BACKEND_RESPONSE_TIMEOUT) {
            return Err(
                "The previous Project Master backend did not release its loopback port."
                    .to_string(),
            );
        }
        child_slot = state
            .child
            .lock()
            .map_err(|_| "Project Master backend state is unavailable.".to_string())?;
        if child_slot.is_some() {
            return Ok(false);
        }
    }

    if endpoint_is_open(backend_address()) {
        let version = active_backend_version(backend_address(), &state.session_token)
            .ok()
            .flatten();
        if backend_version_matches_current(version.as_deref()) {
            return Ok(false);
        }
        // A rejected current token does not establish that the other backend is
        // orphaned. Reclaim only after acquiring the old GUI's released owner
        // lock and authenticating the backend with that GUI's recorded token.
        if !previous_owner
            .as_ref()
            .is_some_and(|owner| reclaim_orphaned_backend(&paths.pid_path, owner))
        {
            let description = version.unwrap_or_else(|| "an unknown version".to_string());
            return Err(format!(
                "Another Project Master backend ({description}) is using \
                 127.0.0.1:{BACKEND_PORT}. Its ownership could not be verified, \
                 so it was not stopped. Close the other Project Master window or \
                 backend, then choose Retry."
            ));
        }
    }
    initialize_backend_owner(&state)?;
    match fs::remove_file(&paths.pid_path) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => {
            return Err(format!(
                "Unable to clear stale backend process data: {error}"
            ));
        }
    }
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
        .env("MASTER_ALLOW_FILE_WRITES", "true")
        .env("MASTER_TERMINAL_ENABLED", "true")
        .env("MASTER_TERMINAL_NETWORK_ENABLED", "false")
        .env("MASTER_LOG_PATH", &paths.log_path)
        .env("MASTER_PID_PATH", &paths.pid_path)
        .env("MASTER_API_PORT", BACKEND_PORT.to_string())
        .env("MASTER_SESSION_TOKEN", &state.session_token);

    let (mut events, child) = sidecar
        .spawn()
        .map_err(|error| format!("Unable to start the packaged Project Master backend: {error}"))?;
    let pid = child.pid();
    if let Err(error) = record_backend_launcher(&state, pid) {
        terminate_process_tree(child);
        return Err(error);
    }
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

    let pid_app_handle = app.clone();
    let pid_path = paths.pid_path.clone();
    tauri::async_runtime::spawn_blocking(move || {
        if let Some(backend_pid) = wait_for_backend_pid(&pid_path, pid, BACKEND_START_TIMEOUT) {
            let state = pid_app_handle.state::<BackendState>();
            record_backend_pid(&state, pid, backend_pid);
        }
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
    // The PyInstaller sidecar forks the real server process. Killing only the
    // launcher orphans that server and leaves it holding the backend port, so
    // collect the descendants first, stop the launcher, then terminate them.
    let descendants = collect_descendant_pids(child.pid());
    let _ = child.kill();
    for pid in descendants {
        let _ = std::process::Command::new("kill")
            .args(["-TERM", &pid.to_string()])
            .status();
    }
}

/// True when the pid belongs to a Project Master backend rather than whatever
/// unrelated process happened to inherit that pid after the original exited.
fn is_project_master_backend_image(name: &str) -> bool {
    let lowercase = name.to_ascii_lowercase();
    let existing_image = lowercase.strip_suffix(" (deleted)").unwrap_or(&lowercase);
    let stem = existing_image
        .strip_suffix(".exe")
        .unwrap_or(existing_image);
    stem == "project-master-backend" || stem.starts_with("project-master-backend-")
}

#[cfg(target_os = "linux")]
fn pid_is_project_master_backend(pid: u32) -> bool {
    let executable = PathBuf::from(format!("/proc/{pid}/exe"));
    let Ok(target) = fs::read_link(executable) else {
        return false;
    };
    target
        .file_name()
        .and_then(|name| name.to_str())
        .is_some_and(is_project_master_backend_image)
}

#[cfg(all(unix, not(target_os = "linux")))]
fn pid_is_project_master_backend(pid: u32) -> bool {
    let Ok(output) = std::process::Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", "comm="])
        .output()
    else {
        return false;
    };
    Path::new(String::from_utf8_lossy(&output.stdout).trim())
        .file_name()
        .and_then(|name| name.to_str())
        .is_some_and(is_project_master_backend_image)
}

#[cfg(target_os = "windows")]
fn pid_is_project_master_backend(pid: u32) -> bool {
    let filter = format!("PID eq {pid}");
    let Ok(output) = std::process::Command::new("tasklist")
        .args(["/FI", &filter, "/FO", "CSV", "/NH"])
        .output()
    else {
        return false;
    };
    let first_field = String::from_utf8_lossy(&output.stdout)
        .split(',')
        .next()
        .unwrap_or_default()
        .trim()
        .trim_matches('"')
        .to_owned();
    is_project_master_backend_image(&first_field)
}

fn read_backend_pid(pid_path: &Path) -> Option<u32> {
    let raw = fs::read_to_string(pid_path).ok()?;
    let pid = raw.trim().parse::<u32>().ok()?;
    (pid != 0).then_some(pid)
}

fn wait_for_backend_pid(pid_path: &Path, launcher_pid: u32, timeout: Duration) -> Option<u32> {
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(pid) = read_backend_pid(pid_path) {
            if pid_is_project_master_backend(pid)
                && backend_pid_belongs_to_launch(pid, launcher_pid)
            {
                return Some(pid);
            }
        }
        if Instant::now() >= deadline {
            return None;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

#[cfg(not(target_os = "windows"))]
fn backend_pid_belongs_to_launch(backend_pid: u32, launcher_pid: u32) -> bool {
    backend_pid == launcher_pid || collect_descendant_pids(launcher_pid).contains(&backend_pid)
}

#[cfg(target_os = "windows")]
fn backend_pid_belongs_to_launch(_backend_pid: u32, _launcher_pid: u32) -> bool {
    // The pid file is removed immediately before spawning and its path is
    // supplied only to that sidecar. The process image is validated separately.
    true
}

fn stale_backend_matches_owner(
    owner: &BackendOwnerMetadata,
    authenticated_version: Option<&str>,
    observed_pid: u32,
) -> bool {
    owner.is_valid()
        && authenticated_version.is_some()
        && owner
            .backend_pid
            .is_none_or(|recorded_pid| recorded_pid == observed_pid)
}

/// Stop a backend left holding the loopback port by an earlier run.
///
/// The caller must already hold the previous GUI's released owner lock. The
/// recorded token proves that the process answering on the port belongs to that
/// dead GUI; the pid and image checks prevent killing an unrelated reused pid.
fn reclaim_orphaned_backend(pid_path: &Path, owner: &BackendOwnerMetadata) -> bool {
    let Some(pid) = read_backend_pid(pid_path) else {
        return false;
    };
    let authenticated_version = active_backend_version(backend_address(), &owner.session_token)
        .ok()
        .flatten();
    if !stale_backend_matches_owner(owner, authenticated_version.as_deref(), pid) {
        return false;
    }
    if !pid_is_project_master_backend(pid) {
        return false;
    }
    for force in [false, true] {
        terminate_orphaned_backend(pid, force);
        if wait_for_endpoint_closed(backend_address(), BACKEND_START_GRACE) {
            let _ = fs::remove_file(pid_path);
            return true;
        }
    }
    false
}

#[cfg(not(target_os = "windows"))]
fn terminate_orphaned_backend(pid: u32, force: bool) {
    let signal = if force { "-KILL" } else { "-TERM" };
    let mut targets = collect_descendant_pids(pid);
    targets.push(pid);
    for target in targets {
        let _ = std::process::Command::new("kill")
            .args([signal, &target.to_string()])
            .status();
    }
}

#[cfg(target_os = "windows")]
fn terminate_orphaned_backend(pid: u32, force: bool) {
    use std::os::windows::process::CommandExt;

    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let pid = pid.to_string();
    let mut command = std::process::Command::new("taskkill");
    command.args(["/PID", &pid, "/T"]);
    if force {
        command.arg("/F");
    }
    let _ = command.creation_flags(CREATE_NO_WINDOW).status();
}

#[cfg(not(target_os = "windows"))]
fn collect_descendant_pids(root: u32) -> Vec<u32> {
    let Ok(output) = std::process::Command::new("ps")
        .args(["-eo", "pid=,ppid="])
        .output()
    else {
        return Vec::new();
    };
    descendants_of(
        &parse_process_table(&String::from_utf8_lossy(&output.stdout)),
        root,
    )
}

#[cfg(any(not(target_os = "windows"), test))]
fn parse_process_table(table: &str) -> Vec<(u32, u32)> {
    table
        .lines()
        .filter_map(|line| {
            let mut fields = line.split_whitespace();
            let pid = fields.next()?.parse().ok()?;
            let ppid = fields.next()?.parse().ok()?;
            Some((pid, ppid))
        })
        .collect()
}

#[cfg(any(not(target_os = "windows"), test))]
fn descendants_of(edges: &[(u32, u32)], root: u32) -> Vec<u32> {
    let mut result: Vec<u32> = Vec::new();
    let mut frontier = vec![root];
    while let Some(parent) = frontier.pop() {
        for &(pid, ppid) in edges {
            if ppid == parent && pid != root && !result.contains(&pid) {
                result.push(pid);
                frontier.push(pid);
            }
        }
    }
    result
}

#[tauri::command]
async fn ensure_backend(app: AppHandle) -> Result<BackendStatus, String> {
    let started = start_backend(&app)?;
    let session_token = app.state::<BackendState>().session_token.clone();
    let readiness_token = session_token.clone();
    let ready = tauri::async_runtime::spawn_blocking(move || {
        wait_for_backend_ready(backend_address(), &readiness_token, BACKEND_START_TIMEOUT)
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
        session_token,
    })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // WebKitGTK's DMA-BUF renderer produces a blank window on hybrid Intel/NVIDIA
    // Wayland systems ("Failed to create GBM buffer"). Disable it unless the user
    // has explicitly configured the variable themselves.
    #[cfg(target_os = "linux")]
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }

    // The AppImage bundler can stage wrong-architecture GStreamer plugins (32-bit
    // i386 alongside a 64-bit libgstreamer), and its AppRun points the plugin path
    // at that directory. WebKit then cannot build any media pipeline — "appsink
    // not found" — and the web process dies the first time audio plays, taking the
    // window with it. Append the host's plugin directories so a bundled path that
    // is empty or unusable still resolves. Appending (rather than replacing) keeps
    // any genuinely bundled plugins ahead of the system ones.
    #[cfg(target_os = "linux")]
    {
        let existing = std::env::var("GST_PLUGIN_SYSTEM_PATH_1_0").unwrap_or_default();
        let mut entries: Vec<String> = existing
            .split(':')
            .filter(|entry| !entry.is_empty())
            .map(str::to_owned)
            .collect();
        let mut appended = false;
        for candidate in ["/usr/lib64/gstreamer-1.0", "/usr/lib/gstreamer-1.0"] {
            if Path::new(candidate).is_dir() && !entries.iter().any(|entry| entry == candidate) {
                entries.push(candidate.to_owned());
                appended = true;
            }
        }
        if appended {
            std::env::set_var("GST_PLUGIN_SYSTEM_PATH_1_0", entries.join(":"));
        }
    }

    let builder = tauri::Builder::default().plugin(tauri_plugin_process::init());
    #[cfg(desktop)]
    let builder = builder.plugin(tauri_plugin_updater::Builder::new().build());

    let backend_state =
        BackendState::new().expect("unable to create the Project Master backend session");
    let app = builder
        .manage(backend_state)
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
        assert_eq!(paths.pid_path, base.join("backend.pid"));
        assert_eq!(paths.owner_path, base.join("backend-owner.json"));
    }

    #[test]
    fn reclaim_refuses_without_a_readable_pid_file() {
        // No pid file means no handle to an orphan, so startup must not
        // silently believe it freed the port.
        let missing = PathBuf::from("test-data/definitely-absent.pid");
        assert!(!reclaim_orphaned_backend(
            &missing,
            &BackendOwnerMetadata::new(&"1".repeat(64))
        ));
    }

    #[test]
    fn reclaim_refuses_a_malformed_pid_file() {
        let dir = std::env::temp_dir().join("pm-pid-test");
        let _ = fs::create_dir_all(&dir);
        let path = dir.join("backend.pid");
        fs::write(&path, "not-a-pid").unwrap();
        assert!(!reclaim_orphaned_backend(
            &path,
            &BackendOwnerMetadata::new(&"1".repeat(64))
        ));
        let _ = fs::remove_file(&path);
    }

    #[test]
    fn owner_lock_excludes_a_second_live_gui_and_recovers_after_drop() {
        let path = std::env::temp_dir().join(format!(
            "pm-owner-lock-{}.json",
            generate_session_token().unwrap()
        ));
        let first = try_acquire_backend_owner(&path, &"1".repeat(64))
            .unwrap()
            .expect("first owner");
        assert!(try_acquire_backend_owner(&path, &"2".repeat(64))
            .unwrap()
            .is_none());

        drop(first);
        assert!(try_acquire_backend_owner(&path, &"2".repeat(64))
            .unwrap()
            .is_some());
        let _ = fs::remove_file(path);
    }

    #[test]
    fn stale_backend_requires_the_recorded_token_and_pid_identity() {
        let mut owner = BackendOwnerMetadata::new(&"a".repeat(64));
        owner.backend_pid = Some(42);

        assert!(stale_backend_matches_owner(&owner, Some("0.3.0"), 42));
        assert!(!stale_backend_matches_owner(&owner, None, 42));
        assert!(!stale_backend_matches_owner(&owner, Some("0.3.0"), 99));

        owner.session_token = "not-a-token".to_string();
        assert!(!stale_backend_matches_owner(&owner, Some("0.3.0"), 42));
    }

    #[test]
    fn owner_metadata_round_trips_through_the_locked_file() {
        let path = std::env::temp_dir().join(format!(
            "pm-owner-data-{}.json",
            generate_session_token().unwrap()
        ));
        let token = "b".repeat(64);
        let mut lease = try_acquire_backend_owner(&path, &token)
            .unwrap()
            .expect("owner");
        lease.current.launcher_pid = Some(100);
        lease.current.backend_pid = Some(101);
        let expected = lease.current.clone();
        write_backend_owner_metadata(&mut lease.file, &expected).unwrap();
        assert_eq!(read_backend_owner_metadata(&mut lease.file), Some(expected));
        drop(lease);
        let _ = fs::remove_file(path);
    }

    #[test]
    fn our_own_pid_is_not_mistaken_for_a_backend() {
        // Guards against killing an unrelated process that inherited the pid.
        assert!(!pid_is_project_master_backend(std::process::id()));
    }

    #[test]
    fn backend_image_matching_accepts_packaged_and_target_suffixed_sidecars() {
        assert!(is_project_master_backend_image("project-master-backend"));
        assert!(is_project_master_backend_image(
            "project-master-backend-x86_64-unknown-linux-gnu"
        ));
        assert!(is_project_master_backend_image(
            "PROJECT-MASTER-BACKEND.EXE"
        ));
        assert!(is_project_master_backend_image(
            "project-master-backend (deleted)"
        ));
        assert!(!is_project_master_backend_image("master"));
        assert!(!is_project_master_backend_image(
            "not-project-master-backend"
        ));
    }

    #[test]
    fn process_table_parsing_skips_malformed_lines() {
        let table = "  10   1\n 20  10\nbad line\n 30  20\n";
        assert_eq!(
            parse_process_table(table),
            vec![(10, 1), (20, 10), (30, 20)]
        );
    }

    #[test]
    fn descendant_walk_finds_the_whole_subtree_and_nothing_else() {
        let edges = [(10, 1), (20, 10), (30, 20), (40, 10), (99, 2)];
        let mut found = descendants_of(&edges, 10);
        found.sort_unstable();
        assert_eq!(found, vec![20, 30, 40]);
        assert!(descendants_of(&edges, 99).is_empty());
    }

    #[test]
    fn descendant_walk_survives_pid_cycles() {
        let edges = [(10, 20), (20, 10)];
        let found = descendants_of(&edges, 10);
        assert_eq!(found, vec![20]);
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

    #[test]
    fn backend_version_matching_rejects_stale_backends() {
        assert!(backend_version_matches_current(Some(env!(
            "CARGO_PKG_VERSION"
        ))));
        assert!(!backend_version_matches_current(Some("0.1.1")));
        assert!(!backend_version_matches_current(None));
    }
}
