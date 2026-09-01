use serde::Serialize;
use std::collections::{HashMap, HashSet};
use std::env;
use std::ffi::OsStr;
use std::io;
use std::os::unix::process::CommandExt;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const DEFAULT_DEADLINE_MS: u64 = 30_000;
const DEFAULT_INTERVAL_MS: u64 = 25;

#[derive(Debug)]
struct Options {
    deadline: Duration,
    interval: Duration,
    exclude_root: bool,
    command: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct ProcessRow {
    pid: u32,
    ppid: u32,
    rss_kib: u64,
}

#[derive(Serialize)]
struct Receipt {
    schema_version: u32,
    platform: Platform,
    command: SanitizedCommand,
    measurement: Measurement,
    outcome: Outcome,
}

#[derive(Serialize)]
struct Platform {
    os: &'static str,
    architecture: &'static str,
}

#[derive(Serialize)]
struct SanitizedCommand {
    executable_name: String,
    argument_count: usize,
    arguments_redacted: bool,
}

#[derive(Serialize)]
struct Measurement {
    scope: &'static str,
    semantic: &'static str,
    source: &'static str,
    interval_ms: u64,
    sample_count: u64,
    peak_tree_resident_bytes: u64,
    peak_process_count: usize,
    observed_unique_process_count: usize,
    limitations: Vec<&'static str>,
}

#[derive(Serialize)]
struct Outcome {
    timed_out: bool,
    wall_time_ms: u128,
    exit: Exit,
    cleanup: Cleanup,
}

#[derive(Serialize)]
struct Exit {
    kind: &'static str,
    code: Option<i32>,
    signal: Option<i32>,
}

#[derive(Serialize)]
struct Cleanup {
    deadline_process_group_termination_requested: bool,
    post_exit_process_group_termination_requested: bool,
    root_reaped: bool,
}

fn usage() -> &'static str {
    "usage: process-tree-sampler [--deadline-ms N] [--interval-ms N] [--exclude-root] -- COMMAND [ARG ...]"
}

fn parse_options<I>(args: I) -> Result<Options, String>
where
    I: IntoIterator<Item = String>,
{
    let mut args = args.into_iter();
    let mut deadline_ms = DEFAULT_DEADLINE_MS;
    let mut interval_ms = DEFAULT_INTERVAL_MS;
    let mut exclude_root = false;
    let mut command = Vec::new();

    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--" => {
                command.extend(args);
                break;
            }
            "--deadline-ms" => deadline_ms = parse_positive(&args.next(), "--deadline-ms")?,
            "--interval-ms" => interval_ms = parse_positive(&args.next(), "--interval-ms")?,
            "--exclude-root" => exclude_root = true,
            "-h" | "--help" => return Err(usage().to_owned()),
            _ => return Err(format!("unknown sampler argument: {arg}\n{}", usage())),
        }
    }

    if command.is_empty() {
        return Err(format!("missing command\n{}", usage()));
    }
    Ok(Options {
        deadline: Duration::from_millis(deadline_ms),
        interval: Duration::from_millis(interval_ms),
        exclude_root,
        command,
    })
}

fn parse_positive(value: &Option<String>, flag: &str) -> Result<u64, String> {
    let value = value
        .as_ref()
        .ok_or_else(|| format!("missing value for {flag}"))?;
    let parsed = value
        .parse::<u64>()
        .map_err(|_| format!("invalid value for {flag}: expected a positive integer"))?;
    if parsed == 0 {
        return Err(format!(
            "invalid value for {flag}: expected a positive integer"
        ));
    }
    Ok(parsed)
}

