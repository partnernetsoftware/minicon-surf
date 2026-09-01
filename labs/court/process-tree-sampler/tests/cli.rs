use serde_json::Value;
use std::process::Command;

fn sampler() -> Command {
    Command::new(env!("CARGO_BIN_EXE_process-tree-sampler"))
}

#[test]
fn observes_root_and_child_without_leaking_arguments() {
    let output = sampler()
        .args([
            "--deadline-ms",
            "3000",
            "--interval-ms",
            "10",
            "--",
            "/bin/sh",
            "-c",
            "echo candidate-output; echo candidate-error >&2; sleep 0.25 & wait",
            "private-marker-that-must-not-appear",
        ])
        .output()
        .unwrap();
    assert!(output.status.success(), "{:?}", output);
    let text = String::from_utf8(output.stdout).unwrap();
    assert!(!text.contains("private-marker"));
    assert!(!text.contains("candidate-output"));
    assert!(!text.contains("candidate-error"));
    assert!(output.stderr.is_empty());
    let json: Value = serde_json::from_str(&text).unwrap();
    assert!(
        json["receipt"]["measurement"]["peak_process_count"]
            .as_u64()
            .unwrap()
            >= 2
    );
    assert_eq!(json["receipt"]["outcome"]["timed_out"], false);
}

#[test]
fn deadline_terminates_and_reaps_process_group() {
    let output = sampler()
        .args([
            "--deadline-ms",
            "100",
            "--interval-ms",
            "10",
            "--",
            "/bin/sh",
            "-c",
            "sleep 10 & wait",
        ])
        .output()
        .unwrap();
    assert!(output.status.success(), "{:?}", output);
    let json: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(json["receipt"]["outcome"]["timed_out"], true);
    assert_eq!(
        json["receipt"]["outcome"]["cleanup"]["deadline_process_group_termination_requested"],
        true
    );
    assert_eq!(
        json["receipt"]["outcome"]["cleanup"]["post_exit_process_group_termination_requested"],
        false
    );
    assert_eq!(json["receipt"]["outcome"]["cleanup"]["root_reaped"], true);
}

#[test]
fn normal_root_exit_cleans_up_background_child() {
    use std::fs;
    use std::thread;
    use std::time::{Duration, SystemTime, UNIX_EPOCH};

    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let pid_file = std::env::temp_dir().join(format!(
        "process-tree-sampler-child-{}-{nonce}.pid",
        std::process::id()
    ));
    let script = format!("sleep 10 & echo $! > '{}'; exit 0", pid_file.display());
    let output = sampler()
        .args([
            "--deadline-ms",
            "3000",
            "--interval-ms",
            "10",
            "--",
            "/bin/sh",
            "-c",
            &script,
        ])
        .output()
        .unwrap();
    assert!(output.status.success(), "{:?}", output);
    let json: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(json["receipt"]["outcome"]["timed_out"], false);
    assert_eq!(json["receipt"]["outcome"]["exit"]["code"], 0);
    assert_eq!(
        json["receipt"]["outcome"]["cleanup"]["deadline_process_group_termination_requested"],
        false
    );
    assert_eq!(
        json["receipt"]["outcome"]["cleanup"]["post_exit_process_group_termination_requested"],
        true
    );

    let child_pid: i32 = fs::read_to_string(&pid_file)
        .expect("wrapper must record its child PID")
        .trim()
        .parse()
        .unwrap();
    let mut exists = true;
    for _ in 0..50 {
        // Signal 0 checks existence without delivering a signal.
        let result = unsafe { libc::kill(child_pid, 0) };
        if result == -1 && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH) {
            exists = false;
            break;
        }
        thread::sleep(Duration::from_millis(10));
    }
    let _ = fs::remove_file(pid_file);
    assert!(!exists, "background child remained after sampler returned");
}

#[test]
fn exclude_root_counts_descendants_but_not_wrapper() {
    let output = sampler()
        .args([
            "--deadline-ms",
            "3000",
            "--interval-ms",
            "10",
            "--exclude-root",
            "--",
            "/bin/sh",
            "-c",
            "sleep 0.35 & wait",
        ])
        .output()
        .unwrap();
    assert!(output.status.success(), "{:?}", output);
    let json: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(
        json["receipt"]["measurement"]["scope"],
        "recursive descendants only (root excluded)"
    );
    assert_eq!(json["receipt"]["measurement"]["peak_process_count"], 1);
    assert_eq!(
        json["receipt"]["measurement"]["observed_unique_process_count"],
        1
    );
    assert!(
        json["receipt"]["measurement"]["peak_tree_resident_bytes"]
            .as_u64()
            .unwrap()
            > 0
    );
}
