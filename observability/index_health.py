"""observability/index_health.py

Corpus health report for intraday-ops-intelligence.
Checks BigQuery summary table freshness, summarizer job success rate,
and DLQ backlog to produce a CorpusHealthReport.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

from google.cloud import bigquery, pubsub_v1

logger = logging.getLogger(__name__)

PROJECT_ID: str = os.environ["GCP_PROJECT_ID"]
SUMMARY_TABLE: str = os.environ.get(
    "SUMMARY_BQ_TABLE", f"{PROJECT_ID}.intraday_ops.summaries"
)
DLQ_SUBSCRIPTION: str = os.environ.get(
    "DLQ_SUBSCRIPTION", f"projects/{PROJECT_ID}/subscriptions/intraday-ops-dlq-sub"
)
STALE_THRESHOLD_MINUTES: float = float(os.environ.get("STALE_THRESHOLD_MINUTES", "30"))
FAILURE_RATE_THRESHOLD: float = float(os.environ.get("FAILURE_RATE_THRESHOLD", "0.05"))
DLQ_THRESHOLD: int = int(os.environ.get("DLQ_THRESHOLD", "10"))


@dataclass
class CorpusHealthReport:
    generated_at: str
    total_summaries: int
    total_tickers: int
    summaries_by_ticker: dict
    last_summary_ts: Optional[str]
    sync_lag_minutes: Optional[float]
    failure_rate: float
    dlq_backlog_count: int
    status: str  # "healthy" | "degraded" | "critical"


def get_corpus_stats(client: bigquery.Client) -> dict:
    """Fetch summary counts from BigQuery."""
    query = f"""
        SELECT
            COUNT(*) AS total_summaries,
            COUNT(DISTINCT ticker) AS total_tickers,
            ARRAY_AGG(
                STRUCT(ticker, COUNT(*) AS cnt)
                ORDER BY cnt DESC LIMIT 20
            ) AS by_ticker
        FROM `{SUMMARY_TABLE}`
        WHERE DATE(created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
    """
    row = list(client.query(query).result())[0]
    by_ticker = {r["ticker"]: r["cnt"] for r in (row["by_ticker"] or [])}
    return {
        "total_summaries": row["total_summaries"],
        "total_tickers": row["total_tickers"],
        "by_ticker": by_ticker,
    }


def get_last_summary_ts(client: bigquery.Client) -> Optional[datetime]:
    """Return the timestamp of the most recent summary written."""
    query = f"""
        SELECT MAX(created_at) AS last_ts
        FROM `{SUMMARY_TABLE}`
    """
    row = list(client.query(query).result())[0]
    return row["last_ts"]


def get_failure_rate(client: bigquery.Client) -> float:
    """Compute summarizer job failure rate over the past 24 hours."""
    query = f"""
        SELECT
            COUNTIF(status = 'FAILED') / NULLIF(COUNT(*), 0) AS failure_rate
        FROM `{PROJECT_ID}.intraday_ops.summarizer_jobs`
        WHERE created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
    """
    try:
        row = list(client.query(query).result())[0]
        return float(row["failure_rate"] or 0.0)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Could not fetch failure rate: %s", exc)
        return 0.0


def get_dlq_backlog(subscription: str) -> int:
    """Return approximate undelivered message count from Pub/Sub DLQ."""
    try:
        subscriber = pubsub_v1.SubscriberClient()
        response = subscriber.get_subscription(request={"subscription": subscription})
        # pull a small batch to estimate; use seek for accurate counts in prod
        pull_response = subscriber.pull(
            request={"subscription": subscription, "max_messages": 100}
        )
        return len(pull_response.received_messages)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("DLQ check failed: %s", exc)
        return 0


def classify_status(
    sync_lag: Optional[float],
    failure_rate: float,
    dlq_count: int,
) -> str:
    if (
        (sync_lag is not None and sync_lag > STALE_THRESHOLD_MINUTES * 2)
        or failure_rate > FAILURE_RATE_THRESHOLD * 2
        or dlq_count > DLQ_THRESHOLD * 5
    ):
        return "critical"
    if (
        (sync_lag is not None and sync_lag > STALE_THRESHOLD_MINUTES)
        or failure_rate > FAILURE_RATE_THRESHOLD
        or dlq_count > DLQ_THRESHOLD
    ):
        return "degraded"
    return "healthy"


def build_health_report() -> CorpusHealthReport:
    """Build a full CorpusHealthReport from BigQuery and Pub/Sub."""
    client = bigquery.Client(project=PROJECT_ID)

    stats = get_corpus_stats(client)
    last_summary = get_last_summary_ts(client)
    failure_rate = get_failure_rate(client)
    dlq_count = get_dlq_backlog(DLQ_SUBSCRIPTION)

    now = datetime.now(timezone.utc)
    sync_lag: Optional[float] = None
    last_ts_str: Optional[str] = None

    if last_summary:
        last_ts_str = last_summary.isoformat()
        sync_lag = (now - last_summary).total_seconds() / 60

    status = classify_status(sync_lag, failure_rate, dlq_count)

    report = CorpusHealthReport(
        generated_at=now.isoformat(),
        total_summaries=stats["total_summaries"],
        total_tickers=stats["total_tickers"],
        summaries_by_ticker=stats["by_ticker"],
        last_summary_ts=last_ts_str,
        sync_lag_minutes=round(sync_lag, 2) if sync_lag is not None else None,
        failure_rate=failure_rate,
        dlq_backlog_count=dlq_count,
        status=status,
    )

    logger.info(
        "Intraday ops health: %s | lag=%.1f min | failures=%.1f%%",
        status,
        sync_lag or 0,
        failure_rate * 100,
    )
    return report


if __name__ == "__main__":
    import json

    report = build_health_report()
    print(json.dumps(asdict(report), indent=2, default=str))

