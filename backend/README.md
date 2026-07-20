# OmniParse Maintenance API (FastAPI)

Heavy extraction backend for the maintenance IDP UI. The browser keeps the grid/chat/theme; this service handles PDF/OCR + Gemini/Ollama for maintenance, spare parts, and troubleshooting registries.

## Run

```bash
# from repo root (/Users/.../1) or backend/
./start-api.sh
```

API: http://127.0.0.1:8001  
Docs: http://127.0.0.1:8001/docs  

UI (separate terminal):

```bash
./start.sh   # http://localhost:8000
```

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Liveness |
| POST | `/api/extract` | Upload PDF/TXT/JPG/PNG → maintenance / spare_parts / troubleshooting |

### `/api/extract` form fields

- `file` (required)
- `engine`: `gemini` \| `ollama`
- `parse_strategy`: `native` \| `ocr`
- `gemini_api_key`, `gemini_model`
- `ollama_url`, `ollama_model`
- `page_start`, `page_end` (optional)
- `equipment_category` (optional; `Default` or `Logbook`)
- `learned_patterns` (optional JSON array string)

## Env (optional)

Copy `.env.example` values into your shell or a `.env` loader. UI can still pass the Gemini key per request.
