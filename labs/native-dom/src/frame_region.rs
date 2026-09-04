//! The surface frame's backing store: one anonymous `mmap` region per
//! surface record, exactly the frame's page-rounded length, written in
//! place by the painter, borrowed by the pipe write, and unmapped exactly
//! once when the record is dropped (`surface-frame-region-0.0.1.md`).
//! Every `unsafe` call of the candidate lives here. No address or length of
//! a mapping leaves this module in any result.

use std::fmt;
use std::sync::atomic::{AtomicU64, Ordering};

use crate::surface::FrameSize;

/// The protocol's bound on a frame's pixel bytes (the codec refuses more).
pub const MAX_FRAME_BYTES: usize = native_dom_surface::MAX_PIXEL_BYTES;

static REGIONS_MAPPED_TOTAL: AtomicU64 = AtomicU64::new(0);
static REGIONS_UNMAPPED_TOTAL: AtomicU64 = AtomicU64::new(0);
static UNMAPPED_BYTES_TOTAL: AtomicU64 = AtomicU64::new(0);

/// Why a region could not be made: a refused length (typed
/// `resource_limit` at the control plane), the platform (typed
/// `unsupported_capability`), or the kernel (typed `internal`; the errno
/// is kept for the host's detail text, never an address).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RegionError {
    TooLarge,
    #[cfg_attr(target_os = "macos", allow(dead_code))]
    Unsupported,
    Os(i32),
}

impl fmt::Display for RegionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            RegionError::TooLarge => write!(f, "frame exceeds the protocol bound"),
            RegionError::Unsupported => write!(f, "frame regions are not supported here"),
            RegionError::Os(errno) => write!(f, "mmap failed (errno {errno})"),
        }
    }
}

/// Lifetime counters for `memory.report`: regions mapped and unmapped and
/// the bytes returned to the kernel so far.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Counters {
    pub regions_mapped_total: u64,
    pub regions_unmapped_total: u64,
    pub unmapped_bytes_total: u64,
}

pub fn counters() -> Counters {
    Counters {
        regions_mapped_total: REGIONS_MAPPED_TOTAL.load(Ordering::SeqCst),
        regions_unmapped_total: REGIONS_UNMAPPED_TOTAL.load(Ordering::SeqCst),
        unmapped_bytes_total: UNMAPPED_BYTES_TOTAL.load(Ordering::SeqCst),
    }
}

/// The frame's pixel bytes and the page-rounded mapping length, both with
/// checked arithmetic; refused above the protocol bound.
pub fn lengths(size: FrameSize) -> Result<(usize, usize), RegionError> {
    let bytes = usize::from(size.width)
        .checked_mul(usize::from(size.height))
        .and_then(|n| n.checked_mul(4))
        .ok_or(RegionError::TooLarge)?;
    if bytes == 0 || bytes > MAX_FRAME_BYTES {
        return Err(RegionError::TooLarge);
    }
    let page = page_size();
    let mapped = bytes
        .checked_add(page - 1)
        .map(|n| n / page * page)
        .ok_or(RegionError::TooLarge)?;
    Ok((bytes, mapped))
}

#[cfg(target_os = "macos")]
mod sys {
    use std::ffi::c_void;

    pub const PROT_READ: i32 = 0x01;
    pub const PROT_WRITE: i32 = 0x02;
    pub const MAP_PRIVATE: i32 = 0x0002;
    pub const MAP_ANON: i32 = 0x1000;
    pub const MINCORE_INCORE: u8 = 0x1;

    unsafe extern "C" {
        pub fn mmap(
            addr: *mut c_void,
            len: usize,
            prot: i32,
            flags: i32,
            fd: i32,
            offset: i64,
        ) -> *mut c_void;
        pub fn munmap(addr: *mut c_void, len: usize) -> i32;
        pub fn mincore(addr: *const c_void, len: usize, vec: *mut u8) -> i32;
        pub fn getpagesize() -> i32;
        pub fn __error() -> *mut i32;
    }

    pub fn errno() -> i32 {
        // SAFETY: __error returns this thread's errno cell.
        unsafe { *__error() }
    }
}

pub fn page_size() -> usize {
    #[cfg(target_os = "macos")]
    {
        // SAFETY: getpagesize has no preconditions.
        let page = unsafe { sys::getpagesize() };
        usize::try_from(page).unwrap_or(4096).max(4096)
    }
    #[cfg(not(target_os = "macos"))]
    {
        4096
    }
}

/// One anonymous private mapping that backs one frame. Only this type
/// touches the mapping; it is unmapped exactly once, in `Drop`.
pub struct FrameRegion {
    #[cfg(target_os = "macos")]
    base: std::ptr::NonNull<u8>,
    bytes: usize,
    mapped: usize,
}

