# Projects — the apps Sentinel produces.
#
# Each app lives in its own subfolder:
#   - Chart.yaml + values.yaml + templates/   (Helm), or
#   - kustomization.yaml + raw YAMLs          (Kustomize)
#
# And gets one ArgoCD Application manifest in ../argocd/apps/<app>.yaml.
