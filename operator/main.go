// Package main is the entry point for the Sentinel Kubernetes operator.
//
// The real operator (Kubebuilder scaffold, CRDs, reconcile loop) lands in
// Phase 3 (T3.8). For T0.8 (CI skeleton) this file exists so the Go CI
// job has something to gofmt + vet + test against.
package main

// Version is the operator's semantic version. Bumped per release.
const Version = "0.1.0"
