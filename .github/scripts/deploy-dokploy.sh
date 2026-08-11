#!/usr/bin/env bash
set -euo pipefail

: "${DOKPLOY_HOST:?DOKPLOY_HOST is required}"
: "${DOKPLOY_TOKEN:?DOKPLOY_TOKEN is required}"
: "${DOKPLOY_APP_ID:?DOKPLOY_APP_ID is required}"
: "${DOKPLOY_IMAGE:?DOKPLOY_IMAGE is required}"
: "${DOKPLOY_REGISTRY_URL:?DOKPLOY_REGISTRY_URL is required}"
: "${DOKPLOY_REGISTRY_USERNAME:?DOKPLOY_REGISTRY_USERNAME is required}"
: "${DOKPLOY_REGISTRY_PASSWORD:?DOKPLOY_REGISTRY_PASSWORD is required}"

base_url="${DOKPLOY_HOST%/}"
if [[ "$base_url" == http://* ]]; then
  echo "DOKPLOY_HOST must use HTTPS" >&2
  exit 1
fi
if [[ "$base_url" != https://* ]]; then
  base_url="https://$base_url"
fi

save_payload="$(
  jq -cn \
    --arg dockerImage "$DOKPLOY_IMAGE" \
    --arg applicationId "$DOKPLOY_APP_ID" \
    --arg username "$DOKPLOY_REGISTRY_USERNAME" \
    --arg password "$DOKPLOY_REGISTRY_PASSWORD" \
    --arg registryUrl "$DOKPLOY_REGISTRY_URL" \
    '{
      dockerImage: $dockerImage,
      applicationId: $applicationId,
      username: $username,
      password: $password,
      registryUrl: $registryUrl
    }'
)"
redeploy_payload="$(
  jq -cn \
    --arg applicationId "$DOKPLOY_APP_ID" \
    '{applicationId: $applicationId}'
)"

application_before="$(
  curl --fail-with-body --silent --show-error \
    --get "$base_url/api/application.one" \
    --header "x-api-key: $DOKPLOY_TOKEN" \
    --header "accept: application/json" \
    --data-urlencode "applicationId=$DOKPLOY_APP_ID"
)"
previous_deployment_id="$(
  jq -r '[.deployments[]?] | max_by(.createdAt).deploymentId // empty' \
    <<< "$application_before"
)"

echo "Updating Dokploy application to $DOKPLOY_IMAGE"
curl --fail-with-body --silent --show-error \
  --request POST "$base_url/api/application.saveDockerProvider" \
  --header "x-api-key: $DOKPLOY_TOKEN" \
  --header "accept: application/json" \
  --header "Content-Type: application/json" \
  --data "$save_payload" \
  --output /dev/null

echo "Redeploying Dokploy application"
curl --fail-with-body --silent --show-error \
  --request POST "$base_url/api/application.redeploy" \
  --header "x-api-key: $DOKPLOY_TOKEN" \
  --header "accept: application/json" \
  --header "Content-Type: application/json" \
  --data "$redeploy_payload" \
  --output /dev/null

echo "Waiting for Dokploy deployment to complete"
for attempt in {1..120}; do
  sleep 5
  application="$(
    curl --fail-with-body --silent --show-error \
      --get "$base_url/api/application.one" \
      --header "x-api-key: $DOKPLOY_TOKEN" \
      --header "accept: application/json" \
      --data-urlencode "applicationId=$DOKPLOY_APP_ID"
  )"
  application_status="$(jq -r '.applicationStatus // empty' <<< "$application")"
  configured_image="$(jq -r '.dockerImage // empty' <<< "$application")"
  latest_deployment_id="$(
    jq -r '[.deployments[]?] | max_by(.createdAt).deploymentId // empty' \
      <<< "$application"
  )"
  latest_deployment_status="$(
    jq -r '[.deployments[]?] | max_by(.createdAt).status // empty' \
      <<< "$application"
  )"

  if [[ -n "$latest_deployment_id" && "$latest_deployment_id" != "$previous_deployment_id" ]]; then
    case "$latest_deployment_status" in
    done)
      if [[ "$configured_image" == "$DOKPLOY_IMAGE" ]]; then
        echo "Dokploy deployment completed: $DOKPLOY_IMAGE"
        exit 0
      fi
      ;;
    error)
      echo "Dokploy deployment failed" >&2
      exit 1
      ;;
    esac
  elif [[ "$application_status" == error ]]; then
    echo "Dokploy application entered an error state" >&2
    exit 1
  fi

  if (( attempt % 6 == 0 )); then
    echo "Dokploy deployment status: ${latest_deployment_status:-$application_status}"
  fi
done

echo "Timed out waiting for Dokploy deployment" >&2
exit 1
