use crate::marker::PendingVerifyMarker;
use crate::state::GuardState;

/// Everything the pure decision needs. No filesystem or process calls
/// happen in here or in `decide()` -- that's the point: this function is
/// exhaustively unit-testable without touching disk.
pub struct DecisionInput<'a> {
    pub marker: Option<&'a PendingVerifyMarker>,
    pub state: Option<&'a GuardState>,
    pub current_boot_id: &'a str,
    pub current_uptime_sec: f64,
    pub health: Option<bool>,
    pub t_verify_sec: f64,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Decision {
    /// No marker pending, or the marker can't be acted on (unparseable, or
    /// no `previous` to roll back to) -- take no action.
    NoAction,
    /// Health passed: caller clears the marker. Nothing to roll back.
    HealthyClearMarker,
    /// Still within the grace period: caller persists `next_state` (only if
    /// it differs from what's stored -- see state::save_state_if_changed).
    Wait { next_state: GuardState },
    /// Roll back to `target` (the marker's `previous`), then persist
    /// `next_state` and rewrite the marker as rolled-back.
    RollBack {
        target: String,
        next_state: GuardState,
    },
}

/// Implements decision D-3, exactly as settled: elapsed time is always
/// measured from monotonic uptime, never wall clock (`applied_at_epoch_ms`
/// on the marker is diagnostics-only and is never read here).
pub fn decide(input: &DecisionInput) -> Decision {
    let marker = match input.marker {
        Some(m) if m.previous.is_some() => m,
        _ => return Decision::NoAction,
    };

    match input.health {
        // No probe available, or it failed to run: guard must never act on
        // an unverified premise -- "we don't know" is not "it's fine" and
        // not "it's broken" either.
        None => return Decision::NoAction,
        Some(true) => return Decision::HealthyClearMarker,
        Some(false) => {}
    }

    let (first_seen_uptime_sec, boot_count) = match input.state {
        Some(s) if s.boot_id == input.current_boot_id => (s.first_seen_uptime_sec, s.boot_count),
        Some(s) => (input.current_uptime_sec, s.boot_count + 1),
        None => (input.current_uptime_sec, 1),
    };

    let elapsed = input.current_uptime_sec - first_seen_uptime_sec;
    let next_state = GuardState {
        boot_id: input.current_boot_id.to_string(),
        first_seen_uptime_sec,
        boot_count,
    };

    if elapsed >= input.t_verify_sec || boot_count >= 2 {
        Decision::RollBack {
            target: marker.previous.clone().expect("checked above"),
            next_state,
        }
    } else {
        Decision::Wait { next_state }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn marker(previous: Option<&str>) -> PendingVerifyMarker {
        PendingVerifyMarker {
            previous: previous.map(str::to_string),
            applied: "hub-0.2.2".to_string(),
            applied_at_epoch_ms: 1_757_212_800_000,
        }
    }

    fn state(boot_id: &str, first_seen: f64, boot_count: u32) -> GuardState {
        GuardState {
            boot_id: boot_id.to_string(),
            first_seen_uptime_sec: first_seen,
            boot_count,
        }
    }

    const PREV: &str = "/opt/fitracestudio/releases/hub-0.2.1";

    #[test]
    fn no_marker_is_no_action() {
        let input = DecisionInput {
            marker: None,
            state: None,
            current_boot_id: "boot-a",
            current_uptime_sec: 100.0,
            health: Some(false),
            t_verify_sec: 600.0,
        };
        assert_eq!(decide(&input), Decision::NoAction);
    }

    #[test]
    fn marker_with_null_previous_is_no_action_even_when_unhealthy_and_overdue() {
        let m = marker(None);
        let input = DecisionInput {
            marker: Some(&m),
            state: None,
            current_boot_id: "boot-a",
            current_uptime_sec: 10_000.0,
            health: Some(false),
            t_verify_sec: 600.0,
        };
        assert_eq!(decide(&input), Decision::NoAction);
    }

    #[test]
    fn healthy_clears_marker_regardless_of_elapsed_or_boot_count() {
        let m = marker(Some(PREV));
        let s = state("boot-a", 0.0, 5);
        let input = DecisionInput {
            marker: Some(&m),
            state: Some(&s),
            current_boot_id: "boot-a",
            current_uptime_sec: 999_999.0,
            health: Some(true),
            t_verify_sec: 600.0,
        };
        assert_eq!(decide(&input), Decision::HealthyClearMarker);
    }

    #[test]
    fn below_t_verify_first_boot_waits_and_seeds_state() {
        let m = marker(Some(PREV));
        let input = DecisionInput {
            marker: Some(&m),
            state: None,
            current_boot_id: "boot-a",
            current_uptime_sec: 50.0,
            health: Some(false),
            t_verify_sec: 600.0,
        };
        assert_eq!(
            decide(&input),
            Decision::Wait {
                next_state: state("boot-a", 50.0, 1)
            }
        );
    }

    #[test]
    fn just_below_t_verify_within_same_boot_waits() {
        let m = marker(Some(PREV));
        let s = state("boot-a", 0.0, 1);
        let input = DecisionInput {
            marker: Some(&m),
            state: Some(&s),
            current_boot_id: "boot-a",
            current_uptime_sec: 599.999,
            health: Some(false),
            t_verify_sec: 600.0,
        };
        assert_eq!(
            decide(&input),
            Decision::Wait {
                next_state: state("boot-a", 0.0, 1)
            }
        );
    }

    #[test]
    fn exactly_at_t_verify_rolls_back() {
        let m = marker(Some(PREV));
        let s = state("boot-a", 0.0, 1);
        let input = DecisionInput {
            marker: Some(&m),
            state: Some(&s),
            current_boot_id: "boot-a",
            current_uptime_sec: 600.0,
            health: Some(false),
            t_verify_sec: 600.0,
        };
        assert_eq!(
            decide(&input),
            Decision::RollBack {
                target: PREV.to_string(),
                next_state: state("boot-a", 0.0, 1),
            }
        );
    }

    #[test]
    fn above_t_verify_rolls_back() {
        let m = marker(Some(PREV));
        let s = state("boot-a", 0.0, 1);
        let input = DecisionInput {
            marker: Some(&m),
            state: Some(&s),
            current_boot_id: "boot-a",
            current_uptime_sec: 10_000.0,
            health: Some(false),
            t_verify_sec: 600.0,
        };
        assert_eq!(
            decide(&input),
            Decision::RollBack {
                target: PREV.to_string(),
                next_state: state("boot-a", 0.0, 1),
            }
        );
    }

    #[test]
    fn second_boot_still_pending_increments_boot_count_and_resets_timer() {
        let m = marker(Some(PREV));
        let s = state("boot-a", 0.0, 1);
        let input = DecisionInput {
            marker: Some(&m),
            state: Some(&s),
            current_boot_id: "boot-b",
            current_uptime_sec: 5.0,
            health: Some(false),
            t_verify_sec: 600.0,
        };
        // boot_count becomes 2 -> the boot-count arm fires immediately,
        // even though elapsed-since-this-boot is tiny.
        assert_eq!(
            decide(&input),
            Decision::RollBack {
                target: PREV.to_string(),
                next_state: state("boot-b", 5.0, 2),
            }
        );
    }

    #[test]
    fn boot_count_one_alone_does_not_force_rollback() {
        let m = marker(Some(PREV));
        let s = state("boot-a", 0.0, 1);
        let input = DecisionInput {
            marker: Some(&m),
            state: Some(&s),
            current_boot_id: "boot-a",
            current_uptime_sec: 5.0,
            health: Some(false),
            t_verify_sec: 600.0,
        };
        assert_eq!(
            decide(&input),
            Decision::Wait {
                next_state: state("boot-a", 0.0, 1)
            }
        );
    }

    #[test]
    fn decision_is_unaffected_by_wildly_wrong_wall_clock_on_the_marker() {
        // applied_at_epoch_ms far in the future and far in the past --
        // decide() must never read it, only monotonic uptime.
        let future = PendingVerifyMarker {
            previous: Some(PREV.to_string()),
            applied: "hub-0.2.2".to_string(),
            applied_at_epoch_ms: 99_999_999_999_999,
        };
        let past = PendingVerifyMarker {
            previous: Some(PREV.to_string()),
            applied: "hub-0.2.2".to_string(),
            applied_at_epoch_ms: -99_999_999_999_999,
        };
        let s = state("boot-a", 0.0, 1);
        let expected = Decision::Wait {
            next_state: state("boot-a", 0.0, 1),
        };

        let input_for = |m: &PendingVerifyMarker| -> Decision {
            decide(&DecisionInput {
                marker: Some(m),
                state: Some(&s),
                current_boot_id: "boot-a",
                current_uptime_sec: 5.0,
                health: Some(false),
                t_verify_sec: 600.0,
            })
        };

        assert_eq!(input_for(&future), expected);
        assert_eq!(input_for(&past), expected);
    }

    #[test]
    fn unknown_health_well_past_t_verify_is_no_action_not_rollback() {
        let m = marker(Some(PREV));
        let s = state("boot-a", 0.0, 1);
        let input = DecisionInput {
            marker: Some(&m),
            state: Some(&s),
            current_boot_id: "boot-a",
            current_uptime_sec: 10_000.0,
            health: None,
            t_verify_sec: 600.0,
        };
        assert_eq!(decide(&input), Decision::NoAction);
    }

    #[test]
    fn unknown_health_at_rollback_boot_count_threshold_is_no_action_not_rollback() {
        let m = marker(Some(PREV));
        let s = state("boot-a", 0.0, 1);
        let input = DecisionInput {
            marker: Some(&m),
            state: Some(&s),
            current_boot_id: "boot-b",
            current_uptime_sec: 5.0,
            health: None,
            t_verify_sec: 600.0,
        };
        // boot_count would become 2 (the rollback threshold) if health were
        // known -- with health unknown, guard must still take no action.
        assert_eq!(decide(&input), Decision::NoAction);
    }

    #[test]
    fn unknown_health_does_not_clear_the_marker_either() {
        // Decision::NoAction leaves the marker file untouched -- unknown
        // health must never be mistaken for success (HealthyClearMarker).
        let m = marker(Some(PREV));
        let s = state("boot-a", 0.0, 1);
        let input = DecisionInput {
            marker: Some(&m),
            state: Some(&s),
            current_boot_id: "boot-a",
            current_uptime_sec: 10_000.0,
            health: None,
            t_verify_sec: 600.0,
        };
        let decision = decide(&input);
        assert_ne!(decision, Decision::HealthyClearMarker);
        assert_eq!(decision, Decision::NoAction);
    }
}
