# ⚡ Intraday Ops Intelligence

> A time-aware operational intelligence copilot that answers "what's happening right now?" using Gemini Flash rolling summaries over live GCP streaming events.

[![Build](https://img.shields.io/badge/build-passing-brightgreen)](https://github.com/jthiruveedula/intraday-ops-intelligence/actions)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Streaming](https://img.shields.io/badge/ingest-Pub%2FSub%20%7C%20Dataflow-red)](https://cloud.google.com/dataflow)
[![BigQuery](https://img.shields.io/badge/store-BigQuery%20Partitioned-4285F4)](https://cloud.google.com/bigquery)
[![Freshness](https://img.shields.io/badge/retrieval-freshness--aware-orange)](https://github.com/jthiruveedula/intraday-ops-intelligence)
[![Latency](https://img.shields.io/badge/p95%20answer-%3C3s-yellow)](https://github.com/jthiruveedula/intraday-ops-intelligence)
[![Gemini](https://img.shields.io/badge/model-Gemini%202.0%20Flash-8E75FF)](https://cloud.google.com/vertex-ai)
[![License](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

---

## ⚡ Why This Exists

Operational events — pipeline failures, SLA breaches, job completions, system alerts — arrive continuously but humans can't keep up.  
`intraday-ops-intelligence` ingests every event through a native GCP streaming stack, builds rolling Gemini summaries, and lets any team member ask **"what failed in the last hour?"** and get a grounded, timestamped answer in seconds.

---

## 🏗️ Architecture

```
Operational Systems (pipelines, APIs, jobs)
       |
       v
  Pub/Sub  (events topic + dead-letter topic)
       |
       v
  Dataflow Flex Template  (streaming)
  +------------------------------------------+
  |  ParseEvent -> Enrich -> BigQuerySink    |
  +------------------------------------------+
       |
       v
  BigQuery  (partitioned by event_date, clustered by event_type)
  +---------------------+   +----------------------+
  |  events table       |   |  summaries table     |
  |  (intraday events)  |   |  (rolling summaries) |
  +---------------------+   +----------------------+
            ^                          ^
            |                          |
            +---- Cloud Scheduler -----+
                  (every 15 min)
                       |
                       v
               Gemini 2.0 Flash
               (window_query -> flash_summarize -> write)
                       |
                       v
  Cloud Run  -  FastAPI /ask + /status + /digest
```

---

## 🕐 Freshness Model

| Layer | Mechanism | Freshness |
|---|---|---|
| Event ingestion | Pub/Sub → Dataflow streaming insert | Near real-time (<30s) |
| Rolling summaries | Cloud Scheduler → `build_summary.py` | Every 15 min |
| Q&A context | Last 4 summaries + time-filtered events | Last 1–24h (configurable) |
| Staleness signal | `newest_source_ts` in every API response | Visible to caller |

---

## 📥 Event Schema

```json
{
  "event_id": "uuid",
  "event_type": "pipeline_failure | sla_breach | job_completed | alert",
  "source_system": "airflow | dataflow | bigquery | custom",
  "severity": "critical | high | medium | low",
  "message": "Detailed event description",
  "metadata": {"job_id": "...", "dataset": "..."},
  "timestamp": "2026-04-08T00:00:00Z"
}
```

---

## 📁 Repo Structure

```
intraday-ops-intelligence/
├── infra/                    # Terraform: Pub/Sub, Dataflow, BQ, Scheduler, IAM
├── ingestion/                # Beam streaming pipeline
│   ├── pipeline_main.py
│   ├── transforms/
│   └── flex_template/
├── summarizer/               # Rolling summary generator
│   ├── build_summary.py
│   ├── window_query.py
│   └── flash_summarizer.py
├── api/                      # FastAPI /ask + /status + /digest
│   ├── main.py
│   └── staleness_middleware.py  # 🆕 Freshness telemetry per request
├── jobs/                     # 🆕 Change digest background job
│   └── build_change_digest.py
├── observability/            # 🆕 Metrics: source age, latency, staleness
│   └── metrics.py
├── simulator/                # Synthetic event publisher
│   └── event_simulator.py
├── tests/
└── docs/
```

---

## 🚀 Quickstart

```bash
# Start event simulator + API
docker-compose up

# Publish test events
python simulator/event_simulator.py --scenario=anomaly_spike --count=100

# Query the API
curl -X POST http://localhost:8080/ask \
  -H 'Content-Type: application/json' \
  -d '{"question": "What failed in the last hour?"}'

# GCP deploy
cd infra && terraform apply -var-file=environments/dev.tfvars
gcloud dataflow flex-template build gs://my-bucket/templates/intraday.json \
  --image-gcr-path=gcr.io/my-proj/intraday-pipeline:latest --sdk-language=PYTHON
gcloud run jobs deploy intraday-summarizer --source summarizer/
gcloud run deploy intraday-api --source api/ --region=us-central1
```

---

## 📊 LLM Usage

| Parameter | Value |
|---|---|
| Model | `gemini-2.0-flash-001` |
| Summary input tokens | 8 000 max (event batch) |
| Summary output tokens | 512 (concise) |
| Q&A input tokens | 4 000 max |
| Q&A output tokens | 1 024 |
| Temperature (summary) | 0.1 |
| Temperature (Q&A) | 0.3 |

---

## 🔭 Observability

- **Staleness telemetry**: every response includes `newest_source_ts`, `oldest_source_ts`, avg source age
- **Request metrics**: latency, token spend, summary hit rate
- **Change digest**: `jobs/build_change_digest.py` — topic-level delta summary per scheduled window

---

## 🛣️ Roadmap

### Now / Next
- [ ] **Freshness-Weighted Retrieval** — timestamp decay scoring in retrieval ranking
- [ ] **Time-Window Query Params** — `?time_window=1h` filter on `/ask` endpoint
- [ ] **Change Digest Job** — periodic delta summary stored in `summaries` table
- [ ] **Staleness Telemetry** — `newest_source_ts` + avg source age in every response
- [ ] **Alert Subscriptions** — topic-level digest push to Slack / PagerDuty

### Future / Wow
- [ ] **Live Decision Copilot** — multi-agent monitor → summarise → recommend next action
- [ ] **Temporal Memory** — rewindable snapshots showing how answers evolved during the day
- [ ] **Anomaly Narrator** — detect unusual event patterns and explain them in plain language
- [ ] **Role-Based Watchlists** — personalised feeds for ops leads, data engineers, on-call
- [ ] **Policy-Aware Action Triggers** — route critical events to tickets / alerts automatically

---

## 🤝 Contributing

PRs welcome. Run `make lint test` before opening a PR.

## 📄 License

MIT — see [LICENSE](LICENSE)
