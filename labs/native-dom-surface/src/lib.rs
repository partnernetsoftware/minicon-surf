//! The bounded binary IPC between the native host and its surface process
//! (`labs/native-dom/surface-ipc-0.0.1.md`, section 3). Fixed 20-byte
//! headers, a spawn generation and a per-direction sequence on every
//! message, per-kind payload bounds, and strict decoding that fails closed.
//! No allocation beyond the payload itself; no dependency.

use std::io::{self, Read, Write};

pub const MAGIC: &[u8; 4] = b"MCSF";
pub const VERSION: u8 = 1;
pub const HEADER_LEN: usize = 20;
pub const MAX_WIDTH: u16 = 1024;
pub const MAX_HEIGHT: u16 = 768;
pub const MAX_PIXEL_BYTES: usize = 3 * 1024 * 1024;
pub const MAX_TITLE: usize = 32;
pub const MAX_ERROR_TEXT: usize = 128;
pub const MAX_INPUT_PER_SECOND: u32 = 64;
pub const FORMAT_BGRA8: u8 = 0;

pub const KIND_HELLO: u8 = 1;
pub const KIND_FRAME: u8 = 2;
pub const KIND_CLOSE: u8 = 3;
pub const KIND_READY: u8 = 16;
pub const KIND_FRAME_ACK: u8 = 17;
pub const KIND_INPUT: u8 = 18;
pub const KIND_ERROR: u8 = 19;
pub const KIND_CLOSED: u8 = 20;

pub const INPUT_MOUSE_DOWN: u8 = 1;
pub const INPUT_MOUSE_UP: u8 = 2;
pub const INPUT_CLICK: u8 = 3;
pub const INPUT_SCROLL: u8 = 4;
pub const INPUT_KEY: u8 = 5;

/// A decoded message: the header's identity plus the typed payload.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Message {
    pub generation: u32,
    pub sequence: u32,
    pub body: Body,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Body {
    Hello {
        width: u16,
        height: u16,
        max_fps: u8,
        queue_max: u8,
        title: String,
    },
    Frame {
        frame: u32,
        width: u16,
        height: u16,
        format: u8,
        pixels: Vec<u8>,
    },
    Close,
    Ready {
        window_number: i64,
        screen_x: i32,
        screen_y: i32,
        content_width: u16,
        content_height: u16,
    },
    FrameAck {
        frame: u32,
    },
    Input {
        kind: u8,
        x: u16,
        y: u16,
        delta: i16,
        key: u16,
        modifiers: u8,
    },
    Error {
        code: u16,
        text: String,
    },
    Closed,
}

/// Why a message was refused; the reason names the rule, never the bytes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProtocolError {
    Magic,
    Version,
    Flags,
    Kind(u8),
    Bound,
    Sequence,
    Generation,
    Io(io::ErrorKind),
}

impl std::fmt::Display for ProtocolError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ProtocolError::Magic => f.write_str("bad magic"),
            ProtocolError::Version => f.write_str("unsupported version"),
            ProtocolError::Flags => f.write_str("reserved flags set"),
            ProtocolError::Kind(k) => write!(f, "unknown message kind {k}"),
            ProtocolError::Bound => f.write_str("payload out of bounds"),
            ProtocolError::Sequence => f.write_str("sequence did not increase"),
            ProtocolError::Generation => f.write_str("stale generation"),
            ProtocolError::Io(kind) => write!(f, "io: {kind:?}"),
        }
    }
}

impl Body {
    pub fn kind(&self) -> u8 {
        match self {
            Body::Hello { .. } => KIND_HELLO,
            Body::Frame { .. } => KIND_FRAME,
            Body::Close => KIND_CLOSE,
            Body::Ready { .. } => KIND_READY,
            Body::FrameAck { .. } => KIND_FRAME_ACK,
            Body::Input { .. } => KIND_INPUT,
            Body::Error { .. } => KIND_ERROR,
            Body::Closed => KIND_CLOSED,
        }
    }

