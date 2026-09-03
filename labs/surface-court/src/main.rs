//! Standalone macOS native-surface candidate probe for the native route's
//! G3 design. Never linked into the host. One binary shape; the candidate is
//! chosen at build time (`--features cocoa` or `winit-softbuffer`; no feature
//! is the plain control that only owns the CPU buffer). Driven over stdio by
//! `court.py`, one JSON command per line:
//!
//! - `headless`: nothing but the buffer accounting; answers with counters.
//! - `show`: creates a real OS window (320 × 200) showing the colour-bar test
//!   pattern from a CPU buffer; answers with the window number and timing.
//! - `pump`: processes pending window-server events without blocking.
//! - `capture`: reads back pixels of the own window only (never the desktop)
//!   and compares them with the pattern; reports "not verifiable" when the
//!   OS refuses the capture.
//! - `hide`: releases window, view and backing store; answers with timing.
//! - `report`: owners, backing bytes, libmalloc statistics, on-screen window
//!   numbers of this process, and the dyld images of interest.
//! - `exit`.

use std::ffi::CStr;
use std::io::{self, BufRead, Write};
use std::time::Instant;

use serde_json::{Value, json};

const WIDTH: usize = 320;
const HEIGHT: usize = 200;
const BACKING_BYTES: usize = WIDTH * HEIGHT * 4;

#[repr(C)]
struct MallocStatistics {
    blocks_in_use: u32,
    size_in_use: usize,
    max_size_in_use: usize,
    size_allocated: usize,
}

unsafe extern "C" {
    fn malloc_zone_statistics(zone: *mut libc::c_void, stats: *mut MallocStatistics);
    fn _dyld_image_count() -> u32;
    fn _dyld_get_image_name(index: u32) -> *const libc::c_char;
}

fn libmalloc() -> Value {
    let mut stats = MallocStatistics {
        blocks_in_use: 0,
        size_in_use: 0,
        max_size_in_use: 0,
        size_allocated: 0,
    };
    // SAFETY: a null zone sums every zone; the struct matches malloc_statistics_t.
    unsafe { malloc_zone_statistics(std::ptr::null_mut(), &mut stats) };
    json!({"blocks_in_use":stats.blocks_in_use,"size_in_use":stats.size_in_use,"size_allocated":stats.size_allocated})
}

/// The dyld images of interest: which GPU and graphics frameworks the
/// process has loaded at this point.
fn images() -> Value {
    let mut found = json!({"Metal":false,"OpenGL":false,"QuartzCore":false,"CoreGraphics":false,"AppKit":false,"SkyLight":false,"total":0});
    // SAFETY: dyld image enumeration reads the process's own image list.
    let count = unsafe { _dyld_image_count() };
    found["total"] = json!(count);
    for index in 0..count {
        // SAFETY: the index is below the count just read; the name is a NUL-terminated string owned by dyld.
        let name = unsafe { CStr::from_ptr(_dyld_get_image_name(index)) }.to_string_lossy();
        for key in [
            "Metal",
            "OpenGL",
            "QuartzCore",
            "CoreGraphics",
            "AppKit",
            "SkyLight",
        ] {
            if name.contains(&format!("/{key}.framework/")) || name.ends_with(&format!("/{key}")) {
                found[key] = json!(true);
            }
        }
    }
    found
}

/// The colour-bar test pattern: eight vertical bars, a checker strip at the
/// bottom, no text, nothing personal. BGRA, premultiplied is irrelevant.
fn pattern() -> Vec<u8> {
    let bars: [[u8; 3]; 8] = [
        [235, 235, 235],
        [235, 235, 16],
        [16, 235, 235],
        [16, 235, 16],
        [235, 16, 235],
        [235, 16, 16],
        [16, 16, 235],
        [16, 16, 16],
    ];
    let mut out = vec![0u8; BACKING_BYTES];
    for y in 0..HEIGHT {
        for x in 0..WIDTH {
            let [r, g, b] = if y >= HEIGHT - 24 {
                if ((x / 12) + (y / 12)) % 2 == 0 {
                    [255, 255, 255]
                } else {
                    [0, 0, 0]
                }
            } else {
                bars[x * 8 / WIDTH]
            };
            let at = (y * WIDTH + x) * 4;
            out[at] = r;
            out[at + 1] = g;
            out[at + 2] = b;
            out[at + 3] = 255;
        }
    }
    out
}

