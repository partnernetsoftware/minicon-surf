//! Realm heap arena: one reserved address range per QuickJS realm, served by a
//! portable boundary-tag heap and returned to the operating system in a
//! single `munmap` once the realm's runtime and its allocator are both gone.
//!
//! The split is deliberate. [`Heap`] is plain Rust over a caller-provided
//! byte range and never talks to the operating system, so its contract is
//! testable on every platform. [`Region`], [`Arena`] and
//! [`ArenaAllocator`] are the macOS `mmap`/`madvise`/`munmap` prototype; other
//! targets compile without them and report `unsupported_capability`.
//!
//! Heap contract (each point covered by a unit test):
//! - every block is 16-byte aligned and `usable_size` is the exact payload
//!   size, stable for the block's lifetime and only changed by `realloc`;
//! - `alloc(0)` and `realloc(p, 0)` yield a minimal non-null block;
//!   `calloc` multiplies with overflow checks and zeroes the whole payload;
//! - exhaustion is reported as null and never charged;
//! - `realloc` shrinks or grows in place when it can, otherwise it allocates
//!   the replacement first, copies `min(old, new)` bytes and frees the old
//!   block only after the copy, so on any failure the old block stays valid,
//!   readable, writable and counted;
//! - `dealloc` and `realloc` accept only pointers inside this heap that are
//!   currently in use; anything else aborts instead of corrupting memory;
//! - the heap is single-threaded by construction: it is reached only through
//!   the QuickJS runtime that owns the allocator, and rquickjs without the
//!   `parallel` feature keeps that runtime on one thread.

#[cfg(target_os = "macos")]
use std::cell::{Cell, UnsafeCell};
#[cfg(target_os = "macos")]
use std::ffi::{c_int, c_void};
#[cfg(target_os = "macos")]
use std::rc::Rc;
#[cfg(target_os = "macos")]
use std::sync::atomic::{AtomicUsize, Ordering};

/// Payload alignment; also the header size and the granularity of every size.
pub const ALIGN: usize = 16;
const HEADER: usize = ALIGN;
/// Header plus the two free-list links.
const MIN_BLOCK: usize = HEADER + 2 * std::mem::size_of::<usize>();
const EXACT_LIMIT: usize = 512;
const BINS: usize = 64;
const IN_USE: usize = 1;

/// Boundary tag in front of every block. `size_and_flag` holds the total
/// block size (header included, a multiple of `ALIGN`) with the low bit set
/// while the block is in use; `prev_size` is the total size of the block
/// physically before this one, or zero for the first block.
#[repr(C)]
struct Header {
    size_and_flag: usize,
    prev_size: usize,
}

/// Links stored in the payload of a free block.
#[repr(C)]
struct Links {
    next: *mut Header,
    prev: *mut Header,
}

pub struct Heap {
    base: *mut u8,
    capacity: usize,
    bins: [*mut Header; BINS],
    used: usize,
    blocks: usize,
    high_water: usize,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Statistics {
    /// Bytes reserved for the heap.
    pub capacity: usize,
    /// Bytes inside blocks currently in use, headers included.
    pub used: usize,
    /// Blocks currently in use.
    pub blocks: usize,
    /// Offset past the highest byte the heap has ever handed out or written.
    pub high_water: usize,
}

fn bin_index(size: usize) -> usize {
    if size <= EXACT_LIMIT {
        size / ALIGN - 2
    } else {
        let log2 = (usize::BITS - 1 - size.leading_zeros()) as usize;
        EXACT_LIMIT / ALIGN - 1 + (log2 - 9)
    }
}

fn abort(reason: &str) -> ! {
    eprintln!("native-dom arena: {reason}");
    std::process::abort()
}

impl Heap {
    /// Take over `capacity` bytes at `base` as one free block.
    ///
    /// # Safety
    /// `base` must be aligned to `ALIGN`, writable for `capacity` bytes, and
    /// used by nothing else while the heap lives. `capacity` must be a
    /// multiple of `ALIGN` and at least `MIN_BLOCK`.
    pub unsafe fn new(base: *mut u8, capacity: usize) -> Heap {
        assert!(
            (base as usize).is_multiple_of(ALIGN),
            "arena base must be aligned"
        );
        assert!(
            capacity.is_multiple_of(ALIGN) && capacity >= MIN_BLOCK && bin_index(capacity) < BINS,
            "arena capacity out of range"
        );
        let mut heap = Heap {
            base,
            capacity,
            bins: [std::ptr::null_mut(); BINS],
            used: 0,
            blocks: 0,
            high_water: 0,
        };
        let first = base.cast::<Header>();
        // SAFETY: the caller guarantees the range is writable.
        unsafe {
            first.write(Header {
                size_and_flag: capacity,
                prev_size: 0,
            });
            heap.insert(first);
        }
        heap
    }

    pub fn statistics(&self) -> Statistics {
        Statistics {
            capacity: self.capacity,
            used: self.used,
            blocks: self.blocks,
            high_water: self.high_water,
        }
    }

