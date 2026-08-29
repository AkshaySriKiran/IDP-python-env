# OmniParse IDP — Antigravity brief

Paste this file (or the section below) into Antigravity as the project brief.
Goal: the agent knows the real product end-to-end and does not waste tokens rediscovering architecture.

---

```text
# OmniParse IDP — Antigravity brief (do exactly this; don’t re-discover)

You are working on repo OmniParse IDP (local path often `/Users/akshayryali/1`, GitHub `AkshaySriKiran/IDP-python-env`).
Build / fix / deploy only what is asked. Prefer existing patterns. Do not invent new architecture.

## What the product is
Hybrid Intelligent Document Processing app for O&M manuals + history cards:
- Extract → Maintenance / Spare Parts / Troubleshooting registries → Excel export
- UI: static HTML/JS (`index.html`, `app.js`, `styles.css`, `auth-admin.js`, `history.html`, `admin.html`)
- API: FastAPI (`backend/app/`) — PDF/OCR + Gemini (Ollama optional)
- Auth: optional JWT users (`AUTH_REQUIRED`); admin console; seed admin `admin@omniparse.local` / `ChangeMeNow!`

## CURRENT PRODUCT RULES (critical — already implemented)
1. **No PC file upload.** Users only extract PDFs already in SharePoint.
2. Intake: Graph download by `sharepoint_item_id` → extract job queue → poll `/api/extract/jobs/{id}`.
3. **Fabric WH_IDP** is central store + cache:
   - Tables: `Tbl_PM_Extraction_logs`, `Tbl_PM_Spare_Parts`, `Tbl_PM_Maintenance`, `Tbl_PM_Troubleshooting`
   - Same file `content_hash` → load from Fabric (`engine=fabric-cache`), do NOT re-Gemini / do NOT insert duplicate rows
   - On first extract → save to Fabric (drive_item_id + etag when from SharePoint)
4. **My extracts** (`history.html`): list Fabric done runs; **Open** → `index.html?fabric_run_id=…` loads rows into workspace (no re-extract).
5. Progress race fixed: late `on_progress` must not flip job `done` → `running`.

## Local run (A→Z)
```bash
# one-time
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# fill backend/.env from backend/.env.example (never commit secrets)

# terminal A
AUTH_REQUIRED=false ./start-api.sh   # http://127.0.0.1:8001  docs:/docs

# terminal B
./start.sh                           # http://localhost:8000
```
Health: `curl http://127.0.0.1:8001/api/health`
If UI “down”: restart `./start.sh` (port 8000). API is 8001.

### backend/.env keys that matter
- `GEMINI_API_KEY`, `GEMINI_MODEL`
- Azure SP (app named IDP_*, not OmniParse): `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`
- `SHAREPOINT_DRIVE_ID`
- Fabric ODBC: `SQL_SERVER`, `SQL_DATABASE=WH_IDP`, `SQL_USE_API_APP_CREDENTIALS=true`, Driver 18
- Graph needs Application `Files.Read.All` + admin consent

### Key APIs
- `GET  /api/health`
- `GET  /api/integrations/sharepoint/files` — list library PDFs
- `POST /api/extract/jobs` — form: `sharepoint_item_id` (+ engine/strategy/page range). NO `file` upload.
- `GET  /api/extract/jobs/{id}` — poll status/result
- `GET  /api/fabric/extracts` — history list
- `GET  /api/fabric/extracts/{run_id}` — load full ExtractResponse
- Auth: `/api/auth/*`, admin extract audit `/api/admin/extract-logs`, legacy local history `/api/me/extract-history`

### Important code paths
- `backend/app/main.py` — jobs, SharePoint, Fabric endpoints, queue
- `backend/app/integrations/graph_sharepoint.py` — Graph token/list/download
- `backend/app/integrations/fabric_sql.py` + `fabric_cache.py` — ODBC + hash cache + save/load
- `app.js` — SharePoint picker, job poll, `loadFabricRunFromQuery`
- `auth-admin.js` + `history.html` — My extracts Open button
- Scripts: `backend/scripts/test_graph_sharepoint.py`, `test_fabric_warehouse.py`