#[allow(dead_code)]
fn expected_pixel(x: usize, y: usize) -> [u8; 3] {
    let all = pattern();
    let at = (y * WIDTH + x) * 4;
    [all[at], all[at + 1], all[at + 2]]
}

/// What a candidate must provide.
trait Surface {
    fn show(&mut self, pixels: &[u8]) -> Result<Value, String>;
    fn pump(&mut self) -> usize;
    fn capture(&mut self) -> Value;
    fn hide(&mut self) -> Result<Value, String>;
    fn window_numbers(&self) -> Vec<i64>;
    fn live(&self) -> usize;
    fn name(&self) -> &'static str;
}

// ------------------------------------------------------------------ plain

#[cfg(not(any(feature = "cocoa", feature = "winit-softbuffer")))]
mod candidate {
    use super::*;
    pub struct Plain {
        buffer: Option<Vec<u8>>,
    }
    pub fn new() -> Result<Plain, String> {
        Ok(Plain { buffer: None })
    }
    impl Surface for Plain {
        fn show(&mut self, pixels: &[u8]) -> Result<Value, String> {
            self.buffer = Some(pixels.to_vec());
            Ok(json!({"window_number":null,"real_window":false}))
        }
        fn pump(&mut self) -> usize {
            0
        }
        fn capture(&mut self) -> Value {
            json!({"verified":false,"reason":"no window in the plain control"})
        }
        fn hide(&mut self) -> Result<Value, String> {
            self.buffer = None;
            Ok(json!({}))
        }
        fn window_numbers(&self) -> Vec<i64> {
            Vec::new()
        }
        fn live(&self) -> usize {
            usize::from(self.buffer.is_some())
        }
        fn name(&self) -> &'static str {
            "plain-buffer"
        }
    }
}

// ------------------------------------------------------------------ cocoa

#[cfg(feature = "cocoa")]
mod candidate {
    use super::*;
    use objc2::rc::Retained;
    use objc2::runtime::AnyObject;
    use objc2::{AnyThread, MainThreadMarker, MainThreadOnly};
    use objc2_app_kit::{
        NSApplication, NSApplicationActivationPolicy, NSBackingStoreType, NSBitmapImageRep,
        NSCalibratedRGBColorSpace, NSEventMask, NSImage, NSImageView, NSWindow,
        NSWindowNumberListOptions, NSWindowStyleMask,
    };
    use objc2_foundation::{NSDate, NSDefaultRunLoopMode, NSPoint, NSRect, NSSize};

    pub struct Cocoa {
        mtm: MainThreadMarker,
        app: Retained<NSApplication>,
        window: Option<Retained<NSWindow>>,
        view: Option<Retained<NSImageView>>,
        image: Option<Retained<NSImage>>,
        rep: Option<Retained<NSBitmapImageRep>>,
        backing: Option<Vec<u8>>,
    }

    pub fn new() -> Result<Cocoa, String> {
        let mtm = MainThreadMarker::new().ok_or("not on the main thread")?;
        let app = NSApplication::sharedApplication(mtm);
        // An accessory app shows windows without a Dock icon or a menu bar and
        // never steals focus; recorded in the receipt.
        app.setActivationPolicy(NSApplicationActivationPolicy::Accessory);
        Ok(Cocoa {
            mtm,
            app,
            window: None,
            view: None,
            image: None,
            rep: None,
            backing: None,
        })
    }

