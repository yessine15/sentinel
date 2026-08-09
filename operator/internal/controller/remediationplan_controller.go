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
	"os"
	"strconv"
	"time"

	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/runtime"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	logf "sigs.k8s.io/controller-runtime/pkg/log"

	sentinelv1 "github.com/yessine15/sentinel/operator/api/v1"
)

// Lifecycle states of a RemediationPlan (T3.9 implements the full
// machine: Proposed → Approved → Applied → Verified → Closed).
const (
	StateProposed = "Proposed"
	StateApproved = "Approved"
	StateApplied  = "Applied"
	StateVerified = "Verified"
	StateClosed   = "Closed"
	StateFailed   = "Failed"
)

// How long a Verified plan waits before being Closed (cooldown).
func cooldown() time.Duration {
	if v := os.Getenv("OPERATOR_COOLDOWN_SECONDS"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			return time.Duration(n) * time.Second
		}
	}
	return 60 * time.Second
}

// How often an Applied (not yet verified) plan is re-checked.
func verifyInterval() time.Duration {
	if v := os.Getenv("OPERATOR_VERIFY_INTERVAL_SECONDS"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			return time.Duration(n) * time.Second
		}
	}
	return 5 * time.Second
}

// nextState is the PURE state machine used by the reconcile loop.  It
// is a separate function so it can be unit-tested without a cluster.
//
// Transitions (T3.9):
//
//	""             → Proposed          (new plan waits for approval)
//	Proposed       → Approved          (human approved via approvedBy)
//	Approved       → Applied           (actions executed OK)
//	Approved       → Failed            (action failed / disallowed)
//	Applied        → Verified          (post-action verification passed)
//	Applied        → Applied           (keep watching — requeue)
//	Verified       → Closed            (cooldown elapsed)
//	Verified       → Verified          (cooldown not elapsed yet)
//	Closed/Failed  → terminal
//	dryRun plans   → never advance past Proposed
func nextState(current string, dryRun bool, approved bool, success bool, verified bool, cooldownDone bool) string {
	if dryRun {
		return StateProposed
	}
	switch current {
	case "":
		return StateProposed
	case StateProposed:
		if approved {
			return StateApproved
		}
		return StateProposed
	case StateApproved:
		if success {
			return StateApplied
		}
		return StateFailed
	case StateApplied:
		if verified {
			return StateVerified
		}
		return StateApplied
	case StateVerified:
		if cooldownDone {
			return StateClosed
		}
		return StateVerified
	default: // StateClosed, StateFailed, anything unknown
		return current
	}
}

// RemediationPlanReconciler reconciles a RemediationPlan object
type RemediationPlanReconciler struct {
	client.Client
	Scheme *runtime.Scheme
}

// T3.10: least-privilege RBAC — the operator only reads RemediationPlan
// objects and writes their status. It never creates/deletes plans (the
// bridge does that), so those verbs are deliberately absent.
// +kubebuilder:rbac:groups=sentinel.io,resources=remediationplans,verbs=get;list;watch
// +kubebuilder:rbac:groups=sentinel.io,resources=remediationplans/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=sentinel.io,resources=remediationplans/finalizers,verbs=update
// +kubebuilder:rbac:groups=apps,resources=deployments;statefulsets;daemonsets,verbs=get;list;watch;patch
// +kubebuilder:rbac:groups="",resources=pods,verbs=get;list;watch;delete
// +kubebuilder:rbac:groups="",resources=nodes,verbs=get;list;watch;patch

// Reconcile is the main reconciliation loop: it drives a
// RemediationPlan through its lifecycle by switching on
// “status.state“ (T3.9):
//
//	Proposed → wait (for human approval recorded in spec.approvedBy)
//	Approved → run the allow-listed executor actions → Applied
//	Applied  → watch the targets until healthy → Verified
//	Verified → after cooldown → Closed
func (r *RemediationPlanReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	log := logf.FromContext(ctx)

	plan := &sentinelv1.RemediationPlan{}
	if err := r.Get(ctx, req.NamespacedName, plan); err != nil {
		if errors.IsNotFound(err) {
			// Object deleted — nothing to do.
			return ctrl.Result{}, nil
		}
		return ctrl.Result{}, err
	}

	state := plan.Status.State
	dryRun := plan.Spec.DryRun
	approved := plan.Spec.ApprovedBy != ""

	// Dry-run plans never advance (they are previews).
	if dryRun {
		if state == "" {
			if err := r.setState(ctx, plan, StateProposed, "Dry-run plan — preview only, never acted on."); err != nil {
				return ctrl.Result{}, err
			}
		}
		return ctrl.Result{}, nil
	}

	switch state {
	case "":
		return ctrl.Result{}, r.setState(ctx, plan, StateProposed, "Plan created — waiting for human approval.")

	case StateProposed:
		if !approved {
			return ctrl.Result{}, nil // still waiting
		}
		log.Info("plan approved — executing", "name", req.Name)
		return ctrl.Result{}, r.setState(ctx, plan, StateApproved, "Approved by human — executing remediation steps.")

	case StateApproved:
		if err := r.executeSteps(ctx, plan); err != nil {
			log.Error(err, "remediation steps failed", "name", req.Name)
			return ctrl.Result{}, r.setState(ctx, plan, StateFailed, "Action failed: "+err.Error())
		}
		return ctrl.Result{}, r.setState(ctx, plan, StateApplied, "Actions applied — verifying targets.")

	case StateApplied:
		ok, errMsg := r.verifyTargets(ctx, plan)
		if ok {
			log.Info("plan verified", "name", req.Name)
			if err := r.setState(ctx, plan, StateVerified, "Targets healthy — closing after cooldown."); err != nil {
				return ctrl.Result{}, err
			}
			return ctrl.Result{RequeueAfter: cooldown()}, nil
		}
		if errMsg != "" {
			log.Info("verification pending", "name", req.Name, "reason", errMsg)
		}
		// Keep watching until the targets recover.
		return ctrl.Result{RequeueAfter: verifyInterval()}, nil

	case StateVerified:
		// Reached only after the cooldown requeue fired.
		log.Info("plan closed", "name", req.Name)
		return ctrl.Result{}, r.setState(ctx, plan, StateClosed, "Closed.")

	default: // StateClosed, StateFailed — terminal
		return ctrl.Result{}, nil
	}
}

// setState updates the plan status unless it is already identical.
func (r *RemediationPlanReconciler) setState(ctx context.Context, plan *sentinelv1.RemediationPlan, state, message string) error {
	if plan.Status.State == state && plan.Status.Message == message {
		return nil
	}
	plan.Status.State = state
	plan.Status.Message = message
	plan.Status.ObservedGeneration = plan.Generation
	return r.Status().Update(ctx, plan)
}

// SetupWithManager sets up the controller with the Manager.
func (r *RemediationPlanReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&sentinelv1.RemediationPlan{}).
		Named("remediationplan").
		Complete(r)
}
