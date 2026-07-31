# OmniParse IDP – Maintenance Extractor

Hybrid app:

- **UI** — JavaScript (`index.html` + `app.js` + `styles.css`) on port **8000**
- **API** — Python FastAPI extraction backend on port **8001**

Core aim unchanged: extract **maintenance**, **spare parts**, and **troubleshooting** from engineering manuals into editable registries + Excel export.

---

## Requirements

- Python **3.10+**
- Modern browser (Chrome / Edge / Firefox)
- Optional:
  - **Gemini API key** (cloud extraction)
  - **Ollama** running locally (local extraction)

---

## 1) One-time setup (API)

```bash
cd /Users/akshayryali/1/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

---

## 2) Run the project (two terminals)

### Terminal A — Python API

```bash
cd /Users/akshayryali/1
./start-api.sh
```

API: [http://127.0.0.1:8001](http://127.0.0.1:8001)  
Swagger docs: [http://127.0.0.1:8001/docs](http://127.0.0.1:8001/docs)

### Terminal B — UI

```bash
cd /Users/akshayryali/1
./start.sh
```

UI: [http://localhost:8000](http://localhost:8000)

---

## 3) Use the app

1. Open [http://localhost:8000](http://localhost:8000)
2. Confirm header status shows **Python API Ready** (or **Browser Engine Ready** if API is down)
3. Choose engine:
   - **Gemini API** → paste your key → **Verify Key**
   - **Ollama** → set endpoint/model → **Sync**
   - **Heuristics** → always uses the in-browser extractor
4. Prefer **OCR Vision** for scanned manuals
5. Upload a PDF / Word / TXT / image manual
6. Edit rows in the registries if needed → **Export to Excel**

---

## Ports (both apps at once)

| App | Port | URL |
|-----|------|-----|
| Maintenance UI (`/1`) | 8000 | http://localhost:8000 |
| Maintenance API (`/1/backend`) | 8001 | http://127.0.0.1:8001 |
| Invoice UI (`/2`) | 8080 | http://localhost:8080 |

> If you run both APIs, give one a different `API_PORT` (e.g. `API_PORT=8002 ./start-api.sh`).

---

## Optional: API defaults via env

```bash
cd /Users/akshayryali/1/backend
export GEMINI_API_KEY="your_key_here"
export GEMINI_MODEL="gemini-3.5-flash"
export OLLAMA_URL="http://localhost:11434"
export OLLAMA_MODEL="llava"
./start-api.sh
```

You can still pass the Gemini key from the UI; the UI forwards it to the API on each extract.

---

## Fallback behavior

If the Python API is **not** running, or you use **Heuristics** / **Word (DOCX)**, the UI still works with the **in-browser** extractor and shows:

`Python API not reachable … using in-browser extractor`

Start the API with `./start-api.sh` to use the FastAPI path for PDF/TXT/images + Gemini/Ollama.

---

## Useful checks

```bash
# API health
curl http://127.0.0.1:8001/api/health

# UI root
curl -I http://localhost:8000/
```

---

## Deploy to AWS (ECS Fargate — recommended)

Static UI on **S3 + CloudFront**; FastAPI on **ECS Fargate** behind an **ALB**. CloudFront proxies `/api/*` to the ALB (same-origin HTTPS).

**Prerequisites:** AWS CLI v2, Docker, IAM rights for CloudFormation / ECS / ECR / S3 / CloudFront / Secrets Manager.

```bash
export AWS_REGION=eu-north-1
export GEMINI_API_KEY="your_key_here"   # optional; UI can still paste a key
chmod +x infra/deploy.sh
./infra/deploy.sh
```

**Region:** pilot deploy uses **`eu-north-1`** (Stockholm).  
**LLM now:** Gemini API (key in Secrets Manager or UI). **Bedrock** is a later phase.  
**Gate:** deploy refuses to run until `aws sts get-caller-identity` succeeds.

### Alternate: Mac Docker + CloudShell (no local AWS login)

If local `aws login` / access keys are blocked, use browser **CloudShell** for AWS API calls and **Docker on a Mac** only for build/push. ECR auth is a password from CloudShell pasted into `docker login` on the Mac.

See **[`infra/MAC-CLOUDSHELL.md`](infra/MAC-CLOUDSHELL.md)** (`cloudshell-phase1.sh` → `mac-push-ecr.sh` → `cloudshell-phase3.sh`).

The script creates/updates the CloudFormation stack, builds and pushes the API image to ECR, scales the Fargate service, syncs the UI to S3, and invalidates CloudFront. When it finishes, open the printed `CloudFrontUrl` and confirm header status shows **Python API Ready**.

| Piece | AWS |
|-------|-----|
| `index.html` / `app.js` / `styles.css` | S3 → CloudFront |
| `backend/` FastAPI | ECR → ECS Fargate → ALB |
| `GEMINI_API_KEY` | Secrets Manager |
| Logs | CloudWatch `/ecs/omniparse-idp-api` |

**Note:** CloudFront’s custom-origin read timeout max is **180 seconds**. Use page ranges for long OCR jobs, or later put an HTTPS ALB on a custom domain for longer requests.

Tear down: `aws cloudformation delete-stack --stack-name omniparse-idp`

---

## Project layout

```text
/Users/akshayryali/1/
├── index.html              # UI
├── app.js                  # UI logic (API-first, browser fallback)
├── styles.css
├── equipment_manifest.json # Domain keywords / normalization
├── start.sh                # UI server :8000
├── start-api.sh            # FastAPI server :8001
├── infra/
│   ├── cloudformation.yml  # Fargate + ALB + S3/CloudFront
│   ├── deploy.sh           # One-shot deploy (needs local AWS CLI)
│   ├── MAC-CLOUDSHELL.md   # Mac Docker + CloudShell (no local AWS login)
│   ├── cloudshell-phase1.sh
│   ├── cloudshell-phase3.sh
│   ├── mac-push-ecr.sh
│   └── .env.deploy.example
├── scripts/                # Scratch / one-off test scripts
└── backend/
    ├── Dockerfile
    ├── app/
    │   ├── main.py
    │   ├── extractors/
    │   └── pdf_utils.py
    ├── requirements.txt
    └── start-api.sh
```