    /// The last physical block when it is free: `(offset, length)` of the
    /// bytes after its header and links, which hold nothing the heap needs.
    pub fn tail_free_span(&self) -> Option<(usize, usize)> {
        let mut offset = 0;
        let mut last = self.base.cast::<Header>();
        loop {
            let size = Self::size(last);
            if offset + size >= self.capacity {
                break;
            }
            offset += size;
            // SAFETY: a block ends before the capacity, so another follows.
            last = unsafe { self.base.add(offset).cast::<Header>() };
        }
        if Self::in_use(last) {
            return None;
        }
        Some((offset + MIN_BLOCK, Self::size(last) - MIN_BLOCK))
    }

    fn size(h: *mut Header) -> usize {
        // SAFETY: every header pointer handled here was written by this heap.
        unsafe { (*h).size_and_flag & !IN_USE }
    }

    fn in_use(h: *mut Header) -> bool {
        // SAFETY: as above.
        unsafe { (*h).size_and_flag & IN_USE != 0 }
    }

    fn set(h: *mut Header, size: usize, in_use: bool) {
        // SAFETY: as above.
        unsafe { (*h).size_and_flag = size | usize::from(in_use) }
    }

    fn offset(&self, h: *mut Header) -> usize {
        h as usize - self.base as usize
    }

    fn links(h: *mut Header) -> *mut Links {
        // SAFETY: a free block is at least MIN_BLOCK bytes, so the links fit.
        unsafe { h.cast::<u8>().add(HEADER).cast::<Links>() }
    }

    fn next(&self, h: *mut Header) -> Option<*mut Header> {
        let end = self.offset(h) + Self::size(h);
        if end >= self.capacity {
            None
        } else {
            // SAFETY: `end` is inside the heap.
            Some(unsafe { self.base.add(end).cast::<Header>() })
        }
    }

    fn prev(&self, h: *mut Header) -> Option<*mut Header> {
        // SAFETY: header written by this heap.
        let prev_size = unsafe { (*h).prev_size };
        if prev_size == 0 {
            None
        } else {
            // SAFETY: prev_size was recorded from the physically previous block.
            Some(unsafe { h.cast::<u8>().sub(prev_size).cast::<Header>() })
        }
    }

    /// Record `h`'s size in the block after it, if any.
    fn fix_next_prev_size(&self, h: *mut Header) {
        if let Some(next) = self.next(h) {
            // SAFETY: `next` is a live header inside the heap.
            unsafe { (*next).prev_size = Self::size(h) }
        }
    }

    unsafe fn insert(&mut self, h: *mut Header) {
        let bin = bin_index(Self::size(h));
        let head = self.bins[bin];
        // SAFETY: `h` is free and at least MIN_BLOCK bytes.
        unsafe {
            Self::links(h).write(Links {
                next: head,
                prev: std::ptr::null_mut(),
            });
            if !head.is_null() {
                (*Self::links(head)).prev = h;
            }
        }
        self.bins[bin] = h;
        self.high_water = self.high_water.max(self.offset(h) + MIN_BLOCK);
    }

    unsafe fn unlink(&mut self, h: *mut Header) {
        // SAFETY: `h` is a free block currently in its bin.
        unsafe {
            let links = Self::links(h).read();
            if links.prev.is_null() {
                self.bins[bin_index(Self::size(h))] = links.next;
            } else {
                (*Self::links(links.prev)).next = links.next;
            }
            if !links.next.is_null() {
                (*Self::links(links.next)).prev = links.prev;
            }
        }
    }

    /// Total block size for a payload request, or `None` when it cannot fit.
    fn block_size_for(&self, request: usize) -> Option<usize> {
        let payload = request.max(ALIGN).checked_add(ALIGN - 1)? & !(ALIGN - 1);
        let total = payload.checked_add(HEADER)?;
        (total <= self.capacity).then_some(total)
    }

    /// Remove and return the first free block of at least `need` bytes.
    fn take_fit(&mut self, need: usize) -> *mut Header {
        let start = bin_index(need);
        let mut h = self.bins[start];
        while !h.is_null() {
            if Self::size(h) >= need {
                // SAFETY: `h` is in its bin.
                unsafe { self.unlink(h) };
                return h;
            }
            // SAFETY: `h` is a free block with links.
            h = unsafe { (*Self::links(h)).next };
        }
        for bin in start + 1..BINS {
            let h = self.bins[bin];
            if !h.is_null() {
                // SAFETY: as above; every block in a higher bin is larger.
                unsafe { self.unlink(h) };
                return h;
            }
        }
        std::ptr::null_mut()
    }

    /// Cut `h` (free, unlinked) down to `need` bytes, returning the
    /// remainder to the bins when it can hold a block of its own.
    unsafe fn split(&mut self, h: *mut Header, need: usize) {
        let size = Self::size(h);
        if size - need >= MIN_BLOCK {
            // SAFETY: the remainder lies inside `h`'s old extent.
            let rest = unsafe { h.cast::<u8>().add(need).cast::<Header>() };
            unsafe {
                rest.write(Header {
                    size_and_flag: size - need,
                    prev_size: need,
                });
            }
            Self::set(h, need, false);
            self.fix_next_prev_size(rest);
            // SAFETY: `rest` is a free block of at least MIN_BLOCK bytes.
            unsafe { self.insert(rest) };
        }
    }

