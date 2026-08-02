# Cloud Agent handoff — OmniParse IDP (AWS + Gemini)

Paste this into a **Cursor Cloud Agent** on repo `AkshaySriKiran/IDP-python-env` (branch `main`) from either laptop.

## Goal (current phase)

Migrate OmniParse IDP to AWS for a **pilot**:

- **UI:** S3 + CloudFront  
- **API:** ECS Fargate + ALB (CloudFront proxies `/api/*`)  
- **LLM now:** Google **AI Studio / Gemini API** (API key in Secrets Manager or UI)  
- **LLM later (production):** replace Gemini with **Amazon Bedrock** only (data sovereignty)  
- **Not in this phase:** Vertex AI, API Gateway + Lambda, Bedrock wiring

## Repo

- GitHub: https://github.com/AkshaySriKiran/IDP-python-env  
- Local hybrid still works: `./start-api.sh` (8001) + `./start.sh` (8000)  
- Deploy: `./infra/deploy.sh` + `infra/cloudformation.yml`

## AWS account / access (blocker)

- Account ID: `912564796433`  
- IAM user: `App@Team`  
- Console region in use: **`eu-north-1`** (Stockholm) — use this for CLI/deploy, not `us-east-1` unless IT says otherwise  
- User **cannot** create IAM access keys (Access denied on IAM dashboard)  
- `aws login` had browser **400 Bad Request** issues  
- **Need from IT:** CLI access keys **or** working SSO/`aws login`, plus deploy IAM for: CloudFormation, VPC, ALB, ECS/Fargate, ECR, S3, CloudFront, Secrets Manager, CloudWatch, IAM roles  

### Cloud Agent status (updated 2026-08-02)

| Check | Status |
|-------|--------|
| AWS CLI in this agent | No credentials (team deploys from Windows/Mac) |
| Region | **`eu-west-1`** (default in `infra/deploy.sh`) |
| VPC | Existing **My-VPC01** `vpc-01cdd75a59e8dafbb` (no new VPC) |
| Stack | `omniparse-idp` → UPDATE_COMPLETE |
| ECS | `omniparse-idp-api` → ACTIVE 1/1 |
| Live CloudFront URL | https://d11bl7hg497hj.cloudfront.net/ |
| API health | `GET /api/health` → `{"status":"ok",...}` (verified 2026-08-02) |
| Bedrock | Not started (after Gemini pilot is stable) |

Morning code update (`17d1d6f`): deploy into My-VPC01; ALB in public subnets; Fargate in private subnets; Docker `--platform linux/amd64`.

**Unblock options:**

1. Inject AWS credentials into the Cloud Agent (access keys / SSO), then `./infra/deploy.sh`  
2. **Mac Docker + CloudShell password** (no local AWS CLI login) — preferred while laptop `aws login` is broken:  
   - CloudShell (eu-north-1): `./infra/cloudshell-phase1.sh` → print ECR password  
   - Mac Docker: paste password → `./infra/mac-push-ecr.sh`  
   - CloudShell: `./infra/cloudshell-phase3.sh`  
   - Guide: `infra/MAC-CLOUDSHELL.md`  

**Phase1 blocked (2026-07-31):** `App@Team` lacks IAM for deploy. Confirmed failures:

- `ecr:CreateRepository` AccessDenied  
- `secretsmanager:CreateSecret` AccessDenied  
- EC2 VPC/IGW `UnauthorizedTaggingOperation` (needs `ec2:CreateTags`)  
- Stack status was `ROLLBACK_FAILED`  

Send IT: `infra/IT-IAM-REQUEST.md` + `infra/iam-pilot-policy.json`. Do not retry phase1 until policy is attached.

## Services requested for this project

VPC, ALB, ECS+Fargate, ECR, S3, CloudFront, Secrets Manager, CloudWatch Logs, IAM, CloudFormation.  
Later: Bedrock, ACM+Route53 (custom domain / >180s API timeout), PrivateLink.

## What is already done

- Hybrid UI + FastAPI extraction (maintenance / spare parts / troubleshooting + Excel)  
- AWS deploy path added and pushed (`infra/`, `backend/Dockerfile`, CloudFront same-origin API in `app.js`)  
- Latest relevant commit on `main`: AWS Fargate deploy for Gemini pilot  
- Gemini is the current extractor; Bedrock not implemented yet  
- Deploy script now defaults to **`eu-north-1`** and aborts unless `sts get-caller-identity` succeeds  

## Known constraints

- CloudFront custom-origin read timeout max **180s** — use page ranges for long OCR jobs  
- Large manuals: up to ~1GB / 5000 pages; full-book runs are slow/expensive  
- Do **not** treat BRD enterprise features (RBAC, virus scan, approval queue, Vertex) as already built  

## Live pilot (verified 2026-08-02)

- **UI + API:** https://d11bl7hg497hj.cloudfront.net/  
- **Health:** https://d11bl7hg497hj.cloudfront.net/api/health → `status: ok`  
- Static assets (`app.js`, `styles.css`, `equipment_manifest.json`) return HTTP 200 via CloudFront→S3  
- Logs: CloudWatch `/ecs/omniparse-idp-api` (confirm region in the account that owns this distribution)

## Next tasks for Cloud Agent / engineer

1. Smoke-test in browser: header **Python API Ready** → small PDF extract (Gemini key in UI if needed)  
2. Confirm deploy region for this live URL (eu-west-1 vs eu-north-1) and align docs/scripts  
3. Later (separate phase): swap Gemini extractor for Bedrock; keep same API contract  

## Do not do yet

- Rework architecture to Lambda-only  
- Add Vertex AI  
- Implement Bedrock until pilot Gemini deploy is stable  
- Force-push or change AWS account settings  
- Run `./infra/deploy.sh` in Cloud Agent while `aws sts get-caller-identity` fails  
  (CloudShell path is fine — that shell already has console credentials)  

## Quick local verify (optional)

```bash
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd .. && ./start-api.sh   # terminal A
./start.sh                # terminal B
```
