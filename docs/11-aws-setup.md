# 11 — AWS setup, click by click

Everything the deployment needs from the AWS console, in the order it has to happen. Budget
notes are inline because there is $200 of credit and $50 of it must survive.

Two regions, on purpose:

| What | Region | Why |
|---|---|---|
| Bedrock (model calls), Bedrock API key | **eu-north-1** (Stockholm) | Model access was enabled here; the key is scoped here |
| App Runner, ECR, S3 bucket | **eu-west-1** (Ireland) | App Runner does not exist in eu-north-1 |

The container gets `AWS_REGION=eu-north-1` (for the model) and lives in eu-west-1; `deploy.sh`
uses `APP_REGION` for the second.

---

## 0. Guard the credits (5 min) — do this first

**Billing and Cost Management → Budgets → Create budget**
- Cost budget, monthly, amount **$75**, alert at 100% to your email. Then a second one at **$125**.
- **Billing → Credits**: confirm *Amazon Bedrock* and *AWS App Runner* appear under the credit's
  applicable products. If Bedrock is not there, every model call is real money — say so before
  running anything.

## 1. Bedrock (10 min) — eu-north-1

1. **Bedrock → Model access** (left nav, bottom). Confirm *Claude Haiku 4.5*, *Claude Sonnet 4.5*
   and the *Amazon Nova* models show **Access granted**. (Done on 2026-09-19.)
2. **Bedrock → API keys → Generate long-term API key.** Name it `repoman-app`, expiry **30 days**
   (the max that is still short). Copy it once; it is not shown again. This is
   `AWS_BEARER_TOKEN_BEDROCK` for the container. The 12-hour short-term key you already made is
   fine for laptop runs but will expire mid-demo — use the long-term one for the deployment.
3. Wait out **account verification** ("Your account is currently being verified") — up to 2 hours
   after the first model-access grant. Test with `scripts/bench_model.py` when it clears.

Cost: Haiku 4.5 on Bedrock ≈ $1 / $5 per million tokens in/out; one submission is roughly
$0.20–0.40. Nova 2 Lite is ~15× cheaper if the bench shows its citations resolve.

## 2. IAM (15 min) — global

You need one **user** (for the CLI on your laptop) and two **roles** (for App Runner).

### 2a. The deploy user

**IAM → Users → Create user** → name `repoman-deploy` → *no* console access →
**Attach policies directly** → **Create policy** → JSON:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["sts:GetCallerIdentity"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["ecr:*"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["apprunner:*"], "Resource": "*"},
    {"Effect": "Allow", "Action": ["s3:*"], "Resource": ["arn:aws:s3:::repoman-*", "arn:aws:s3:::repoman-*/*"]},
    {"Effect": "Allow", "Action": ["iam:PassRole"], "Resource": "arn:aws:iam::*:role/RepoMan*"},
    {"Effect": "Allow", "Action": ["logs:*"], "Resource": "*"}
  ]
}
```

Name the policy `RepoManDeploy`, attach it, create the user. Then open the user →
**Security credentials → Create access key → Command Line Interface** → copy the
**Access key ID** and **Secret access key**. These go in `~/.aws/credentials` on the laptop that
runs `deploy.sh` (step 4), never in the repo.

### 2b. The instance role (what the running app is allowed to do)

**IAM → Roles → Create role → Custom trust policy**:

```json
{
  "Version": "2012-10-17",
  "Statement": [{"Effect": "Allow", "Principal": {"Service": "tasks.apprunner.amazonaws.com"}, "Action": "sts:AssumeRole"}]
}
```

Permissions → **Create policy** → JSON (only the bucket; the model is reached with the API key,
so no Bedrock permission is needed here):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": "arn:aws:s3:::repoman-<yourname>"},
    {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], "Resource": "arn:aws:s3:::repoman-<yourname>/*"}
  ]
}
```

Name the policy `RepoManBucket`, the role **`RepoManInstance`**. Copy its ARN → `INSTANCE_ROLE_ARN`.

### 2c. The ECR access role (lets App Runner pull the image)

**IAM → Roles → Create role → Custom trust policy**:

```json
{
  "Version": "2012-10-17",
  "Statement": [{"Effect": "Allow", "Principal": {"Service": "build.apprunner.amazonaws.com"}, "Action": "sts:AssumeRole"}]
}
```

Permissions → search and attach the AWS-managed **`AWSAppRunnerServicePolicyForECRAccess`**.
Name the role **`RepoManECRAccess`**. Copy its ARN → `ACCESS_ROLE_ARN`.

## 3. S3 bucket (2 min) — eu-west-1

**S3 → Create bucket** → name `repoman-<yourname>` (globally unique, lowercase) → region
**eu-west-1** → keep *Block all public access* on → create. That name is `REPOMAN_BUCKET`.

## 4. The laptop that deploys (10 min)

Needs **Docker Desktop** running and the **AWS CLI v2**:

```
brew install awscli            # macOS
aws configure                  # paste the repoman-deploy access key + secret; default region eu-west-1; output json
aws sts get-caller-identity    # must print your account id
docker info | head -3          # must not error
```

## 5. Deploy

From the repo root, in one shell (nothing here goes into a file):

```
export APP_REGION=eu-west-1 AWS_REGION=eu-north-1
export REPOMAN_BUCKET=repoman-<yourname>
export REPOMAN_MODEL_ID=eu.anthropic.claude-haiku-4-5-20251001-v1:0   # or the Nova id the bench picked
export AWS_BEARER_TOKEN_BEDROCK=<the long-term Bedrock API key from step 1>
export INSTANCE_ROLE_ARN=arn:aws:iam::<account>:role/RepoManInstance
export ACCESS_ROLE_ARN=arn:aws:iam::<account>:role/RepoManECRAccess
export REPOMAN_TOKEN=$(openssl rand -hex 16); echo "UI password: $REPOMAN_TOKEN"
./infra/deploy.sh
```

First deploy takes ~5 minutes and ends by printing the `https://….awsapprunner.com` URL. Sign in
with any username and the token as the password.

Cost: 1 vCPU / 2 GB ≈ $0.07 per hour while active. **Pause the service between sessions**
(App Runner → the service → Actions → Pause; ≈ $0.35/day paused) and resume before the demo.
ECR and S3 are cents.

## 6. If it fails

| Symptom | Cause |
|---|---|
| `AccessDenied` on `ecr:` or `apprunner:` | the `RepoManDeploy` policy is not attached, or `aws configure` used a different key |
| `create-service` fails on the role | trust policy service name: `tasks.apprunner.amazonaws.com` (instance) vs `build.apprunner.amazonaws.com` (access) — they are different |
| service stuck `OPERATION_IN_PROGRESS` > 10 min, then `CREATE_FAILED` | open the service → Logs → *Application logs*; usually a crash on start (missing env var) |
| app runs but every finding is `UNVERIFIED: AccessDenied` | Bedrock key expired or wrong region; `AWS_REGION` in the container must be `eu-north-1` |
| `Your account is currently being verified` | wait; nothing else helps |

## The fallback that costs nothing

If step 5 is not green by mid-afternoon on demo day, stop. Run `uv run uvicorn repoman.web.app:app`
on the laptop with the same environment variables minus the App Runner ones. The story — *no byte
of a submission left this machine* — is the product's Track 1 pitch, not a downgrade.