    /// Header of a live block handed out by this heap, or abort.
    fn live_header(&self, ptr: *mut u8) -> *mut Header {
        let address = ptr as usize;
        let base = self.base as usize;
        if !address.is_multiple_of(ALIGN)
            || address < base + HEADER
            || address >= base + self.capacity
        {
            abort("pointer does not belong to this heap");
        }
        // SAFETY: the address is inside the heap and aligned.
        let h = unsafe { ptr.sub(HEADER).cast::<Header>() };
        if !Self::in_use(h)
            || Self::size(h) < MIN_BLOCK
            || self.offset(h) + Self::size(h) > self.capacity
        {
            abort("pointer is not a live block (double free or corruption)");
        }
        h
    }

    pub fn alloc(&mut self, request: usize) -> *mut u8 {
        let Some(need) = self.block_size_for(request) else {
            return std::ptr::null_mut();
        };
        let h = self.take_fit(need);
        if h.is_null() {
            return std::ptr::null_mut();
        }
        // SAFETY: `h` is free and unlinked.
        unsafe { self.split(h, need) };
        let size = Self::size(h);
        Self::set(h, size, true);
        self.used += size;
        self.blocks += 1;
        self.high_water = self.high_water.max(self.offset(h) + size);
        // SAFETY: the payload starts right after the header.
        unsafe { h.cast::<u8>().add(HEADER) }
    }

    pub fn calloc(&mut self, count: usize, size: usize) -> *mut u8 {
        let Some(total) = count.checked_mul(size) else {
            return std::ptr::null_mut();
        };
        let ptr = self.alloc(total);
        if !ptr.is_null() {
            // SAFETY: the block is live and `usable_size` bytes long.
            unsafe { std::ptr::write_bytes(ptr, 0, Self::usable_size(ptr)) };
        }
        ptr
    }

    /// # Safety
    /// `ptr` must be a live block from this heap.
    pub unsafe fn dealloc(&mut self, ptr: *mut u8) {
        let mut h = self.live_header(ptr);
        let mut size = Self::size(h);
        self.used -= size;
        self.blocks -= 1;
        if let Some(next) = self.next(h)
            && !Self::in_use(next)
        {
            // SAFETY: `next` is free and in its bin.
            unsafe { self.unlink(next) };
            size += Self::size(next);
        }
        if let Some(prev) = self.prev(h)
            && !Self::in_use(prev)
        {
            // SAFETY: `prev` is free and in its bin.
            unsafe { self.unlink(prev) };
            size += Self::size(prev);
            h = prev;
        }
        Self::set(h, size, false);
        self.fix_next_prev_size(h);
        // SAFETY: `h` is now one free block of at least MIN_BLOCK bytes.
        unsafe { self.insert(h) };
    }

    /// # Safety
    /// `ptr` must be null or a live block from this heap.
    pub unsafe fn realloc(&mut self, ptr: *mut u8, request: usize) -> *mut u8 {
        if ptr.is_null() {
            return self.alloc(request);
        }
        let h = self.live_header(ptr);
        let Some(need) = self.block_size_for(request) else {
            return std::ptr::null_mut();
        };
        let current = Self::size(h);
        if need <= current {
            if current - need >= MIN_BLOCK {
                // Shrink in place; the tail becomes a free block merged with a
                // free successor.
                // SAFETY: the tail lies inside the block's old extent.
                let rest = unsafe { h.cast::<u8>().add(need).cast::<Header>() };
                let mut rest_size = current - need;
                Self::set(h, need, true);
                unsafe {
                    rest.write(Header {
                        size_and_flag: rest_size,
                        prev_size: need,
                    });
                }
                if let Some(next) = self.next(rest)
                    && !Self::in_use(next)
                {
                    // SAFETY: `next` is free and in its bin.
                    unsafe { self.unlink(next) };
                    rest_size += Self::size(next);
                    Self::set(rest, rest_size, false);
                }
                self.fix_next_prev_size(rest);
                // SAFETY: `rest` is free and at least MIN_BLOCK bytes.
                unsafe { self.insert(rest) };
                self.used -= current - need;
            }
            return ptr;
        }
        if let Some(next) = self.next(h)
            && !Self::in_use(next)
            && current + Self::size(next) >= need
        {
            // Grow in place into the free successor.
            // SAFETY: `next` is free and in its bin.
            unsafe { self.unlink(next) };
            Self::set(h, current + Self::size(next), false);
            // SAFETY: `h` is temporarily free-shaped and unlinked; its payload
            // is untouched because `split` writes only beyond `need`.
            unsafe { self.split(h, need) };
            let size = Self::size(h);
            Self::set(h, size, true);
            self.fix_next_prev_size(h);
            self.used += size - current;
            self.high_water = self.high_water.max(self.offset(h) + size);
            return ptr;
        }
        let replacement = self.alloc(request);
        if replacement.is_null() {
            return std::ptr::null_mut();
        }
        // SAFETY: both blocks are live and distinct; the copy length is
        // bounded by both payloads.
        unsafe {
            std::ptr::copy_nonoverlapping(ptr, replacement, (current - HEADER).min(request));
            self.dealloc(ptr);
        }
        replacement
    }

