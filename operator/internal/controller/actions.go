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
	"regexp"
	"strconv"
	"strings"
	"time"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"

	sentinelv1 "github.com/yessine15/sentinel/operator/api/v1"
)

// AllowedExecutorActions mirrors ALLOWED_EXECUTOR_ACTIONS in the agent
// layer (agents/sentinel_agents/tools/base.py) — the operator enforces
// the same vocabulary, so a step that slipped past the bridge is still
// rejected here.
var AllowedExecutorActions = map[string]bool{
	"restart":    true,
	"scale":      true,
	"rollback":   true,
	"cordon":     true,
	"drain":      true,
	"patch":      true,
	"delete_pod": true,
	"escalate":   true,
}

// stepTarget is a parsed "kind/name" target string.
type stepTarget struct {
	kind string // deployment | statefulset | daemonset | node | pod
	name string
}

var targetRe = regexp.MustCompile(`^(deployment|statefulset|daemonset|node|pod)/([a-z0-9][a-z0-9.-]*)$`)

// parseTarget parses "deployment/demo-api" style targets.
func parseTarget(target string) (stepTarget, error) {
	m := targetRe.FindStringSubmatch(strings.TrimSpace(target))
	if m == nil {
		return stepTarget{}, fmt.Errorf("unsupported target %q (expected kind/name, kind in deployment|statefulset|daemonset|node|pod)", target)
	}
	return stepTarget{kind: m[1], name: m[2]}, nil
}

var numberRe = regexp.MustCompile(`\b(\d+)\b`)

// parseReplicas extracts the replica count from a scale step's detail,
// e.g. "Scale to 3 replicas".
func parseReplicas(detail string) (int32, error) {
	m := numberRe.FindStringSubmatch(detail)
	if m == nil {
		return 0, fmt.Errorf("no replica count found in %q", detail)
	}
	n, err := strconv.Atoi(m[1])
	if err != nil || n < 1 {
		return 0, fmt.Errorf("invalid replica count in %q", detail)
	}
	return int32(n), nil
}

var quantityRe = regexp.MustCompile(`(\d+(?:\.\d+)?)\s*(m|Mi|Gi)`)

// parseResourceChange extracts a resource-limit change from a patch
// step's detail, e.g. "Raise memory limit 1Gi -> 2Gi".  Returns the
// resource ("memory" | "cpu"), the current value and the target value.
func parseResourceChange(detail string) (resource, from, to string, err error) {
	lower := strings.ToLower(detail)
	switch {
	case strings.Contains(lower, "memory"):
		resource = "memory"
	case strings.Contains(lower, "cpu"):
		resource = "cpu"
	default:
		return "", "", "", fmt.Errorf("patch detail must mention memory or cpu: %q", detail)
	}
	qs := quantityRe.FindAllStringSubmatch(detail, -1)
	if len(qs) < 2 {
		return "", "", "", fmt.Errorf("patch detail needs 'from -> to' quantities: %q", detail)
	}
	from = qs[len(qs)-2][1] + qs[len(qs)-2][2]
	to = qs[len(qs)-1][1] + qs[len(qs)-1][2]
	return resource, from, to, nil
}

// newTargetObject returns a typed object for a target kind.
func newTargetObject(kind string) (client.Object, error) {
	switch kind {
	case "deployment":
		return &appsv1.Deployment{}, nil
	case "statefulset":
		return &appsv1.StatefulSet{}, nil
	case "daemonset":
		return &appsv1.DaemonSet{}, nil
	case "node":
		return &corev1.Node{}, nil
	case "pod":
		return &corev1.Pod{}, nil
	}
	return nil, fmt.Errorf("unsupported target kind %q", kind)
}

// executeSteps runs every step of the plan through the allow-listed
// executor.  The first failure aborts the plan.
func (r *RemediationPlanReconciler) executeSteps(ctx context.Context, plan *sentinelv1.RemediationPlan) error {
	for _, step := range plan.Spec.Steps {
		if !AllowedExecutorActions[step.Action] {
			return fmt.Errorf("step action %q is NOT allowed", step.Action)
		}
		if err := r.executeStep(ctx, plan.Namespace, step); err != nil {
			return fmt.Errorf("step %q %s failed: %w", step.Action, step.Target, err)
		}
	}
	return nil
}

