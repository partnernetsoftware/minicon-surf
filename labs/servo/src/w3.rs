use std::cell::{Cell, RefCell};
use std::error::Error;
use std::io::{self, Write};
use std::path::PathBuf;
use std::rc::Rc;
use std::time::{Duration, Instant};

use percent_encoding::{NON_ALPHANUMERIC, percent_encode};
use servo::{
    JSValue, LoadStatus, Opts, RenderingContext, ServoBuilder, SoftwareRenderingContext, WebView,
    WebViewBuilder, WebViewDelegate,
};
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

fn observe_stage(servo: &servo::Servo, stage: &str, duration: Duration) -> io::Result<()> {
    println!("{{\"stage\":\"{stage}\"}}");
    io::stdout().flush()?;
    let deadline = Instant::now() + duration;
    while Instant::now() < deadline {
        servo.spin_event_loop();
        std::thread::sleep(Duration::from_millis(1));
    }
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

fn parse_arguments() -> Result<(PathBuf, PathBuf, Duration), Box<dyn Error>> {
    let mut arguments = std::env::args_os().skip(1);
    let fixture = arguments.next().ok_or("missing fixture path")?.into();
    let config_directory = arguments.next().ok_or("missing config directory")?.into();
    let stage_ms: u64 = arguments
        .next()
        .ok_or("missing stage duration")?
        .to_string_lossy()
        .parse()?;
    if arguments.next().is_some() || stage_ms == 0 {
        return Err("usage: servo-w3-runtime FIXTURE CONFIG_DIRECTORY STAGE_MS".into());
    }
    Ok((fixture, config_directory, Duration::from_millis(stage_ms)))
}

fn main() -> Result<(), Box<dyn Error>> {
    rustls::crypto::aws_lc_rs::default_provider()
        .install_default()
        .map_err(|_| "failed to install rustls crypto provider")?;
    let (fixture, config_directory, stage_duration) = parse_arguments()?;
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

    observe_stage(&servo, "empty", stage_duration)?;
    for index in 1..=TARGET_COUNT {
        let (webview, delegate) =
            open_verified_target(&servo, rendering_context.clone(), url.clone())?;
        if index == 1 {
            observe_stage(&servo, "one_target", stage_duration)?;
        } else if index == TARGET_COUNT {
            observe_stage(&servo, "eighth_target", stage_duration)?;
        }
        drop(webview);
        drop(delegate);
        if index == 1 {
            observe_stage(&servo, "post_one_close", stage_duration)?;
        } else if index == TARGET_COUNT {
            observe_stage(&servo, "post_eight_closes", stage_duration)?;
        } else {
            // Process CloseWebView before constructing the next target.
            servo.spin_event_loop();
        }
    }
    drop(servo);
    Ok(())
}