    /// # Safety
    /// `ptr` must be a live block from a `Heap`.
    pub unsafe fn usable_size(ptr: *mut u8) -> usize {
        // SAFETY: the header sits right before the payload.
        let h = unsafe { ptr.sub(HEADER).cast::<Header>() };
        Self::size(h) - HEADER
    }

    /// Walk every block and free list and report the first inconsistency.
    #[cfg(test)]
    pub fn check(&self) -> Result<(), String> {
        let mut offset = 0;
        let mut prev_size = 0;
        let mut prev_free = false;
        let mut used = 0;
        let mut blocks = 0;
        let mut free_blocks = 0;
        while offset < self.capacity {
            // SAFETY: offsets stay inside the heap by construction.
            let h = unsafe { self.base.add(offset).cast::<Header>() };
            let size = Self::size(h);
            if size < MIN_BLOCK || size % ALIGN != 0 || offset + size > self.capacity {
                return Err(format!("block at {offset} has size {size}"));
            }
            // SAFETY: as above.
            if unsafe { (*h).prev_size } != prev_size {
                return Err(format!("block at {offset} has a wrong prev_size"));
            }
            if Self::in_use(h) {
                used += size;
                blocks += 1;
                prev_free = false;
            } else {
                if prev_free {
                    return Err(format!("adjacent free blocks at {offset}"));
                }
                prev_free = true;
                free_blocks += 1;
                let mut found = false;
                let mut cursor = self.bins[bin_index(size)];
                while !cursor.is_null() {
                    if cursor == h {
                        found = true;
                        break;
                    }
                    // SAFETY: free-list nodes are free blocks.
                    cursor = unsafe { (*Self::links(cursor)).next };
                }
                if !found {
                    return Err(format!("free block at {offset} is not in its bin"));
                }
            }
            prev_size = size;
            offset += size;
        }
        if offset != self.capacity {
            return Err("blocks do not tile the heap".into());
        }
        if used != self.used || blocks != self.blocks {
            return Err(format!(
                "accounting {}/{} differs from walk {used}/{blocks}",
                self.used, self.blocks
            ));
        }
        let mut listed = 0;
        for (bin, head) in self.bins.iter().enumerate() {
            let mut cursor = *head;
            let mut prev = std::ptr::null_mut();
            while !cursor.is_null() {
                if Self::in_use(cursor) || bin_index(Self::size(cursor)) != bin {
                    return Err(format!("bin {bin} holds a wrong block"));
                }
                // SAFETY: free-list nodes are free blocks.
                let links = unsafe { Self::links(cursor).read() };
                if links.prev != prev {
                    return Err(format!("bin {bin} has a broken back link"));
                }
                listed += 1;
                prev = cursor;
                cursor = links.next;
            }
        }
        if listed != free_blocks {
            return Err(format!(
                "{listed} listed free blocks but {free_blocks} walked"
            ));
        }
        Ok(())
    }
}

// ------------------------------------------------------------ macOS prototype

#[cfg(target_os = "macos")]
unsafe extern "C" {
    fn mmap(
        addr: *mut c_void,
        len: usize,
        prot: c_int,
        flags: c_int,
        fd: c_int,
        offset: i64,
    ) -> *mut c_void;
    fn munmap(addr: *mut c_void, len: usize) -> c_int;
    fn madvise(addr: *mut c_void, len: usize, advice: c_int) -> c_int;
    fn getpagesize() -> c_int;
}

#[cfg(target_os = "macos")]
const PROT_READ: c_int = 0x01;
#[cfg(target_os = "macos")]
const PROT_WRITE: c_int = 0x02;
#[cfg(target_os = "macos")]
const MAP_PRIVATE: c_int = 0x0002;
#[cfg(target_os = "macos")]
const MAP_ANON: c_int = 0x1000;
#[cfg(target_os = "macos")]
const MADV_FREE_REUSABLE: c_int = 7;
#[cfg(target_os = "macos")]
const MADV_FREE_REUSE: c_int = 8;

/// A private anonymous mapping. Pages cost nothing until written; the whole
/// range goes back to the kernel in `Drop`.
#[cfg(target_os = "macos")]
pub struct Region {
    base: *mut u8,
    len: usize,
}

#[cfg(target_os = "macos")]
impl Region {
    pub fn reserve(len: usize) -> Result<Region, String> {
        // SAFETY: anonymous private mapping with no address hint; the result
        // is checked against MAP_FAILED.
        let base = unsafe {
            mmap(
                std::ptr::null_mut(),
                len,
                PROT_READ | PROT_WRITE,
                MAP_PRIVATE | MAP_ANON,
                -1,
                0,
            )
        };
        if base as isize == -1 {
            return Err(format!("mmap of {len} bytes failed"));
        }
        Ok(Region {
            base: base.cast(),
            len,
        })
    }

    fn page_size() -> usize {
        // SAFETY: no preconditions.
        unsafe { getpagesize() as usize }
    }

