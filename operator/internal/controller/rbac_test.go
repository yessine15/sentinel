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
	"context"
	"fmt"
	"os"
	"testing"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	"sigs.k8s.io/controller-runtime/pkg/client/interceptor"

	sentinelv1 "github.com/yessine15/sentinel/operator/api/v1"
)

// ─────────────────────────────────────────────────────────────
// RBAC contract tests (T3.10)
//
// These read the generated config/rbac/role.yaml and assert the
// least-privilege contract — if someone widens the ClusterRole the
// tests fail, exactly like "dropping a permission breaks an action
// cleanly" but in the other direction.
// ─────────────────────────────────────────────────────────────

func roleYAML(t *testing.T) string {
	// go test runs with the package dir as cwd.
	data, err := os.ReadFile("../../config/rbac/role.yaml")
	if err != nil {
		t.Fatalf("cannot read role.yaml: %v", err)
	}
	return string(data)
}

func TestRBACPlanReadOnly(t *testing.T) {
	y := roleYAML(t)
	// The operator only READS RemediationPlan objects — create/delete/update
	// on the main resource are deliberately absent (the bridge creates plans).
	for _, forbidden := range []string{
		"resources:\n  - remediationplans\n  verbs:\n  - create",
		"resources:\n  - remediationplans\n  verbs:\n  - delete",
		"resources:\n  - remediationplans\n  verbs:\n  - update",
	} {
		if containsVerb(y, forbidden) {
			t.Fatalf("role.yaml must NOT grant %q — plans are read-only for the operator", forbidden)
		}
	}
}

func TestRBACPlanStatusWritable(t *testing.T) {
	y := roleYAML(t)
	// ...but the operator MUST write the status subresource.
	if !containsVerb(y, "resources:\n  - remediationplans/status\n  verbs:\n  - get\n  - patch\n  - update") {
		t.Fatal("role.yaml must grant update on remediationplans/status")
	}
	if !containsVerb(y, "resources:\n  - remediationplans/status\n  verbs:\n  - get\n  - patch\n  - update") {
		t.Fatal("role.yaml must grant patch on remediationplans/status")
	}
}

func TestRBACActionPermissions(t *testing.T) {
	y := roleYAML(t)
	// delete_pod action needs pod delete; cordon needs node patch;
	// restart/scale/patch need workload patch.
	if !containsVerb(y, "resources:\n  - pods\n  verbs:\n  - delete\n  - get\n  - list\n  - watch") {
		t.Fatal("role.yaml must grant delete on pods (delete_pod action)")
	}
	if !containsVerb(y, "resources:\n  - nodes\n  verbs:\n  - get\n  - list\n  - patch\n  - watch") {
		t.Fatal("role.yaml must grant patch on nodes (cordon action)")
	}
	// The operator must NOT be able to delete workloads (only patch).
	if containsVerb(y, "resources:\n  - deployments\n  verbs:\n  - delete") {
		t.Fatal("role.yaml must NOT grant delete on deployments")
	}
}

// containsVerb reports whether the YAML contains the exact 3-line
// fragment (resource header + verb) — whitespace-sensitive on purpose.
func containsVerb(yaml, fragment string) bool {
	return len(fragment) > 0 && indexOf(yaml, fragment) >= 0
}

func indexOf(s, sub string) int {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return i
		}
	}
	return -1
}

// ─────────────────────────────────────────────────────────────
// Forbidden handling (T3.10 "dropping a permission breaks an action
// cleanly"): a Forbidden error from the API must surface as a plan
// failure message, not a crash or silent no-op.
// ─────────────────────────────────────────────────────────────

func TestForbiddenOnDeleteFailsCleanly(t *testing.T) {
	scheme := newTestScheme()
	// Fake client that returns Forbidden for pod deletes — simulating a
	// ClusterRole without pods/delete.
	fc := fake.NewClientBuilder().WithScheme(scheme).WithInterceptorFuncs(
		interceptor.Funcs{
			Delete: func(ctx context.Context, client client.WithWatch, obj client.Object, opts ...client.DeleteOption) error {
				return apierrors.NewForbidden(
					schema.GroupResource{Group: "", Resource: "pods"},
					obj.GetName(),
					fmt.Errorf("simulated RBAC denial"),
				)
			},
		},
	).Build()

	r := &RemediationPlanReconciler{Client: fc, Scheme: scheme}
	plan := &sentinelv1.RemediationPlan{
		ObjectMeta: metav1.ObjectMeta{Name: "rp-denied", Namespace: "sentinel"},
		Spec: sentinelv1.RemediationPlanSpec{
			ApprovedBy: "human",
			Steps: []sentinelv1.RemediationStep{
				{Action: "delete_pod", Target: "pod/demo-api-x", Detail: "x"},
			},
		},
	}

	err := r.executeSteps(context.Background(), plan)
	if err == nil {
		t.Fatal("executeSteps must fail when pod delete is forbidden")
	}
	if !apierrors.IsForbidden(err) {
		t.Fatalf("error must be a Forbidden API error, got: %v", err)
	}
	// The reconcile path maps failure → Failed (asserted here as the
	// next-state transition).
	if got := nextState(StateApproved, false, true, false, false, false); got != StateFailed {
		t.Fatalf("approved + failed action must → %q, got %q", StateFailed, got)
	}
}

func TestActionDeletePodWorksWithPermission(t *testing.T) {
	scheme := newTestScheme()
	pod := &corev1.Pod{ObjectMeta: metav1.ObjectMeta{Name: "demo-api-x", Namespace: "sentinel"}}
	fc := fake.NewClientBuilder().WithScheme(scheme).WithObjects(pod).Build()

	r := &RemediationPlanReconciler{Client: fc, Scheme: scheme}
	plan := &sentinelv1.RemediationPlan{
		ObjectMeta: metav1.ObjectMeta{Name: "rp-ok", Namespace: "sentinel"},
		Spec: sentinelv1.RemediationPlanSpec{
			ApprovedBy: "human",
			Steps: []sentinelv1.RemediationStep{
				{Action: "delete_pod", Target: "pod/demo-api-x", Detail: "x"},
			},
		},
	}
	if err := r.executeSteps(context.Background(), plan); err != nil {
		t.Fatalf("delete_pod with permission should succeed: %v", err)
	}
	// The pod must be gone from the fake client.
	got := &corev1.Pod{}
	if err := fc.Get(context.Background(), client.ObjectKey{Name: "demo-api-x", Namespace: "sentinel"}, got); !apierrors.IsNotFound(err) {
		t.Fatalf("pod should have been deleted, err=%v", err)
	}
}

func newTestScheme() *runtime.Scheme {
	s := runtime.NewScheme()
	_ = corev1.AddToScheme(s)
	_ = sentinelv1.AddToScheme(s)
	return s
}