    fn payload(&self) -> Result<Vec<u8>, ProtocolError> {
        let mut out = Vec::new();
        match self {
            Body::Hello {
                width,
                height,
                max_fps,
                queue_max,
                title,
            } => {
                if *width == 0
                    || *width > MAX_WIDTH
                    || *height == 0
                    || *height > MAX_HEIGHT
                    || *max_fps > 30
                    || *queue_max != 1
                    || title.len() > MAX_TITLE
                    || !title.is_ascii()
                {
                    return Err(ProtocolError::Bound);
                }
                out.extend_from_slice(&width.to_be_bytes());
                out.extend_from_slice(&height.to_be_bytes());
                out.push(*max_fps);
                out.push(*queue_max);
                out.push(title.len() as u8);
                out.extend_from_slice(title.as_bytes());
            }
            Body::Frame {
                frame,
                width,
                height,
                format,
                pixels,
            } => {
                let expected = usize::from(*width) * usize::from(*height) * 4;
                if *width == 0
                    || *width > MAX_WIDTH
                    || *height == 0
                    || *height > MAX_HEIGHT
                    || *format != FORMAT_BGRA8
                    || pixels.len() != expected
                    || expected > MAX_PIXEL_BYTES
                {
                    return Err(ProtocolError::Bound);
                }
                out.extend_from_slice(&frame.to_be_bytes());
                out.extend_from_slice(&width.to_be_bytes());
                out.extend_from_slice(&height.to_be_bytes());
                out.push(*format);
                out.extend_from_slice(pixels);
            }
            Body::Close | Body::Closed => {}
            Body::Ready {
                window_number,
                screen_x,
                screen_y,
                content_width,
                content_height,
            } => {
                out.extend_from_slice(&window_number.to_be_bytes());
                out.extend_from_slice(&screen_x.to_be_bytes());
                out.extend_from_slice(&screen_y.to_be_bytes());
                out.extend_from_slice(&content_width.to_be_bytes());
                out.extend_from_slice(&content_height.to_be_bytes());
            }
            Body::FrameAck { frame } => out.extend_from_slice(&frame.to_be_bytes()),
            Body::Input {
                kind,
                x,
                y,
                delta,
                key,
                modifiers,
            } => {
                if !(INPUT_MOUSE_DOWN..=INPUT_KEY).contains(kind) {
                    return Err(ProtocolError::Bound);
                }
                out.push(*kind);
                out.extend_from_slice(&x.to_be_bytes());
                out.extend_from_slice(&y.to_be_bytes());
                out.extend_from_slice(&delta.to_be_bytes());
                out.extend_from_slice(&key.to_be_bytes());
                out.push(*modifiers);
            }
            Body::Error { code, text } => {
                if text.len() > MAX_ERROR_TEXT || !text.is_ascii() {
                    return Err(ProtocolError::Bound);
                }
                out.extend_from_slice(&code.to_be_bytes());
                out.push(text.len() as u8);
                out.extend_from_slice(text.as_bytes());
            }
        }
        Ok(out)
    }