    impl Surface for Cocoa {
        fn show(&mut self, pixels: &[u8]) -> Result<Value, String> {
            if self.window.is_some() {
                return Err("already shown".into());
            }
            let mut backing = pixels.to_vec();
            let mut planes: [*mut u8; 5] = [
                backing.as_mut_ptr(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                std::ptr::null_mut(),
            ];
            // SAFETY: the plane pointer stays valid while `backing` lives in this struct,
            // and the bitmap parameters describe exactly that buffer (RGBA, 8 bits, 4 samples).
            let rep = unsafe {
                NSBitmapImageRep::initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel(
                    NSBitmapImageRep::alloc(),
                    planes.as_mut_ptr(),
                    WIDTH as isize,
                    HEIGHT as isize,
                    8,
                    4,
                    true,
                    false,
                    NSCalibratedRGBColorSpace,
                    (WIDTH * 4) as isize,
                    32,
                )
            }
            .ok_or("bitmap representation")?;
            let size = NSSize::new(WIDTH as f64, HEIGHT as f64);
            // SAFETY: plain AppKit constructors on the main thread.
            let image = NSImage::initWithSize(NSImage::alloc(), size);
            image.addRepresentation(&rep);
            let frame = NSRect::new(NSPoint::new(0.0, 0.0), size);
            let view = NSImageView::initWithFrame(NSImageView::alloc(self.mtm), frame);
            view.setImage(Some(&image));
            let style = NSWindowStyleMask::Titled | NSWindowStyleMask::Closable;
            // SAFETY: the window is created and used on the main thread; `defer` false
            // creates the backing store now so its cost is measured while shown.
            let window = unsafe {
                NSWindow::initWithContentRect_styleMask_backing_defer(
                    NSWindow::alloc(self.mtm),
                    NSRect::new(NSPoint::new(80.0, 80.0), size),
                    style,
                    NSBackingStoreType::Buffered,
                    false,
                )
            };
            // The window is released explicitly when dropped, not by `close`.
            // SAFETY: the window is owned by `Retained` and released when that drops.
            unsafe { window.setReleasedWhenClosed(false) };
            window.setContentView(Some(&view));
            window.makeKeyAndOrderFront(None);
            let number = window.windowNumber();
            self.backing = Some(backing);
            self.rep = Some(rep);
            self.image = Some(image);
            self.view = Some(view);
            self.window = Some(window);
            let pumped = self.pump();
            Ok(
                json!({"window_number":number,"real_window":true,"events_pumped":pumped,"activation_policy":"accessory"}),
            )
        }

        fn pump(&mut self) -> usize {
            let mut count = 0;
            loop {
                // SAFETY: a non-blocking event fetch with a past date on the main thread.
                let event = unsafe {
                    self.app.nextEventMatchingMask_untilDate_inMode_dequeue(
                        NSEventMask::Any,
                        Some(&NSDate::distantPast()),
                        NSDefaultRunLoopMode,
                        true,
                    )
                };
                match event {
                    Some(event) => {
                        self.app.sendEvent(&event);
                        count += 1;
                    }
                    None => break,
                }
                if count > 1000 {
                    break;
                }
            }
            count
        }

        fn capture(&mut self) -> Value {
            let Some(window) = &self.window else {
                return json!({"verified":false,"reason":"no window"});
            };
            super::capture_own_window(window.windowNumber() as u32)
        }

        fn hide(&mut self) -> Result<Value, String> {
            let Some(window) = self.window.take() else {
                return Err("not shown".into());
            };
            window.close();
            window.setContentView(None);
            if let Some(view) = self.view.take() {
                view.setImage(None);
                drop(view);
            }
            drop(self.image.take());
            drop(self.rep.take());
            drop(self.backing.take());
            drop(window);
            let pumped = self.pump();
            Ok(json!({"events_pumped":pumped}))
        }

        fn window_numbers(&self) -> Vec<i64> {
            let numbers =
                NSWindow::windowNumbersWithOptions(NSWindowNumberListOptions::empty(), self.mtm);
            match numbers {
                Some(list) => list
                    .to_vec()
                    .iter()
                    .map(|n| n.integerValue() as i64)
                    .collect(),
                None => Vec::new(),
            }
        }

        fn live(&self) -> usize {
            usize::from(self.window.is_some())
        }

        fn name(&self) -> &'static str {
            "cocoa-objc2"
        }
    }

