//! The host side of the G3 surface: the bounded semantic painter, the
//! surface process manager over the bounded binary IPC, the own-window
//! capture used only for the court, and the court-only event log
//! (`surface-ipc-0.0.1.md`). The host alone owns the target; the process
//! here receives pixels and returns coordinates.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant};

use native_dom_surface::{Body, INPUT_CLICK, INPUT_SCROLL, Message, ProtocolError, Reader, Writer};
use serde_json::{Value, json};

use crate::frame_region::{FrameRegion, RegionError};

pub const FRAME_WIDTH: u16 = 640;
pub const FRAME_HEIGHT: u16 = 400;

/// The frame's dimensions: the product frame is 640 × 400; the attribution
/// court sets smaller ones through a court-only flag.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FrameSize {
    pub width: u16,
    pub height: u16,
}

impl FrameSize {
    pub const DEFAULT: FrameSize = FrameSize {
        width: FRAME_WIDTH,
        height: FRAME_HEIGHT,
    };

    pub fn parse(text: &str) -> Option<FrameSize> {
        let (w, h) = text.split_once('x')?;
        let (width, height) = (w.parse::<u16>().ok()?, h.parse::<u16>().ok()?);
        (width >= 64 && height >= 64 && width <= 1024 && height <= 768)
            .then_some(FrameSize { width, height })
    }
}

/// Where this process stands: physical footprint and RSS from the kernel,
/// libmalloc's in-use and allocated bytes, and the thread count.
pub struct RawSample {
    pub footprint: u64,
    pub rss: u64,
    pub virtual_size: u64,
    pub in_use: usize,
    pub allocated: usize,
    pub blocks: u32,
    pub threads: i32,
}

/// The court-only stage log's sample, as JSON.
pub fn self_sample() -> Value {
    let s = raw_sample();
    json!({
        "footprint":s.footprint,"rss":s.rss,"virtual":s.virtual_size,
        "in_use":s.in_use,"allocated":s.allocated,"blocks":s.blocks,"threads":s.threads,
    })
}

/// Physical footprint and virtual size only, for `owners.surfaces.frame`.
pub fn vm_sample() -> (u64, u64) {
    let s = raw_sample();
    (s.footprint, s.virtual_size)
}

pub fn raw_sample() -> RawSample {
    #[repr(C)]
    struct RusageInfoV2 {
        uuid: [u8; 16],
        user_time: u64,
        system_time: u64,
        pkg_idle_wkups: u64,
        interrupt_wkups: u64,
        pageins: u64,
        wired_size: u64,
        resident_size: u64,
        phys_footprint: u64,
        proc_start_abstime: u64,
        proc_exit_abstime: u64,
        child_user_time: u64,
        child_system_time: u64,
        child_pkg_idle_wkups: u64,
        child_interrupt_wkups: u64,
        child_pageins: u64,
        child_elapsed_abstime: u64,
        diskio_bytesread: u64,
        diskio_byteswritten: u64,
    }
    #[repr(C)]
    struct ProcTaskInfo {
        virtual_size: u64,
        resident_size: u64,
        total_user: u64,
        total_system: u64,
        threads_user: u64,
        threads_system: u64,
        policy: i32,
        faults: i32,
        pageins: i32,
        cow_faults: i32,
        messages_sent: i32,
        messages_received: i32,
        syscalls_mach: i32,
        syscalls_unix: i32,
        csw: i32,
        threadnum: i32,
        numrunning: i32,
        priority: i32,
    }
    #[repr(C)]
    struct MallocStatistics {
        blocks_in_use: u32,
        size_in_use: usize,
        max_size_in_use: usize,
        size_allocated: usize,
    }
    unsafe extern "C" {
        fn getpid() -> i32;
        fn proc_pid_rusage(pid: i32, flavor: i32, buffer: *mut RusageInfoV2) -> i32;
        fn proc_pidinfo(
            pid: i32,
            flavor: i32,
            arg: u64,
            buffer: *mut ProcTaskInfo,
            size: i32,
        ) -> i32;
        fn malloc_zone_statistics(zone: *mut std::ffi::c_void, stats: *mut MallocStatistics);
    }
    let mut usage = RusageInfoV2 {
        uuid: [0; 16],
        user_time: 0,
        system_time: 0,
        pkg_idle_wkups: 0,
        interrupt_wkups: 0,
        pageins: 0,
        wired_size: 0,
        resident_size: 0,
        phys_footprint: 0,
        proc_start_abstime: 0,
        proc_exit_abstime: 0,
        child_user_time: 0,
        child_system_time: 0,
        child_pkg_idle_wkups: 0,
        child_interrupt_wkups: 0,
        child_pageins: 0,
        child_elapsed_abstime: 0,
        diskio_bytesread: 0,
        diskio_byteswritten: 0,
    };
    let mut task = ProcTaskInfo {
        virtual_size: 0,
        resident_size: 0,
        total_user: 0,
        total_system: 0,
        threads_user: 0,
        threads_system: 0,
        policy: 0,
        faults: 0,
        pageins: 0,
        cow_faults: 0,
        messages_sent: 0,
        messages_received: 0,
        syscalls_mach: 0,
        syscalls_unix: 0,
        csw: 0,
        threadnum: 0,
        numrunning: 0,
        priority: 0,
    };
    let mut malloc = MallocStatistics {
        blocks_in_use: 0,
        size_in_use: 0,
        max_size_in_use: 0,
        size_allocated: 0,
    };
    // SAFETY: libproc and libmalloc queries on this process with correctly sized buffers.
    unsafe {
        let pid = getpid();
        proc_pid_rusage(pid, 2, &mut usage);
        proc_pidinfo(
            pid,
            4,
            0,
            &mut task,
            std::mem::size_of::<ProcTaskInfo>() as i32,
        );
        malloc_zone_statistics(std::ptr::null_mut(), &mut malloc);
    }
    RawSample {
        footprint: usage.phys_footprint,
        rss: usage.resident_size,
        virtual_size: task.virtual_size,
        in_use: malloc.size_in_use,
        allocated: malloc.size_allocated,
        blocks: malloc.blocks_in_use,
        threads: task.threadnum,
    }
}
pub const ROW_HEIGHT: usize = 20;
pub const MAX_SCROLL: u64 = 1_000_000;
pub const MAX_SURFACES: usize = 8;
pub const PAINTER: &str = "bounded-semantic-painter";
pub const READY_DEADLINE: Duration = Duration::from_millis(2000);
pub const ACK_DEADLINE: Duration = Duration::from_millis(1000);
pub const CLOSE_DEADLINE: Duration = Duration::from_millis(1000);
/// How long a child that has closed its stdout may take to actually exit
/// before cleanup gives up and kills it. End of file arrives a moment before
/// the process is reapable, and that moment must not be counted as a kill.
pub const EXIT_GRACE: Duration = Duration::from_millis(200);