    fn decode(kind: u8, payload: &[u8]) -> Result<Body, ProtocolError> {
        fn u16_at(p: &[u8], at: usize) -> Result<u16, ProtocolError> {
            p.get(at..at + 2)
                .map(|b| u16::from_be_bytes([b[0], b[1]]))
                .ok_or(ProtocolError::Bound)
        }
        fn i16_at(p: &[u8], at: usize) -> Result<i16, ProtocolError> {
            u16_at(p, at).map(|v| v as i16)
        }
        fn u32_at(p: &[u8], at: usize) -> Result<u32, ProtocolError> {
            p.get(at..at + 4)
                .map(|b| u32::from_be_bytes([b[0], b[1], b[2], b[3]]))
                .ok_or(ProtocolError::Bound)
        }
        fn i32_at(p: &[u8], at: usize) -> Result<i32, ProtocolError> {
            u32_at(p, at).map(|v| v as i32)
        }
        match kind {
            KIND_HELLO => {
                let width = u16_at(payload, 0)?;
                let height = u16_at(payload, 2)?;
                let max_fps = *payload.get(4).ok_or(ProtocolError::Bound)?;
                let queue_max = *payload.get(5).ok_or(ProtocolError::Bound)?;
                let len = usize::from(*payload.get(6).ok_or(ProtocolError::Bound)?);
                let title = payload.get(7..7 + len).ok_or(ProtocolError::Bound)?;
                if payload.len() != 7 + len || len > MAX_TITLE || !title.is_ascii() {
                    return Err(ProtocolError::Bound);
                }
                let body = Body::Hello {
                    width,
                    height,
                    max_fps,
                    queue_max,
                    title: String::from_utf8_lossy(title).into_owned(),
                };
                body.payload()?;
                Ok(body)
            }
            KIND_FRAME => {
                let frame = u32_at(payload, 0)?;
                let width = u16_at(payload, 4)?;
                let height = u16_at(payload, 6)?;
                let format = *payload.get(8).ok_or(ProtocolError::Bound)?;
                let pixels = payload.get(9..).ok_or(ProtocolError::Bound)?.to_vec();
                let body = Body::Frame {
                    frame,
                    width,
                    height,
                    format,
                    pixels,
                };
                body.payload()?;
                Ok(body)
            }
            KIND_CLOSE if payload.is_empty() => Ok(Body::Close),
            KIND_CLOSED if payload.is_empty() => Ok(Body::Closed),
            KIND_READY if payload.len() == 20 => Ok(Body::Ready {
                window_number: i64::from_be_bytes(payload[0..8].try_into().expect("8 bytes")),
                screen_x: i32_at(payload, 8)?,
                screen_y: i32_at(payload, 12)?,
                content_width: u16_at(payload, 16)?,
                content_height: u16_at(payload, 18)?,
            }),
            KIND_FRAME_ACK if payload.len() == 4 => Ok(Body::FrameAck {
                frame: u32_at(payload, 0)?,
            }),
            KIND_INPUT if payload.len() == 10 => {
                let body = Body::Input {
                    kind: payload[0],
                    x: u16_at(payload, 1)?,
                    y: u16_at(payload, 3)?,
                    delta: i16_at(payload, 5)?,
                    key: u16_at(payload, 7)?,
                    modifiers: payload[9],
                };
                body.payload()?;
                Ok(body)
            }
            KIND_ERROR => {
                let code = u16_at(payload, 0)?;
                let len = usize::from(*payload.get(2).ok_or(ProtocolError::Bound)?);
                let text = payload.get(3..3 + len).ok_or(ProtocolError::Bound)?;
                if payload.len() != 3 + len || len > MAX_ERROR_TEXT || !text.is_ascii() {
                    return Err(ProtocolError::Bound);
                }
                Ok(Body::Error {
                    code,
                    text: String::from_utf8_lossy(text).into_owned(),
                })
            }
            KIND_CLOSE | KIND_CLOSED | KIND_READY | KIND_FRAME_ACK | KIND_INPUT => {
                Err(ProtocolError::Bound)
            }
            other => Err(ProtocolError::Kind(other)),
        }
    }
}

/// Encode one message: header then payload.
pub fn encode(message: &Message) -> Result<Vec<u8>, ProtocolError> {
    let payload = message.body.payload()?;
    let mut out = Vec::with_capacity(HEADER_LEN + payload.len());
    out.extend_from_slice(MAGIC);
    out.push(VERSION);
    out.push(message.body.kind());
    out.push(0);
    out.push(0);
    out.extend_from_slice(&message.generation.to_be_bytes());
    out.extend_from_slice(&message.sequence.to_be_bytes());
    out.extend_from_slice(&(payload.len() as u32).to_be_bytes());
    out.extend_from_slice(&payload);
    Ok(out)
}

/// A strict reader: one message per call, the header validated before the
/// payload is read, the sequence checked against the last one seen and the
/// generation against the expected one.
pub struct Reader<R: Read> {
    inner: R,
    generation: u32,
    last_sequence: Option<u32>,
}

impl<R: Read> Reader<R> {
    pub fn new(inner: R, generation: u32) -> Self {
        Reader {
            inner,
            generation,
            last_sequence: None,
        }
    }

