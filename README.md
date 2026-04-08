# intraday-ops-intelligence

> Streaming Pub/Sub → Dataflow → BigQuery pipeline that answers "what's happening right now?" using Gemini Flash rolling summaries over intraday operational events.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![GCP](https://img.shields.io/badge/GCP-Pub%2FSub%20%7C%20Dataflow%20%7C%20BigQuery-orange)](https://cloud.google.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Overview

`intraday-ops-intelligence` is a real-time operational intelligence platform built entirely on native GCP streaming primitives. It ingests operational events (pipeline failures, SLA breaches, system alerts, job completions) via Pub/Sub, processes them through an Apache Beam streaming job on Dataflow, stores them in BigQuery partitioned/clustered tables, and periodically generates rolling summaries using Gemini Flash that can be queried in natural language.

Key capabilities:
- **True streaming ingestion**: Pub/Sub → Dataflow Flex Template → BigQuery streaming inserts with exactly-once semantics.
- **Sliding-window summaries**: Cloud Scheduler triggers `summarizer/build_summary.py` every N minutes; Gemini Flash summarises the last rolling window of events with token-budget enforcement.
- **Natural-language Q&A**: FastAPI `/ask` endpoint answers "what failed in the last hour?" or "are there SLA breaches today?" using summary+event context.
- **Event simulator**: `simulator/event_simulator.py` publishes synthetic events for local development and load testing.

---

## Architecture

```
Operational Systems (pipelines, APIs, jobs)
       |
       v
  Pub/Sub (events topic + dead-letter topic)
       |
       v
  Dataflow Flex Template (streaming)
  +------------------------------------------+
  | ParseEvent -> Enrich -> BigQuerySink      |
  +------------------------------------------+
       |
       v
  BigQuery (partitioned by event_date, clustered by event_type)
  +-----------------------+    +-----------------------+
  | events table          |    | summaries table        |
  | (intraday events)     |    | (rolling summaries)    |
  +-----------------------+    +-----------------------+
            ^                           ^
            |                           |
            +---  Cloud Scheduler  ------+
                  (triggers summarizer)
                       |
                       v
               Gemini 2.0 Flash
               (window_query -> flash_summarize -> write)
                       |
                       v
  Cloud Run - FastAPI /ask + /status
```

---

## Data Sources & Ingestion

| Source | Mechanism | Frequency |
|---|---|---|
| Operational event publishers | Pub/Sub push subscription | Real-time |
| Synthetic events (dev) | `simulator/event_simulator.py` | On-demand |
| Rolling summaries | Cloud Scheduler -> `summarizer/build_summary.py` | Every 15 min |

Event schema (JSON in Pub/Sub message):
```json
{
  "event_id": "uuid",
  "event_type": "pipeline_failure | sla_breach | job_completed | alert",
  "source_system": "airflow | dataflow | bigquery | custom",
  "severity": "critical | high | medium | low",
  "message": "Detailed event description",
  "metadata": {"job_id": "...", "dataset": "..."},
  "timestamp": "2026-04-07T23:00:00Z"
}
```

---

## RAG / Search Layer

- **Primary context**: Rolling summaries stored in `summaries` BigQuery table (last 4 summaries injected).
- **Secondary context**: Direct event rows from `events` table via `window_query.py` (last N hours).
- **Retrieval**: Full-text search (`LIKE`/`SEARCH`) on summaries + time-filtered event scan.
- No vector index needed for this pattern — recency-based retrieval is more relevant than semantic similarity for operational events.

---

## LLM Usage

| Parameter | Value |
|---|---|
| Model | `gemini-2.0-flash-001` |
| Summary max input tokens | 8 000 (event batch) |
| Summary max output tokens | 512 (concise summary) |
| Q&A max input tokens | 4 000 (summaries + question) |
| Q&A max output tokens | 1 024 |
| Temperature (summary) | 0.1 (deterministic) |
| Temperature (Q&A) | 0.3 |

Token budget for summaries: `flash_summarizer.py` truncates the event batch to fit the window before the API call.

---

## Deployment

### Local Dev
```bash
# Start event simulator + API
docker-compose up

# Publish test events
python simulator/event_simulator.py --scenario=anomaly_spike --count=100

# Query the API
curl -X POST http://localhost:8080/ask -d '{"question": "What failed in the last hour?"}'
```

### GCP Deployment
```bash
# 1. Provision infra
cd infra && terraform apply -var-file=environments/dev.tfvars

# 2. Build and submit Dataflow Flex Template
gcloud dataflow flex-template build gs://my-bucket/templates/intraday.json \
  --image-gcr-path=gcr.io/my-proj/intraday-pipeline:latest \
  --sdk-language=PYTHON

# 3. Deploy summarizer Cloud Run job
gcloud run jobs deploy intraday-summarizer --source summarizer/

# 4. Deploy API
gcloud run deploy intraday-api --source api/ --region=us-central1
```

---

## Repo Structure

```
intraday-ops-intelligence/
|-- infra/                    # Terraform: Pub/Sub, Dataflow, BQ, Scheduler, IAM
|-- ingestion/                # Beam streaming pipeline
|   |-- pipeline_main.py
|   |-- transforms/
|   `-- flex_template/
|-- summarizer/               # Rolling summary generator
|   |-- build_summary.py
|   |-- window_query.py
|   `-- flash_summarizer.py
|-- api/                      # FastAPI /ask + /status
|   `-- main.py
|-- simulator/                # Synthetic event publisher for dev/testing
|   `-- event_simulator.py
|-- tests/
`-- docs/
```

---

## Roadmap

1. **Anomaly detection layer** — statistical baseline per event type; Gemini Flash flags deviations in the summary.
2. **PagerDuty / Slack webhook integration** — push critical summaries to on-call channels automatically.
3. **Multi-project federation** — aggregate events from multiple GCP projects into a central BigQuery dataset for cross-org ops intelligence.
