/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package controller

import (
	"testing"
)

// T3.8/T3.9: pure unit tests for the state machine — no cluster
// required (deliberately avoids envtest binaries; the full reconcile
// behaviour is covered by the Python operator-bridge tests + live
// verification).

func TestNextStateEmptyBecomesProposed(t *testing.T) {
	if got := nextState("", false, false, false, false, false); got != StateProposed {
		t.Fatalf("nextState('') = %q, want %q", got, StateProposed)
	}
}

func TestNextStateProposedApproval(t *testing.T) {
	// Without approval: stay Proposed (wait).
	if got := nextState(StateProposed, false, false, false, false, false); got != StateProposed {
		t.Fatalf("nextState(Proposed, no approval) = %q, want Proposed", got)
	}
	// With approval: Approved.
	if got := nextState(StateProposed, false, true, false, false, false); got != StateApproved {
		t.Fatalf("nextState(Proposed, approved) = %q, want %q", got, StateApproved)
	}
}

func TestNextStateApprovedTransition(t *testing.T) {
	// Success → Applied.
	if got := nextState(StateApproved, false, true, true, false, false); got != StateApplied {
		t.Fatalf("nextState(Approved, success) = %q, want %q", got, StateApplied)
	}
	// Failure → Failed.
	if got := nextState(StateApproved, false, true, false, false, false); got != StateFailed {
		t.Fatalf("nextState(Approved, failure) = %q, want %q", got, StateFailed)
	}
}

func TestNextStateAppliedTransition(t *testing.T) {
	// Verified → Verified.
	if got := nextState(StateApplied, false, true, true, true, false); got != StateVerified {
		t.Fatalf("nextState(Applied, verified) = %q, want %q", got, StateVerified)
	}
	// Not verified yet → keep watching (stay Applied).
	if got := nextState(StateApplied, false, true, true, false, false); got != StateApplied {
		t.Fatalf("nextState(Applied, not verified) = %q, want Applied", got)
	}
}

func TestNextStateVerifiedTransition(t *testing.T) {
	// Cooldown elapsed → Closed.
	if got := nextState(StateVerified, false, true, true, true, true); got != StateClosed {
		t.Fatalf("nextState(Verified, cooldown done) = %q, want %q", got, StateClosed)
	}
	// Cooldown pending → stay Verified.
	if got := nextState(StateVerified, false, true, true, true, false); got != StateVerified {
		t.Fatalf("nextState(Verified, cooldown pending) = %q, want Verified", got)
	}
}

func TestNextStateTerminal(t *testing.T) {
	if got := nextState(StateClosed, false, true, true, true, true); got != StateClosed {
		t.Fatalf("nextState(Closed) = %q, want Closed", got)
	}
	if got := nextState(StateFailed, false, true, false, false, false); got != StateFailed {
		t.Fatalf("nextState(Failed) = %q, want Failed", got)
	}
}

func TestNextStateDryRunNeverAdvances(t *testing.T) {
	for _, current := range []string{"", StateProposed, StateApproved, StateApplied, StateVerified} {
		if got := nextState(current, true, true, true, true, true); got != StateProposed {
			t.Fatalf("nextState(%q, dryRun) = %q, want Proposed (dry-run plans never advance)", current, got)
		}
	}
}

func TestLifecycleStateConstants(t *testing.T) {
	expected := []string{StateProposed, StateApproved, StateApplied, StateVerified, StateClosed, StateFailed}
	seen := map[string]bool{}
	for _, s := range expected {
		if s == "" {
			t.Fatal("state constant must not be empty")
		}
		if seen[s] {
			t.Fatalf("duplicate state constant %q", s)
		}
		seen[s] = true
	}
}