    /// Whole pages inside `[offset, offset + len)` of the mapping.
    fn pages_within(&self, offset: usize, len: usize) -> Option<(usize, usize)> {
        let page = Self::page_size();
        let start = offset.div_ceil(page) * page;
        let end = (offset + len) / page * page;
        (end > start && end <= self.len).then_some((start, end - start))
    }

    fn advise(&self, offset: usize, len: usize, advice: c_int) -> bool {
        // SAFETY: the range lies inside this mapping and is page aligned.
        unsafe { madvise(self.base.add(offset).cast(), len, advice) == 0 }
    }
}

#[cfg(target_os = "macos")]
impl Drop for Region {
    fn drop(&mut self) {
        // SAFETY: the mapping came from mmap with exactly this base and length.
        unsafe { munmap(self.base.cast(), self.len) };
    }
}

/// Blocks still in use inside an arena at the moment it was unmapped, summed
/// over every closed realm; non-zero means QuickJS or the shim leaked.
#[cfg(target_os = "macos")]
pub static ARENA_BLOCKS_LEAKED: AtomicUsize = AtomicUsize::new(0);
#[cfg(target_os = "macos")]
pub static ARENAS_UNMAPPED: AtomicUsize = AtomicUsize::new(0);

/// One realm's arena: the mapping, the heap over it, and the tail-trim state.
///
/// Ownership: the `Realm` and the `ArenaAllocator` inside the QuickJS runtime
/// each hold an `Rc`. The mapping is released in `Drop`, which runs only when
/// both are gone; rquickjs drops the allocator after `JS_FreeRuntime`, so no
/// allocator call and no QuickJS block can outlive the mapping whatever the
/// field order or any stray runtime handle.
#[cfg(target_os = "macos")]
pub struct Arena {
    region: Region,
    heap: UnsafeCell<Heap>,
    /// Offset from which the free tail was marked reusable, if any.
    decommitted_from: Cell<Option<usize>>,
}

#[cfg(target_os = "macos")]
impl Arena {
    pub fn reserve(len: usize) -> Result<Rc<Arena>, String> {
        let region = Region::reserve(len)?;
        // SAFETY: the mapping is page aligned, writable and exclusively ours.
        let heap = unsafe { Heap::new(region.base, len) };
        Ok(Rc::new(Arena {
            region,
            heap: UnsafeCell::new(heap),
            decommitted_from: Cell::new(None),
        }))
    }

    /// Statistics for `memory.report`. Sound because the host reads them from
    /// the thread that owns the realm, never while QuickJS is running.
    pub fn statistics(&self) -> Statistics {
        // SAFETY: no allocator call is in flight (single thread, see above).
        unsafe { (*self.heap.get()).statistics() }
    }

    pub fn decommitted_from(&self) -> Option<usize> {
        self.decommitted_from.get()
    }

    /// Mark the whole pages of the free tail reusable and report their bytes.
    /// Mark the whole pages of the free tail reusable and report the bytes
    /// newly marked. Only the touched extent counts: pages above the heap's
    /// high-water mark were never written, cost nothing, and are not
    /// reported; pages already marked by an earlier trim are not counted
    /// again. The marked region is always `[decommitted_from, capacity)`.
    pub fn trim(&self) -> usize {
        // SAFETY: as in `statistics`.
        let (span, high_water) = unsafe {
            let heap = &*self.heap.get();
            (heap.tail_free_span(), heap.high_water)
        };
        let Some((offset, len)) = span else {
            return 0;
        };
        let end = (offset + len).min(high_water);
        if end <= offset {
            return 0;
        }
        let Some((start, len)) = self.region.pages_within(offset, end - offset) else {
            return 0;
        };
        if !self.region.advise(start, len, MADV_FREE_REUSABLE) {
            return 0;
        }
        let already = self.decommitted_from.get();
        let newly = match already {
            Some(from) if from <= start => 0,
            Some(from) => from.min(start + len) - start,
            None => len,
        };
        let from = already.map_or(start, |f| f.min(start));
        self.decommitted_from.set(Some(from));
        newly
    }

    /// After the heap grew past a trimmed tail, tell the kernel those pages
    /// are in use again so the footprint accounting stays honest.
    fn recommit_to(&self, high_water: usize) {
        let Some(from) = self.decommitted_from.get() else {
            return;
        };
        if high_water <= from {
            return;
        }
        let page = Region::page_size();
        let end = high_water.div_ceil(page) * page;
        let end = end.min(self.region.len);
        if end > from {
            self.region.advise(from, end - from, MADV_FREE_REUSE);
        }
        self.decommitted_from
            .set((end < self.region.len).then_some(end));
    }
}

#[cfg(target_os = "macos")]
impl Drop for Arena {
    fn drop(&mut self) {
        let leaked = self.heap.get_mut().blocks;
        ARENA_BLOCKS_LEAKED.fetch_add(leaked, Ordering::Relaxed);
        ARENAS_UNMAPPED.fetch_add(1, Ordering::Relaxed);
        // The region field is unmapped right after this by its own Drop.
    }
}

/// rquickjs allocator serving one arena. It owns no memory of its own, so
/// dropping it never frees anything twice; it only lowers the arena's count.
#[cfg(target_os = "macos")]
pub struct ArenaAllocator(pub Rc<Arena>);

#[cfg(target_os = "macos")]
impl ArenaAllocator {
    fn heap(&mut self) -> &mut Heap {
        // SAFETY: the allocator is the only mutator of the heap and QuickJS
        // calls it from one thread, one call at a time.
        unsafe { &mut *self.0.heap.get() }
    }

