//! Adapter references and teardown ordering (X9 micro-experiment ME2).
//!
//! An adapter is anything outside the control desk that keeps a reference to
//! a target between operations: the loopback CDP edge today, an embedder or
//! plugin later. The contract is that such a reference can never extend the
//! target, its realm, its session or its profile. The proof is the reference
//! shape itself, in safe Rust: the target owns the only strong `Arc` to its
//! anchor, adapters hold a `Weak`, and the host checks `Arc::strong_count`
//! before it drops the target. An adapter that stored an upgraded `Arc` is
//! detected there and counted as an owner-reference leak; the target is
//! removed from the ledger regardless, so the leak cannot keep an owner.
//!
//! Teardown order is fixed and reported: adapters are detached first, then
//! surfaces are released, then the target is dropped; `session.close` runs
//! that per target and only then releases the profile writer lock.

use std::sync::{Arc, Weak};

use serde_json::{Value, json};

use crate::{ControlError, ControlState, limit, not_found};

pub const MAX_ADAPTERS: usize = 16;

/// The identity an adapter may hold. It carries names, never state: the
/// revision, nodes and policy stay in the host and are read through
/// operations under the host's authority.
#[derive(Debug)]
pub struct TargetAnchor {
    pub target_id: String,
    pub session_id: String,
    pub realm_id: String,
}

/// What an adapter keeps between operations. `upgrade` yields a transient
/// strong reference for the duration of one operation; storing it is the
/// violation the teardown detector reports.
#[derive(Debug, Clone)]
pub struct AdapterHandle {
    pub id: String,
    anchor: Weak<TargetAnchor>,
}

impl AdapterHandle {
    /// The anchor while the target is alive; `None` once the host dropped it.
    pub fn upgrade(&self) -> Option<Arc<TargetAnchor>> {
        self.anchor.upgrade()
    }

    pub fn target_id(&self) -> Option<String> {
        self.upgrade().map(|anchor| anchor.target_id.clone())
    }
}

#[derive(Debug)]
pub(crate) struct Adapter {
    pub id: String,
    pub kind: String,
    pub anchor: Weak<TargetAnchor>,
}

/// What one target teardown released, in the order it happened.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Teardown {
    pub adapters_detached: usize,
    pub surfaces_released: usize,
    pub released_presentation_bytes: usize,
    /// True when a strong reference other than the target's own survived to
    /// the drop: an adapter extended the owner. Counted, never tolerated.
    pub owner_reference_extended: bool,
}

impl Teardown {
    pub fn to_json(&self) -> Value {
        json!({
            "adapters_detached":self.adapters_detached,
            "surfaces_released":self.surfaces_released,
            "released_presentation_bytes":self.released_presentation_bytes,
            "owner_reference_extended":self.owner_reference_extended,
        })
    }
}

impl ControlState {
    /// Register an adapter on a live target and hand back its weak handle.
    pub fn attach_adapter(
        &mut self,
        target_id: &str,
        kind: &str,
    ) -> Result<AdapterHandle, ControlError> {
        let target = self
            .targets
            .get(target_id)
            .ok_or_else(|| not_found("target", target_id))?;
        if self.adapters.len() >= MAX_ADAPTERS {
            return Err(limit("adapter capacity reached"));
        }
        self.next_adapter += 1;
        let id = format!("adapter_{}", self.next_adapter);
        let anchor = Arc::downgrade(&target.anchor);
        self.adapters.insert(
            id.clone(),
            Adapter {
                id: id.clone(),
                kind: kind.to_owned(),
                anchor: anchor.clone(),
            },
        );
        Ok(AdapterHandle { id, anchor })
    }

    /// Release an adapter explicitly. Unknown or already-detached ids are
    /// `not_found`; the target itself is untouched either way.
    pub fn detach_adapter(&mut self, adapter_id: &str) -> Result<(), ControlError> {
        self.adapters
            .remove(adapter_id)
            .map(|_| ())
            .ok_or_else(|| ControlError::new("not_found", "adapter does not exist", false))
    }

    pub fn adapter_count(&self) -> usize {
        self.adapters.len()
    }

    /// Logical bytes the adapter ledger holds: records and their names only.
    pub(crate) fn adapter_bytes(&self) -> usize {
        self.adapters
            .values()
            .map(|item| std::mem::size_of::<Adapter>() + item.id.capacity() + item.kind.capacity())
            .sum()
    }

    /// Tear one target down in contract order and drop its anchor last.
    pub(crate) fn teardown_target(&mut self, target_id: &str) -> Result<Teardown, ControlError> {
        let target = self
            .targets
            .remove(target_id)
            .ok_or_else(|| not_found("target", target_id))?;
        let mut report = Teardown::default();
        // 1. Adapters: every handle that points at this target is detached.
        let before = self.adapters.len();
        self.adapters.retain(|_, adapter| {
            adapter
                .anchor
                .upgrade()
                .is_none_or(|anchor| anchor.target_id != target_id)
        });
        report.adapters_detached = before - self.adapters.len();
        // 2. Surfaces: presentation resources are released.
        let surfaces_before = self.surfaces.len();
        let mut released = 0;
        self.surfaces.retain(|_, surface| {
            if surface.target_id == target_id {
                released += surface.presentation.len();
                false
            } else {
                true
            }
        });
        report.surfaces_released = surfaces_before - self.surfaces.len();
        report.released_presentation_bytes = released;
        // 3. The target: only its own strong reference may remain.
        report.owner_reference_extended = Arc::strong_count(&target.anchor) > 1;
        if report.owner_reference_extended {
            self.owner_references_extended_total += 1;
        }
        self.targets_closed_total += 1;
        self.adapters_detached_total += report.adapters_detached;
        drop(target);
        Ok(report)
    }