// executeStep dispatches one step to its action implementation.
func (r *RemediationPlanReconciler) executeStep(ctx context.Context, ns string, step sentinelv1.RemediationStep) error {
	t, err := parseTarget(step.Target)
	if err != nil {
		return err
	}
	switch step.Action {
	case "escalate":
		return fmt.Errorf("escalate requires a human — no automatic action")
	case "restart":
		return r.actionRestart(ctx, ns, t)
	case "scale":
		return r.actionScale(ctx, ns, t, step.Detail)
	case "patch":
		return r.actionPatch(ctx, ns, t, step.Detail)
	case "cordon":
		return r.actionCordon(ctx, t)
	case "delete_pod":
		return r.actionDeletePod(ctx, ns, t)
	case "rollback", "drain":
		return fmt.Errorf("action %q is not implemented by the operator yet", step.Action)
	}
	return fmt.Errorf("unknown action %q", step.Action)
}

// actionRestart performs a rollout restart (annotation patch) on a
// workload — the same mechanism as `kubectl rollout restart`.
func (r *RemediationPlanReconciler) actionRestart(ctx context.Context, ns string, t stepTarget) error {
	if t.kind == "node" || t.kind == "pod" {
		return fmt.Errorf("restart does not apply to %s targets", t.kind)
	}
	obj, err := newTargetObject(t.kind)
	if err != nil {
		return err
	}
	if err := r.Get(ctx, types.NamespacedName{Name: t.name, Namespace: ns}, obj); err != nil {
		return err
	}
	restartedAt := time.Now().UTC().Format(time.RFC3339)
	data := []byte(fmt.Sprintf(
		`{"spec":{"template":{"metadata":{"annotations":{"kubectl.kubernetes.io/restartedAt":%q}}}}}`,
		restartedAt,
	))
	return r.Patch(ctx, obj, client.RawPatch(types.MergePatchType, data))
}

// actionScale patches spec.replicas on a deployment/statefulset.
func (r *RemediationPlanReconciler) actionScale(ctx context.Context, ns string, t stepTarget, detail string) error {
	if t.kind != "deployment" && t.kind != "statefulset" {
		return fmt.Errorf("scale does not apply to %s targets", t.kind)
	}
	replicas, err := parseReplicas(detail)
	if err != nil {
		return err
	}
	obj, err := newTargetObject(t.kind)
	if err != nil {
		return err
	}
	if err := r.Get(ctx, types.NamespacedName{Name: t.name, Namespace: ns}, obj); err != nil {
		return err
	}
	data := []byte(fmt.Sprintf(`{"spec":{"replicas":%d}}`, replicas))
	return r.Patch(ctx, obj, client.RawPatch(types.MergePatchType, data))
}

// actionPatch applies a resource-limit change to the first container of
// a workload, e.g. "Raise memory limit 1Gi -> 2Gi".
func (r *RemediationPlanReconciler) actionPatch(ctx context.Context, ns string, t stepTarget, detail string) error {
	if t.kind != "deployment" && t.kind != "statefulset" && t.kind != "daemonset" {
		return fmt.Errorf("patch does not apply to %s targets", t.kind)
	}
	resource, _, to, err := parseResourceChange(detail)
	if err != nil {
		return err
	}
	obj, err := newTargetObject(t.kind)
	if err != nil {
		return err
	}
	if err := r.Get(ctx, types.NamespacedName{Name: t.name, Namespace: ns}, obj); err != nil {
		return err
	}

	var containerName string
	switch o := obj.(type) {
	case *appsv1.Deployment:
		if len(o.Spec.Template.Spec.Containers) == 0 {
			return fmt.Errorf("deployment %s has no containers", t.name)
		}
		containerName = o.Spec.Template.Spec.Containers[0].Name
	case *appsv1.StatefulSet:
		if len(o.Spec.Template.Spec.Containers) == 0 {
			return fmt.Errorf("statefulset %s has no containers", t.name)
		}
		containerName = o.Spec.Template.Spec.Containers[0].Name
	case *appsv1.DaemonSet:
		if len(o.Spec.Template.Spec.Containers) == 0 {
			return fmt.Errorf("daemonset %s has no containers", t.name)
		}
		containerName = o.Spec.Template.Spec.Containers[0].Name
	}

	data := []byte(fmt.Sprintf(
		`{"spec":{"template":{"spec":{"containers":[{"name":%q,"resources":{"limits":{%q:%q}}}]}}}}`,
		containerName, resource, to,
	))
	// Strategic merge patch: JSON merge patch (RFC 7386) REPLACES the
	// whole containers array instead of merging by name, which would
	// wipe image/ports etc.  Strategic merge understands the k8s
	// patchStrategy for container lists (merge by "name").
	return r.Patch(ctx, obj, client.RawPatch(types.StrategicMergePatchType, data))
}