    fn after_growth(&mut self) {
        let high_water = self.heap().high_water;
        self.0.recommit_to(high_water);
    }
}

// SAFETY: every method forwards to the heap with pointers the rquickjs bridge
// guarantees came from this allocator; the heap aborts on any other pointer.
#[cfg(target_os = "macos")]
unsafe impl rquickjs::allocator::Allocator for ArenaAllocator {
    fn alloc(&mut self, size: usize) -> *mut u8 {
        let ptr = self.heap().alloc(size);
        self.after_growth();
        ptr
    }

    fn calloc(&mut self, count: usize, size: usize) -> *mut u8 {
        let ptr = self.heap().calloc(count, size);
        self.after_growth();
        ptr
    }

    unsafe fn dealloc(&mut self, ptr: *mut u8) {
        if ptr.is_null() {
            return;
        }
        // SAFETY: the caller guarantees the block came from this allocator.
        unsafe { self.heap().dealloc(ptr) }
    }

    unsafe fn realloc(&mut self, ptr: *mut u8, new_size: usize) -> *mut u8 {
        // SAFETY: as above.
        let out = unsafe { self.heap().realloc(ptr, new_size) };
        self.after_growth();
        out
    }

    unsafe fn usable_size(ptr: *mut u8) -> usize {
        if ptr.is_null() {
            return 0;
        }
        // SAFETY: as above.
        unsafe { Heap::usable_size(ptr) }
    }
}

// ------------------------------------------------------------------- tests

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    /// An aligned scratch buffer standing in for a mapping on any platform.
    struct Buffer(Vec<u128>);

    impl Buffer {
        fn new(bytes: usize) -> Buffer {
            Buffer(vec![0u128; bytes / 16])
        }

        fn heap(&mut self) -> Heap {
            let bytes = self.0.len() * 16;
            // SAFETY: the vector is 16-byte aligned, writable and exclusive.
            unsafe { Heap::new(self.0.as_mut_ptr().cast(), bytes) }
        }
    }

    fn fill(ptr: *mut u8, len: usize, byte: u8) {
        unsafe { std::ptr::write_bytes(ptr, byte, len) }
    }

    fn all(ptr: *mut u8, len: usize, byte: u8) -> bool {
        unsafe { std::slice::from_raw_parts(ptr, len) }
            .iter()
            .all(|b| *b == byte)
    }

    #[test]
    fn serves_aligned_blocks_with_exact_usable_size() {
        let mut buffer = Buffer::new(64 * 1024);
        let mut heap = buffer.heap();
        assert_eq!(heap.statistics().used, 0);
        let a = heap.alloc(1);
        let b = heap.alloc(17);
        let c = heap.alloc(1000);
        for (ptr, request) in [(a, 1), (b, 17), (c, 1000)] {
            assert!(!ptr.is_null());
            assert_eq!(ptr as usize % ALIGN, 0, "payloads are 16-byte aligned");
            let usable = unsafe { Heap::usable_size(ptr) };
            assert!(usable >= request && usable < request.max(16) + ALIGN);
            assert_eq!(usable % ALIGN, 0);
        }
        heap.check().unwrap();
        let stats = heap.statistics();
        assert_eq!(stats.blocks, 3);
        assert_eq!(stats.used, 3 * HEADER + 16 + 32 + 1008);
        assert!(stats.high_water >= stats.used);
        unsafe {
            heap.dealloc(b);
            heap.dealloc(a);
            heap.dealloc(c);
        }
        heap.check().unwrap();
        assert_eq!(heap.statistics().used, 0);
        assert_eq!(heap.statistics().blocks, 0);
        assert_eq!(
            heap.tail_free_span(),
            Some((MIN_BLOCK, 64 * 1024 - MIN_BLOCK)),
            "everything coalesced back into one free block"
        );
    }

    #[test]
    fn zero_sizes_and_overflow_follow_the_contract() {
        let mut buffer = Buffer::new(16 * 1024);
        let mut heap = buffer.heap();
        let zero = heap.alloc(0);
        assert!(!zero.is_null(), "alloc(0) is a minimal block");
        assert_eq!(unsafe { Heap::usable_size(zero) }, 16);
        let from_null = unsafe { heap.realloc(std::ptr::null_mut(), 0) };
        assert!(!from_null.is_null());
        let shrunk_to_zero = unsafe { heap.realloc(zero, 0) };
        assert!(!shrunk_to_zero.is_null(), "realloc(p, 0) keeps a block");
        assert!(heap.calloc(usize::MAX, 2).is_null(), "calloc overflow");
        assert!(!heap.calloc(0, 8).is_null(), "calloc(0) is a block");
        assert!(heap.alloc(usize::MAX).is_null(), "size overflow");
        assert!(heap.alloc(16 * 1024).is_null(), "larger than the heap");
        let zeroed = heap.calloc(3, 100);
        assert!(all(zeroed, 300, 0));
        unsafe {
            heap.dealloc(zeroed);
            heap.dealloc(shrunk_to_zero);
            heap.dealloc(from_null);
        }
        heap.check().unwrap();
        assert_eq!(heap.statistics().blocks, 1, "the calloc(0) block remains");
    }

