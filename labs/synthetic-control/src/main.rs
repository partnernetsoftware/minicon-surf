use minicon_surf_synthetic_control::{ControlState, MAX_REQUEST_BYTES, Response, parse_request};
use std::io::{self, BufRead, Write};

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

fn serve(reader: &mut impl BufRead, writer: &mut impl Write) -> io::Result<()> {
    let mut state = ControlState::default();
    loop {
        let response = match read_bounded_line(reader)? {
            Line::Eof => return Ok(()),
            Line::Oversized => Response::invalid("req_invalid", "request exceeds byte limit"),
            Line::Bytes(bytes) if bytes.is_empty() => {
                Response::invalid("req_invalid", "request is empty")
            }
            Line::Bytes(bytes) => match parse_request(&bytes) {
                Ok(request) => state.execute(request),
                Err(error) => error.into_response(),
            },
        };
        emit(writer, response)?;
    }
}

fn main() -> io::Result<()> {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    if arguments != ["serve", "--stdio"] {
        eprintln!("usage: minicon-surf-synthetic-control serve --stdio");
        std::process::exit(64);
    }
    let stdin = io::stdin();
    let stdout = io::stdout();
    serve(&mut stdin.lock(), &mut stdout.lock())
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
