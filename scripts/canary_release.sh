#!/usr/bin/env bash
set -euo pipefail

action="${1:-plan}"
namespace="${OMNI_KUBERNETES_NAMESPACE:-omni}"
candidate="${OMNI_CANDIDATE_TAG:-candidate}"
image_prefix="${OMNI_IMAGE_PREFIX:-}"

image_name() {
  if [[ -n "$image_prefix" ]]; then
    echo "${image_prefix}/omni-$1:${candidate}"
  else
    echo "omni-$1:${candidate}"
  fi
}

case "$action" in
  plan)
    kubectl kustomize deploy/kubernetes/canary
    ;;
  deploy)
    kubectl -n "$namespace" delete job omni-migrate-candidate --ignore-not-found
    kubectl apply -k deploy/kubernetes/canary
    kubectl -n "$namespace" set image job/omni-migrate-candidate "migrate=$(image_name api)"
    kubectl -n "$namespace" wait --for=condition=complete job/omni-migrate-candidate --timeout=5m
    kubectl -n "$namespace" set image deployment/omni-voice-canary "voice=$(image_name voice)"
    kubectl -n "$namespace" rollout status deployment/omni-voice-canary --timeout=5m
    ;;
  promote)
    for service in api worker voice; do
      kubectl -n "$namespace" set image "deployment/omni-${service}" "${service}=$(image_name "$service")"
      kubectl -n "$namespace" rollout status "deployment/omni-${service}" --timeout=5m
    done
    kubectl -n "$namespace" delete deployment omni-voice-canary --ignore-not-found
    ;;
  rollback)
    for service in api worker voice; do
      kubectl -n "$namespace" rollout undo "deployment/omni-${service}"
      kubectl -n "$namespace" rollout status "deployment/omni-${service}" --timeout=5m
    done
    kubectl -n "$namespace" delete deployment omni-voice-canary --ignore-not-found
    ;;
  *)
    echo "usage: $0 {plan|deploy|promote|rollback}" >&2
    exit 2
    ;;
esac
