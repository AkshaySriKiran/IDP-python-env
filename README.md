# OmniParse IDP — Maintenance & Spare Parts Extractor

A secure, high-throughput intelligent document processing (IDP) platform designed to extract **maintenance routines**, **spare parts lists**, and **troubleshooting protocols** from complex engineering manuals into validated registries and structured Excel exports.

## Architecture

- **Backend API**: Python FastAPI service on port `8001` handling asynchronous PDF/document OCR, LLM structured extraction, caching, and enterprise integrations.
- **Frontend UI**: Modern, vanilla JavaScript SPA on port `8000` with interactive registry editing, quality validation scoring, and Excel report export.

---

## Security Hardening Highlights

1. **SSRF Protection**: All outbound LLM requests (Ollama/custom endpoints) validate against a strict allowed-host whitelist and block private network ranges & cloud metadata endpoints (`169.254.169.254`).
2. **Zero Plaintext Secrets**: Sensitive keys and service principal credentials are exclusively loaded from environment variables and secret stores.
3. **Safe SQL Identifiers**: Explicit allowlisting for all table and column names in Fabric SQL operations to eliminate dynamic SQL injection vectors.
4. **Secure Token Handling**: JWT access tokens are validated with explicit algorithms, proper expiration timestamps, and protected from URL query-string leakage.
5. **Memory & DoS Protections**: Enforced bounded batch processing, input size validation, and resource-conscious document rendering.

---

## Quick Start

### 1. Backend Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
cp ../.env.example .env
# Edit .env to set your GEMINI_API_KEY or Azure credentials if needed
./start-api.sh
```

API will be running at `http://127.0.0.1:8001` (Docs: `http://127.0.0.1:8001/docs`).

### 2. Frontend Setup

In a separate terminal:
```bash
./start.sh
```

Access the UI at `http://localhost:8000`.

---

## Developer Commands

```bash
make run-api      # Start FastAPI backend
make run-ui       # Start UI server
make test         # Run security & unit test suite
make lint         # Run linter checks
```
