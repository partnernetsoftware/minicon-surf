//! Optional capability attenuation for one request (control 0.0.1 extension).
//!
//! A capability never grants. The host resolves the operation's principal
//! object and its ownership chain (target → session → profile) from its own
//! state, exactly as it would without a capability, and then checks that the
//! caller's declared owner is on that chain, that the operation is in the
//! declared scope, and that the request stays inside the declared budget.
//! Every decision is appended to a bounded audit ledger that is diagnostics,
//! not authority: nothing reads it to decide anything.

use std::collections::BTreeSet;

use serde_json::{Value, json};

use crate::{ControlError, ControlState, KNOWN_OPERATIONS, MAX_RESPONSE_BYTES, valid_typed_id};

/// Audit records kept per host; older records are dropped first.
pub const MAX_AUDIT_RECORDS: usize = 64;
const MAX_REASON_CHARS: usize = 128;
const SCOPE_KINDS: &[&str] = &["profile", "session", "target", "frame", "realm", "surface"];
const OWNER_KINDS: &[&str] = &["profile", "session", "target"];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Owner {
    pub kind: String,
    pub id: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Capability {
    pub owner: Owner,
    pub scope: BTreeSet<String>,
    pub result_bytes: usize,
    pub deadline_ms: u64,
    pub actor: String,
    pub reason: String,
}

/// The objects an operation's authority flows through, innermost first.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Chain {
    pub target: Option<String>,
    pub session: Option<String>,
    pub profile: Option<String>,
}

#[derive(Debug, Clone)]
pub struct AuditRecord {
    pub request_id: String,
    pub actor: String,
    pub reason: String,
    pub operation: String,
    pub owner: Owner,
    pub chain: Chain,
    pub decision: String,
}

impl AuditRecord {
    pub fn to_json(&self) -> Value {
        json!({
            "request_id":self.request_id,
            "actor":self.actor,
            "reason":self.reason,
            "operation":self.operation,
            "owner":{"kind":self.owner.kind,"id":self.owner.id},
            "decision":self.decision,
        })
    }
}

fn valid_actor(value: &str) -> bool {
    let bytes = value.as_bytes();
    (1..=64).contains(&bytes.len())
        && bytes.iter().enumerate().all(|(index, byte)| {
            byte.is_ascii_lowercase()
                || byte.is_ascii_digit()
                || (index > 0 && matches!(byte, b'_' | b'.' | b'-'))
        })
}

impl Capability {
    /// Parse the envelope field; any deviation from the schema is a
    /// single `capability differs` message so shape errors leak nothing.
    pub fn parse(value: &Value) -> Result<Capability, &'static str> {
        const DIFFERS: &str = "capability differs";
        let object = value.as_object().ok_or(DIFFERS)?;
        let keys = ["owner", "scope", "budget", "audit"];
        if object.len() != keys.len() || !keys.iter().all(|key| object.contains_key(*key)) {
            return Err(DIFFERS);
        }
        let owner = object["owner"].as_object().ok_or(DIFFERS)?;
        if owner.len() != 2 {
            return Err(DIFFERS);
        }
        let kind = owner
            .get("kind")
            .and_then(Value::as_str)
            .filter(|kind| SCOPE_KINDS.contains(kind))
            .ok_or(DIFFERS)?;
        let id = owner
            .get("id")
            .and_then(Value::as_str)
            .filter(|id| valid_typed_id(kind, id))
            .ok_or(DIFFERS)?;
        let scope_values = object["scope"].as_array().ok_or(DIFFERS)?;
        if scope_values.is_empty() || scope_values.len() > KNOWN_OPERATIONS.len() {
            return Err(DIFFERS);
        }
        let mut scope = BTreeSet::new();
        for entry in scope_values {
            let operation = entry
                .as_str()
                .filter(|operation| KNOWN_OPERATIONS.contains(operation))
                .ok_or(DIFFERS)?;
            if !scope.insert(operation.to_owned()) {
                return Err(DIFFERS);
            }
        }
        let budget = object["budget"].as_object().ok_or(DIFFERS)?;
        if budget.len() != 2 {
            return Err(DIFFERS);
        }
        let result_bytes = budget
            .get("result_bytes")
            .and_then(Value::as_u64)
            .filter(|bytes| (1..=MAX_RESPONSE_BYTES as u64).contains(bytes))
            .ok_or(DIFFERS)? as usize;
        let deadline_ms = budget
            .get("deadline_ms")
            .and_then(Value::as_u64)
            .filter(|ms| (1..=120_000).contains(ms))
            .ok_or(DIFFERS)?;
        let audit = object["audit"].as_object().ok_or(DIFFERS)?;
        if audit.len() != 2 {
            return Err(DIFFERS);
        }
        let actor = audit
            .get("actor")
            .and_then(Value::as_str)
            .filter(|actor| valid_actor(actor))
            .ok_or(DIFFERS)?;
        let reason = audit
            .get("reason")
            .and_then(Value::as_str)
            .filter(|reason| (1..=MAX_REASON_CHARS).contains(&reason.chars().count()))
            .ok_or(DIFFERS)?;
        Ok(Capability {
            owner: Owner {
                kind: kind.to_owned(),
                id: id.to_owned(),
            },
            scope,
            result_bytes,
            deadline_ms,
            actor: actor.to_owned(),
            reason: reason.to_owned(),
        })
    }
}

