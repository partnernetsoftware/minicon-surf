//! The native route's surface process (G3, macOS): one window over a CPU
//! bitmap, fed with bounded frames by the host over its stdin pipe and
//! answering bounded input over its stdout pipe. It owns no authority
//! object: no target, realm, profile, URL, cookie or node reference ever
//! reaches it. Direct Cocoa through objc2; no Wry, winit or softbuffer.
//!
//! Protocol: `native_dom_surface` (the crate's library). The host spawns
//! this binary with one argument, the spawn generation, and the child
//! refuses to serve if any descriptor beyond stdio is open.

use std::io::{self, Write};
use std::sync::mpsc;
use std::time::{Duration, Instant};

use native_dom_surface::{
    Body, INPUT_CLICK, INPUT_SCROLL, MAX_INPUT_PER_SECOND, Message, ProtocolError, Reader, Writer,
};
use objc2::rc::Retained;
use objc2::{AnyThread, MainThreadMarker, MainThreadOnly};
use objc2_app_kit::{
    NSApplication, NSApplicationActivationPolicy, NSBackingStoreType, NSBitmapImageRep,
    NSCalibratedRGBColorSpace, NSEvent, NSEventMask, NSEventType, NSImage, NSImageView, NSScreen,
    NSWindow, NSWindowStyleMask,
};
use objc2_foundation::{NSDate, NSDefaultRunLoopMode, NSPoint, NSRect, NSSize, NSString};

const EXIT_PROTOCOL: i32 = 65;
const EXIT_DESCRIPTORS: i32 = 66;
const EXIT_NO_MAIN_THREAD: i32 = 67;

/// Descriptors open beyond 0, 1 and 2: the whitelist check.
fn open_descriptors_beyond_stdio() -> u16 {
    let mut count = 0u16;
    for fd in 3..1024 {
        // SAFETY: F_GETFD only queries the descriptor flags.
        if unsafe { libc::fcntl(fd, libc::F_GETFD) } != -1 {
            count += 1;
        }
    }
    count
}

struct Window {
    window: Retained<NSWindow>,
    view: Retained<NSImageView>,
    image: Option<Retained<NSImage>>,
    rep: Option<Retained<NSBitmapImageRep>>,
    backing: Option<Vec<u8>>,
    width: u16,
    height: u16,
}

impl Window {
    fn create(mtm: MainThreadMarker, width: u16, height: u16, title: &str) -> Window {
        let size = NSSize::new(f64::from(width), f64::from(height));
        let view = NSImageView::initWithFrame(
            NSImageView::alloc(mtm),
            NSRect::new(NSPoint::new(0.0, 0.0), size),
        );
        let style = NSWindowStyleMask::Titled | NSWindowStyleMask::Closable;
        // SAFETY: created and used on the main thread; `defer` false creates
        // the backing store now so its cost is measured while shown.
        let window = unsafe {
            NSWindow::initWithContentRect_styleMask_backing_defer(
                NSWindow::alloc(mtm),
                NSRect::new(NSPoint::new(80.0, 80.0), size),
                style,
                NSBackingStoreType::Buffered,
                false,
            )
        };
        // SAFETY: the window is owned by `Retained` and released when that drops.
        unsafe { window.setReleasedWhenClosed(false) };
        window.setTitle(&NSString::from_str(title));
        window.setContentView(Some(&view));
        // A floating level keeps the surface above ordinary windows without
        // activating the process or stealing focus.
        window.setLevel(3);
        window.makeKeyAndOrderFront(None);
        Window {
            window,
            view,
            image: None,
            rep: None,
            backing: None,
            width,
            height,
        }
    }