    #[allow(dead_code)]
    fn _unused(_: &AnyObject) {}
}

// ------------------------------------------------------- winit + softbuffer

#[cfg(feature = "winit-softbuffer")]
mod candidate {
    use super::*;
    use std::num::NonZeroU32;
    use std::rc::Rc;
    use std::time::Duration;
    use winit::application::ApplicationHandler;
    use winit::event::WindowEvent;
    use winit::event_loop::{ActiveEventLoop, EventLoop};
    use winit::platform::macos::{ActivationPolicy, EventLoopBuilderExtMacOS};
    use winit::platform::pump_events::{EventLoopExtPumpEvents, PumpStatus};
    use winit::window::{Window, WindowId};

    struct App {
        want: Option<Vec<u8>>,
        pixels: Vec<u8>,
        window: Option<Rc<Window>>,
        surface: Option<softbuffer::Surface<Rc<Window>, Rc<Window>>>,
        context: Option<softbuffer::Context<Rc<Window>>>,
        events: usize,
        error: Option<String>,
    }

    impl App {
        /// Fill a softbuffer frame of `w` × `h` physical pixels from the 320 × 200
        /// pattern (nearest neighbour), in softbuffer's 0RGB layout.
        fn fill(buffer: &mut [u32], pixels: &[u8], w: usize, h: usize) {
            for y in 0..h {
                let sy = y * HEIGHT / h.max(1);
                for x in 0..w {
                    let sx = x * WIDTH / w.max(1);
                    let at = (sy * WIDTH + sx) * 4;
                    if at + 2 < pixels.len() && y * w + x < buffer.len() {
                        buffer[y * w + x] = (u32::from(pixels[at]) << 16)
                            | (u32::from(pixels[at + 1]) << 8)
                            | u32::from(pixels[at + 2]);
                    }
                }
            }
        }

        /// winit calls `resumed` once per lifecycle, so a second show has to
        /// create its window from `about_to_wait` instead.
        fn ensure_window(&mut self, event_loop: &ActiveEventLoop) {
            if let Some(pixels) = self.want.take() {
                self.pixels = pixels.clone();
                let attributes = Window::default_attributes()
                    .with_title("minicon-surf surface probe")
                    .with_inner_size(winit::dpi::LogicalSize::new(WIDTH as f64, HEIGHT as f64))
                    .with_position(winit::dpi::LogicalPosition::new(80.0, 80.0));
                match event_loop.create_window(attributes) {
                    Ok(window) => {
                        let window = Rc::new(window);
                        match softbuffer::Context::new(window.clone()) {
                            Ok(context) => match softbuffer::Surface::new(&context, window.clone())
                            {
                                Ok(mut surface) => {
                                    let size = window.inner_size();
                                    let (w, h) =
                                        (size.width.max(1) as usize, size.height.max(1) as usize);
                                    let ok = surface
                                        .resize(
                                            NonZeroU32::new(w as u32).unwrap(),
                                            NonZeroU32::new(h as u32).unwrap(),
                                        )
                                        .and_then(|()| {
                                            let mut buffer = surface.buffer_mut()?;
                                            Self::fill(&mut buffer, &pixels, w, h);
                                            buffer.present()
                                        });
                                    if let Err(e) = ok {
                                        self.error = Some(format!("softbuffer: {e}"));
                                    }
                                    self.surface = Some(surface);
                                    self.context = Some(context);
                                    window.request_redraw();
                                    self.window = Some(window);
                                }
                                Err(e) => self.error = Some(format!("surface: {e}")),
                            },
                            Err(e) => self.error = Some(format!("context: {e}")),
                        }
                    }
                    Err(e) => self.error = Some(format!("window: {e}")),
                }
            }
        }
    }

