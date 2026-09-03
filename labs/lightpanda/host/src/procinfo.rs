//! Attributable process metrics for the process-per-target host (X9 ME3).
//!
//! Everything here reads public libproc interfaces (`libproc.h`,
//! `sys/proc_info.h`, `sys/resource.h`): `proc_pidinfo` for identity and
//! resident size, `proc_pid_rusage` for the kernel's physical footprint, and
//! `proc_listchildpids` to enumerate descendants the host did not spawn
//! itself. No task port is requested, so a private/shared split is reported
//! as unavailable rather than approximated. Nothing here changes a process:
//! the report is read-only and never terminates a child.
//!
//! Semantics that the report states explicitly:
//! - `resident_bytes` is the task's resident set (`pti_resident_size`, the
//!   same source `ps` prints); summing it over processes double counts
//!   shared pages such as the engine executable, so the sum is named
//!   `summed_resident_bytes`, never "total memory";
//! - `physical_footprint_bytes` is `ri_phys_footprint`, the kernel's
//!   per-process accounting used by the shared court;
//! - a child is identified by its opaque ordinal and target, its pid, and
//!   the process start time recorded at spawn, so a reused pid is reported
//!   as `pid_reused` instead of being measured;
//! - the report carries a host generation counter that advances on every
//!   spawn and every reap; a child's `spawned_generation` is at most the
//!   report's generation, which gives the set a before/after meaning.

use serde_json::{Value, json};

/// Process states the report can name; anything else is a recorded gap.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChildState {
    Running,
    Zombie,
    Exited,
    PidReused,
    Unreadable,
    ExitedDuringSample,
}

impl ChildState {
    pub fn name(self) -> &'static str {
        match self {
            ChildState::Running => "running",
            ChildState::Zombie => "zombie",
            ChildState::Exited => "exited",
            ChildState::PidReused => "pid_reused",
            ChildState::Unreadable => "unreadable",
            ChildState::ExitedDuringSample => "exited_during_sample",
        }
    }

    pub fn is_complete(self) -> bool {
        matches!(self, ChildState::Running | ChildState::Zombie)
    }
}

/// The identity captured when a child was spawned, compared on every sample.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ProcessIdentity {
    pub parent_pid: u32,
    pub start_tvsec: u64,
    pub start_tvusec: u64,
}

#[derive(Debug, Clone, Copy, Default)]
pub struct Metrics {
    pub resident_bytes: u64,
    pub virtual_bytes: u64,
    pub physical_footprint_bytes: u64,
    pub lifetime_max_physical_footprint_bytes: u64,
}

impl Metrics {
    pub fn to_json(self) -> Value {
        json!({
            "resident_bytes":self.resident_bytes,
            "virtual_bytes":self.virtual_bytes,
            "physical_footprint_bytes":self.physical_footprint_bytes,
            "lifetime_max_physical_footprint_bytes":self.lifetime_max_physical_footprint_bytes,
        })
    }
}

pub fn private_bytes_statement() -> Value {
    json!({
        "available":false,
        "reason":"a private versus shared split needs the task port (task_for_pid), which this host does not request; resident sums double count shared pages",
    })
}

#[cfg(target_os = "macos")]
mod sys {
    use super::{ChildState, Metrics, ProcessIdentity};
    use std::ffi::{c_int, c_void};

    const PROC_PIDTBSDINFO: c_int = 3;
    const PROC_PIDTASKINFO: c_int = 4;
    const RUSAGE_INFO_V4: c_int = 4;
    const SZOMB: u32 = 5;
    const MAXCOMLEN: usize = 16;

    /// `struct proc_bsdinfo` from `sys/proc_info.h`.
    #[repr(C)]
    struct ProcBsdInfo {
        pbi_flags: u32,
        pbi_status: u32,
        pbi_xstatus: u32,
        pbi_pid: u32,
        pbi_ppid: u32,
        pbi_uid: u32,
        pbi_gid: u32,
        pbi_ruid: u32,
        pbi_rgid: u32,
        pbi_svuid: u32,
        pbi_svgid: u32,
        rfu_1: u32,
        pbi_comm: [u8; MAXCOMLEN],
        pbi_name: [u8; 2 * MAXCOMLEN],
        pbi_nfiles: u32,
        pbi_pgid: u32,
        pbi_pjobc: u32,
        e_tdev: u32,
        e_tpgid: u32,
        pbi_nice: i32,
        pbi_start_tvsec: u64,
        pbi_start_tvusec: u64,
    }

    /// `struct proc_taskinfo` from `sys/proc_info.h`.
    #[repr(C)]
    struct ProcTaskInfo {
        pti_virtual_size: u64,
        pti_resident_size: u64,
        pti_total_user: u64,
        pti_total_system: u64,
        pti_threads_user: u64,
        pti_threads_system: u64,
        pti_policy: i32,
        pti_faults: i32,
        pti_pageins: i32,
        pti_cow_faults: i32,
        pti_messages_sent: i32,
        pti_messages_received: i32,
        pti_syscalls_mach: i32,
        pti_syscalls_unix: i32,
        pti_csw: i32,
        pti_threadnum: i32,
        pti_numrunning: i32,
        pti_priority: i32,
    }