    #[test]
    fn exhaustion_is_null_and_failed_realloc_keeps_the_old_block() {
        let mut buffer = Buffer::new(4096);
        let mut heap = buffer.heap();
        let old = heap.alloc(1000);
        assert!(!old.is_null());
        fill(old, 1000, 0xa5);
        let blocker = heap.alloc(1000);
        assert!(!blocker.is_null());
        let counted = heap.statistics();
        assert!(heap.alloc(3000).is_null(), "no block fits");
        assert_eq!(heap.statistics(), counted, "failure charges nothing");
        // Growing `old` cannot happen in place (blocker follows it) and no
        // free block of 3000 bytes exists.
        let failed = unsafe { heap.realloc(old, 3000) };
        assert!(failed.is_null());
        assert_eq!(heap.statistics(), counted, "the count is unchanged");
        assert!(all(old, 1000, 0xa5), "the old block is still readable");
        fill(old, 1000, 0x5a);
        assert!(all(old, 1000, 0x5a), "and writable");
        heap.check().unwrap();
        unsafe {
            heap.dealloc(blocker);
            heap.dealloc(old);
        }
        assert_eq!(heap.statistics().used, 0);
    }

    #[test]
    fn realloc_shrinks_grows_in_place_and_moves_with_its_bytes() {
        let mut buffer = Buffer::new(64 * 1024);
        let mut heap = buffer.heap();
        let a = heap.alloc(1000);
        fill(a, 1000, 0x3c);
        // Grow in place: the free tail follows `a`.
        let grown = unsafe { heap.realloc(a, 4000) };
        assert_eq!(grown, a, "grew into the free successor");
        assert!(all(grown, 1000, 0x3c));
        heap.check().unwrap();
        // Shrink in place and give the tail back.
        let shrunk = unsafe { heap.realloc(grown, 100) };
        assert_eq!(shrunk, a);
        assert_eq!(unsafe { Heap::usable_size(shrunk) }, 112);
        assert!(all(shrunk, 100, 0x3c));
        heap.check().unwrap();
        assert_eq!(heap.statistics().blocks, 1);
        // Block a successor, then growth must move.
        let blocker = heap.alloc(64);
        let moved = unsafe { heap.realloc(shrunk, 8000) };
        assert!(!moved.is_null());
        assert_ne!(moved, a);
        assert!(all(moved, 100, 0x3c), "bytes moved to the replacement");
        heap.check().unwrap();
        assert_eq!(heap.statistics().blocks, 2, "the old block was freed");
        unsafe {
            heap.dealloc(blocker);
            heap.dealloc(moved);
        }
        heap.check().unwrap();
        assert_eq!(heap.statistics().used, 0);
    }

    #[test]
    fn randomized_operations_keep_every_invariant() {
        let capacity = 256 * 1024;
        let mut buffer = Buffer::new(capacity);
        let mut heap = buffer.heap();
        let mut model: HashMap<usize, (usize, u8)> = HashMap::new();
        let mut order: Vec<usize> = Vec::new();
        let mut state: u64 = 0x9e37_79b9_7f4a_7c15;
        let mut next = || {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            state
        };
        let mut moves = 0usize;
        let mut failures = 0usize;
        for step in 0..20_000 {
            let roll = next();
            let size = match roll % 8 {
                0 => 0,
                1..=4 => (next() % 64) as usize,
                5 | 6 => (next() % 2048) as usize,
                _ => (next() % 40_000) as usize,
            };
            let byte = (next() % 251) as u8 + 1;
            match roll / 8 % 3 {
                0 if !order.is_empty() => {
                    let index = (next() as usize) % order.len();
                    let ptr = order.swap_remove(index);
                    let (len, fill_byte) = model.remove(&ptr).unwrap();
                    assert!(all(ptr as *mut u8, len, fill_byte), "block {step} intact");
                    unsafe { heap.dealloc(ptr as *mut u8) };
                }
                1 if !order.is_empty() => {
                    let index = (next() as usize) % order.len();
                    let ptr = order[index];
                    let (len, fill_byte) = model[&ptr];
                    let out = unsafe { heap.realloc(ptr as *mut u8, size) };
                    if out.is_null() {
                        failures += 1;
                        assert!(all(ptr as *mut u8, len, fill_byte), "old block kept");
                        fill(ptr as *mut u8, len, fill_byte);
                    } else {
                        assert!(all(out, len.min(size), fill_byte), "bytes preserved");
                        assert!(unsafe { Heap::usable_size(out) } >= size);
                        if out as usize != ptr {
                            moves += 1;
                        }
                        model.remove(&ptr);
                        fill(out, size, byte);
                        model.insert(out as usize, (size, byte));
                        order[index] = out as usize;
                    }
                }
                _ => {
                    let ptr = if roll % 2 == 0 {
                        heap.alloc(size)
                    } else {
                        let out = heap.calloc(size, 1);
                        if !out.is_null() {
                            assert!(all(out, size, 0), "calloc zeroes");
                        }
                        out
                    };
                    if ptr.is_null() {
                        failures += 1;
                    } else {
                        assert_eq!(ptr as usize % ALIGN, 0);
                        assert!(unsafe { Heap::usable_size(ptr) } >= size);
                        fill(ptr, size, byte);
                        model.insert(ptr as usize, (size, byte));
                        order.push(ptr as usize);
                    }
                }
            }
            if step % 32 == 0 {
                heap.check().unwrap_or_else(|e| panic!("step {step}: {e}"));
                let stats = heap.statistics();
                assert_eq!(stats.blocks, model.len());
                assert!(stats.used <= capacity && stats.high_water <= capacity);
            }
        }
        assert!(
            moves > 0 && failures > 0,
            "the run exercised moves and exhaustion"
        );
        for ptr in order {
            let (len, fill_byte) = model[&ptr];
            assert!(all(ptr as *mut u8, len, fill_byte));
            unsafe { heap.dealloc(ptr as *mut u8) };
        }
        heap.check().unwrap();
        let stats = heap.statistics();
        assert_eq!((stats.used, stats.blocks), (0, 0));
        assert_eq!(
            heap.tail_free_span(),
            Some((MIN_BLOCK, capacity - MIN_BLOCK))
        );
    }