// SAFETY: the mapping is private to this value; the pointer is only ever
// dereferenced through the borrows this type hands out, which follow Rust's
// aliasing rules.
unsafe impl Send for FrameRegion {}

impl FrameRegion {
    /// Map a region for a frame of `size`, zero-filled by the kernel.
    #[cfg(target_os = "macos")]
    pub fn map(size: FrameSize) -> Result<FrameRegion, RegionError> {
        let (bytes, mapped) = lengths(size)?;
        // SAFETY: an anonymous private mapping with no address hint and no
        // file; the arguments are the constants above and a checked length.
        let base = unsafe {
            sys::mmap(
                std::ptr::null_mut(),
                mapped,
                sys::PROT_READ | sys::PROT_WRITE,
                sys::MAP_PRIVATE | sys::MAP_ANON,
                -1,
                0,
            )
        };
        if base as isize == -1 || base.is_null() {
            return Err(RegionError::Os(sys::errno()));
        }
        REGIONS_MAPPED_TOTAL.fetch_add(1, Ordering::SeqCst);
        Ok(FrameRegion {
            base: std::ptr::NonNull::new(base.cast::<u8>()).ok_or(RegionError::Os(0))?,
            bytes,
            mapped,
        })
    }

    #[cfg(not(target_os = "macos"))]
    pub fn map(size: FrameSize) -> Result<FrameRegion, RegionError> {
        let _ = lengths(size)?;
        Err(RegionError::Unsupported)
    }

    /// The frame's pixel bytes (what the painter and the pipe see).
    pub fn frame_len(&self) -> usize {
        self.bytes
    }

    /// The mapping's page-rounded length (what the owner reports).
    pub fn mapped_len(&self) -> usize {
        self.mapped
    }

    /// Resident bytes of the mapping right now (`mincore`), for reporting.
    pub fn touched_bytes(&self) -> usize {
        #[cfg(target_os = "macos")]
        {
            let page = page_size();
            let pages = self.mapped.div_ceil(page);
            let mut vec = vec![0u8; pages];
            // SAFETY: the mapping covers `mapped` bytes and `vec` has one
            // entry per page of it.
            let rc = unsafe {
                sys::mincore(
                    self.base.as_ptr().cast::<std::ffi::c_void>(),
                    self.mapped,
                    vec.as_mut_ptr(),
                )
            };
            if rc != 0 {
                return 0;
            }
            vec.iter()
                .filter(|flag| **flag & sys::MINCORE_INCORE != 0)
                .count()
                * page
        }
        #[cfg(not(target_os = "macos"))]
        {
            0
        }
    }

    #[cfg(target_os = "macos")]
    pub fn as_slice(&self) -> &[u8] {
        // SAFETY: the mapping is readable for `bytes` (≤ mapped) and lives
        // as long as `self`.
        unsafe { std::slice::from_raw_parts(self.base.as_ptr(), self.bytes) }
    }

    #[cfg(target_os = "macos")]
    pub fn as_mut_slice(&mut self) -> &mut [u8] {
        // SAFETY: as above, and `&mut self` makes this the only borrow.
        unsafe { std::slice::from_raw_parts_mut(self.base.as_ptr(), self.bytes) }
    }

    #[cfg(not(target_os = "macos"))]
    pub fn as_slice(&self) -> &[u8] {
        &[]
    }

    #[cfg(not(target_os = "macos"))]
    pub fn as_mut_slice(&mut self) -> &mut [u8] {
        &mut []
    }
}

impl Drop for FrameRegion {
    fn drop(&mut self) {
        #[cfg(target_os = "macos")]
        {
            // SAFETY: the pair (base, mapped) came from a successful mmap in
            // `map` and this is the only unmap: a value drops once.
            let _ =
                unsafe { sys::munmap(self.base.as_ptr().cast::<std::ffi::c_void>(), self.mapped) };
        }
        REGIONS_UNMAPPED_TOTAL.fetch_add(1, Ordering::SeqCst);
        UNMAPPED_BYTES_TOTAL.fetch_add(self.mapped as u64, Ordering::SeqCst);
    }
}

impl fmt::Debug for FrameRegion {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // No address, by rule.
        write!(
            f,
            "FrameRegion({} bytes, {} mapped)",
            self.bytes, self.mapped
        )
    }
}

#[cfg(test)]
pub(crate) fn test_lock() -> std::sync::MutexGuard<'static, ()> {
    static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
    LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

#[cfg(all(test, target_os = "macos"))]
mod tests {
    use super::*;

