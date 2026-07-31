# IT request — IAM for OmniParse IDP pilot (`App@Team`)

## Account / identity

- Account: `9125XXXX6433`
- IAM user: `App@Team`
- Region: **`eu-north-1`** (Stockholm)
- Stack name: `omniparse-idp`

## What failed (evidence)

CloudFormation create failed with:

1. `ecr:CreateRepository` — **AccessDenied**
2. `secretsmanager:CreateSecret` — **AccessDenied**
3. EC2 VPC / InternetGateway — **UnauthorizedTaggingOperation** (needs `ec2:CreateTags`)

Stack ended in `ROLLBACK_FAILED`. Cleanup then also failed with:

4. `logs:DeleteLogGroup` on `/ecs/omniparse-idp` — **AccessDenied**

(User can remove the stack with `--retain-resources LogGroup`, but IT should still grant log group delete.)

## Ask

Attach the policy in [`iam-pilot-policy.json`](iam-pilot-policy.json) to `App@Team` (or an equivalent deploy role this user can assume), covering:

| Area | Why |
|------|-----|
| CloudFormation | Deploy `infra/cloudformation.yml` |
| EC2 VPC/subnet/IGW/SG + **CreateTags** | Network for ALB + Fargate |
| ECR create + push | Host API Docker image |
| ECS + IAM PassRole for `omniparse-idp-*` roles | Run Fargate API |
| ELB (ALB) | Front the API |
| S3 + CloudFront | Host UI + same-origin `/api/*` |
| Secrets Manager | Optional Gemini API key |
| CloudWatch Logs | Container logs `/ecs/omniparse-idp` |

**Not needed for this pilot:** Bedrock, Lambda, API Gateway, Vertex.

## After IT attaches policy

User will:

1. Delete failed stack `omniparse-idp` (if still present)
2. Re-run CloudShell `./infra/cloudshell-phase1.sh`
3. Push image from Mac Docker → ECR
4. Finish with `./infra/cloudshell-phase3.sh`
