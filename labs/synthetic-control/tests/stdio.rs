use serde_json::{Value, json};
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

struct Host {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
    next_request: u64,
}

impl Host {
    fn start() -> Self {
        let mut child = Command::new(env!("CARGO_BIN_EXE_minicon-surf-synthetic-control"))
            .args(["serve", "--stdio"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let input = child.stdin.take().unwrap();
        let output = BufReader::new(child.stdout.take().unwrap());
        Self {
            child,
            input,
            output,
            next_request: 0,
        }
    }

    fn call(&mut self, operation: &str, arguments: Value) -> Value {
        self.next_request += 1;
        let request_id = format!("req_journey_{}", self.next_request);
        let request = json!({
            "protocol":"minicon-surf.control",
            "version":"0.0.1",
            "request_id":request_id,
            "deadline_ms":100,
            "operation":operation,
            "arguments":arguments,
        });
        serde_json::to_writer(&mut self.input, &request).unwrap();
        self.input.write_all(b"\n").unwrap();
        self.input.flush().unwrap();
        let mut line = String::new();
        self.output.read_line(&mut line).unwrap();
        assert!(!line.is_empty(), "host closed stdout before responding");
        let response: Value = serde_json::from_str(&line).unwrap();
        assert_eq!(response["request_id"], request_id);
        response
    }

    fn finish(mut self) {
        drop(self.input);
        assert!(self.child.wait().unwrap().success());
    }
}

#[test]
fn one_stdio_host_preserves_target_identity_revision_and_memory_owners() {
    let mut host = Host::start();
    let profile = host.call("profile.create", json!({"persistence":"ephemeral"}));
    assert_eq!(profile["ok"], true);
    let profile_id = profile["result"]["profile"].as_str().unwrap();

    let session = host.call("session.open", json!({"profile":profile_id}));
    let session_id = session["result"]["session"].as_str().unwrap();
    let target = host.call("target.open", json!({"session":session_id}));
    let target_id = target["result"]["target"].as_str().unwrap();

    let snapshot = host.call(
        "target.snapshot",
        json!({"target":target_id,"format":"semantic","max_bytes":65536,"max_nodes":10}),
    );
    assert_eq!(snapshot["result"]["target"], target_id);
    assert_eq!(snapshot["result"]["revision"], 0);
    let reference = snapshot["result"]["nodes"][1]["reference"].clone();

    let action = host.call(
        "target.act",
        json!({"target":target_id,"reference":reference,"action":{"kind":"click"}}),
    );
    assert_eq!(action["result"]["target"], target_id);
    assert_eq!(action["result"]["revision"], 1);

    let stale = host.call(
        "target.act",
        json!({"target":target_id,"reference":snapshot["result"]["nodes"][1]["reference"],"action":{"kind":"click"}}),
    );
    assert_eq!(stale["error"]["code"], "stale_revision");
    assert_eq!(stale["error"]["scope"]["id"], target_id);

    let wait = host.call(
        "target.wait",
        json!({"target":target_id,"condition":{"kind":"revision_at_least","revision":1}}),
    );
    assert_eq!(wait["result"]["matched"], true);

    let memory = host.call("memory.report", json!({}));
    assert_eq!(memory["result"]["owners"]["profiles"]["objects"], 1);
    assert_eq!(memory["result"]["owners"]["sessions"]["objects"], 1);
    assert_eq!(memory["result"]["owners"]["targets"]["objects"], 1);
    let live_bytes = memory["result"]["total_accounted_bytes"].as_u64().unwrap();
    assert!(live_bytes > 0);

    let closed = host.call("target.close", json!({"target":target_id}));
    assert_eq!(closed["result"]["target"], target_id);
    let post_close = host.call("memory.report", json!({}));
    assert_eq!(post_close["result"]["owners"]["targets"]["objects"], 0);
    assert!(
        post_close["result"]["total_accounted_bytes"]
            .as_u64()
            .unwrap()
            < live_bytes
    );
    host.finish();
}
