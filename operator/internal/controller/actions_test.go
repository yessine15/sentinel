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

// T3.9: unit tests for the executor action helpers — target parsing,
// replica parsing, resource-change parsing and the action allow-list.

func TestParseTargetValid(t *testing.T) {
	cases := []struct {
		in       string
		kind, nm string
	}{
		{"deployment/demo-api", "deployment", "demo-api"},
		{"statefulset/postgres", "statefulset", "postgres"},
		{"node/kind-worker", "node", "kind-worker"},
		{"pod/demo-api-7d9-abcde", "pod", "demo-api-7d9-abcde"},
		{"daemonset/promtail", "daemonset", "promtail"},
	}
	for _, c := range cases {
		got, err := parseTarget(c.in)
		if err != nil {
			t.Fatalf("parseTarget(%q) unexpected error: %v", c.in, err)
		}
		if got.kind != c.kind || got.name != c.nm {
			t.Fatalf("parseTarget(%q) = %+v, want kind=%q name=%q", c.in, got, c.kind, c.nm)
		}
	}
}

func TestParseTargetInvalid(t *testing.T) {
	for _, in := range []string{"", "demo-api", "service/demo-api", "deployment/", "Deployment/demo-api", "deployment/a b", "deployment/../etc"} {
		if _, err := parseTarget(in); err == nil {
			t.Fatalf("parseTarget(%q) should fail", in)
		}
	}
}

func TestParseReplicas(t *testing.T) {
	n, err := parseReplicas("Scale to 3 replicas")
	if err != nil || n != 3 {
		t.Fatalf("parseReplicas('Scale to 3 replicas') = %d, %v; want 3", n, err)
	}
	n, err = parseReplicas("scale deployment to 2")
	if err != nil || n != 2 {
		t.Fatalf("parseReplicas('scale to 2') = %d, %v; want 2", n, err)
	}
	if _, err := parseReplicas("no number here"); err == nil {
		t.Fatal("parseReplicas should fail without a number")
	}
	if _, err := parseReplicas("scale to 0"); err == nil {
		t.Fatal("parseReplicas should fail with 0 replicas")
	}
}

func TestParseResourceChange(t *testing.T) {
	resource, from, to, err := parseResourceChange("Raise memory limit 1Gi -> 2Gi")
	if err != nil {
		t.Fatalf("parseResourceChange unexpected error: %v", err)
	}
	if resource != "memory" || from != "1Gi" || to != "2Gi" {
		t.Fatalf("parseResourceChange = %q,%q,%q; want memory,1Gi,2Gi", resource, from, to)
	}

	resource, from, to, err = parseResourceChange("reduce cpu from 500m to 250m")
	if err != nil {
		t.Fatalf("parseResourceChange(cpu) unexpected error: %v", err)
	}
	if resource != "cpu" || from != "500m" || to != "250m" {
		t.Fatalf("parseResourceChange(cpu) = %q,%q,%q; want cpu,500m,250m", resource, from, to)
	}

	if _, _, _, err := parseResourceChange("no quantities here"); err == nil {
		t.Fatal("parseResourceChange should fail without memory/cpu")
	}
	if _, _, _, err := parseResourceChange("memory 1Gi only"); err == nil {
		t.Fatal("parseResourceChange should fail with a single quantity")
	}
}

func TestAllowedExecutorActions(t *testing.T) {
	for _, a := range []string{"restart", "scale", "rollback", "cordon", "drain", "patch", "delete_pod", "escalate"} {
		if !AllowedExecutorActions[a] {
			t.Fatalf("action %q must be allowed", a)
		}
	}
	// Dangerous verbs are never allowed.
	for _, a := range []string{"delete", "exec", "create", "apply", "edit", "replace", "rm", ""} {
		if AllowedExecutorActions[a] {
			t.Fatalf("action %q must NOT be allowed", a)
		}
	}
}