fn process_rows() -> io::Result<Vec<ProcessRow>> {
    let output = Command::new("ps")
        .args(["-axo", "pid=,ppid=,rss="])
        .output()?;
    if !output.status.success() {
        return Err(io::Error::other(format!(
            "ps failed with status {}",
            output.status
        )));
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    Ok(parse_ps_rows(&stdout))
}

fn parse_ps_rows(text: &str) -> Vec<ProcessRow> {
    text.lines()
        .filter_map(|line| {
            let mut fields = line.split_whitespace();
            let pid = fields.next()?.parse().ok()?;
            let ppid = fields.next()?.parse().ok()?;
            let rss_kib = fields.next()?.parse().ok()?;
            Some(ProcessRow { pid, ppid, rss_kib })
        })
        .collect()
}

fn attributable_tree(root: u32, rows: &[ProcessRow]) -> Vec<ProcessRow> {
    let by_parent = rows
        .iter()
        .fold(HashMap::<u32, Vec<ProcessRow>>::new(), |mut map, row| {
            map.entry(row.ppid).or_default().push(*row);
            map
        });
    let by_pid: HashMap<u32, ProcessRow> = rows.iter().map(|row| (row.pid, *row)).collect();
    let mut result = Vec::new();
    let mut pending = vec![root];
    let mut visited = HashSet::new();
    while let Some(pid) = pending.pop() {
        if !visited.insert(pid) {
            continue;
        }
        if let Some(row) = by_pid.get(&pid) {
            result.push(*row);
        }
        if let Some(children) = by_parent.get(&pid) {
            pending.extend(children.iter().map(|row| row.pid));
        }
    }
    result
}

fn spawn_in_process_group(command: &[String]) -> io::Result<Child> {
    let mut child = Command::new(&command[0]);
    child.args(&command[1..]);
    child
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    // SAFETY: setpgid is async-signal-safe and this closure performs no allocation.
    unsafe {
        child.pre_exec(|| {
            if libc::setpgid(0, 0) == -1 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        });
    }
    child.spawn()
}

fn terminate_process_group(root: u32) -> io::Result<()> {
    // A negative PID targets the process group created with PGID == root PID.
    let result = unsafe { libc::kill(-(root as i32), libc::SIGKILL) };
    if result == 0 {
        Ok(())
    } else {
        let error = io::Error::last_os_error();
        if error.raw_os_error() == Some(libc::ESRCH) {
            Ok(())
        } else {
            Err(error)
        }
    }
}

fn exit_description(status: ExitStatus) -> Exit {
    use std::os::unix::process::ExitStatusExt;
    if let Some(code) = status.code() {
        Exit {
            kind: "code",
            code: Some(code),
            signal: None,
        }
    } else {
        Exit {
            kind: "signal",
            code: None,
            signal: status.signal(),
        }
    }
}

fn sanitized_executable(command: &str) -> String {
    std::path::Path::new(command)
        .file_name()
        .and_then(OsStr::to_str)
        .unwrap_or("<non-utf8-executable>")
        .to_owned()
}

fn run(options: Options) -> Result<Receipt, Box<dyn std::error::Error>> {
    let command_meta = SanitizedCommand {
        executable_name: sanitized_executable(&options.command[0]),
        argument_count: options.command.len() - 1,
        arguments_redacted: true,
    };
    let mut child = spawn_in_process_group(&options.command)?;
    let root = child.id();
    let started = Instant::now();
    let mut samples = 0_u64;
    let mut peak_bytes = 0_u64;
    let mut peak_processes = 0_usize;
    let mut unique_pids = HashSet::new();
    let mut timed_out = false;
    let mut post_exit_cleanup = false;
    let status;

    loop {
        let rows = match process_rows() {
            Ok(rows) => rows,
            Err(error) => {
                let _ = terminate_process_group(root);
                let _ = child.wait();
                return Err(error.into());
            }
        };
        let mut tree = attributable_tree(root, &rows);
        if options.exclude_root {
            tree.retain(|row| row.pid != root);
        }
        samples += 1;
        let total_kib = tree.iter().map(|row| row.rss_kib).sum::<u64>();
        peak_bytes = peak_bytes.max(total_kib.saturating_mul(1024));
        peak_processes = peak_processes.max(tree.len());
        unique_pids.extend(tree.iter().map(|row| row.pid));

        if let Some(observed) = child.try_wait()? {
            status = observed;
            // The root has exited normally, but children can remain in the
            // dedicated process group after reparenting. Preserve the root's
            // real exit result and clean up those remaining group members.
            post_exit_cleanup = true;
            let _ = terminate_process_group(root);
            break;
        }
        if started.elapsed() >= options.deadline {
            timed_out = true;
            terminate_process_group(root)?;
            status = child.wait()?;
            break;
        }
        thread::sleep(options.interval);
    }

    Ok(Receipt {
        schema_version: 1,
        platform: Platform {
            os: env::consts::OS,
            architecture: env::consts::ARCH,
        },
        command: command_meta,
        measurement: Measurement {
            scope: if options.exclude_root {
                "recursive descendants only (root excluded)"
            } else {
                "root and recursive descendants"
            },
            semantic: "sampled sum of resident set size for processes in the selected scope",
            source: "ps -axo pid=,ppid=,rss= (RSS reported in KiB)",
            interval_ms: options.interval.as_millis() as u64,
            sample_count: samples,
            peak_tree_resident_bytes: peak_bytes,
            peak_process_count: peak_processes,
            observed_unique_process_count: unique_pids.len(),
            limitations: vec![
                "sampled RSS is not private memory, proportional set size, or live heap",
                "short-lived processes may start and exit between samples",
                "shared resident pages are summed once per process and can therefore be double-counted",
                "descendants that reparent before a snapshot may not remain attributable",
            ],
        },
        outcome: Outcome {
            timed_out,
            wall_time_ms: started.elapsed().as_millis(),
            exit: exit_description(status),
            cleanup: Cleanup {
                deadline_process_group_termination_requested: timed_out,
                post_exit_process_group_termination_requested: post_exit_cleanup,
                root_reaped: true,
            },
        },
    })
}

fn main() {
    let options = match parse_options(env::args().skip(1)) {
        Ok(options) => options,
        Err(message) => {
            eprintln!("{message}");
            std::process::exit(2);
        }
    };
    match run(options) {
        Ok(receipt) => {
            let generated_at = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs();
            let envelope = serde_json::json!({
                "generated_at_unix_seconds": generated_at,
                "receipt": receipt,
            });
            println!("{}", serde_json::to_string_pretty(&envelope).unwrap());
        }
        Err(error) => {
            eprintln!("sampling failed: {error}");
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_ps_and_finds_recursive_tree() {
        let rows = parse_ps_rows(" 10 1 100\n 11 10 25\n12 11 5\n99 1 500\ninvalid\n");
        assert_eq!(rows.len(), 4);
        let tree = attributable_tree(10, &rows);
        let pids: HashSet<u32> = tree.into_iter().map(|row| row.pid).collect();
        assert_eq!(pids, HashSet::from([10, 11, 12]));
    }

    #[test]
    fn options_require_separator_and_command() {
        assert!(parse_options(["--".into()]).is_err());
        assert!(parse_options(["sleep".into(), "1".into()]).is_err());
    }

    #[test]
    fn sanitizes_executable_path() {
        assert_eq!(sanitized_executable("/private/example/tool"), "tool");
    }
}