    impl ApplicationHandler for App {
        fn resumed(&mut self, event_loop: &ActiveEventLoop) {
            self.ensure_window(event_loop);
        }

        fn about_to_wait(&mut self, event_loop: &ActiveEventLoop) {
            self.ensure_window(event_loop);
        }

        fn window_event(
            &mut self,
            _event_loop: &ActiveEventLoop,
            _id: WindowId,
            event: WindowEvent,
        ) {
            self.events += 1;
            if let WindowEvent::RedrawRequested = event {
                // softbuffer does not keep the previous frame: refill from the pixels.
                if let Some(surface) = self.surface.as_mut() {
                    let size = self.window.as_ref().map(|w| w.inner_size());
                    if let (Some(size), Ok(mut buffer)) = (size, surface.buffer_mut()) {
                        Self::fill(
                            &mut buffer,
                            &self.pixels,
                            size.width.max(1) as usize,
                            size.height.max(1) as usize,
                        );
                        let _ = buffer.present();
                    }
                }
            }
        }
    }

    pub struct Winit {
        event_loop: EventLoop<()>,
        app: App,
        backing: Option<Vec<u8>>,
    }

    pub fn new() -> Result<Winit, String> {
        let mut builder = EventLoop::builder();
        builder
            .with_activation_policy(ActivationPolicy::Accessory)
            .with_default_menu(false)
            .with_activate_ignoring_other_apps(false);
        let event_loop = builder.build().map_err(|e| format!("event loop: {e}"))?;
        Ok(Winit {
            event_loop,
            app: App {
                want: None,
                pixels: Vec::new(),
                window: None,
                surface: None,
                context: None,
                events: 0,
                error: None,
            },
            backing: None,
        })
    }

    impl Surface for Winit {
        fn show(&mut self, pixels: &[u8]) -> Result<Value, String> {
            if self.app.window.is_some() {
                return Err("already shown".into());
            }
            self.app.want = Some(pixels.to_vec());
            self.backing = Some(pixels.to_vec());
            let pumped = self.pump();
            if let Some(error) = self.app.error.take() {
                return Err(error);
            }
            let number = self.window_numbers().first().copied();
            Ok(
                json!({"window_number":number,"real_window":self.app.window.is_some(),"events_pumped":pumped,"activation_policy":"accessory"}),
            )
        }

        fn pump(&mut self) -> usize {
            let before = self.app.events;
            if let Some(window) = &self.app.window {
                window.request_redraw();
            }
            for _ in 0..4 {
                match self
                    .event_loop
                    .pump_app_events(Some(Duration::ZERO), &mut self.app)
                {
                    PumpStatus::Continue => {}
                    PumpStatus::Exit(_) => break,
                }
            }
            self.app.events - before
        }

        fn capture(&mut self) -> Value {
            let Some(number) = self.window_numbers().first().copied() else {
                return json!({"verified":false,"reason":"no window"});
            };
            super::capture_own_window(number as u32)
        }

        fn hide(&mut self) -> Result<Value, String> {
            if self.app.window.is_none() {
                return Err("not shown".into());
            }
            drop(self.app.surface.take());
            drop(self.app.context.take());
            drop(self.app.window.take());
            drop(self.backing.take());
            let pumped = self.pump();
            Ok(json!({"events_pumped":pumped}))
        }

        fn window_numbers(&self) -> Vec<i64> {
            super::own_window_numbers()
        }

        fn live(&self) -> usize {
            usize::from(self.app.window.is_some())
        }

        fn name(&self) -> &'static str {
            "winit-softbuffer"
        }
    }
}

