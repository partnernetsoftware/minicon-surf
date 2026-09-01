use minicon_surf_synthetic_control::{
    ControlState, MAX_REQUEST_BYTES, Response, cdp, parse_request,
};
use serde_json::json;
use std::fs;
use std::io::{self, BufRead, Write};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

enum Line {
    Eof,
    Oversized,
    Bytes(Vec<u8>),
}

fn read_bounded_line(reader: &mut impl BufRead) -> io::Result<Line> {
    let mut output = Vec::new();
    let mut oversized = false;
    loop {
        let available = reader.fill_buf()?;
        if available.is_empty() {
            if output.is_empty() && !oversized {
                return Ok(Line::Eof);
            }
            return Ok(if oversized {
                Line::Oversized
            } else {
                Line::Bytes(output)
            });
        }
        let line_end = available.iter().position(|byte| *byte == b'\n');
        let consumed = line_end.map_or(available.len(), |index| index + 1);
        let content = &available[..line_end.unwrap_or(available.len())];
        if !oversized {
            if output.len() + content.len() > MAX_REQUEST_BYTES {
                oversized = true;
                output.clear();
            } else {
                output.extend_from_slice(content);
            }
        }
        reader.consume(consumed);
        if line_end.is_some() {
            return Ok(if oversized {
                Line::Oversized
            } else {
                Line::Bytes(output)
            });
        }
    }
}

fn emit(writer: &mut impl Write, response: Response) -> io::Result<()> {
    writer.write_all(&response.to_bounded_json())?;
    writer.write_all(b"\n")?;
    writer.flush()
}

fn serve(
    reader: &mut impl BufRead,
    writer: &mut impl Write,
    state: &Arc<Mutex<ControlState>>,
) -> io::Result<()> {
    loop {
        let response = match read_bounded_line(reader)? {
            Line::Eof => return Ok(()),
            Line::Oversized => Response::invalid("req_invalid", "request exceeds byte limit"),
            Line::Bytes(bytes) if bytes.is_empty() => {
                Response::invalid("req_invalid", "request is empty")
            }
            Line::Bytes(bytes) => match parse_request(&bytes) {
                Ok(request) => state
                    .lock()
                    .map_err(|_| io::Error::other("control state lock failed"))?
                    .execute(request),
                Err(error) => error.into_response(),
            },
        };
        emit(writer, response)?;
    }
}

fn main() -> io::Result<()> {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    if arguments.first().map(String::as_str) != Some("serve")
        || arguments.get(1).map(String::as_str) != Some("--stdio")
        || !(arguments.len() - 2).is_multiple_of(2)
    {
        usage();
    }
    let mut cdp_port = None;
    let mut ready_file = None;
    let mut profile_root = None;
    for pair in arguments[2..].chunks_exact(2) {
        match pair[0].as_str() {
            "--cdp-port" if cdp_port.is_none() => {
                cdp_port = Some(pair[1].parse::<u16>().unwrap_or_else(|_| usage()));
            }
            "--ready-file" if ready_file.is_none() => ready_file = Some(PathBuf::from(&pair[1])),
            "--profile-root" if profile_root.is_none() => {
                profile_root = Some(PathBuf::from(&pair[1]));
            }
            _ => usage(),
        }
    }
    if cdp_port.is_some() != ready_file.is_some() {
        usage();
    }
    let state = Arc::new(Mutex::new(ControlState::with_profile_root(profile_root)?));
    let _cdp_server = if let (Some(port), Some(ready_file)) = (cdp_port, ready_file) {
        let server = cdp::Server::start(port, state.clone())?;
        let receipt = json!({
            "cdp_port":server.port(),
            "browser_websocket_url":server.browser_websocket_url(),
        });
        fs::write(ready_file, serde_json::to_vec(&receipt)?)?;
        Some(server)
    } else {
        None
    };
    let stdin = io::stdin();
    let stdout = io::stdout();
    serve(&mut stdin.lock(), &mut stdout.lock(), &state)
}

fn usage() -> ! {
    eprintln!(
        "usage: minicon-surf-synthetic-control serve --stdio [--profile-root PATH] [--cdp-port PORT --ready-file PATH]"
    );
    std::process::exit(64);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn bounded_reader_drains_an_oversized_line_and_recovers() {
        let mut input = vec![b'x'; MAX_REQUEST_BYTES + 1];
        input.extend_from_slice(b"\n{}\n");
        let mut reader = Cursor::new(input);
        assert!(matches!(
            read_bounded_line(&mut reader).unwrap(),
            Line::Oversized
        ));
        assert!(
            matches!(read_bounded_line(&mut reader).unwrap(), Line::Bytes(bytes) if bytes == b"{}")
        );
    }
}