    pub(crate) fn teardown_counters(&self) -> Value {
        json!({
            "targets_closed_total":self.targets_closed_total,
            "adapters_detached_total":self.adapters_detached_total,
            "owner_references_extended_total":self.owner_references_extended_total,
            "order":["adapters","surfaces","target","profile_lock"],
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tests::{request, result};
    use serde_json::json;

    fn host_with_target() -> (ControlState, String, String, String) {
        let mut state = ControlState::default();
        let profile = result(state.execute(request(
            "req_p",
            "profile.create",
            json!({"persistence":"ephemeral"}),
        )))["profile"]
            .as_str()
            .unwrap()
            .to_owned();
        let session = result(state.execute(request(
            "req_s",
            "session.open",
            json!({"profile":profile}),
        )))["session"]
            .as_str()
            .unwrap()
            .to_owned();
        let target = result(state.execute(request(
            "req_t",
            "target.open",
            json!({"session":session}),
        )))["target"]
            .as_str()
            .unwrap()
            .to_owned();
        (state, profile, session, target)
    }

    fn owners(state: &mut ControlState) -> Value {
        result(state.execute(request("req_m", "memory.report", json!({}))))
    }

    #[test]
    fn adapter_handles_never_extend_the_target() {
        let (mut state, _profile, _session, target) = host_with_target();
        let handle = state.attach_adapter(&target, "test").unwrap();
        assert_eq!(Arc::strong_count(&state.targets[&target].anchor), 1);
        // The ledger record and the handle each hold one weak reference.
        assert_eq!(Arc::weak_count(&state.targets[&target].anchor), 2);
        assert_eq!(handle.target_id().as_deref(), Some(target.as_str()));
        let report = owners(&mut state);
        assert_eq!(report["owners"]["adapters"]["objects"], 1);
        assert!(report["owners"]["adapters"]["bytes"].as_u64().unwrap() > 0);
        let closed =
            result(state.execute(request("req_c", "target.close", json!({"target":target}))));
        assert_eq!(closed["teardown"]["adapters_detached"], 1);
        assert_eq!(closed["teardown"]["owner_reference_extended"], false);
        assert!(
            handle.upgrade().is_none(),
            "the anchor is gone with the target"
        );
        let report = owners(&mut state);
        assert_eq!(report["owners"]["adapters"]["objects"], 0);
        assert_eq!(report["owners"]["targets"]["objects"], 0);
        assert_eq!(report["teardown"]["owner_references_extended_total"], 0);
        assert_eq!(
            state.detach_adapter(&handle.id).unwrap_err().code,
            "not_found",
            "detaching after teardown is a typed failure"
        );
    }

    #[test]
    fn a_stored_strong_reference_is_detected_and_cannot_keep_the_owner() {
        let (mut state, _profile, _session, target) = host_with_target();
        let handle = state.attach_adapter(&target, "rogue").unwrap();
        // The violation: an adapter keeps the upgraded reference.
        let stolen = handle.upgrade().unwrap();
        let closed =
            result(state.execute(request("req_c", "target.close", json!({"target":target}))));
        assert_eq!(closed["teardown"]["owner_reference_extended"], true);
        let report = owners(&mut state);
        assert_eq!(
            report["owners"]["targets"]["objects"], 0,
            "the ledger still dropped the owner"
        );
        assert_eq!(report["owners"]["adapters"]["objects"], 0);
        assert_eq!(report["teardown"]["owner_references_extended_total"], 1);
        assert_eq!(stolen.target_id, target, "the leak holds names, not state");
        let stale = state.execute(request("req_i", "target.inspect", json!({"target":target})));
        assert!(!stale.ok, "names do not resurrect the target");
        drop(stolen);
    }

    #[test]
    fn session_close_detaches_adapters_before_releasing_the_profile_lock() {
        let (mut state, profile, session, target) = host_with_target();
        let second = result(state.execute(request(
            "req_t2",
            "target.open",
            json!({"session":session}),
        )))["target"]
            .as_str()
            .unwrap()
            .to_owned();
        let first_handle = state.attach_adapter(&target, "test").unwrap();
        let second_handle = state.attach_adapter(&second, "test").unwrap();
        result(state.execute(request("req_sh", "surface.show", json!({"target":target}))));
        let closed = result(state.execute(request(
            "req_sc",
            "session.close",
            json!({"session":session}),
        )));
        assert_eq!(closed["closed_targets"], 2);
        assert_eq!(closed["teardown"]["adapters_detached"], 2);
        assert_eq!(closed["teardown"]["surfaces_released"], 1);
        assert_eq!(closed["teardown"]["owner_reference_extended"], false);
        assert!(first_handle.upgrade().is_none() && second_handle.upgrade().is_none());
        let report = owners(&mut state);
        assert_eq!(report["owners"]["adapters"]["objects"], 0);
        assert_eq!(report["owners"]["surfaces"]["objects"], 0);
        assert_eq!(report["owners"]["targets"]["objects"], 0);
        // The profile lock is released only after the targets: deletion now succeeds.
        result(state.execute(request(
            "req_pd",
            "profile.delete",
            json!({"profile":profile}),
        )));
    }

    #[test]
    fn adapter_capacity_is_bounded() {
        let (mut state, _profile, _session, target) = host_with_target();
        for _ in 0..MAX_ADAPTERS {
            state.attach_adapter(&target, "test").unwrap();
        }
        let error = state.attach_adapter(&target, "test").unwrap_err();
        assert_eq!(error.code, "resource_limit");
        let missing = state.attach_adapter("target_404", "test").unwrap_err();
        assert_eq!(missing.code, "not_found");
    }
}
