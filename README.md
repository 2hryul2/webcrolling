# claude_webcroll — Step 1

FastAPI-based RSS monitoring system with file-based persistence (no database).

Monitors external data sources (DART, FSC, 국회, YouTube) for compliance and policy changes with keyword-based alerting.

---

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env with your SMTP settings (optional)
```

### 3. Run Application
```bash
uvicorn main:app --reload
```

### 4. Check Status
```bash
curl http://localhost:8000/status
```

---

## Architecture (Step 1)

```
Scheduler (APScheduler)
  ↓
RSS Collector (DART, FSC)
  ↓
Normalizer (ExternalEvent)
  ↓
Deduplicator (content_hash)
  ↓
Keyword Matcher
  ↓
Notification Router
  ├─→ Email (SMTP, urgent/watch only)
  └─→ File Log (JSONL, all events)
```

---

## API Endpoints

- **GET /status** — System health, event counts, source status
- **GET /events?limit=100** — Recent events (JSONL)
- **GET /alerts?limit=100** — Recent alerts (JSONL)
- **POST /trigger** — Manual poll trigger

---

## File Structure

```
D:\source\claude_webcroll\
├── main.py                          # FastAPI entry point
├── requirements.txt                 # Dependencies
├── .env.example                     # Environment template
├── README.md                        # This file
├── @spec_20260509.md               # EARS specification
├── config/
│   ├── sources.yaml                # RSS source configuration
│   └── keywords.yaml               # Keyword definitions
├── app/
│   ├── models.py                   # Pydantic models
│   ├── database.py                 # JSONL I/O utilities
│   ├── scheduler.py                # APScheduler setup
│   └── routes/
│       └── status.py               # REST endpoints
├── monitor/
│   ├── collectors/
│   │   ├── rss.py                 # Base RSS collector
│   │   ├── dart.py                # DART RSS implementation
│   │   └── fsc.py                 # FSC RSS implementation
│   ├── matcher.py                 # Keyword matching engine
│   ├── notifier.py                # Email + file notification
│   └── worker.py                  # Main polling worker
├── data/
│   ├── events.jsonl               # Collected events
│   ├── alerts.jsonl               # Alert log
│   └── state.json                 # Last poll timestamps
├── logs/
│   └── app.log                    # Application logs
├── docs/
│   └── 개발노트_step1_20260509.md # Development notes
├── ideation/
│   └── ideation_step1_20260509.md # Architecture decisions
└── tests/
    ├── test_models.py
    ├── test_database.py
    ├── test_rss.py
    ├── test_matcher.py
    └── test_dedup.py
```

---

## Development Notes

See `docs/개발노트_step1_20260509.md` for ongoing development progress.

---

## Step 1 Deliverables

- ✅ FastAPI application with APScheduler
- ✅ RSS collection from DART & FSC
- ✅ Event normalization (Pydantic models)
- ✅ Deduplication via content_hash
- ✅ Keyword matching with severity levels
- ✅ Email alerts (urgent/watch events)
- ✅ File-based logging (JSONL)
- ✅ REST API endpoints
- ✅ Comprehensive tests

---

## Step 2+ Roadmap

- National Assembly (국회) API integration
- YouTube search API integration
- DART OpenAPI enhancement
- Advanced keyword matching (fuzzy, exclusions)
- PostgreSQL migration option
- Web dashboard

---

## Configuration

### sources.yaml
```yaml
sources:
  dart:
    name: "DART RSS"
    type: "rss"
    endpoint: "https://dart.fss.or.kr/api/rssFeeds.json"
    poll_interval_sec: 300
    enabled: true
  fsc:
    name: "FSC Press Release"
    type: "rss"
    endpoint: "https://www.fsc.go.kr/rss/pressRelease.xml"
    poll_interval_sec: 600
    enabled: true
```

### keywords.yaml
See config/keywords.yaml for keyword definitions (urgent/watch/info categories).

---

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=. tests/

# Run specific test file
pytest tests/test_rss.py -v
```

---

## Troubleshooting

**App won't start:**
- Check Python 3.9+ installed: `python --version`
- Check dependencies: `pip install -r requirements.txt`
- Check .env exists: `ls -la .env`

**No events collected:**
- Check RSS endpoints are accessible
- Check log file: `tail -f logs/app.log`
- Trigger manual poll: `curl -X POST http://localhost:8000/trigger`

**Email not sending:**
- SMTP is optional - app works without it
- Check .env has SMTP credentials
- Check ALERT_EMAIL is valid
- Review logs for SMTP errors

---

## License

MIT

---

Created: 2026-05-09
Project Owner: (Your Name)
Tech Lead: Architect
Builder: Claude Builder
Reviewer: Claude Reviewer
