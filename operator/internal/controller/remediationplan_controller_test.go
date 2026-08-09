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

// T3.8: pure unit tests for the state-machine skeleton — no cluster
// required (deliberately avoids envtest binaries; the full reconcile
// behaviour is covered by the Python operator-bridge tests + T3.9).

func TestNextStateEmptyBecomesProposed(t *testing.T) {
	if got := nextState("", false); got != StateProposed {
		t.Fatalf("nextState('', false) = %q, want %q", got, StateProposed)
	}
}

func TestNextStateDryRunStaysProposed(t *testing.T) {
	for _, current := range []string{"", "Proposed", "Approved"} {
		if got := nextState(current, true); got != StateProposed {
			t.Fatalf("nextState(%q, true) = %q, want %q (dry-run plans never advance)", current, got, StateProposed)
		}
	}
}

func TestNextStateNonEmptyStays(t *testing.T) {
	for _, current := range []string{StateProposed, StateApproved, StateApplied, StateVerified, StateClosed} {
		if got := nextState(current, false); got != current {
			t.Fatalf("nextState(%q, false) = %q, want %q (T3.9 adds real transitions)", current, got, current)
		}
	}
}

func TestLifecycleStateConstants(t *testing.T) {
	expected := []string{StateProposed, StateApproved, StateApplied, StateVerified, StateClosed}
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