    pub fn read_message(&mut self) -> Result<Message, ProtocolError> {
        let mut header = [0u8; HEADER_LEN];
        self.inner
            .read_exact(&mut header)
            .map_err(|e| ProtocolError::Io(e.kind()))?;
        if &header[0..4] != MAGIC {
            return Err(ProtocolError::Magic);
        }
        if header[4] != VERSION {
            return Err(ProtocolError::Version);
        }
        if header[6] != 0 || header[7] != 0 {
            return Err(ProtocolError::Flags);
        }
        let kind = header[5];
        let generation = u32::from_be_bytes([header[8], header[9], header[10], header[11]]);
        let sequence = u32::from_be_bytes([header[12], header[13], header[14], header[15]]);
        let length = u32::from_be_bytes([header[16], header[17], header[18], header[19]]) as usize;
        if length > HEADER_LEN + MAX_PIXEL_BYTES + 16 {
            return Err(ProtocolError::Bound);
        }
        if generation != self.generation {
            return Err(ProtocolError::Generation);
        }
        if self.last_sequence.is_some_and(|last| sequence <= last) {
            return Err(ProtocolError::Sequence);
        }
        let mut payload = vec![0u8; length];
        self.inner
            .read_exact(&mut payload)
            .map_err(|e| ProtocolError::Io(e.kind()))?;
        let body = Body::decode(kind, &payload)?;
        self.last_sequence = Some(sequence);
        Ok(Message {
            generation,
            sequence,
            body,
        })
    }
}

/// A writer that stamps generation and an increasing sequence.
pub struct Writer<W: Write> {
    inner: W,
    generation: u32,
    next_sequence: u32,
}

impl<W: Write> Writer<W> {
    pub fn new(inner: W, generation: u32) -> Self {
        Writer {
            inner,
            generation,
            next_sequence: 1,
        }
    }

    /// Send a frame without building an intermediate copy of the pixels:
    /// header and fixed fields first, then the pixel slice as it is.
    pub fn send_frame(
        &mut self,
        frame: u32,
        width: u16,
        height: u16,
        pixels: &[u8],
    ) -> Result<u32, ProtocolError> {
        let expected = usize::from(width) * usize::from(height) * 4;
        if width == 0
            || width > MAX_WIDTH
            || height == 0
            || height > MAX_HEIGHT
            || pixels.len() != expected
            || expected > MAX_PIXEL_BYTES
        {
            return Err(ProtocolError::Bound);
        }
        let sequence = self.next_sequence;
        let mut head = Vec::with_capacity(HEADER_LEN + 9);
        head.extend_from_slice(MAGIC);
        head.push(VERSION);
        head.push(KIND_FRAME);
        head.push(0);
        head.push(0);
        head.extend_from_slice(&self.generation.to_be_bytes());
        head.extend_from_slice(&sequence.to_be_bytes());
        head.extend_from_slice(&((9 + pixels.len()) as u32).to_be_bytes());
        head.extend_from_slice(&frame.to_be_bytes());
        head.extend_from_slice(&width.to_be_bytes());
        head.extend_from_slice(&height.to_be_bytes());
        head.push(FORMAT_BGRA8);
        self.inner
            .write_all(&head)
            .and_then(|()| self.inner.write_all(pixels))
            .and_then(|()| self.inner.flush())
            .map_err(|e| ProtocolError::Io(e.kind()))?;
        self.next_sequence = self.next_sequence.wrapping_add(1);
        Ok(sequence)
    }

    pub fn send(&mut self, body: Body) -> Result<u32, ProtocolError> {
        let sequence = self.next_sequence;
        let bytes = encode(&Message {
            generation: self.generation,
            sequence,
            body,
        })?;
        self.inner
            .write_all(&bytes)
            .and_then(|()| self.inner.flush())
            .map_err(|e| ProtocolError::Io(e.kind()))?;
        self.next_sequence = self.next_sequence.wrapping_add(1);
        Ok(sequence)
    }
}

