use std::cell::{Cell, RefCell};
use std::collections::BTreeMap;
use std::error::Error;
use std::ffi::{CStr, c_void};
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
const MAX_CYCLES: usize = 256;
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

#[derive(Clone, Copy, PartialEq, Eq)]
enum Report {
    Rss,
    Internal,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Action {
    Control,
    JemallocPurge,
    LibmallocRelief,
    Both,
}

impl Action {
    fn name(self) -> &'static str {
        match self {
            Action::Control => "control_wait",
            Action::JemallocPurge => "jemalloc_all_arenas_purge",
            Action::LibmallocRelief => "libmalloc_zone_pressure_relief",
            Action::Both => "jemalloc_purge_then_libmalloc_relief",
        }
    }
}

// Apple libmalloc statistics for every registered malloc zone. jemalloc is
// linked with the `_rjem_` symbol prefix, so these zones are the system heap
// that C/C++ dependencies (SpiderMonkey, swgl, FreeType, HarfBuzz) allocate
// from; the Rust global allocator is not counted here.
#[repr(C)]
#[derive(Default)]
struct MallocStatistics {
    blocks_in_use: u32,
    size_in_use: usize,
    max_size_in_use: usize,
    size_allocated: usize,
}

unsafe extern "C" {
    fn malloc_zone_statistics(zone: *mut c_void, stats: *mut MallocStatistics);
    fn malloc_zone_pressure_relief(zone: *mut c_void, goal: usize) -> usize;
}

fn libmalloc_statistics() -> Value {
    let mut stats = MallocStatistics::default();
    // SAFETY: a null zone aggregates every malloc zone; the out-pointer is a
    // valid, exclusively borrowed C-layout struct for the duration of the call.
    unsafe { malloc_zone_statistics(std::ptr::null_mut(), &mut stats) };
    json!({
        "blocks_in_use": stats.blocks_in_use,
        "size_in_use": stats.size_in_use,
        "max_size_in_use": stats.max_size_in_use,
        "size_allocated": stats.size_allocated,
    })
}

fn jemalloc_stat(name: &CStr) -> Option<usize> {
    let mut epoch: u64 = 1;
    let mut epoch_len = size_of::<u64>();
    // SAFETY: `epoch` refreshes cached statistics; both pointers reference
    // live locals of the declared lengths.
    let code = unsafe {
        tikv_jemalloc_sys::mallctl(
            c"epoch".as_ptr(),
            (&raw mut epoch).cast(),
            &mut epoch_len,
            (&raw mut epoch).cast(),
            epoch_len,
        )
    };
    if code != 0 {
        return None;
    }
    let mut value: usize = 0;
    let mut value_len = size_of::<usize>();
    // SAFETY: read-only mallctl of a size_t statistic into a live local.
    let code = unsafe {
        tikv_jemalloc_sys::mallctl(
            name.as_ptr(),
            (&raw mut value).cast(),
            &mut value_len,
            std::ptr::null_mut(),
            0,
        )
    };
    (code == 0).then_some(value)
}

fn jemalloc_statistics() -> Value {
    json!({
        "allocated": jemalloc_stat(c"stats.allocated"),
        "active": jemalloc_stat(c"stats.active"),
        "metadata": jemalloc_stat(c"stats.metadata"),
        "resident": jemalloc_stat(c"stats.resident"),
        "mapped": jemalloc_stat(c"stats.mapped"),
        "retained": jemalloc_stat(c"stats.retained"),
    })
}

fn allocator_statistics() -> Value {
    json!({"jemalloc": jemalloc_statistics(), "libmalloc": libmalloc_statistics()})
}

