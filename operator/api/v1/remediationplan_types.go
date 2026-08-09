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
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
)

// RemediationPlanSpec defines the desired state of RemediationPlan.
//
// This mirrors the manifest produced by the Executor Agent (T3.7) and
// the operator bridge: every step's Action comes from the allow-list
// ALLOWED_EXECUTOR_ACTIONS (restart, scale, rollback, cordon, drain,
// patch, delete_pod, escalate) — enforced by the API and the bridge.
type RemediationPlanSpec struct {
	// Incident is the raw incident/alert text that triggered the plan.
	// +optional
	Incident string `json:"incident,omitempty"`

	// Priority is one of "high", "medium", "low".
	// +optional
	Priority string `json:"priority,omitempty"`

	// Rationale explains why this plan exists (tied to the synthesis).
	// +optional
	Rationale string `json:"rationale,omitempty"`

	// DryRun marks the plan as a preview — the operator must NOT act.
	// +optional
	DryRun bool `json:"dryRun,omitempty"`

	// ApprovedBy records who approved the plan ("human" for now).
	// +optional
	ApprovedBy string `json:"approvedBy,omitempty"`

	// PlanRef is the UUID of the persisted approval plan in Postgres.
	// +optional
	PlanRef string `json:"planRef,omitempty"`

	// Steps is the ordered list of remediation actions (1-3 steps).
	// +kubebuilder:validation:MinItems=1
	// +kubebuilder:validation:MaxItems=5
	Steps []RemediationStep `json:"steps"`
}

// RemediationStep is one concrete action in the plan.
type RemediationStep struct {
	// Action is an allow-listed verb: restart, scale, rollback,
	// cordon, drain, patch, delete_pod, escalate.
	Action string `json:"action"`

	// Target is what the action acts on, e.g. "deployment/demo-api".
	Target string `json:"target"`

	// Detail is a human-readable description of the change + outcome.
	// +optional
	Detail string `json:"detail,omitempty"`
}

// RemediationPlanStatus defines the observed state of RemediationPlan.
type RemediationPlanStatus struct {
	// State is the lifecycle state driven by the reconcile loop (T3.9):
	// Proposed → Approved → Applied → Verified → Closed.
	// +optional
	State string `json:"state,omitempty"`

	// Message is a human-readable explanation of the current state.
	// +optional
	Message string `json:"message,omitempty"`

	// ObservedGeneration is the metadata.generation this status reflects.
	// +optional
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`

	// Conditions represent the current state of the resource.
	// +listType=map
	// +listMapKey=type
	// +optional
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="State",type="string",JSONPath=".status.state"
// +kubebuilder:printcolumn:name="Priority",type="string",JSONPath=".spec.priority"
// +kubebuilder:printcolumn:name="DryRun",type="boolean",JSONPath=".spec.dryRun"
// +kubebuilder:printcolumn:name="Age",type="date",JSONPath=".metadata.creationTimestamp"

// RemediationPlan is the Schema for the remediationplans API
type RemediationPlan struct {
	metav1.TypeMeta `json:",inline"`

	// metadata is a standard object metadata
	// +optional
	metav1.ObjectMeta `json:"metadata,omitzero"`

	// spec defines the desired state of RemediationPlan
	// +required
	Spec RemediationPlanSpec `json:"spec"`

	// status defines the observed state of RemediationPlan
	// +optional
	Status RemediationPlanStatus `json:"status,omitzero"`
}

// +kubebuilder:object:root=true

// RemediationPlanList contains a list of RemediationPlan
type RemediationPlanList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitzero"`
	Items           []RemediationPlan `json:"items"`
}

func init() {
	SchemeBuilder.Register(func(s *runtime.Scheme) error {
		s.AddKnownTypes(SchemeGroupVersion, &RemediationPlan{}, &RemediationPlanList{})
		return nil
	})
}
