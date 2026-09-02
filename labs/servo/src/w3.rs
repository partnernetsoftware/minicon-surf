use std::cell::{Cell, RefCell};
use std::collections::BTreeMap;
use std::error::Error;
use std::io::{self, Write};
use std::path::PathBuf;
use std::rc::Rc;
use std::time::{Duration, Instant};

use percent_encoding::{NON_ALPHANUMERIC, percent_encode};
use profile_traits::mem::{MemoryReportResult, ReportKind};
use serde_json::{Value, json};
use servo::{
    JSValue, LoadStatus, Opts, RenderingContext, ServoBuilder, SoftwareRenderingContext, WebView,
    WebViewBuilder, WebViewDelegate,
};
use servo_base::generic_channel::{GenericCallback, TryReceiveError};
use url::Url;

const VIEWPORT_WIDTH: u32 = 800;
const VIEWPORT_HEIGHT: u32 = 600;
const TARGET_COUNT: usize = 8;
const LOAD_DEADLINE: Duration = Duration::from_secs(15);

#[derive(Default)]
struct CourtDelegate {
    loaded: Cell<bool>,
    crashed: RefCell<Option<String>>,
}

impl WebViewDelegate for CourtDelegate {
    fn notify_load_status_changed(&self, _webview: WebView, status: LoadStatus) {
        if status == LoadStatus::Complete {
            self.loaded.set(true);
        }
    }

    fn notify_new_frame_ready(&self, webview: WebView) {
        webview.paint();
    }

    fn notify_crashed(&self, _webview: WebView, reason: String, _backtrace: Option<String>) {
        self.crashed.replace(Some(reason));
    }
}

fn spin_until(
    servo: &servo::Servo,
    deadline: Instant,
    mut complete: impl FnMut() -> bool,
) -> Result<(), Box<dyn Error>> {
    while !complete() {
        if Instant::now() >= deadline {
            return Err("Servo W3 court deadline expired".into());
        }
        servo.spin_event_loop();
        std::thread::sleep(Duration::from_millis(1));
    }
    Ok(())
}

fn observe_rss_stage(servo: &servo::Servo, stage: &str, duration: Duration) -> io::Result<()> {
    println!("{{\"stage\":\"{stage}\"}}");
    io::stdout().flush()?;
    let deadline = Instant::now() + duration;
    while Instant::now() < deadline {
        servo.spin_event_loop();
        std::thread::sleep(Duration::from_millis(1));
    }
    Ok(())
}

fn safe_path_prefix(path: &[String]) -> String {
    let labels: Vec<&str> = path
        .iter()
        .take(2)
        .map(|label| {
            if !label.is_empty()
                && label.len() <= 64
                && label
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
            {
                label.as_str()
            } else {
                "redacted"
            }
        })
        .collect();
    if labels.is_empty() {
        "unlabelled".into()
    } else {
        labels.join("/")
    }
}

fn collect_internal_memory(servo: &servo::Servo) -> Result<Value, Box<dyn Error>> {
    let (callback, receiver) = GenericCallback::<MemoryReportResult>::new_blocking()?;
    servo.create_memory_report(callback);
    let deadline = Instant::now() + LOAD_DEADLINE;
    let result = loop {
        servo.spin_event_loop();
        match receiver.try_recv() {
            Ok(result) => break result,
            Err(TryReceiveError::Empty) => {}
            Err(TryReceiveError::ReceiveError(_)) => {
                return Err("Servo memory-report callback disconnected".into());
            }
        }
        if Instant::now() >= deadline {
            return Err("Servo memory-report callback deadline expired".into());
        }
        std::thread::sleep(Duration::from_millis(1));
    };

    let mut kind_totals = BTreeMap::new();
    let mut explicit_path_prefix_totals = BTreeMap::new();
    let mut non_explicit_reports = BTreeMap::new();
    let mut report_count = 0usize;
    for process in &result.results {
        for report in &process.reports {
            report_count += 1;
            let path = safe_path_prefix(&report.path);
            let kind = match report.kind {
                ReportKind::ExplicitJemallocHeapSize => Some("explicit_jemalloc_heap"),
                ReportKind::ExplicitSystemHeapSize => Some("explicit_system_heap"),
                ReportKind::ExplicitNonHeapSize => Some("explicit_non_heap"),
                ReportKind::ExplicitUnknownLocationSize => Some("explicit_unknown_location"),
                ReportKind::NonExplicitSize => None,
            };
            if let Some(kind) = kind {
                *kind_totals.entry(kind).or_insert(0usize) += report.size;
                *explicit_path_prefix_totals.entry(path).or_insert(0usize) += report.size;
            } else {
                *non_explicit_reports.entry(path).or_insert(0usize) += report.size;
            }
        }
    }
    let explicit_total = kind_totals.values().sum::<usize>();
    let mut prefixes: Vec<_> = explicit_path_prefix_totals.into_iter().collect();
    prefixes.sort_by(|left, right| right.1.cmp(&left.1).then_with(|| left.0.cmp(&right.0)));
    prefixes.truncate(12);
    let mut non_explicit: Vec<_> = non_explicit_reports.into_iter().collect();
    non_explicit.sort_by(|left, right| right.1.cmp(&left.1).then_with(|| left.0.cmp(&right.0)));
    non_explicit.truncate(12);
    Ok(json!({
        "process_report_count":result.results.len(),
        "report_count":report_count,
        "explicit_reported_bytes":explicit_total,
        "bytes_by_kind":kind_totals,
        "largest_sanitized_explicit_path_prefixes":prefixes.into_iter().map(|(path, bytes)| json!({"path":path,"bytes":bytes})).collect::<Vec<_>>(),
        "sanitized_non_explicit_reports":non_explicit.into_iter().map(|(path, bytes)| json!({"path":path,"bytes":bytes})).collect::<Vec<_>>()
    }))
}