/// On-screen window numbers of this process through AppKit (winit already
/// depends on objc2-app-kit, so this adds no crate family).
#[cfg(feature = "winit-softbuffer")]
fn own_window_numbers() -> Vec<i64> {
    use objc2::MainThreadMarker;
    use objc2_app_kit::{NSWindow, NSWindowNumberListOptions};
    let Some(mtm) = MainThreadMarker::new() else {
        return Vec::new();
    };
    match NSWindow::windowNumbersWithOptions(NSWindowNumberListOptions::empty(), mtm) {
        Some(list) => list
            .to_vec()
            .iter()
            .map(|n| n.integerValue() as i64)
            .collect(),
        None => Vec::new(),
    }
}

/// Read back the own window's pixels (the window number of this process
/// only) and compare a few sample points with the pattern.
#[allow(dead_code)]
fn capture_own_window(window_number: u32) -> Value {
    #[cfg(target_os = "macos")]
    {
        use std::ptr::NonNull;
        #[repr(C)]
        struct CGRect {
            x: f64,
            y: f64,
            w: f64,
            h: f64,
        }
        unsafe extern "C" {
            fn CGWindowListCreateImage(
                bounds: CGRect,
                options: u32,
                window_id: u32,
                image_options: u32,
            ) -> *mut libc::c_void;
            fn CGImageGetWidth(image: *mut libc::c_void) -> usize;
            fn CGImageGetHeight(image: *mut libc::c_void) -> usize;
            fn CGImageGetBytesPerRow(image: *mut libc::c_void) -> usize;
            fn CGImageGetDataProvider(image: *mut libc::c_void) -> *mut libc::c_void;
            fn CGDataProviderCopyData(provider: *mut libc::c_void) -> *mut libc::c_void;
            fn CFDataGetLength(data: *mut libc::c_void) -> isize;
            fn CFDataGetBytePtr(data: *mut libc::c_void) -> *const u8;
            fn CFRelease(object: *mut libc::c_void);
        }
        const K_CG_WINDOW_LIST_OPTION_INCLUDING_WINDOW: u32 = 1 << 3;
        const K_CG_WINDOW_IMAGE_BOUNDS_IGNORE_FRAMING: u32 = 1 << 0;
        // kCGRectNull: the window's own bounds.
        let null_rect = CGRect {
            x: f64::INFINITY,
            y: f64::INFINITY,
            w: 0.0,
            h: 0.0,
        };
        // SAFETY: CoreGraphics C API; the image, when non-null, is released below.
        let image = unsafe {
            CGWindowListCreateImage(
                null_rect,
                K_CG_WINDOW_LIST_OPTION_INCLUDING_WINDOW,
                window_number,
                K_CG_WINDOW_IMAGE_BOUNDS_IGNORE_FRAMING,
            )
        };
        if image.is_null() {
            return json!({"verified":false,"reason":"the OS returned no image for the own window (screen capture not permitted or window not composited yet)"});
        }
        let (width, height, stride) = unsafe {
            (
                CGImageGetWidth(image),
                CGImageGetHeight(image),
                CGImageGetBytesPerRow(image),
            )
        };
        let provider = unsafe { CGImageGetDataProvider(image) };
        let data = unsafe { CGDataProviderCopyData(provider) };
        if data.is_null() || width == 0 || height == 0 {
            unsafe { CFRelease(image) };
            return json!({"verified":false,"reason":"the own window's image carried no data","width":width,"height":height});
        }
        let length = unsafe { CFDataGetLength(data) } as usize;
        let bytes = unsafe { std::slice::from_raw_parts(CFDataGetBytePtr(data), length) };
        // Sample the centre of each bar and of the checker strip, scaled to the captured size.
        let mut matches = 0;
        let mut samples = Vec::new();
        for bar in 0..8 {
            let x = (bar * WIDTH / 8 + WIDTH / 16) * width / WIDTH;
            let y = (HEIGHT / 2) * height / HEIGHT;
            let at = y * stride + x * 4;
            if at + 3 <= length {
                let (b, g, r) = (bytes[at], bytes[at + 1], bytes[at + 2]);
                let expected = expected_pixel(bar * WIDTH / 8 + WIDTH / 16, HEIGHT / 2);
                // Colour management shifts saturated values on the way to the display, so
                // each bar is classified by which channels are high, which the pattern fixes.
                let class = |c: [u8; 3]| [c[0] >= 128, c[1] >= 128, c[2] >= 128];
                let ok = class([r, g, b]) == class(expected);
                matches += usize::from(ok);
                samples.push(json!({"bar":bar,"got":[r,g,b],"expected":expected,"ok":ok}));
            }
        }
        let verified = matches == 8;
        unsafe {
            CFRelease(data);
            CFRelease(image);
        }
        NonNull::new(std::ptr::null_mut::<u8>());
        json!({"verified":verified,"matches":matches,"of":8,"captured":{"width":width,"height":height},"samples":samples})
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = window_number;
        json!({"verified":false,"reason":"not macOS"})
    }
}