/// The environment the child needs before it may create a window; the host
/// sets it only under its own double opt-in.
pub const VISIBLE_ENV: &str = "MINICON_SURF_ALLOW_VISIBLE_COURT";

/// Court-only child modes that never touch AppKit: `protocol` and `drain`
/// speak the protocol without a window, `exit` leaves at once,
/// `replay:<script>` is the paired causal court's counterfactual (a bounded
/// input script the child sends after frame acknowledgements).
pub fn is_headless_child_mode(mode: &str) -> bool {
    matches!(mode, "protocol" | "drain" | "exit")
        || mode
            .strip_prefix("replay:")
            .is_some_and(|script| native_dom_surface::ReplayScript::parse(script).is_ok())
}

// ---------------------------------------------------------------- painter

/// One painted row: the node it stands for and where it is in the frame.
#[derive(Debug, Clone)]
pub struct Row {
    pub node: String,
    pub role: String,
    pub y: usize,
    pub height: usize,
}

/// A painted frame with its hit map and the revision it depicts. The
/// pixels live in the surface's own mapping (`frame_region`), never in a
/// zone.
#[derive(Debug)]
pub struct Painting {
    pub pixels: FrameRegion,
    pub rows: Vec<Row>,
    pub revision: u64,
    pub scroll_y: u64,
    pub size: FrameSize,
}

impl Painting {
    pub fn row_at(&self, y: usize) -> Option<&Row> {
        self.rows.iter().find(|r| y >= r.y && y < r.y + r.height)
    }

    pub fn layout_json(&self) -> Value {
        json!({
            "frame":{"width":self.size.width,"height":self.size.height,"row_height":ROW_HEIGHT},
            "revision":self.revision,"scroll_y":self.scroll_y,
            "rows":self.rows.iter().map(|r| json!({"node":r.node,"role":r.role,"y":r.y,"height":r.height,"bar_bgr":role_colour(&r.role)})).collect::<Vec<_>>(),
            "background_bgr":[24,24,28],
        })
    }
}

