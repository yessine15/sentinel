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
)

// nextState is the PURE state machine used by the reconcile loop.
// It is a separate function so it can be unit-tested without a cluster.
//
// T3.8 skeleton rules:
//   - empty status → Proposed (new plans wait for approval)
//   - DryRun plans never advance past Proposed
//   - anything else stays where it is (T3.9 adds the real transitions)
func nextState(current string, dryRun bool) string {
	if current == "" {
		return StateProposed
	}
	if dryRun {
		return StateProposed
	}
	return current
}

// RemediationPlanReconciler reconciles a RemediationPlan object
type RemediationPlanReconciler struct {
	client.Client
	Scheme *runtime.Scheme
}

// +kubebuilder:rbac:groups=sentinel.io,resources=remediationplans,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=sentinel.io,resources=remediationplans/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=sentinel.io,resources=remediationplans/finalizers,verbs=update

// Reconcile is part of the main kubernetes reconciliation loop which aims to
// move the current state of the cluster closer to the desired state.
//
// T3.8 skeleton: fetch the RemediationPlan; if it has no status yet,
// initialise it to the "Proposed" state (new plans wait for approval).
// T3.9 implements the full state machine (Approved → Applied → Verified
// → Closed) and the actual remediation actions.
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

	desired := nextState(plan.Status.State, plan.Spec.DryRun)
	if plan.Status.State == desired {
		return ctrl.Result{}, nil // nothing to do
	}

	plan.Status.State = desired
	plan.Status.Message = "Plan created — waiting for human approval."
	if err := r.Status().Update(ctx, plan); err != nil {
		log.Error(err, "failed to update RemediationPlan status")
		return ctrl.Result{}, err
	}
	log.Info("RemediationPlan state initialised", "name", req.Name, "state", desired)
	return ctrl.Result{}, nil
}

// SetupWithManager sets up the controller with the Manager.
func (r *RemediationPlanReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&sentinelv1.RemediationPlan{}).
		Named("remediationplan").
		Complete(r)
}
