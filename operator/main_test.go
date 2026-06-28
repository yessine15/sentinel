package main

import "testing"

func TestVersionStartsWithZero(t *testing.T) {
	if !startsWithZero(Version) {
		t.Fatalf("expected Version to start with '0.', got %q", Version)
	}
}

// startsWithZero is a tiny helper to give the build something to test.
// It's deliberately not exported.
func startsWithZero(s string) bool {
	return len(s) >= 2 && s[0] == '0' && s[1] == '.'
}