    #[test]
    fn tail_free_span_needs_a_free_last_block() {
        let mut buffer = Buffer::new(8192);
        let mut heap = buffer.heap();
        let a = heap.alloc(100); // a 128-byte block
        // 8032 bytes of payload make an 8048-byte block; the 16 bytes left
        // over cannot hold a block, so `b` is the last block and in use.
        let b = heap.alloc(8192 - 128 - HEADER - 16);
        assert!(!b.is_null());
        assert_eq!(unsafe { Heap::usable_size(b) }, 8192 - 128 - HEADER);
        assert_eq!(heap.tail_free_span(), None);
        unsafe { heap.dealloc(b) };
        let span = heap.tail_free_span().unwrap();
        assert_eq!(span.0, 128 + MIN_BLOCK);
        assert_eq!(span.0 + span.1, 8192);
        unsafe { heap.dealloc(a) };
        heap.check().unwrap();
    }
}

#[cfg(all(test, target_os = "macos"))]
mod macos_tests {
    use super::*;

    #[test]
    fn arena_is_unmapped_only_when_the_last_holder_drops() {
        // The unmap counter is process-global and other arena tests run in
        // parallel, so this test observes its own arena through a weak
        // handle and reads the counter only as a lower bound.
        let unmapped = ARENAS_UNMAPPED.load(Ordering::Relaxed);
        let arena = Arena::reserve(1 << 20).unwrap();
        let watch = Rc::downgrade(&arena);
        let allocator = ArenaAllocator(arena.clone());
        assert_eq!(Rc::strong_count(&arena), 2);
        drop(arena);
        assert!(
            watch.upgrade().is_some(),
            "the allocator still holds the arena"
        );
        drop(allocator);
        assert!(
            watch.upgrade().is_none(),
            "the last holder dropped the arena"
        );
        assert!(ARENAS_UNMAPPED.load(Ordering::Relaxed) > unmapped);
    }

    #[test]
    fn trim_marks_the_free_tail_reusable_and_growth_recommits() {
        let arena = Arena::reserve(1 << 20).unwrap();
        let mut allocator = ArenaAllocator(arena.clone());
        use rquickjs::allocator::Allocator;
        let block = allocator.alloc(100_000);
        assert!(!block.is_null());
        unsafe { std::ptr::write_bytes(block, 0x11, 100_000) };
        unsafe { allocator.dealloc(block) };
        let released = arena.trim();
        let page = Region::page_size();
        let touched = arena.statistics().high_water;
        assert!(touched >= 100_000 && touched < 100_000 + 2 * page);
        assert!(
            released >= touched - 3 * page && released <= touched,
            "only the touched pages of the free tail are reported, never the untouched reservation: {released} of {touched}"
        );
        assert_eq!(arena.trim(), 0, "a second trim marks nothing new");
        assert!(arena.decommitted_from().is_some());
        let again = allocator.alloc(200_000);
        assert!(!again.is_null());
        unsafe { std::ptr::write_bytes(again, 0x22, 200_000) };
        assert!(
            arena.decommitted_from().unwrap() >= 200_000,
            "pages the heap grew into were recommitted"
        );
        assert!(
            unsafe { std::slice::from_raw_parts(again, 200_000) }
                .iter()
                .all(|b| *b == 0x22)
        );
        unsafe { allocator.dealloc(again) };
        let again_released = arena.trim();
        assert!(
            again_released > 0 && again_released <= 200_000 + 2 * page,
            "regrown pages are reported once more after they were recommitted: {again_released}"
        );
    }
}
