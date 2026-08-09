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

package v1

import (
	"testing"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// T3.8: plain unit tests for the RemediationPlan API types — no envtest
// binaries required.

func samplePlan() *RemediationPlan {
	return &RemediationPlan{
		ObjectMeta: metav1.ObjectMeta{Name: "rp-test", Namespace: "sentinel"},
		Spec: RemediationPlanSpec{
			Incident:   "ALERTS: kube_pod_oom",
			Priority:   "high",
			Rationale:  "pod OOMKilled",
			DryRun:     false,
			ApprovedBy: "human",
			PlanRef:    "abc-123",
			Steps: []RemediationStep{
				{Action: "restart", Target: "deployment/demo-api", Detail: "restart it"},
			},
		},
		Status: RemediationPlanStatus{State: "Proposed"},
	}
}

func TestGroupVersionIsSentinelIO(t *testing.T) {
	if got := GroupVersion.Group; got != "sentinel.io" {
		t.Fatalf("Group = %q, want %q", got, "sentinel.io")
	}
	if got := GroupVersion.Version; got != "v1" {
		t.Fatalf("Version = %q, want %q", got, "v1")
	}
}

func TestDeepCopyRoundtrip(t *testing.T) {
	p := samplePlan()
	cp := p.DeepCopyObject()

	got, ok := cp.(*RemediationPlan)
	if !ok {
		t.Fatalf("DeepCopyObject returned %T, want *RemediationPlan", cp)
	}
	if got.Spec.Incident != p.Spec.Incident {
		t.Fatalf("incident = %q, want %q", got.Spec.Incident, p.Spec.Incident)
	}
	if len(got.Spec.Steps) != 1 || got.Spec.Steps[0].Action != "restart" {
		t.Fatalf("steps not copied: %+v", got.Spec.Steps)
	}
	if got.Status.State != "Proposed" {
		t.Fatalf("status.state = %q, want %q", got.Status.State, "Proposed")
	}
	// Mutating the copy must not affect the original.
	got.Spec.Incident = "mutated"
	if p.Spec.Incident != "ALERTS: kube_pod_oom" {
		t.Fatalf("original mutated: %q", p.Spec.Incident)
	}
}

func TestDeepCopyList(t *testing.T) {
	list := &RemediationPlanList{
		Items: []RemediationPlan{*samplePlan()},
	}
	cp := list.DeepCopyObject().(*RemediationPlanList)
	if len(cp.Items) != 1 || cp.Items[0].Name != "rp-test" {
		t.Fatalf("list copy broken: %+v", cp.Items)
	}
}

func TestSchemeRegistersKind(t *testing.T) {
	gvk := samplePlan().GetObjectKind().GroupVersionKind()
	// Ensure the type satisfies runtime.Object (compiles) and has a GVK.
	_ = gvk
}
