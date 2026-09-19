#!/usr/bin/env bash
# Build, push to ECR, and create or update the App Runner service.
#
# Prerequisites, done once by hand because each needs a decision:
#   - Bedrock → Model access → Claude Sonnet enabled in $AWS_REGION
#   - an S3 bucket for $REPOMAN_BUCKET
#   - an instance role granting s3:* on that bucket and bedrock:InvokeModel* ($INSTANCE_ROLE_ARN)
#   - an ECR access role for App Runner to pull the image ($ACCESS_ROLE_ARN)
#
# Usage:
#   REPOMAN_BUCKET=my-bucket REPOMAN_MODEL_ID=us.anthropic.claude-sonnet-... \
#   INSTANCE_ROLE_ARN=arn:aws:iam::...:role/RepoManInstance \
#   ACCESS_ROLE_ARN=arn:aws:iam::...:role/RepoManECRAccess \
#   REPOMAN_TOKEN=$(openssl rand -hex 16) ./infra/deploy.sh

set -euo pipefail

SERVICE="${SERVICE:-repoman}"
AWS_REGION="${AWS_REGION:-us-east-1}"
REPO="${REPO:-repoman}"
TAG="${TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%s)}"

for var in REPOMAN_BUCKET REPOMAN_MODEL_ID INSTANCE_ROLE_ARN ACCESS_ROLE_ARN; do
  if [ -z "${!var:-}" ]; then echo "error: $var is not set" >&2; exit 1; fi
done

# The model id must be the inference profile copied from the Bedrock console. Constructing one
# from memory produces a string that looks right and fails at invoke time (docs/04).
case "$REPOMAN_MODEL_ID" in
  *anthropic*|*nova*) ;;
  *) echo "warning: REPOMAN_MODEL_ID=$REPOMAN_MODEL_ID does not look like a Bedrock profile id" >&2 ;;
esac

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE="${REGISTRY}/${REPO}:${TAG}"

echo "==> ECR"
aws ecr describe-repositories --repository-names "$REPO" --region "$AWS_REGION" >/dev/null 2>&1 \
  || aws ecr create-repository --repository-name "$REPO" --region "$AWS_REGION" >/dev/null
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"

echo "==> build ${IMAGE}"
docker build --platform linux/amd64 -f infra/Dockerfile -t "$IMAGE" .
docker push "$IMAGE"

RUNTIME_ENV="$(cat <<JSON
{
  "REPOMAN_BUCKET": "${REPOMAN_BUCKET}",
  "REPOMAN_MODEL_ID": "${REPOMAN_MODEL_ID}",
  "AWS_REGION": "${AWS_REGION}",
  "REPOMAN_TOKEN": "${REPOMAN_TOKEN:-}",
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

ARN="$(aws apprunner list-services --region "$AWS_REGION" \
        --query "ServiceSummaryList[?ServiceName=='${SERVICE}'].ServiceArn | [0]" --output text)"

if [ "$ARN" = "None" ] || [ -z "$ARN" ]; then
  echo "==> creating App Runner service"
  ARN="$(aws apprunner create-service --region "$AWS_REGION" \
      --service-name "$SERVICE" \
      --source-configuration "$SOURCE_CONFIG" \
      --instance-configuration "{\"Cpu\":\"1 vCPU\",\"Memory\":\"2 GB\",\"InstanceRoleArn\":\"${INSTANCE_ROLE_ARN}\"}" \
      --health-check-configuration '{"Protocol":"HTTP","Path":"/","Interval":10,"Timeout":5,"HealthyThreshold":1,"UnhealthyThreshold":5}' \
      --query 'Service.ServiceArn' --output text)"
else
  echo "==> updating App Runner service"
  aws apprunner update-service --region "$AWS_REGION" --service-arn "$ARN" \
      --source-configuration "$SOURCE_CONFIG" \
      --instance-configuration "{\"Cpu\":\"1 vCPU\",\"Memory\":\"2 GB\",\"InstanceRoleArn\":\"${INSTANCE_ROLE_ARN}\"}" >/dev/null
fi

echo "==> waiting for the service to settle"
aws apprunner wait service-updated --region "$AWS_REGION" --service-arn "$ARN" 2>/dev/null || true
URL="$(aws apprunner describe-service --region "$AWS_REGION" --service-arn "$ARN" \
        --query 'Service.ServiceUrl' --output text)"
echo
echo "https://${URL}"
[ -n "${REPOMAN_TOKEN:-}" ] && echo "sign in with any username and the token as the password"
