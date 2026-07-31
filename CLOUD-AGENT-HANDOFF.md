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

## Services requested for this project

VPC, ALB, ECS+Fargate, ECR, S3, CloudFront, Secrets Manager, CloudWatch Logs, IAM, CloudFormation.  
Later: Bedrock, ACM+Route53 (custom domain / >180s API timeout), PrivateLink.

## What is already done

- Hybrid UI + FastAPI extraction (maintenance / spare parts / troubleshooting + Excel)  
- AWS deploy path added and pushed (`infra/`, `backend/Dockerfile`, CloudFront same-origin API in `app.js`)  
- Latest relevant commit on `main`: AWS Fargate deploy for Gemini pilot  
- Gemini is the current extractor; Bedrock not implemented yet  

## Known constraints

- CloudFront custom-origin read timeout max **180s** — use page ranges for long OCR jobs  
- Large manuals: up to ~1GB / 5000 pages; full-book runs are slow/expensive  
- Do **not** treat BRD enterprise features (RBAC, virus scan, approval queue, Vertex) as already built  

## Next tasks for Cloud Agent / engineer

1. Confirm AWS CLI works: `aws sts get-caller-identity` in `eu-north-1`  
2. Confirm Docker is running  
3. Deploy:  
   ```bash
   export AWS_REGION=eu-north-1
   export GEMINI_API_KEY="..."   # optional; UI can paste key
   ./infra/deploy.sh
   ```  
4. Smoke-test CloudFront URL → header shows **Python API Ready** → small PDF extract  
5. Document live URL + CloudWatch log group for the team  
6. Later (separate phase): swap Gemini extractor for Bedrock; keep same API contract  

## Do not do yet

- Rework architecture to Lambda-only  
- Add Vertex AI  
- Implement Bedrock until pilot Gemini deploy is stable  
- Force-push or change AWS account settings  

## Quick local verify (optional)

```bash
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd .. && ./start-api.sh   # terminal A
./start.sh                # terminal B
```