fn spin_for(servo: &servo::Servo, duration: Duration) {
    let deadline = Instant::now() + duration;
    while Instant::now() < deadline {
        servo.spin_event_loop();
        std::thread::sleep(Duration::from_millis(1));
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

/// One measured stage: a start marker, a spinning window in which the driver
/// samples the process, then an end line carrying in-process allocator
/// statistics (and, in internal mode, Servo's own memory report). Allocator
/// statistics are read after the window so their tiny cost never lands inside
/// the driver's sampling window.
fn observe_stage(
    servo: &servo::Servo,
    report: Report,
    stage: &str,
    action: Value,
    duration: Duration,
) -> Result<(), Box<dyn Error>> {
    println!("{}", json!({"stage":stage,"action":action}));
    io::stdout().flush()?;
    spin_for(servo, duration);
    let mut end = json!({"stage_end":stage,"allocators":allocator_statistics()});
    if report == Report::Internal {
        end["internal_memory"] = collect_internal_memory(servo)?;
    }
    println!("{end}");
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

struct Arguments {
    fixture: PathBuf,
    config_directory: PathBuf,
    stage_duration: Duration,
    cycles: usize,
    report: Report,
    action: Action,
}

fn parse_arguments() -> Result<Arguments, Box<dyn Error>> {
    const USAGE: &str = "usage: servo-w3-runtime FIXTURE CONFIG_DIRECTORY STAGE_MS CYCLES \
        {rss|internal}-{control|jemalloc-purge|libmalloc-relief|both}";
    let mut arguments = std::env::args_os().skip(1);
    let fixture = arguments.next().ok_or(USAGE)?.into();
    let config_directory = arguments.next().ok_or(USAGE)?.into();
    let stage_ms: u64 = arguments.next().ok_or(USAGE)?.to_string_lossy().parse()?;
    let cycles: usize = arguments.next().ok_or(USAGE)?.to_string_lossy().parse()?;
    let mode = arguments.next().ok_or(USAGE)?.to_string_lossy().into_owned();
    let (report, action) = mode.split_once('-').ok_or(USAGE)?;
    let report = match report {
        "rss" => Report::Rss,
        "internal" => Report::Internal,
        _ => return Err(USAGE.into()),
    };
    let action = match action {
        "control" => Action::Control,
        "jemalloc-purge" => Action::JemallocPurge,
        "libmalloc-relief" => Action::LibmallocRelief,
        "both" => Action::Both,
        _ => return Err(USAGE.into()),
    };
    if arguments.next().is_some() || stage_ms == 0 || cycles == 0 || cycles > MAX_CYCLES {
        return Err(USAGE.into());
    }
    Ok(Arguments {
        fixture,
        config_directory,
        stage_duration: Duration::from_millis(stage_ms),
        cycles,
        report,
        action,
    })
}

fn jemalloc_purge_all_arenas() -> i32 {
    // SAFETY: the no-argument `arena.<i>.purge` command with jemalloc's
    // MALLCTL_ARENAS_ALL sentinel (4096); every pointer is null.
    unsafe {
        tikv_jemalloc_sys::mallctl(
            c"arena.4096.purge".as_ptr(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            0,
        )
    }
}

fn libmalloc_relief_all_zones() -> usize {
    // SAFETY: a null zone requests pressure relief from every malloc zone and a
    // zero goal asks for everything reclaimable; no Rust pointer is retained.
    unsafe { malloc_zone_pressure_relief(std::ptr::null_mut(), 0) }
}

fn perform_action(action: Action) -> Result<Value, Box<dyn Error>> {
    let mut purge_code = None;
    let mut released = None;
    match action {
        Action::Control => {}
        Action::JemallocPurge => purge_code = Some(jemalloc_purge_all_arenas()),
        Action::LibmallocRelief => released = Some(libmalloc_relief_all_zones()),
        Action::Both => {
            purge_code = Some(jemalloc_purge_all_arenas());
            released = Some(libmalloc_relief_all_zones());
        }
    }
    if matches!(purge_code, Some(code) if code != 0) {
        return Err(format!("jemalloc purge mallctl failed with code {purge_code:?}").into());
    }
    Ok(json!({
        "name": action.name(),
        "jemalloc_purge_result_code": purge_code,
        "libmalloc_released_bytes": released,
    }))
}

fn main() -> Result<(), Box<dyn Error>> {
    rustls::crypto::aws_lc_rs::default_provider()
        .install_default()
        .map_err(|_| "failed to install rustls crypto provider")?;
    let arguments = parse_arguments()?;
    if jemalloc_stat(c"stats.resident").is_none() {
        return Err("jemalloc statistics are unavailable; build with the stats feature".into());
    }
    let fixture = std::fs::read(&arguments.fixture)?;
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
            config_dir: Some(arguments.config_directory.clone()),
            temporary_storage: true,
            ..Opts::default()
        })
        .build();

    let none = json!({"name":"none"});
    let observe = |stage: &str, action: Value| -> Result<(), Box<dyn Error>> {
        observe_stage(&servo, arguments.report, stage, action, arguments.stage_duration)
    };
    observe("empty", none.clone())?;
    for index in 1..=arguments.cycles {
        let (webview, delegate) =
            open_verified_target(&servo, rendering_context.clone(), url.clone())?;
        if index == 1 {
            observe("one_target", none.clone())?;
        }
        if index == arguments.cycles {
            observe("last_target", none.clone())?;
        }
        drop(webview);
        drop(delegate);
        if index == 1 {
            observe("post_one_close", none.clone())?;
        }
        if index == arguments.cycles {
            observe("post_all_closes", none.clone())?;
        } else if index != 1 {
            // Let the constellation process CloseWebView and the script thread
            // exit before the next build, so cycles do not overlap.
            spin_for(&servo, Duration::from_millis(50));
        }
    }
    let action = perform_action(arguments.action)?;
    observe("post_action", action)?;
    drop(servo);
    Ok(())
}