    /// `struct rusage_info_v4` from `sys/resource.h`: 16 uuid bytes then 35
    /// `uint64_t` fields; the ones this host reads are indexed by name.
    #[repr(C)]
    struct RusageInfoV4 {
        ri_uuid: [u8; 16],
        fields: [u64; 35],
    }
    const RI_PHYS_FOOTPRINT: usize = 7;
    const RI_LIFETIME_MAX_PHYS_FOOTPRINT: usize = 28;

    unsafe extern "C" {
        fn proc_pidinfo(
            pid: c_int,
            flavor: c_int,
            arg: u64,
            buffer: *mut c_void,
            buffersize: c_int,
        ) -> c_int;
        fn proc_pid_rusage(pid: c_int, flavor: c_int, buffer: *mut c_void) -> c_int;
        fn proc_listchildpids(ppid: c_int, buffer: *mut c_void, buffersize: c_int) -> c_int;
        fn getpid() -> c_int;
    }

    pub fn host_pid() -> u32 {
        // SAFETY: no preconditions.
        unsafe { getpid() as u32 }
    }

    /// Identity and BSD status of one process, or `None` if it cannot be
    /// read (gone, or not visible to this user).
    pub fn identity(pid: u32) -> Option<(ProcessIdentity, bool)> {
        let mut info = std::mem::MaybeUninit::<ProcBsdInfo>::uninit();
        let size = std::mem::size_of::<ProcBsdInfo>() as c_int;
        // SAFETY: the out-buffer is exactly PROC_PIDTBSDINFO_SIZE bytes and
        // exclusively ours; the call fills it or returns less than `size`.
        let written = unsafe {
            proc_pidinfo(
                pid as c_int,
                PROC_PIDTBSDINFO,
                0,
                info.as_mut_ptr().cast(),
                size,
            )
        };
        if written != size {
            return None;
        }
        // SAFETY: the kernel wrote the whole struct.
        let info = unsafe { info.assume_init() };
        Some((
            ProcessIdentity {
                parent_pid: info.pbi_ppid,
                start_tvsec: info.pbi_start_tvsec,
                start_tvusec: info.pbi_start_tvusec,
            },
            info.pbi_status == SZOMB,
        ))
    }

    pub fn metrics(pid: u32) -> Option<Metrics> {
        let mut task = std::mem::MaybeUninit::<ProcTaskInfo>::uninit();
        let size = std::mem::size_of::<ProcTaskInfo>() as c_int;
        // SAFETY: as in `identity`, with PROC_PIDTASKINFO_SIZE.
        let written = unsafe {
            proc_pidinfo(
                pid as c_int,
                PROC_PIDTASKINFO,
                0,
                task.as_mut_ptr().cast(),
                size,
            )
        };
        if written != size {
            return None;
        }
        // SAFETY: the kernel wrote the whole struct.
        let task = unsafe { task.assume_init() };
        let mut usage = RusageInfoV4 {
            ri_uuid: [0; 16],
            fields: [0; 35],
        };
        // SAFETY: the buffer has the exact rusage_info_v4 layout.
        if unsafe {
            proc_pid_rusage(
                pid as c_int,
                RUSAGE_INFO_V4,
                (&mut usage as *mut RusageInfoV4).cast(),
            )
        } != 0
        {
            return None;
        }
        Some(Metrics {
            resident_bytes: task.pti_resident_size,
            virtual_bytes: task.pti_virtual_size,
            physical_footprint_bytes: usage.fields[RI_PHYS_FOOTPRINT],
            lifetime_max_physical_footprint_bytes: usage.fields[RI_LIFETIME_MAX_PHYS_FOOTPRINT],
        })
    }

    /// Direct children of `pid` as the kernel lists them now.
    pub fn children(pid: u32) -> Vec<u32> {
        let mut buffer = vec![0 as c_int; 256];
        // SAFETY: the buffer length in bytes is passed and the call writes at
        // most that many bytes of pid_t values.
        let written = unsafe {
            proc_listchildpids(
                pid as c_int,
                buffer.as_mut_ptr().cast(),
                (buffer.len() * std::mem::size_of::<c_int>()) as c_int,
            )
        };
        if written <= 0 {
            return Vec::new();
        }
        buffer.truncate(written as usize);
        buffer
            .into_iter()
            .filter(|p| *p > 0)
            .map(|p| p as u32)
            .collect()
    }

    /// Classify a child the host spawned against the identity it recorded.
    pub fn classify(pid: u32, recorded: Option<ProcessIdentity>, host: u32) -> ChildState {
        match identity(pid) {
            None => ChildState::Unreadable,
            Some((now, zombie)) => {
                let same = now.parent_pid == host
                    && recorded.is_none_or(|r| {
                        r.start_tvsec == now.start_tvsec && r.start_tvusec == now.start_tvusec
                    });
                if !same {
                    ChildState::PidReused
                } else if zombie {
                    ChildState::Zombie
                } else {
                    ChildState::Running
                }
            }
        }
    }
}

#[cfg(target_os = "macos")]
pub use sys::{children, classify, host_pid, identity, metrics};