/// A 5 × 7 bitmap font for the printable subset the painter draws
/// (letters are drawn as capitals); everything else is a box.
fn glyph(c: char) -> [u8; 7] {
    match c.to_ascii_uppercase() {
        'A' => [0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
        'B' => [0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E],
        'C' => [0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E],
        'D' => [0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E],
        'E' => [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F],
        'F' => [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10],
        'G' => [0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F],
        'H' => [0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
        'I' => [0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
        'J' => [0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C],
        'K' => [0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11],
        'L' => [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F],
        'M' => [0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11],
        'N' => [0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11],
        'O' => [0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
        'P' => [0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10],
        'Q' => [0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D],
        'R' => [0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11],
        'S' => [0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E],
        'T' => [0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
        'U' => [0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
        'V' => [0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04],
        'W' => [0x11, 0x11, 0x11, 0x15, 0x15, 0x15, 0x0A],
        'X' => [0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11],
        'Y' => [0x11, 0x11, 0x11, 0x0A, 0x04, 0x04, 0x04],
        'Z' => [0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F],
        '0' => [0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E],
        '1' => [0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E],
        '2' => [0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F],
        '3' => [0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E],
        '4' => [0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02],
        '5' => [0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E],
        '6' => [0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E],
        '7' => [0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08],
        '8' => [0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E],
        '9' => [0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C],
        ' ' => [0; 7],
        '.' => [0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C],
        ',' => [0x00, 0x00, 0x00, 0x00, 0x0C, 0x04, 0x08],
        ':' => [0x00, 0x0C, 0x0C, 0x00, 0x0C, 0x0C, 0x00],
        '-' => [0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00],
        '/' => [0x01, 0x02, 0x02, 0x04, 0x08, 0x08, 0x10],
        '?' => [0x0E, 0x11, 0x01, 0x02, 0x04, 0x00, 0x04],
        '!' => [0x04, 0x04, 0x04, 0x04, 0x04, 0x00, 0x04],
        '(' => [0x02, 0x04, 0x08, 0x08, 0x08, 0x04, 0x02],
        ')' => [0x08, 0x04, 0x02, 0x02, 0x02, 0x04, 0x08],
        '=' => [0x00, 0x00, 0x1F, 0x00, 0x1F, 0x00, 0x00],
        _ => [0x1F, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1F],
    }
}

fn role_colour(role: &str) -> [u8; 3] {
    // BGRA colour of the role bar: links blue, buttons green, headings
    // amber, text grey, other roles purple.
    // Every channel is far from the classifier's midpoint so colour
    // management cannot flip a class.
    match role {
        "link" => [240, 64, 32],
        "button" => [48, 208, 48],
        "heading" => [32, 160, 240],
        "text" => [176, 176, 176],
        "textbox" => [208, 208, 48],
        _ => [208, 48, 176],
    }
}

fn put(pixels: &mut [u8], size: FrameSize, x: usize, y: usize, bgr: [u8; 3]) {
    if x < usize::from(size.width) && y < usize::from(size.height) {
        let at = (y * usize::from(size.width) + x) * 4;
        pixels[at] = bgr[0];
        pixels[at + 1] = bgr[1];
        pixels[at + 2] = bgr[2];
        pixels[at + 3] = 255;
    }
}

fn draw_text(pixels: &mut [u8], size: FrameSize, x0: usize, y0: usize, text: &str, bgr: [u8; 3]) {
    let scale = 2;
    let mut x = x0;
    for c in text.chars().take(48) {
        let rows = glyph(c);
        for (gy, bits) in rows.iter().enumerate() {
            for gx in 0..5 {
                if bits & (0x10 >> gx) != 0 {
                    for sy in 0..scale {
                        for sx in 0..scale {
                            put(pixels, size, x + gx * scale + sx, y0 + gy * scale + sy, bgr);
                        }
                    }
                }
            }
        }
        x += 6 * scale;
        if x + 6 * scale > usize::from(size.width) {
            break;
        }
    }
}

/// Paint the semantic snapshot as rows: role bar, name text, indented by
/// the row index modulo nothing (the snapshot is flat); `scroll_y` shifts
/// the rows up. Not a layout or CSS renderer.
/// Map a fresh frame region and paint into it. A refused or failed mapping
/// is the caller's typed error; no buffer exists in that case.
pub fn paint(
    nodes: &[(String, String, String)],
    scroll_y: u64,
    revision: u64,
    size: FrameSize,
) -> Result<Painting, RegionError> {
    let mut painting = Painting {
        pixels: FrameRegion::map(size)?,
        rows: Vec::new(),
        revision,
        scroll_y,
        size,
    };
    paint_into(&mut painting, nodes, scroll_y, revision);
    Ok(painting)
}

/// Repaint into the frame the surface already owns: the painter writes the
/// mapping in place and allocates no pixel buffer.
pub fn paint_into(
    painting: &mut Painting,
    nodes: &[(String, String, String)],
    scroll_y: u64,
    revision: u64,
) {
    let size = painting.size;
    let pixels = painting.pixels.as_mut_slice();
    for px in pixels.chunks_exact_mut(4) {
        px.copy_from_slice(&[24, 24, 28, 255]);
    }
    // A status strip: the painter's name and the revision, for humans.
    draw_text(
        pixels,
        size,
        6,
        4,
        &format!("{PAINTER} REV {revision} SCROLL {scroll_y}"),
        [200, 200, 200],
    );
    let top = ROW_HEIGHT + 4;
    let scroll = usize::try_from(scroll_y).unwrap_or(usize::MAX);
    let mut rows = Vec::new();
    for (index, (node, role, name)) in nodes.iter().enumerate() {
        let absolute = top + index * ROW_HEIGHT;
        let Some(y) = absolute.checked_sub(scroll) else {
            continue;
        };
        // Rows scrolled into the status strip are gone, not overdrawn.
        if y < top {
            continue;
        }
        if y + ROW_HEIGHT > usize::from(size.height) {
            break;
        }
        let colour = role_colour(role);
        for yy in y..y + ROW_HEIGHT - 2 {
            for xx in 0..8 {
                put(pixels, size, 4 + xx, yy, colour);
            }
        }
        draw_text(
            pixels,
            size,
            18,
            y + 3,
            &format!("{role}: {name}"),
            [230, 230, 230],
        );
        rows.push(Row {
            node: node.clone(),
            role: role.clone(),
            y,
            height: ROW_HEIGHT,
        });
    }
    painting.rows = rows;
    painting.revision = revision;
    painting.scroll_y = scroll_y;
}

// ---------------------------------------------------------- the process

/// Counters the host reports under `owners.surfaces.process`.
#[derive(Debug, Default, Clone)]
pub struct Stats {
    pub spawns_total: u64,
    pub exits_clean_total: u64,
    pub kills_total: u64,
    pub timeouts_total: u64,
    pub protocol_failures_total: u64,
    pub gone_total: u64,
    pub frames_sent_total: u64,
    pub frames_acked_total: u64,
    pub input_events_total: u64,
    pub stale_events_dropped_total: u64,
}

impl Stats {
    pub fn to_json(&self, generation: u32, live: usize) -> Value {
        json!({
            "generation":generation,"live":live,"spawns_total":self.spawns_total,"exits_clean_total":self.exits_clean_total,
            "kills_total":self.kills_total,"timeouts_total":self.timeouts_total,"protocol_failures_total":self.protocol_failures_total,
            "gone_total":self.gone_total,"frames_sent_total":self.frames_sent_total,"frames_acked_total":self.frames_acked_total,
            "input_events_total":self.input_events_total,"stale_events_dropped_total":self.stale_events_dropped_total,
        })
    }
}

/// What the child said when its window existed (court-only facts).
#[derive(Debug, Clone)]
pub struct ReadyInfo {
    pub window_number: i64,
    pub screen_x: i32,
    pub screen_y: i32,
    pub content_width: u16,
    pub content_height: u16,
}

enum Event {
    Message(Message),
    Failure(ProtocolError),
    Eof,
}

/// Why a wait ended. The three failures are different events and are counted
/// differently: a deadline that expired while the child was still answering
/// nothing, a child that left on its own (its stdout reached end of file or
/// the reader thread stopped), and a message that did not decode.
enum Waited {
    Body(Body),
    Timeout,
    Ended,
    Protocol,
}

/// Count a failed wait under the counter that describes it and name the cause
/// for the caller's error text. A child that ended on its own is never a
/// timeout, and a decode failure is never one either.
fn attribute_wait(outcome: Waited, stats: &mut Stats) -> &'static str {
    match outcome {
        Waited::Body(_) | Waited::Timeout => {
            stats.timeouts_total += 1;
            "did not answer before the deadline"
        }
        Waited::Ended => {
            stats.gone_total += 1;
            "exited on its own"
        }
        Waited::Protocol => {
            stats.protocol_failures_total += 1;
            "broke the protocol"
        }
    }
}

/// A human input the child reported, in frame coordinates.
#[derive(Debug, Clone, Copy)]
pub struct Input {
    pub kind: u8,
    pub x: u16,
    pub y: u16,
    pub delta: i16,
}

/// How a surface process ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Exit {
    Protocol,
    Killed,
    Gone,
}

impl Exit {
    pub fn name(self) -> &'static str {
        match self {
            Exit::Protocol => "protocol",
            Exit::Killed => "killed",
            Exit::Gone => "gone",
        }
    }
}

pub struct Teardown {
    pub exit: Exit,
    pub reaped: bool,
    pub ms: u64,
}

pub struct Process {
    size: FrameSize,
    child: Child,
    writer: Writer<std::process::ChildStdin>,
    generation: u32,
    events: mpsc::Receiver<Event>,
    reader: Option<std::thread::JoinHandle<()>>,
    pub ready: ReadyInfo,
    next_frame: u32,
    in_flight: Option<u32>,
    /// A repaint happened while a frame was in flight: after the
    /// acknowledgement the current mapping is written again. No copy.
    resend: bool,
    gone: bool,
    protocol_failure: bool,
    last_error: Option<String>,
}

impl Process {
    /// Spawn the child (posix_spawn through `Command`: absolute path, no
    /// closure, no cwd, no uid/gid), send `HELLO` and the first frame, and
    /// wait for `READY` and the first `FRAME_ACK` under their deadlines.
    #[allow(clippy::too_many_arguments)]
    pub fn spawn(
        binary: &Path,
        generation: u32,
        title: &str,
        first_frame: &[u8],
        size: FrameSize,
        child_mode: Option<&str>,
        visual: bool,
        stats: &mut Stats,
        stage: &mut dyn FnMut(&str),
    ) -> Result<(Process, u64, u64), String> {
        stats.spawns_total += 1;
        let mut command = Command::new(binary);
        command.arg(generation.to_string());
        if let Some(mode) = child_mode {
            // Court-only: a lab-local child mode for the attribution court.
            command.arg(mode);
        }
        // The child creates a window only with this variable; it is handed
        // down only under the host's double opt-in and removed otherwise.
        if visual {
            command.env(VISIBLE_ENV, "1");
        } else {
            command.env_remove(VISIBLE_ENV);
        }
        let mut child = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("surface spawn failed: {}", e.kind()))?;
        stage("after_command_spawn");
        let stdin = child.stdin.take().expect("piped stdin");
        let stdout = child.stdout.take().expect("piped stdout");
        let (sender, events) = mpsc::channel();
        let reader = std::thread::Builder::new()
            .name("surface-reader".into())
            .stack_size(256 * 1024)
            .spawn(move || {
                let mut reader = Reader::new(stdout, generation);
                loop {
                    match reader.read_message() {
                        Ok(message) => {
                            let closed = matches!(message.body, Body::Closed);
                            if sender.send(Event::Message(message)).is_err() || closed {
                                break;
                            }
                        }
                        Err(ProtocolError::Io(std::io::ErrorKind::UnexpectedEof)) => {
                            let _ = sender.send(Event::Eof);
                            break;
                        }
                        Err(error) => {
                            let _ = sender.send(Event::Failure(error));
                            break;
                        }
                    }
                }
            })
            .map_err(|e| format!("surface reader thread: {}", e.kind()))?;
        stage("after_reader_thread");
        let mut process = Process {
            size,
            child,
            writer: Writer::new(stdin, generation),
            generation,
            events,
            reader: Some(reader),
            ready: ReadyInfo {
                window_number: 0,
                screen_x: 0,
                screen_y: 0,
                content_width: 0,
                content_height: 0,
            },
            next_frame: 1,
            in_flight: None,
            resend: false,
            gone: false,
            protocol_failure: false,
            last_error: None,
        };
        let started = Instant::now();
        let hello = process.writer.send(Body::Hello {
            width: size.width,
            height: size.height,
            max_fps: 30,
            queue_max: 1,
            title: title.chars().take(32).collect(),
        });
        if hello.is_err() {
            // The child's stdin is already closed: it left rather than
            // refused, so this is not a kill unless it is somehow still alive.
            if !process.end_after_failure(stats, true) {
                stats.gone_total += 1;
            }
            return Err("surface refused the hello".into());
        }
        let ready_ms = match process.wait_for(started + READY_DEADLINE, |body| {
            matches!(body, Body::Ready { .. })
        }) {
            Waited::Body(Body::Ready {
                window_number,
                screen_x,
                screen_y,
                content_width,
                content_height,
            }) => {
                process.ready = ReadyInfo {
                    window_number,
                    screen_x,
                    screen_y,
                    content_width,
                    content_height,
                };
                started.elapsed().as_millis() as u64
            }
            outcome => {
                let ended = matches!(outcome, Waited::Ended);
                let cause = attribute_wait(outcome, stats);
                process.end_after_failure(stats, ended);
                return Err(format!("surface did not become ready: it {cause}"));
            }
        };
        stage("after_hello_ready");
        let frame_started = Instant::now();
        process.send_frame(first_frame, stats)?;
        match process.wait_for(frame_started + ACK_DEADLINE, |body| {
            matches!(body, Body::FrameAck { .. })
        }) {
            Waited::Body(_) => {}
            outcome => {
                let ended = matches!(outcome, Waited::Ended);
                let cause = attribute_wait(outcome, stats);
                process.end_after_failure(stats, ended);
                return Err(format!(
                    "surface did not acknowledge the first frame: it {cause}"
                ));
            }
        }
        stats.frames_acked_total += 1;
        process.in_flight = None;
        stage("after_first_frame_ack");
        Ok((
            process,
            ready_ms,
            frame_started.elapsed().as_millis() as u64,
        ))
    }

    fn wait_for(&mut self, deadline: Instant, wanted: impl Fn(&Body) -> bool) -> Waited {
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Waited::Timeout;
            }
            match self.events.recv_timeout(remaining) {
                Ok(Event::Message(message)) => {
                    if wanted(&message.body) {
                        return Waited::Body(message.body);
                    }
                    // Anything else arriving early is ignored here; input is
                    // not accepted before the first frame is acknowledged.
                }
                Ok(Event::Failure(error)) => {
                    self.last_error = Some(error.to_string());
                    self.protocol_failure = true;
                    self.gone = true;
                    return Waited::Protocol;
                }
                Ok(Event::Eof) | Err(mpsc::RecvTimeoutError::Disconnected) => {
                    self.gone = true;
                    return Waited::Ended;
                }
                Err(mpsc::RecvTimeoutError::Timeout) => return Waited::Timeout,
            }
        }
    }

    /// Write the frame to the pipe (a synchronous `write_all` that borrows
    /// the mapping; nothing keeps a pointer to it), or mark a resend while
    /// one is in flight: the newest painting wins and is never copied.
    pub fn send_frame(&mut self, pixels: &[u8], stats: &mut Stats) -> Result<(), String> {
        if self.gone {
            return Err("surface is gone".into());
        }
        if self.in_flight.is_some() {
            self.resend = true;
            return Ok(());
        }
        let frame = self.next_frame;
        self.next_frame = self.next_frame.wrapping_add(1);
        self.writer
            .send_frame(frame, self.size.width, self.size.height, pixels)
            .map_err(|e| format!("surface frame refused: {e}"))?;
        stats.frames_sent_total += 1;
        self.in_flight = Some(frame);
        Ok(())
    }

    /// Drain the child's messages: acknowledge frames, collect input, notice
    /// failures. `pixels` is the surface's current mapping, written again
    /// after an acknowledgement when a repaint was pending. Returns the
    /// inputs in arrival order.
    pub fn poll(&mut self, stats: &mut Stats, pixels: &[u8]) -> Vec<Input> {
        let mut inputs = Vec::new();
        loop {
            match self.events.try_recv() {
                Ok(Event::Message(message)) => match message.body {
                    Body::FrameAck { frame } => {
                        if self.in_flight == Some(frame) {
                            self.in_flight = None;
                            stats.frames_acked_total += 1;
                            if std::mem::take(&mut self.resend) {
                                let _ = self.send_frame(pixels, stats);
                            }
                        }
                    }
                    Body::Input {
                        kind, x, y, delta, ..
                    } => inputs.push(Input { kind, x, y, delta }),
                    Body::Error { .. } => {
                        self.protocol_failure = true;
                        self.gone = true;
                    }
                    Body::Closed => self.gone = true,
                    _ => {
                        self.protocol_failure = true;
                        self.gone = true;
                    }
                },
                Ok(Event::Failure(error)) => {
                    self.last_error = Some(error.to_string());
                    self.protocol_failure = true;
                    self.gone = true;
                }
                Ok(Event::Eof) => self.gone = true,
                Err(mpsc::TryRecvError::Empty) => break,
                Err(mpsc::TryRecvError::Disconnected) => {
                    self.gone = true;
                    break;
                }
            }
        }
        if self.gone {
            // A child that ended on its own is reaped right away.
            let _ = self.child.try_wait();
        }
        inputs
    }

    pub fn is_gone(&mut self) -> bool {
        if !self.gone
            && let Ok(Some(_)) = self.child.try_wait()
        {
            self.gone = true;
        }
        self.gone
    }

    pub fn generation(&self) -> u32 {
        self.generation
    }

    /// The protocol rule the child broke, if any (never the bytes).
    pub fn last_error(&self) -> Option<&str> {
        self.last_error.as_deref()
    }

    /// End the child: `CLOSE`, wait for `CLOSED` and the exit under the
    /// deadline, else kill and reap as failure cleanup.
    pub fn hide(mut self, stats: &mut Stats) -> Teardown {
        let started = Instant::now();
        // Nothing of the frame is pending in host memory: the write was
        // synchronous and no resend follows a hide.
        self.resend = false;
        if self.is_gone() {
            let reaped = self.reap(Duration::from_millis(200));
            stats.gone_total += 1;
            if self.protocol_failure {
                stats.protocol_failures_total += 1;
            }
            return Teardown {
                exit: Exit::Gone,
                reaped,
                ms: started.elapsed().as_millis() as u64,
            };
        }
        let outcome = if self.writer.send(Body::Close).is_ok() {
            self.wait_for(started + CLOSE_DEADLINE, |body| {
                matches!(body, Body::Closed)
            })
        } else {
            // The child's stdin is closed: it is already on its way out.
            Waited::Ended
        };
        if matches!(outcome, Waited::Body(_))
            && self.reap(CLOSE_DEADLINE.saturating_sub(started.elapsed()))
        {
            stats.exits_clean_total += 1;
            return Teardown {
                exit: Exit::Protocol,
                reaped: true,
                ms: started.elapsed().as_millis() as u64,
            };
        }
        // `CLOSED` did not complete the exchange: say why it did not, and kill
        // only a child that is still alive.
        let ended = matches!(outcome, Waited::Ended);
        attribute_wait(outcome, stats);
        let killed = self.end_after_failure(stats, ended);
        Teardown {
            exit: if killed { Exit::Killed } else { Exit::Gone },
            reaped: true,
            ms: started.elapsed().as_millis() as u64,
        }
    }

    fn reap(&mut self, within: Duration) -> bool {
        let deadline = Instant::now() + within;
        loop {
            match self.child.try_wait() {
                Ok(Some(_)) => {
                    self.join_reader();
                    return true;
                }
                Ok(None) if Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(5))
                }
                _ => return false,
            }
        }
    }

    /// End a child after a failed exchange and report whether it had to be
    /// killed. A child that already left is only reaped, so `kills_total`
    /// never counts one that ended on its own; `ended` says the wait stopped
    /// because its stdout closed, which precedes the exit by a moment.
    fn end_after_failure(&mut self, stats: &mut Stats, ended: bool) -> bool {
        if (ended && self.reap(EXIT_GRACE)) || self.child.try_wait().ok().flatten().is_some() {
            self.gone = true;
            self.join_reader();
            return false;
        }
        let _ = self.child.kill();
        stats.kills_total += 1;
        let _ = self.child.wait();
        self.gone = true;
        self.join_reader();
        true
    }

    fn join_reader(&mut self) {
        if let Some(reader) = self.reader.take() {
            let _ = reader.join();
        }
    }
}