// actionCordon marks a node unschedulable (containment).
func (r *RemediationPlanReconciler) actionCordon(ctx context.Context, t stepTarget) error {
	if t.kind != "node" {
		return fmt.Errorf("cordon only applies to nodes, got %s", t.kind)
	}
	node := &corev1.Node{}
	if err := r.Get(ctx, types.NamespacedName{Name: t.name}, node); err != nil {
		return err
	}
	data := []byte(`{"spec":{"unschedulable":true}}`)
	return r.Patch(ctx, node, client.RawPatch(types.MergePatchType, data))
}

// actionDeletePod deletes one pod so its controller recreates it.
func (r *RemediationPlanReconciler) actionDeletePod(ctx context.Context, ns string, t stepTarget) error {
	if t.kind != "pod" {
		return fmt.Errorf("delete_pod only applies to pods, got %s", t.kind)
	}
	pod := &corev1.Pod{ObjectMeta: metav1.ObjectMeta{Name: t.name, Namespace: ns}}
	return r.Delete(ctx, pod)
}

// verifyTargets checks every step target for health.  For workloads it
// waits until the desired replicas are ready (the "metrics recovered"
// proxy); for nodes it checks unschedulable (cordon took effect).
// Returns ok=true when every target is healthy.
func (r *RemediationPlanReconciler) verifyTargets(ctx context.Context, plan *sentinelv1.RemediationPlan) (bool, string) {
	for _, step := range plan.Spec.Steps {
		ok, msg := r.verifyTarget(ctx, plan.Namespace, step)
		if !ok {
			return false, msg
		}
	}
	return true, ""
}

func (r *RemediationPlanReconciler) verifyTarget(ctx context.Context, ns string, step sentinelv1.RemediationStep) (bool, string) {
	t, err := parseTarget(step.Target)
	if err != nil {
		return false, err.Error()
	}

	switch t.kind {
	case "deployment":
		dep := &appsv1.Deployment{}
		if err := r.Get(ctx, types.NamespacedName{Name: t.name, Namespace: ns}, dep); err != nil {
			if errors.IsNotFound(err) {
				return false, fmt.Sprintf("deployment %s not found", t.name)
			}
			return false, err.Error()
		}
		want := int32(1)
		if dep.Spec.Replicas != nil {
			want = *dep.Spec.Replicas
		}
		if dep.Status.ReadyReplicas < want || dep.Status.UpdatedReplicas < want {
			return false, fmt.Sprintf("deployment %s: ready=%d/%d updated=%d", t.name, dep.Status.ReadyReplicas, want, dep.Status.UpdatedReplicas)
		}
		return true, ""

	case "statefulset":
		sts := &appsv1.StatefulSet{}
		if err := r.Get(ctx, types.NamespacedName{Name: t.name, Namespace: ns}, sts); err != nil {
			if errors.IsNotFound(err) {
				return false, fmt.Sprintf("statefulset %s not found", t.name)
			}
			return false, err.Error()
		}
		want := int32(1)
		if sts.Spec.Replicas != nil {
			want = *sts.Spec.Replicas
		}
		if sts.Status.ReadyReplicas < want {
			return false, fmt.Sprintf("statefulset %s: ready=%d/%d", t.name, sts.Status.ReadyReplicas, want)
		}
		return true, ""

	case "node":
		node := &corev1.Node{}
		if err := r.Get(ctx, types.NamespacedName{Name: t.name}, node); err != nil {
			if errors.IsNotFound(err) {
				return false, fmt.Sprintf("node %s not found", t.name)
			}
			return false, err.Error()
		}
		// Cordon verification: node must be unschedulable.
		if !node.Spec.Unschedulable {
			return false, fmt.Sprintf("node %s not yet cordoned", t.name)
		}
		return true, ""

	case "pod":
		pod := &corev1.Pod{}
		if err := r.Get(ctx, types.NamespacedName{Name: t.name, Namespace: ns}, pod); err != nil {
			if errors.IsNotFound(err) {
				// Deleted pod is being recreated — acceptable.
				return true, ""
			}
			return false, err.Error()
		}
		return true, ""

	default:
		return false, fmt.Sprintf("cannot verify %s targets", t.kind)
	}
}