fn denied(reason: &str, message: &str, owner: &Owner, operation: &str) -> ControlError {
    ControlError::new("permission_denied", message, false)
        .scoped_owned(owner.kind.clone(), owner.id.clone())
        .details(json!({"reason":reason,"operation":operation}))
}

impl ControlState {
    /// The principal object an operation acts on and the chain above it,
    /// resolved from the host's own state. `Ok(None)` means the operation has
    /// no owned object. Missing objects fail exactly as the operation would.
    pub(crate) fn ownership_chain(
        &self,
        operation: &str,
        arguments: &Value,
    ) -> Result<Option<Chain>, ControlError> {
        let field = |key: &str, kind: &'static str| -> Result<String, ControlError> {
            arguments
                .get(key)
                .and_then(Value::as_str)
                .filter(|value| valid_typed_id(kind, value))
                .map(str::to_owned)
                .ok_or_else(|| crate::invalid(format!("{key} ID differs")))
        };
        let from_target = |target_id: String| -> Result<Chain, ControlError> {
            let target = self
                .targets
                .get(&target_id)
                .ok_or_else(|| crate::not_found("target", &target_id))?;
            let session = self
                .sessions
                .get(&target.session_id)
                .ok_or_else(|| crate::not_found("session", &target.session_id))?;
            Ok(Chain {
                target: Some(target_id),
                session: Some(session.id.clone()),
                profile: Some(session.profile_id.clone()),
            })
        };
        let from_session = |session_id: String| -> Result<Chain, ControlError> {
            let session = self
                .sessions
                .get(&session_id)
                .ok_or_else(|| crate::not_found("session", &session_id))?;
            Ok(Chain {
                target: None,
                session: Some(session_id),
                profile: Some(session.profile_id.clone()),
            })
        };
        let from_profile = |profile_id: String| -> Result<Chain, ControlError> {
            if !self.profiles.contains_key(&profile_id) {
                return Err(crate::not_found("profile", &profile_id));
            }
            Ok(Chain {
                target: None,
                session: None,
                profile: Some(profile_id),
            })
        };
        let chain = match operation {
            "target.inspect" | "target.close" | "target.snapshot" | "target.act"
            | "target.wait" | "target.screenshot" | "surface.show" => {
                from_target(field("target", "target")?)?
            }
            "surface.hide" => {
                let surface_id = field("surface", "surface")?;
                let surface = self
                    .surfaces
                    .get(&surface_id)
                    .ok_or_else(|| crate::not_found("surface", &surface_id))?;
                from_target(surface.target_id.clone())?
            }
            "target.open"
            | "session.close"
            | "session.inspect"
            | "profile.storage.put"
            | "profile.storage.get"
            | "profile.policy.set" => from_session(field("session", "session")?)?,
            "session.open" | "profile.inspect" | "profile.delete" => {
                from_profile(field("profile", "profile")?)?
            }
            _ => return Ok(None),
        };
        Ok(Some(chain))
    }

    /// Decide one attenuated request. Returns the chain for the audit record.
    pub(crate) fn authorize(
        &self,
        capability: &Capability,
        operation: &str,
        arguments: &Value,
        deadline_ms: u64,
    ) -> Result<Chain, ControlError> {
        let owner = &capability.owner;
        if owner.kind == "surface" {
            return Err(denied(
                "surface_is_not_an_owner",
                "a surface is never an owner; name the target, its session or its profile",
                owner,
                operation,
            ));
        }
        if !OWNER_KINDS.contains(&owner.kind.as_str()) {
            return Err(denied(
                "kind_is_not_an_owner",
                "only a profile, session or target can own an operation",
                owner,
                operation,
            ));
        }
        let Some(chain) = self.ownership_chain(operation, arguments)? else {
            return Err(denied(
                "operation_has_no_owner",
                "this operation acts on the host, not on an owned object, so it cannot be attenuated",
                owner,
                operation,
            ));
        };
        let on_chain = match owner.kind.as_str() {
            "target" => chain.target.as_deref() == Some(owner.id.as_str()),
            "session" => chain.session.as_deref() == Some(owner.id.as_str()),
            _ => chain.profile.as_deref() == Some(owner.id.as_str()),
        };
        if !on_chain {
            return Err(denied(
                "owner_not_on_chain",
                "the named owner does not own this operation's object",
                owner,
                operation,
            ));
        }
        if !capability.scope.contains(operation) {
            return Err(denied(
                "operation_outside_scope",
                "the operation is not in the capability scope",
                owner,
                operation,
            ));
        }
        if deadline_ms > capability.deadline_ms {
            return Err(denied(
                "deadline_exceeds_budget",
                "the request deadline exceeds the capability deadline budget",
                owner,
                operation,
            ));
        }
        if operation == "target.snapshot"
            && arguments
                .get("max_bytes")
                .and_then(Value::as_u64)
                .is_some_and(|max_bytes| max_bytes > capability.result_bytes as u64)
        {
            return Err(denied(
                "result_budget_exceeded",
                "the requested snapshot bytes exceed the capability result budget",
                owner,
                operation,
            ));
        }
        Ok(chain)
    }

    pub(crate) fn record_audit(&mut self, record: AuditRecord) {
        if self.audit.len() >= MAX_AUDIT_RECORDS {
            self.audit.pop_front();
        }
        self.audit.push_back(record);
    }

    pub(crate) fn audit_for_session(&self, session_id: &str) -> Vec<Value> {
        self.audit
            .iter()
            .filter(|record| record.chain.session.as_deref() == Some(session_id))
            .map(AuditRecord::to_json)
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn capability(owner_kind: &str, owner_id: &str, scope: &[&str]) -> Value {
        json!({
            "owner":{"kind":owner_kind,"id":owner_id},
            "scope":scope,
            "budget":{"result_bytes":4096,"deadline_ms":500},
            "audit":{"actor":"agent.test","reason":"unit"},
        })
    }

    #[test]
    fn parse_accepts_the_schema_shape_and_nothing_else() {
        let parsed =
            Capability::parse(&capability("target", "target_1", &["target.snapshot"])).unwrap();
        assert_eq!(parsed.owner.kind, "target");
        assert_eq!(parsed.result_bytes, 4096);
        assert!(parsed.scope.contains("target.snapshot"));
        let mut extra = capability("target", "target_1", &["target.snapshot"]);
        extra["grant"] = json!("everything");
        assert!(Capability::parse(&extra).is_err());
        assert!(Capability::parse(&capability("node", "node_1", &["target.snapshot"])).is_err());
        assert!(
            Capability::parse(&capability("target", "session_1", &["target.snapshot"])).is_err()
        );
        assert!(Capability::parse(&capability("target", "target_1", &[])).is_err());
        assert!(Capability::parse(&capability("target", "target_1", &["engine.any"])).is_err());
        assert!(
            Capability::parse(&capability(
                "target",
                "target_1",
                &["target.act", "target.act"]
            ))
            .is_err()
        );
        let mut zero = capability("target", "target_1", &["target.snapshot"]);
        zero["budget"]["result_bytes"] = json!(0);
        assert!(Capability::parse(&zero).is_err());
        let mut actor = capability("target", "target_1", &["target.snapshot"]);
        actor["audit"]["actor"] = json!("Agent Court");
        assert!(Capability::parse(&actor).is_err());
        let mut reason = capability("target", "target_1", &["target.snapshot"]);
        reason["audit"]["reason"] = json!("x".repeat(129));
        assert!(Capability::parse(&reason).is_err());
    }

    #[test]
    fn authorization_attenuates_and_never_amplifies() {
        let mut state = ControlState::default();
        let profile = crate::tests::result(state.execute(crate::tests::request(
            "req_p",
            "profile.create",
            json!({"persistence":"ephemeral"}),
        )))["profile"]
            .as_str()
            .unwrap()
            .to_owned();
        let session = crate::tests::result(state.execute(crate::tests::request(
            "req_s",
            "session.open",
            json!({"profile":profile}),
        )))["session"]
            .as_str()
            .unwrap()
            .to_owned();
        let target = crate::tests::result(state.execute(crate::tests::request(
            "req_t",
            "target.open",
            json!({"session":session}),
        )))["target"]
            .as_str()
            .unwrap()
            .to_owned();
        let other_session = crate::tests::result(state.execute(crate::tests::request(
            "req_s2",
            "session.open",
            json!({"profile":profile}),
        )))["session"]
            .as_str()
            .unwrap()
            .to_owned();
        let snapshot = json!({"target":target,"format":"semantic","max_bytes":4096,"max_nodes":10});
        let reason = |outcome: Result<Chain, ControlError>| -> String {
            let error = outcome.unwrap_err();
            assert_eq!(error.code, "permission_denied");
            error.details.unwrap()["reason"]
                .as_str()
                .unwrap()
                .to_owned()
        };
        for (kind, id) in [
            ("target", target.as_str()),
            ("session", session.as_str()),
            ("profile", profile.as_str()),
        ] {
            let ok = Capability::parse(&capability(kind, id, &["target.snapshot"])).unwrap();
            let chain = state
                .authorize(&ok, "target.snapshot", &snapshot, 100)
                .unwrap();
            assert_eq!(chain.target.as_deref(), Some(target.as_str()));
        }
        let surface =
            Capability::parse(&capability("surface", "surface_1", &["target.snapshot"])).unwrap();
        assert_eq!(
            reason(state.authorize(&surface, "target.snapshot", &snapshot, 100)),
            "surface_is_not_an_owner"
        );
        let realm =
            Capability::parse(&capability("realm", "realm_1", &["target.snapshot"])).unwrap();
        assert_eq!(
            reason(state.authorize(&realm, "target.snapshot", &snapshot, 100)),
            "kind_is_not_an_owner"
        );
        let off_chain =
            Capability::parse(&capability("session", &other_session, &["target.snapshot"]))
                .unwrap();
        assert_eq!(
            reason(state.authorize(&off_chain, "target.snapshot", &snapshot, 100)),
            "owner_not_on_chain"
        );
        let ghost =
            Capability::parse(&capability("target", "target_999", &["target.snapshot"])).unwrap();
        assert_eq!(
            reason(state.authorize(&ghost, "target.snapshot", &snapshot, 100)),
            "owner_not_on_chain"
        );
        let narrow = Capability::parse(&capability("target", &target, &["target.wait"])).unwrap();
        assert_eq!(
            reason(state.authorize(&narrow, "target.snapshot", &snapshot, 100)),
            "operation_outside_scope"
        );
        let wide = Capability::parse(&capability("target", &target, &["target.snapshot"])).unwrap();
        assert_eq!(
            reason(state.authorize(&wide, "target.snapshot", &snapshot, 501)),
            "deadline_exceeds_budget"
        );
        let big = json!({"target":target,"format":"semantic","max_bytes":4097,"max_nodes":10});
        assert_eq!(
            reason(state.authorize(&wide, "target.snapshot", &big, 100)),
            "result_budget_exceeded"
        );
        let host_wide =
            Capability::parse(&capability("profile", &profile, &["memory.report"])).unwrap();
        assert_eq!(
            reason(state.authorize(&host_wide, "memory.report", &json!({}), 100)),
            "operation_has_no_owner"
        );
        // A missing principal fails exactly as the operation would.
        let missing =
            json!({"target":"target_404","format":"semantic","max_bytes":4096,"max_nodes":10});
        let error = state
            .authorize(&wide, "target.snapshot", &missing, 100)
            .unwrap_err();
        assert_eq!(error.code, "not_found");
    }

    #[test]
    fn audit_ledger_is_bounded_and_scoped_to_sessions() {
        let mut state = ControlState::default();
        for index in 0..(MAX_AUDIT_RECORDS + 10) {
            state.record_audit(AuditRecord {
                request_id: format!("req_{index}"),
                actor: "agent.test".into(),
                reason: "unit".into(),
                operation: "target.snapshot".into(),
                owner: Owner {
                    kind: "session".into(),
                    id: "session_1".into(),
                },
                chain: Chain {
                    target: None,
                    session: Some(
                        if index % 2 == 0 {
                            "session_1"
                        } else {
                            "session_2"
                        }
                        .into(),
                    ),
                    profile: None,
                },
                decision: "allowed".into(),
            });
        }
        assert_eq!(state.audit.len(), MAX_AUDIT_RECORDS);
        assert_eq!(
            state.audit.front().unwrap().request_id,
            "req_10",
            "oldest records drop first"
        );
        assert_eq!(
            state.audit_for_session("session_1").len(),
            MAX_AUDIT_RECORDS / 2
        );
        assert!(state.audit_for_session("session_3").is_empty());
    }
}