impl Drop for Process {
    fn drop(&mut self) {
        // Never leave a child unreaped: an unexpected drop still ends it.
        if self.child.try_wait().ok().flatten().is_none() {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
        self.join_reader();
    }
}

// ------------------------------------------------------------ court log

/// The court-only event log: only with `--surface-court-file`, mode 0600,
/// one JSON line per event, removed when the host exits.
pub struct CourtLog {
    path: PathBuf,
    started: Instant,
}

impl CourtLog {
    pub fn create(path: PathBuf) -> std::io::Result<CourtLog> {
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create(true).truncate(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let _ = options.open(&path)?;
        Ok(CourtLog {
            path,
            started: Instant::now(),
        })
    }

    pub fn append(&self, mut event: Value) {
        event["monotonic_ms"] = json!(self.started.elapsed().as_millis() as u64);
        if let Ok(mut file) = std::fs::OpenOptions::new().append(true).open(&self.path) {
            let _ = writeln!(file, "{event}");
        }
    }
}

impl Drop for CourtLog {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

pub const INPUT_KIND_CLICK: u8 = INPUT_CLICK;
pub const INPUT_KIND_SCROLL: u8 = INPUT_SCROLL;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn painter_rows_follow_scroll_and_hit_map() {
        let _guard = crate::frame_region::test_lock();
        let nodes = vec![
            (
                "node_1".to_owned(),
                "heading".to_owned(),
                "Representative".to_owned(),
            ),
            (
                "node_2".to_owned(),
                "button".to_owned(),
                "Continue".to_owned(),
            ),
            ("node_3".to_owned(), "link".to_owned(), "About".to_owned()),
        ];
        let painting = paint(&nodes, 0, 7, FrameSize::DEFAULT).unwrap();
        assert_eq!(painting.pixels.frame_len(), 640 * 400 * 4);
        assert!(painting.pixels.mapped_len() >= painting.pixels.frame_len());
        assert_eq!(painting.rows.len(), 3);
        let button = painting.rows.iter().find(|r| r.role == "button").unwrap();
        assert_eq!(painting.row_at(button.y + 5).unwrap().node, "node_2");
        let scrolled = paint(&nodes, ROW_HEIGHT as u64, 8, FrameSize::DEFAULT).unwrap();
        assert_eq!(scrolled.rows.len(), 2, "the first row scrolled out");
        assert_eq!(scrolled.rows[0].node, "node_2");
        assert_eq!(scrolled.rows[0].y, painting.rows[0].y);
        let far = paint(&nodes, MAX_SCROLL, 9, FrameSize::DEFAULT).unwrap();
        let mut small = paint(&nodes, 0, 1, FrameSize::parse("128x128").unwrap()).unwrap();
        assert_eq!(small.pixels.frame_len(), 128 * 128 * 4);
        // A repaint writes the same mapping in place.
        paint_into(&mut small, &nodes, 0, 2);
        assert_eq!(small.revision, 2);
        assert!(
            paint(
                &nodes,
                0,
                1,
                FrameSize {
                    width: 2048,
                    height: 768
                }
            )
            .is_err(),
            "over the protocol bound: refused before any mapping"
        );
        assert!(FrameSize::parse("32x32").is_none() && FrameSize::parse("2000x10").is_none());
        assert!(far.rows.is_empty());
    }