/// A court-only replay script for the headless counterfactual child
/// (`surface-paired-causal-court-0.0.1.md` §4): after the acknowledgement
/// of frame `frame` the child sends the event. Bounded: at most
/// `MAX_REPLAY_BYTES` of ASCII and `MAX_REPLAY_EVENTS` events, kinds
/// `click` (delta 0) and `scroll` (non-zero delta), coordinates inside the
/// protocol's frame bounds. Format: `frame:kind:x:y:delta;...`.
pub const MAX_REPLAY_BYTES: usize = 256;
pub const MAX_REPLAY_EVENTS: usize = 16;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ReplayEvent {
    pub frame: u32,
    pub kind: u8,
    pub x: u16,
    pub y: u16,
    pub delta: i16,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct ReplayScript {
    pub events: Vec<ReplayEvent>,
}

impl ReplayScript {
    pub fn parse(text: &str) -> Result<ReplayScript, ProtocolError> {
        if text.len() > MAX_REPLAY_BYTES || !text.is_ascii() {
            return Err(ProtocolError::Bound);
        }
        let mut events = Vec::new();
        for item in text.split(';').filter(|s| !s.is_empty()) {
            let parts: Vec<&str> = item.split(':').collect();
            if parts.len() != 5 {
                return Err(ProtocolError::Bound);
            }
            let frame: u32 = parts[0].parse().map_err(|_| ProtocolError::Bound)?;
            let kind = match parts[1] {
                "click" => INPUT_CLICK,
                "scroll" => INPUT_SCROLL,
                _ => return Err(ProtocolError::Bound),
            };
            let x: u16 = parts[2].parse().map_err(|_| ProtocolError::Bound)?;
            let y: u16 = parts[3].parse().map_err(|_| ProtocolError::Bound)?;
            let delta: i16 = parts[4].parse().map_err(|_| ProtocolError::Bound)?;
            if frame == 0
                || x >= MAX_WIDTH
                || y >= MAX_HEIGHT
                || (kind == INPUT_CLICK && delta != 0)
                || (kind == INPUT_SCROLL && delta == 0)
            {
                return Err(ProtocolError::Bound);
            }
            events.push(ReplayEvent {
                frame,
                kind,
                x,
                y,
                delta,
            });
            if events.len() > MAX_REPLAY_EVENTS {
                return Err(ProtocolError::Bound);
            }
        }
        Ok(ReplayScript { events })
    }

    /// The events bound to one frame, in script order.
    pub fn for_frame(&self, frame: u32) -> impl Iterator<Item = &ReplayEvent> {
        self.events.iter().filter(move |e| e.frame == frame)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn round_trip(body: Body) -> Message {
        let bytes = encode(&Message {
            generation: 7,
            sequence: 3,
            body,
        })
        .unwrap();
        Reader::new(std::io::Cursor::new(bytes), 7)
            .read_message()
            .unwrap()
    }

    #[test]
    fn every_kind_round_trips() {
        let hello = Body::Hello {
            width: 640,
            height: 400,
            max_fps: 30,
            queue_max: 1,
            title: "surface_1".into(),
        };
        assert_eq!(round_trip(hello.clone()).body, hello);
        let frame = Body::Frame {
            frame: 9,
            width: 2,
            height: 2,
            format: FORMAT_BGRA8,
            pixels: vec![1u8; 16],
        };
        assert_eq!(round_trip(frame.clone()).body, frame);
        for body in [
            Body::Close,
            Body::Closed,
            Body::Ready {
                window_number: -5,
                screen_x: 80,
                screen_y: -40,
                content_width: 640,
                content_height: 400,
            },
            Body::FrameAck { frame: 9 },
            Body::Input {
                kind: INPUT_SCROLL,
                x: 10,
                y: 20,
                delta: -240,
                key: 0,
                modifiers: 0,
            },
            Body::Error {
                code: 3,
                text: "no window".into(),
            },
        ] {
            assert_eq!(round_trip(body.clone()).body, body);
        }
    }

    #[test]
    fn bounds_and_identity_fail_closed() {
        let too_wide = Body::Frame {
            frame: 1,
            width: MAX_WIDTH + 1,
            height: 1,
            format: FORMAT_BGRA8,
            pixels: vec![0; (usize::from(MAX_WIDTH) + 1) * 4],
        };
        assert!(
            encode(&Message {
                generation: 1,
                sequence: 1,
                body: too_wide
            })
            .is_err()
        );
        let short = Body::Frame {
            frame: 1,
            width: 2,
            height: 2,
            format: FORMAT_BGRA8,
            pixels: vec![0; 15],
        };
        assert!(
            encode(&Message {
                generation: 1,
                sequence: 1,
                body: short
            })
            .is_err()
        );
        let good = encode(&Message {
            generation: 1,
            sequence: 1,
            body: Body::Close,
        })
        .unwrap();
        let mut bad = good.clone();
        bad[0] = b'X';
        assert_eq!(
            Reader::new(std::io::Cursor::new(bad), 1)
                .read_message()
                .unwrap_err(),
            ProtocolError::Magic
        );
        let mut bad = good.clone();
        bad[4] = 9;
        assert_eq!(
            Reader::new(std::io::Cursor::new(bad), 1)
                .read_message()
                .unwrap_err(),
            ProtocolError::Version
        );
        let mut bad = good.clone();
        bad[6] = 1;
        assert_eq!(
            Reader::new(std::io::Cursor::new(bad), 1)
                .read_message()
                .unwrap_err(),
            ProtocolError::Flags
        );
        let mut bad = good.clone();
        bad[5] = 99;
        assert_eq!(
            Reader::new(std::io::Cursor::new(bad), 1)
                .read_message()
                .unwrap_err(),
            ProtocolError::Kind(99)
        );
        assert_eq!(
            Reader::new(std::io::Cursor::new(good.clone()), 2)
                .read_message()
                .unwrap_err(),
            ProtocolError::Generation
        );
        // A sequence that does not increase is refused.
        let mut two = encode(&Message {
            generation: 1,
            sequence: 5,
            body: Body::Close,
        })
        .unwrap();
        two.extend(
            encode(&Message {
                generation: 1,
                sequence: 5,
                body: Body::Close,
            })
            .unwrap(),
        );
        let mut reader = Reader::new(std::io::Cursor::new(two), 1);
        assert!(reader.read_message().is_ok());
        assert_eq!(reader.read_message().unwrap_err(), ProtocolError::Sequence);
        // A truncated payload is an io failure, not a partial message.
        let frame = encode(&Message {
            generation: 1,
            sequence: 1,
            body: Body::Frame {
                frame: 1,
                width: 1,
                height: 1,
                format: FORMAT_BGRA8,
                pixels: vec![0; 4],
            },
        })
        .unwrap();
        assert!(matches!(
            Reader::new(std::io::Cursor::new(frame[..frame.len() - 1].to_vec()), 1).read_message(),
            Err(ProtocolError::Io(_))
        ));
        // send_frame produces exactly what encode(Frame) produces.
        let mut direct = Vec::new();
        Writer::new(&mut direct, 4)
            .send_frame(3, 2, 1, &[9u8; 8])
            .unwrap();
        let encoded = encode(&Message {
            generation: 4,
            sequence: 1,
            body: Body::Frame {
                frame: 3,
                width: 2,
                height: 1,
                format: FORMAT_BGRA8,
                pixels: vec![9u8; 8],
            },
        })
        .unwrap();
        assert_eq!(direct, encoded);
        // A writer stamps increasing sequences under one generation.
        let mut sink = Vec::new();
        {
            let mut writer = Writer::new(&mut sink, 4);
            assert_eq!(writer.send(Body::Close).unwrap(), 1);
            assert_eq!(writer.send(Body::Closed).unwrap(), 2);
        }
        let mut reader = Reader::new(std::io::Cursor::new(sink), 4);
        assert_eq!(reader.read_message().unwrap().sequence, 1);
        assert_eq!(reader.read_message().unwrap().sequence, 2);
    }

    #[test]
    fn replay_scripts_are_bounded_and_typed() {
        let script =
            ReplayScript::parse("1:click:100:70:0;2:scroll:100:70:20;3:scroll:100:70:-20").unwrap();
        assert_eq!(script.events.len(), 3);
        assert_eq!(script.for_frame(2).count(), 1);
        assert_eq!(script.for_frame(2).next().unwrap().kind, INPUT_SCROLL);
        assert!(ReplayScript::parse("").unwrap().events.is_empty());
        for bad in [
            "0:click:1:1:0",
            "1:click:1:1:5",
            "1:scroll:1:1:0",
            "1:tap:1:1:0",
            "1:click:1024:1:0",
            "1:click:1:768:0",
            "1:click:1:1",
            "1:click:1:1:0:9",
        ] {
            assert!(ReplayScript::parse(bad).is_err(), "{bad}");
        }
        let many = (1..=17)
            .map(|i| format!("{i}:click:1:1:0"))
            .collect::<Vec<_>>()
            .join(";");
        assert!(ReplayScript::parse(&many).is_err(), "17 events");
        let long = "1:click:1:1:0;".repeat(20);
        assert!(long.len() > MAX_REPLAY_BYTES && ReplayScript::parse(&long).is_err());
    }
}