    #[test]
    fn lengths_are_checked_and_bounded() {
        // This test maps and drops a region too, so it takes the same lock:
        // the counters below are process-global and every test that moves
        // them is serialised against every test that reads them.
        let _guard = test_lock();
        let (bytes, mapped) = lengths(FrameSize::DEFAULT).unwrap();
        assert_eq!(bytes, 640 * 400 * 4);
        assert!(mapped >= bytes && mapped - bytes < page_size() && mapped % page_size() == 0);
        let over = FrameSize {
            width: 2048,
            height: 768,
        };
        assert_eq!(
            lengths(over),
            Err(RegionError::TooLarge),
            "2048 × 768 × 4 is over 3 MiB"
        );
        assert!(
            FrameRegion::map(over).is_err(),
            "refused before any mapping"
        );
        let biggest = FrameSize {
            width: 1024,
            height: 768,
        };
        assert_eq!(
            lengths(biggest).unwrap().0,
            MAX_FRAME_BYTES,
            "exactly the bound is allowed"
        );
        drop(FrameRegion::map(biggest).unwrap());
        assert_eq!(
            lengths(FrameSize {
                width: 0,
                height: 64
            }),
            Err(RegionError::TooLarge)
        );
    }

    #[test]
    fn map_write_and_unmap_exactly_once() {
        let _guard = test_lock();
        let before = counters();
        // The counters are process-global and monotonic, so they are read as
        // lower bounds. What this test owns is one region, and what it proves
        // exactly is that region's own lifetime: the live difference rises by
        // one while it is alive and returns to where it was when it is gone.
        let live_before = before.regions_mapped_total - before.regions_unmapped_total;
        let mut region = FrameRegion::map(FrameSize::parse("128x128").unwrap()).unwrap();
        let after_map = counters();
        assert!(
            after_map.regions_mapped_total > before.regions_mapped_total,
            "a map is at least one map"
        );
        assert_eq!(
            after_map.regions_mapped_total - after_map.regions_unmapped_total,
            live_before + 1,
            "exactly this region is live"
        );
        assert_eq!(region.frame_len(), 128 * 128 * 4);
        assert!(
            region.as_slice().iter().all(|b| *b == 0),
            "the kernel zero-fills"
        );
        let untouched = region.touched_bytes();
        region.as_mut_slice()[0..4].copy_from_slice(&[1, 2, 3, 4]);
        let last = region.frame_len() - 1;
        region.as_mut_slice()[last] = 9;
        assert_eq!(&region.as_slice()[0..4], &[1, 2, 3, 4]);
        assert!(region.touched_bytes() >= untouched);
        assert!(region.touched_bytes() <= region.mapped_len());
        let mapped = region.mapped_len() as u64;
        drop(region);
        let after_drop = counters();
        assert!(
            after_drop.regions_unmapped_total > after_map.regions_unmapped_total,
            "the drop unmapped at least this region"
        );
        assert!(
            after_drop.unmapped_bytes_total >= after_map.unmapped_bytes_total + mapped,
            "and returned at least its mapped bytes"
        );
        assert_eq!(
            after_drop.regions_mapped_total - after_drop.regions_unmapped_total,
            live_before,
            "nothing of this region is left live: unmapped exactly once"
        );
    }

    /// The same property under parallel stress: many regions mapped and
    /// dropped from several threads leave the live difference exactly where
    /// they found it, and each counter advances by at least what was done.
    #[test]
    fn parallel_maps_and_drops_conserve_the_live_difference() {
        let _guard = test_lock();
        let before = counters();
        let live_before = before.regions_mapped_total - before.regions_unmapped_total;
        const THREADS: u64 = 4;
        const EACH: u64 = 16;
        let mut handles = Vec::new();
        for _ in 0..THREADS {
            handles.push(std::thread::spawn(|| {
                let size = FrameSize::parse("64x64").expect("a size");
                for _ in 0..EACH {
                    let mut region = FrameRegion::map(size).expect("a region");
                    region.as_mut_slice()[0] = 7;
                    assert_eq!(region.as_slice()[0], 7);
                    drop(region);
                }
            }));
        }
        for handle in handles {
            handle.join().expect("a thread");
        }
        let after = counters();
        assert!(
            after.regions_mapped_total >= before.regions_mapped_total + THREADS * EACH,
            "every region was mapped"
        );
        assert!(
            after.regions_unmapped_total >= before.regions_unmapped_total + THREADS * EACH,
            "and every one of them was unmapped"
        );
        assert_eq!(
            after.regions_mapped_total - after.regions_unmapped_total,
            live_before,
            "none of them leaked and none was unmapped twice"
        );
    }
}