    #[test]
    fn court_log_is_private_and_removed() {
        let path =
            std::env::temp_dir().join(format!("minicon-surf-court-log-{}", std::process::id()));
        {
            let log = CourtLog::create(path.clone()).unwrap();
            log.append(json!({"event":"shown"}));
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                assert_eq!(
                    std::fs::metadata(&path).unwrap().permissions().mode() & 0o777,
                    0o600
                );
            }
            let text = std::fs::read_to_string(&path).unwrap();
            assert!(text.contains("\"event\":\"shown\"") && text.contains("monotonic_ms"));
        }
        assert!(!path.exists(), "removed on drop");
    }

    /// Every failed show ends with the child reaped and the frame's mapping
    /// unmapped exactly once: a child that exits at once, one that answers
    /// with garbage (protocol error), and one that never answers (timeout).
    #[cfg(target_os = "macos")]
    #[test]
    fn failed_shows_unmap_the_frame_and_reap_the_child() {
        let _guard = crate::frame_region::test_lock();
        let nodes = vec![("node_1".to_owned(), "text".to_owned(), "x".to_owned())];
        let size = FrameSize::parse("128x128").unwrap();
        // Three different failures, counted three different ways. Every child
        // is given the generation as its first argument, so `true` and `cat`
        // both leave before `READY` (`cat` because that argument is not a
        // file); this is the path the replay child's exit 69 takes. `yes`
        // writes bytes that are not a message. `sleep` stays alive and silent
        // past the deadline, its argument being 5 seconds.
        for (binary, generation, expect) in [
            ("/usr/bin/true", 1u32, "ended"),
            ("/bin/cat", 3, "ended"),
            ("/usr/bin/yes", 2, "protocol"),
            ("/bin/sleep", 5, "timeout"),
        ] {
            let before = crate::frame_region::counters();
            let mut stats = Stats::default();
            let painting = paint(&nodes, 0, 1, size).unwrap();
            assert_eq!(
                crate::frame_region::counters().regions_mapped_total,
                before.regions_mapped_total + 1
            );
            let started = Instant::now();
            let result = Process::spawn(
                Path::new(binary),
                generation,
                "test",
                painting.pixels.as_slice(),
                size,
                None,
                false,
                &mut stats,
                &mut |_| {},
            );
            let error = match result {
                Ok(_) => panic!("{binary} must not become a surface"),
                Err(error) => error,
            };
            assert!(started.elapsed() < READY_DEADLINE + Duration::from_millis(500));
            assert_eq!(stats.spawns_total, 1);
            match expect {
                // The child left on its own: no timeout and no kill.
                "ended" => {
                    assert_eq!(stats.gone_total, 1, "{binary}: {error}");
                    assert_eq!(stats.timeouts_total, 0, "{binary}: {error}");
                    assert_eq!(stats.kills_total, 0, "{binary}: {error}");
                    assert!(
                        error.contains("exited on its own") || error.contains("refused the hello"),
                        "{binary}: {error}"
                    );
                }
                // A message that does not decode: a protocol failure, and the
                // child is still alive, so ending it is a real kill.
                "protocol" => {
                    assert_eq!(stats.protocol_failures_total, 1, "{binary}: {error}");
                    assert_eq!(stats.timeouts_total, 0, "{binary}: {error}");
                    assert_eq!(stats.gone_total, 0, "{binary}: {error}");
                    assert_eq!(stats.kills_total, 1, "{binary}: {error}");
                    assert!(error.contains("broke the protocol"), "{binary}: {error}");
                }
                // The deadline expired with the child still running.
                _ => {
                    assert_eq!(stats.timeouts_total, 1, "{binary}: {error}");
                    assert_eq!(stats.gone_total, 0, "{binary}: {error}");
                    assert_eq!(stats.protocol_failures_total, 0, "{binary}: {error}");
                    assert_eq!(stats.kills_total, 1, "{binary}: {error}");
                    assert!(
                        error.contains("did not answer before the deadline"),
                        "{binary}: {error}"
                    );
                }
            }
            drop(painting);
            let after = crate::frame_region::counters();
            assert_eq!(
                after.regions_unmapped_total,
                before.regions_unmapped_total + 1
            );
            assert_eq!(after.regions_mapped_total, before.regions_mapped_total + 1);
        }
    }
}