    /// Replace the bitmap with a new frame (BGRA in, RGBA to AppKit).
    fn present(&mut self, width: u16, height: u16, pixels: &[u8]) -> bool {
        if width != self.width || height != self.height {
            return false;
        }
        let mut backing = Vec::with_capacity(pixels.len());
        for px in pixels.chunks_exact(4) {
            backing.extend_from_slice(&[px[2], px[1], px[0], px[3]]);
        }
        let mut planes: [*mut u8; 5] = [
            backing.as_mut_ptr(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        ];
        // SAFETY: the plane pointer stays valid while `backing` lives in this
        // struct, and the parameters describe exactly that buffer.
        let rep = unsafe {
            NSBitmapImageRep::initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel(
                NSBitmapImageRep::alloc(),
                planes.as_mut_ptr(),
                width as isize,
                height as isize,
                8,
                4,
                true,
                false,
                NSCalibratedRGBColorSpace,
                width as isize * 4,
                32,
            )
        };
        let Some(rep) = rep else {
            return false;
        };
        let image = NSImage::initWithSize(
            NSImage::alloc(),
            NSSize::new(f64::from(width), f64::from(height)),
        );
        image.addRepresentation(&rep);
        self.view.setImage(Some(&image));
        self.backing = Some(backing);
        self.rep = Some(rep);
        self.image = Some(image);
        true
    }

    /// The content rectangle in CoreGraphics screen coordinates (top-left
    /// origin of the primary display), for the court's input placement.
    fn ready(&self, mtm: MainThreadMarker) -> Body {
        let frame = self.window.frame();
        let content = self.window.contentRectForFrameRect(frame);
        let screen_height = NSScreen::mainScreen(mtm)
            .map(|s| s.frame().size.height)
            .unwrap_or(0.0);
        Body::Ready {
            window_number: self.window.windowNumber() as i64,
            screen_x: content.origin.x as i32,
            screen_y: (screen_height - (content.origin.y + content.size.height)) as i32,
            content_width: content.size.width as u16,
            content_height: content.size.height as u16,
        }
    }

    fn is_ours(&self, event: &NSEvent, mtm: MainThreadMarker) -> bool {
        event
            .window(mtm)
            .is_some_and(|w| std::ptr::eq(&*w as *const NSWindow, &*self.window as *const NSWindow))
    }

    fn close(mut self) {
        self.window.close();
        self.window.setContentView(None);
        self.view.setImage(None);
        drop(self.image.take());
        drop(self.rep.take());
        drop(self.backing.take());
    }
}

struct Limiter {
    window_start: Instant,
    sent: u32,
}

impl Limiter {
    fn allow(&mut self) -> bool {
        if self.window_start.elapsed() >= Duration::from_secs(1) {
            self.window_start = Instant::now();
            self.sent = 0;
        }
        if self.sent >= MAX_INPUT_PER_SECOND {
            return false;
        }
        self.sent += 1;
        true
    }
}

fn main() {
    let generation: u32 = std::env::args()
        .nth(1)
        .and_then(|g| g.parse().ok())
        .unwrap_or(0);
    if open_descriptors_beyond_stdio() > 0 {
        std::process::exit(EXIT_DESCRIPTORS);
    }
    let Some(mtm) = MainThreadMarker::new() else {
        std::process::exit(EXIT_NO_MAIN_THREAD);
    };
    let app = NSApplication::sharedApplication(mtm);
    app.setActivationPolicy(NSApplicationActivationPolicy::Accessory);

    // The host's messages arrive on a reader thread; the main thread owns
    // the window and pumps AppKit between messages.
    let (sender, receiver) = mpsc::channel::<Result<Message, ProtocolError>>();
    std::thread::spawn(move || {
        let mut reader = Reader::new(io::stdin().lock(), generation);
        loop {
            match reader.read_message() {
                Ok(message) => {
                    let close = matches!(message.body, Body::Close);
                    if sender.send(Ok(message)).is_err() || close {
                        break;
                    }
                }
                Err(error) => {
                    let _ = sender.send(Err(error));
                    break;
                }
            }
        }
    });
    let mut writer = Writer::new(io::stdout().lock(), generation);
    let mut window: Option<Window> = None;
    let mut limiter = Limiter {
        window_start: Instant::now(),
        sent: 0,
    };
    let mut pressed_at: Option<(u16, u16)> = None;
    let exit_code = loop {
        let outcome: Result<Option<i32>, ProtocolError> = objc2::rc::autoreleasepool(|_| {
            // 1. Messages from the host.
            loop {
                match receiver.try_recv() {
                    Ok(Ok(message)) => match message.body {
                        Body::Hello {
                            width,
                            height,
                            title,
                            ..
                        } => {
                            if window.is_some() {
                                return Err(ProtocolError::Bound);
                            }
                            let created = Window::create(mtm, width, height, &title);
                            let ready = created.ready(mtm);
                            window = Some(created);
                            writer.send(ready)?;
                        }
                        Body::Frame {
                            frame,
                            width,
                            height,
                            pixels,
                            ..
                        } => {
                            let Some(target) = window.as_mut() else {
                                return Err(ProtocolError::Bound);
                            };
                            if !target.present(width, height, &pixels) {
                                writer.send(Body::Error {
                                    code: 2,
                                    text: "frame size differs from the window".into(),
                                })?;
                                return Err(ProtocolError::Bound);
                            }
                            writer.send(Body::FrameAck { frame })?;
                        }
                        Body::Close => {
                            if let Some(open) = window.take() {
                                open.close();
                            }
                            writer.send(Body::Closed)?;
                            return Ok(Some(0));
                        }
                        _ => return Err(ProtocolError::Bound),
                    },
                    Ok(Err(error)) => return Err(error),
                    Err(mpsc::TryRecvError::Empty) => break,
                    Err(mpsc::TryRecvError::Disconnected) => return Ok(Some(0)),
                }
            }
            // 2. AppKit events: translate the ones on the own window into input.
            let mut pumped = 0;
            loop {
                // SAFETY: a non-blocking event fetch with a past date on the main thread.
                let event = unsafe {
                    app.nextEventMatchingMask_untilDate_inMode_dequeue(
                        NSEventMask::Any,
                        Some(&NSDate::distantPast()),
                        NSDefaultRunLoopMode,
                        true,
                    )
                };
                let Some(event) = event else { break };
                if let Some(open) = window.as_ref()
                    && open.is_ours(&event, mtm)
                {
                    let location = event.locationInWindow();
                    let x = location.x.clamp(0.0, f64::from(open.width)) as u16;
                    let y = (f64::from(open.height) - location.y).clamp(0.0, f64::from(open.height))
                        as u16;
                    match event.r#type() {
                        NSEventType::LeftMouseDown => pressed_at = Some((x, y)),
                        NSEventType::LeftMouseUp => {
                            if pressed_at.take().is_some() && limiter.allow() {
                                writer.send(Body::Input {
                                    kind: INPUT_CLICK,
                                    x,
                                    y,
                                    delta: 0,
                                    key: 0,
                                    modifiers: 0,
                                })?;
                            }
                        }
                        NSEventType::ScrollWheel => {
                            let delta = event.scrollingDeltaY();
                            let rounded = (-delta).round().clamp(-32_000.0, 32_000.0) as i16;
                            if rounded != 0 && limiter.allow() {
                                writer.send(Body::Input {
                                    kind: INPUT_SCROLL,
                                    x,
                                    y,
                                    delta: rounded,
                                    key: 0,
                                    modifiers: 0,
                                })?;
                            }
                        }
                        _ => {}
                    }
                }
                app.sendEvent(&event);
                pumped += 1;
                if pumped > 1000 {
                    break;
                }
            }
            Ok(None)
        });
        match outcome {
            Ok(Some(code)) => break code,
            Ok(None) => std::thread::sleep(Duration::from_millis(2)),
            Err(_) => {
                if let Some(open) = window.take() {
                    open.close();
                }
                break EXIT_PROTOCOL;
            }
        }
    };
    drop(writer);
    let _ = io::stdout().flush();
    // AppKit registers exit-time handlers that can wait on the run loop; the
    // protocol is complete and stdout is flushed, so leave without them.
    // SAFETY: _exit terminates the process immediately; nothing is left to run.
    unsafe { libc::_exit(exit_code) };
}
