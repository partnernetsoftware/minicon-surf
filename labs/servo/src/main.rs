use std::cell::{Cell, RefCell};
use std::error::Error;
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
const DEADLINE: Duration = Duration::from_secs(15);

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
            return Err("Servo W1 court deadline expired".into());
        }
        servo.spin_event_loop();
        std::thread::sleep(Duration::from_millis(1));
    }
    Ok(())
}

fn parse_arguments() -> Result<(PathBuf, PathBuf, Duration), Box<dyn Error>> {
    let mut arguments = std::env::args_os().skip(1);
    let fixture = arguments.next().ok_or("missing fixture path")?.into();
    let config_directory = arguments.next().ok_or("missing config directory")?.into();
    let hold_ms: u64 = arguments
        .next()
        .ok_or("missing hold duration")?
        .to_string_lossy()
        .parse()?;
    if arguments.next().is_some() || hold_ms == 0 {
        return Err("usage: servo-w1-runtime FIXTURE CONFIG_DIRECTORY HOLD_MS".into());
    }
    Ok((fixture, config_directory, Duration::from_millis(hold_ms)))
}

fn main() -> Result<(), Box<dyn Error>> {
    rustls::crypto::aws_lc_rs::default_provider()
        .install_default()
        .map_err(|_| "failed to install rustls crypto provider")?;
    let (fixture, config_directory, hold) = parse_arguments()?;
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

    let options = Opts {
        config_dir: Some(config_directory),
        temporary_storage: true,
        ..Opts::default()
    };
    let servo = ServoBuilder::default().opts(options).build();
    let delegate = Rc::new(CourtDelegate::default());
    let webview = WebViewBuilder::new(&servo, rendering_context.clone())
        .delegate(delegate.clone())
        .url(url)
        .build();
    let deadline = Instant::now() + DEADLINE;
    spin_until(&servo, deadline, || {
        delegate.loaded.get() || delegate.crashed.borrow().is_some()
    })?;
    if let Some(reason) = delegate.crashed.borrow_mut().take() {
        return Err(format!("Servo content crashed: {reason}").into());
    }

    let semantic_result = Rc::new(RefCell::new(None));
    let callback_result = semantic_result.clone();
    webview.evaluate_javascript(
        "[document.querySelector('h1')?.textContent,document.querySelector('input')?.value,document.querySelector('button')?.textContent,document.querySelector('a')?.textContent]",
        move |result| {
            callback_result.replace(Some(result));
        },
    );
    spin_until(&servo, deadline, || semantic_result.borrow().is_some())?;
    let expected = JSValue::Array(vec![
        JSValue::String("Memory and Agent Court".into()),
        JSValue::String("bounded browser".into()),
        JSValue::String("Continue".into()),
        JSValue::String("Example result".into()),
    ]);
    let semantic_value = semantic_result
        .borrow_mut()
        .take()
        .ok_or("missing JS result")?
        .map_err(|error| format!("JavaScript evaluation failed: {error:?}"))?;
    if semantic_value != expected {
        return Err("Servo W1 semantic result differed".into());
    }

    let screenshot_result = Rc::new(RefCell::new(None));
    let callback_result = screenshot_result.clone();
    webview.take_screenshot(None, move |result| {
        callback_result.replace(Some(result));
    });
    spin_until(&servo, deadline, || screenshot_result.borrow().is_some())?;
    let image = screenshot_result
        .borrow_mut()
        .take()
        .ok_or("missing screenshot result")?
        .map_err(|error| format!("screenshot failed: {error:?}"))?;
    if image.width() != VIEWPORT_WIDTH || image.height() != VIEWPORT_HEIGHT {
        return Err("Servo W1 screenshot dimensions differed".into());
    }

    let hold_deadline = Instant::now() + hold;
    while Instant::now() < hold_deadline {
        servo.spin_event_loop();
        std::thread::sleep(Duration::from_millis(1));
    }
    drop(webview);
    drop(servo);
    Ok(())
}