fn observe_internal_stage(
    servo: &servo::Servo,
    stage: &str,
    settle: Duration,
) -> Result<(), Box<dyn Error>> {
    let deadline = Instant::now() + settle;
    while Instant::now() < deadline {
        servo.spin_event_loop();
        std::thread::sleep(Duration::from_millis(1));
    }
    println!(
        "{}",
        json!({"stage":stage,"internal_memory":collect_internal_memory(servo)?})
    );
    io::stdout().flush()?;
    Ok(())
}

fn open_verified_target(
    servo: &servo::Servo,
    context: Rc<SoftwareRenderingContext>,
    url: Url,
) -> Result<(WebView, Rc<CourtDelegate>), Box<dyn Error>> {
    let delegate = Rc::new(CourtDelegate::default());
    let webview = WebViewBuilder::new(servo, context)
        .delegate(delegate.clone())
        .url(url)
        .build();
    let deadline = Instant::now() + LOAD_DEADLINE;
    spin_until(servo, deadline, || {
        delegate.loaded.get() || delegate.crashed.borrow().is_some()
    })?;
    if let Some(reason) = delegate.crashed.borrow_mut().take() {
        return Err(format!("Servo content crashed: {reason}").into());
    }

    let semantic_result = Rc::new(RefCell::new(None));
    let callback_result = semantic_result.clone();
    webview.evaluate_javascript("document.querySelector('h1')?.textContent", move |result| {
        callback_result.replace(Some(result));
    });
    spin_until(servo, deadline, || semantic_result.borrow().is_some())?;
    let semantic_value = semantic_result
        .borrow_mut()
        .take()
        .ok_or("missing JS result")?
        .map_err(|error| format!("JavaScript evaluation failed: {error:?}"))?;
    if semantic_value != JSValue::String("Memory and Agent Court".into()) {
        return Err("Servo W3 semantic result differed".into());
    }
    Ok((webview, delegate))
}

fn parse_arguments() -> Result<(PathBuf, PathBuf, Duration, String), Box<dyn Error>> {
    let mut arguments = std::env::args_os().skip(1);
    let fixture = arguments.next().ok_or("missing fixture path")?.into();
    let config_directory = arguments.next().ok_or("missing config directory")?.into();
    let stage_ms: u64 = arguments
        .next()
        .ok_or("missing stage duration")?
        .to_string_lossy()
        .parse()?;
    let mode = arguments
        .next()
        .ok_or("missing measurement mode")?
        .to_string_lossy()
        .into_owned();
    if arguments.next().is_some() || stage_ms == 0 || !matches!(mode.as_str(), "rss" | "internal") {
        return Err(
            "usage: servo-w3-runtime FIXTURE CONFIG_DIRECTORY STAGE_MS rss|internal".into(),
        );
    }
    Ok((
        fixture,
        config_directory,
        Duration::from_millis(stage_ms),
        mode,
    ))
}

fn main() -> Result<(), Box<dyn Error>> {
    rustls::crypto::aws_lc_rs::default_provider()
        .install_default()
        .map_err(|_| "failed to install rustls crypto provider")?;
    let (fixture, config_directory, stage_duration, mode) = parse_arguments()?;
    let fixture = std::fs::read(fixture)?;
    let encoded = percent_encode(&fixture, NON_ALPHANUMERIC).to_string();
    let url = Url::parse(&format!("data:text/html,{encoded}"))?;

    let rendering_context = Rc::new(
        SoftwareRenderingContext::new(dpi::PhysicalSize {
            width: VIEWPORT_WIDTH,
            height: VIEWPORT_HEIGHT,
        })
        .map_err(|error| format!("failed to create software context: {error:?}"))?,
    );
    rendering_context
        .make_current()
        .map_err(|error| format!("failed to make software context current: {error:?}"))?;
    let servo = ServoBuilder::default()
        .opts(Opts {
            config_dir: Some(config_directory),
            temporary_storage: true,
            ..Opts::default()
        })
        .build();

    let observe = |servo: &servo::Servo, stage: &str| -> Result<(), Box<dyn Error>> {
        if mode == "rss" {
            observe_rss_stage(servo, stage, stage_duration)?;
            Ok(())
        } else {
            observe_internal_stage(servo, stage, stage_duration)
        }
    };
    observe(&servo, "empty")?;
    for index in 1..=TARGET_COUNT {
        let (webview, delegate) =
            open_verified_target(&servo, rendering_context.clone(), url.clone())?;
        if index == 1 {
            observe(&servo, "one_target")?;
        } else if index == TARGET_COUNT {
            observe(&servo, "eighth_target")?;
        }
        drop(webview);
        drop(delegate);
        if index == 1 {
            observe(&servo, "post_one_close")?;
        } else if index == TARGET_COUNT {
            observe(&servo, "post_eight_closes")?;
        } else {
            // Process CloseWebView before constructing the next target.
            servo.spin_event_loop();
        }
    }
    drop(servo);
    Ok(())
}