## AWS stack (already designed / deployed for pilot)
Template: `infra/cloudformation.yml` · Deploy: `./infra/deploy.sh`
- UI: S3 + CloudFront
- API: ECR → ECS Fargate → ALB; CloudFront `/api/*` → ALB (same-origin)
- Secrets Manager: Gemini key
- CloudWatch: `/ecs/omniparse-idp-api`
- Extract audit S3 bucket
- Uses EXISTING company VPC **My-VPC01** — do NOT create VPC/IGW/NAT
- Region default in deploy: **eu-west-1** (some older notes say eu-north-1 — verify live account)
- Live (historical pilot): `https://d11bl7hg497hj.cloudfront.net/` — confirm still valid before changing
- CloudFront origin read timeout max ~180s → async jobs + page ranges for long OCR
- First CFN create: `DesiredCount=0` until image pushed, then scale to 1
- Docker build: `--platform linux/amd64`
- Alternate no-local-AWS-login: `infra/MAC-CLOUDSHELL.md` (CloudShell + Mac Docker push)
- IAM needs: see `infra/IT-IAM-REQUEST.md` / `infra/iam-pilot-policy.json`
- Older handoff: `CLOUD-AGENT-HANDOFF.md` (AWS/Gemini pilot; **outdated** on upload — SharePoint/Fabric supersede PC upload)

Deploy sketch:
```bash
export AWS_REGION=eu-west-1
export VPC_ID=vpc-01cdd75a59e8dafbb
export PUBLIC_SUBNET_1=... PUBLIC_SUBNET_2=...
export PRIVATE_SUBNET_1=... PRIVATE_SUBNET_2=...
# omit GEMINI_API_KEY if secret already exists
./infra/deploy.sh
```

## LLM roadmap
- Now: Gemini API
- Later: Amazon Bedrock only (data sovereignty)
- Do NOT add Vertex / Lambda-only rewrite unless asked

## How you should work (token discipline)
1. Read only the files needed for the asked task.
2. Don’t rewrite README/SOP unless asked.
3. Don’t reintroduce PC upload / drag-drop / `input type=file` extract path.
4. Don’t insert duplicate Fabric rows on cache hit.
5. Don’t paste secrets into chat; use `backend/.env` (gitignored).
6. After API code changes: restart `./start-api.sh`. After UI: hard-refresh (cache-bust `?v=` on js/css).
7. Prefer small diffs matching existing style.
8. Commits/PRs only when user asks.

## Quick verify checklist
- [ ] UI :8000 and API :8001 healthy
- [ ] SharePoint list returns PDFs
- [ ] Extract job with `sharepoint_item_id` works
- [ ] Second open of same PDF → `fabric-cache`, Fabric `runs` per hash stays 1
- [ ] My extracts → Open loads registries
- [ ] AWS: `/api/health` via CloudFront after deploy

When the user gives a task, execute against THIS reality — don’t rebuild from scratch.
```

---

## How to use with Antigravity

1. Open this repo in Antigravity (same folder as this file).
2. Start a new chat / agent run.
3. Attach or `@` mention **`ANTIGRAVITY-BRIEF.md`** (or paste the fenced block above into the first message).
4. Then give **one concrete task**, for example:
   - `Follow ANTIGRAVITY-BRIEF.md. Restart local UI+API and verify /api/health.`
   - `Follow ANTIGRAVITY-BRIEF.md. Fix My extracts Open if fabric_run_id fails to load.`
   - `Follow ANTIGRAVITY-BRIEF.md. Deploy to AWS with ./infra/deploy.sh; do not create a new VPC.`
5. Keep secrets in `backend/.env` only — never paste Azure/Gemini keys into Antigravity.

Optional: also point it at `CLOUD-AGENT-HANDOFF.md` for older AWS notes, but treat **this file as source of truth** for SharePoint/Fabric (no PC upload).