fn reply(value: Value) {
    let mut out = io::stdout().lock();
    let _ = writeln!(out, "{value}");
    let _ = out.flush();
}

/// Run one command inside an autorelease pool on the Objective-C candidates:
/// a stdio main loop has no AppKit run loop draining pools, and without this
/// every autoreleased object of a round would stay alive.
fn with_pool<T>(work: impl FnOnce() -> T) -> T {
    #[cfg(any(feature = "cocoa", feature = "winit-softbuffer"))]
    {
        objc2::rc::autoreleasepool(|_| work())
    }
    #[cfg(not(any(feature = "cocoa", feature = "winit-softbuffer")))]
    {
        work()
    }
}

fn main() {
    let mut surface = match candidate::new() {
        Ok(s) => s,
        Err(e) => {
            reply(json!({"ok":false,"fatal":e}));
            std::process::exit(65);
        }
    };
    let pixels = pattern();
    let stdin = io::stdin();
    let name = surface.name();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        let command: Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(e) => {
                reply(json!({"ok":false,"error":format!("malformed: {e}")}));
                continue;
            }
        };
        if command["op"].as_str() == Some("exit") {
            reply(json!({"ok":true}));
            break;
        }
        with_pool(|| match command["op"].as_str().unwrap_or_default() {
            "headless" => reply(
                json!({"ok":true,"candidate":name,"surfaces":surface.live(),"backing_bytes":0}),
            ),
            "show" => {
                let started = Instant::now();
                match surface.show(&pixels) {
                    Ok(mut facts) => {
                        facts["ok"] = json!(true);
                        facts["show_ms"] = json!(started.elapsed().as_secs_f64() * 1000.0);
                        facts["surfaces"] = json!(surface.live());
                        facts["backing_bytes"] = json!(BACKING_BYTES);
                        reply(facts);
                    }
                    Err(e) => reply(json!({"ok":false,"error":e})),
                }
            }
            "pump" => {
                let pumped = surface.pump();
                reply(json!({"ok":true,"events_pumped":pumped}));
            }
            "capture" => {
                let mut facts = surface.capture();
                facts["ok"] = json!(true);
                reply(facts);
            }
            "hide" => {
                let started = Instant::now();
                match surface.hide() {
                    Ok(mut facts) => {
                        facts["ok"] = json!(true);
                        facts["hide_ms"] = json!(started.elapsed().as_secs_f64() * 1000.0);
                        facts["surfaces"] = json!(surface.live());
                        facts["backing_bytes"] = json!(0);
                        reply(facts);
                    }
                    Err(e) => reply(json!({"ok":false,"error":e})),
                }
            }
            "report" => reply(json!({
                "ok":true,"candidate":name,"surfaces":surface.live(),
                "backing_bytes":if surface.live() > 0 { BACKING_BYTES } else { 0 },
                "window_numbers":surface.window_numbers(),"libmalloc":libmalloc(),"images":images(),
            })),
            other => reply(json!({"ok":false,"error":format!("unknown op {other}")})),
        });
    }
}
