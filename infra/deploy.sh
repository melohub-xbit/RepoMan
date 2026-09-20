#!/usr/bin/env bash
# Build, push to ECR, and create or update the App Runner service.
#
# Prerequisites, done once by hand (docs/11-aws-setup.md walks through every one):
#   - Bedrock → Model access enabled in $AWS_REGION, and a long-term Bedrock API key ($AWS_BEARER_TOKEN_BEDROCK)
#   - an S3 bucket for $REPOMAN_BUCKET
#   - an instance role granting s3:* on that bucket ($INSTANCE_ROLE_ARN)
#   - an ECR access role for App Runner to pull the image ($ACCESS_ROLE_ARN)
#
# Two regions: APP_REGION is where App Runner and ECR live (App Runner is not in every region —
# eu-west-1, not eu-north-1); AWS_REGION is where the model is called, and is what the container sees.
#
# Usage:
#   APP_REGION=eu-west-1 AWS_REGION=eu-north-1 \
#   REPOMAN_BUCKET=my-bucket REPOMAN_MODEL_ID=eu.anthropic.claude-haiku-4-5-20251001-v1:0 \
#   AWS_BEARER_TOKEN_BEDROCK=... \
#   INSTANCE_ROLE_ARN=arn:aws:iam::...:role/RepoManInstance \
#   ACCESS_ROLE_ARN=arn:aws:iam::...:role/RepoManECRAccess \
#   REPOMAN_TOKEN=$(openssl rand -hex 16) ./infra/deploy.sh

set -euo pipefail

SERVICE="${SERVICE:-repoman}"
AWS_REGION="${AWS_REGION:-us-east-1}"          # model region, passed into the container
APP_REGION="${APP_REGION:-$AWS_REGION}"        # App Runner + ECR region
REPO="${REPO:-repoman}"
TAG="${TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%s)}"

for var in REPOMAN_BUCKET REPOMAN_MODEL_ID INSTANCE_ROLE_ARN ACCESS_ROLE_ARN AWS_BEARER_TOKEN_BEDROCK; do
  if [ -z "${!var:-}" ]; then echo "error: $var is not set" >&2; exit 1; fi
done

# The model id must be the inference profile copied from the Bedrock console. Constructing one
# from memory produces a string that looks right and fails at invoke time (docs/04).
case "$REPOMAN_MODEL_ID" in
  *anthropic*|*nova*) ;;
  *) echo "warning: REPOMAN_MODEL_ID=$REPOMAN_MODEL_ID does not look like a Bedrock profile id" >&2 ;;
esac

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${APP_REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${REPO}:${TAG}"

echo "==> ECR"
aws ecr describe-repositories --repository-names "$REPO" --region "$APP_REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$REPO" --region "$APP_REGION" >/dev/null
aws ecr get-login-password --region "$APP_REGION" | docker login --username AWS --password-stdin "$REGISTRY"

echo "==> build ${IMAGE}"
docker build --platform linux/amd64 -f infra/Dockerfile -t "$IMAGE" .
docker push "$IMAGE"

RUNTIME_ENV="$(cat <<JSON
{
  "REPOMAN_BUCKET": "${REPOMAN_BUCKET}",
  "REPOMAN_MODEL_ID": "${REPOMAN_MODEL_ID}",
  "AWS_REGION": "${AWS_REGION}",
  "REPOMAN_TOKEN": "${REPOMAN_TOKEN:-}",
  "AWS_BEARER_TOKEN_BEDROCK": "${AWS_BEARER_TOKEN_BEDROCK}",
  "REPOMAN_CACHE_DIR": "/tmp/repoman"
}
JSON
)"

SOURCE_CONFIG="$(cat <<JSON
{
  "ImageRepository": {
    "ImageIdentifier": "${IMAGE}",
    "ImageRepositoryType": "ECR",
    "ImageConfiguration": {"Port": "8080", "RuntimeEnvironmentVariables": ${RUNTIME_ENV}}
  },
  "AutoDeploymentsEnabled": false,
  "AuthenticationConfiguration": {"AccessRoleArn": "${ACCESS_ROLE_ARN}"}
}
JSON
)"

ARN="$(aws apprunner list-services --region "$APP_REGION" \
        --query "ServiceSummaryList[?ServiceName=='${SERVICE}'].ServiceArn | [0]" --output text)"

if [ -n "$ARN" ] && [ "$ARN" != "None" ]; then
  STATUS="$(aws apprunner describe-service --region "$APP_REGION" --service-arn "$ARN" --query 'Service.Status' --output text)"
  if [ "$STATUS" = "CREATE_FAILED" ]; then
    echo "==> previous create failed; deleting it first"
    aws apprunner delete-service --region "$APP_REGION" --service-arn "$ARN" >/dev/null
    for _ in $(seq 1 30); do
      aws apprunner describe-service --region "$APP_REGION" --service-arn "$ARN" >/dev/null 2>&1 || break
      sleep 10
    done
    ARN=""
  fi
fi

if [ "$ARN" = "None" ] || [ -z "$ARN" ]; then
  echo "==> creating App Runner service"
  ARN="$(aws apprunner create-service --region "$APP_REGION" \
      --service-name "$SERVICE" \
      --source-configuration "$SOURCE_CONFIG" \
      --instance-configuration "{\"Cpu\":\"1 vCPU\",\"Memory\":\"2 GB\",\"InstanceRoleArn\":\"${INSTANCE_ROLE_ARN}\"}" \
      --health-check-configuration '{"Protocol":"HTTP","Path":"/healthz","Interval":10,"Timeout":5,"HealthyThreshold":1,"UnhealthyThreshold":5}' \
      --query 'Service.ServiceArn' --output text)"
else
  echo "==> updating App Runner service"
  aws apprunner update-service --region "$APP_REGION" --service-arn "$ARN" \
      --source-configuration "$SOURCE_CONFIG" \
      --instance-configuration "{\"Cpu\":\"1 vCPU\",\"Memory\":\"2 GB\",\"InstanceRoleArn\":\"${INSTANCE_ROLE_ARN}\"}" >/dev/null
fi

# App Runner has no CLI waiters (botocore ships none for it), so poll the status ourselves.
# A first deploy takes about five minutes; OPERATION_IN_PROGRESS is normal until then.
echo "==> waiting for the service to settle"
for _ in $(seq 1 60); do
  STATUS="$(aws apprunner describe-service --region "$APP_REGION" --service-arn "$ARN" \
             --query 'Service.Status' --output text)"
  case "$STATUS" in
    RUNNING) break ;;
    CREATE_FAILED|DELETE_FAILED|DELETED) echo "error: service status $STATUS" >&2; exit 1 ;;
    *) sleep 10 ;;
  esac
done
URL="$(aws apprunner describe-service --region "$APP_REGION" --service-arn "$ARN" \
        --query 'Service.ServiceUrl' --output text)"
echo
echo "https://${URL}"
[ -n "${REPOMAN_TOKEN:-}" ] && echo "sign in with any username and the token as the password"
