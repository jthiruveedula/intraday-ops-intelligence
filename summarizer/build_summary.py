"""summarizer/build_summary.py

Rolling-window summary job.
Triggered by Cloud Scheduler every N minutes.
Queries BigQuery for events in the last rolling window,
calls Gemini Flash for a concise summary, and writes it back to BigQuery.

Usage (local):
  python -m summarizer.build_summary \\
      --project=my-proj \\
      --window_minutes=15 \\
      --bq_dataset=ops_intelligence
"""
from __future__ import annotations

import argparse
import logging
import uuid
from datetime import datetime, timezone

from google.cloud import bigquery
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

from summarizer.window_query import build_window_query
from summarizer.flash_summarizer import FlashSummarizer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# BigQuery helpers
# ---------------------------------------------------------------------------

def fetch_events_in_window(client: bigquery.Client, project: str,
                           dataset: str, window_minutes: int) -> list[dict]:
    """Fetch events from BigQuery for the last `window_minutes` minutes.

    Returns a list of event dicts, ordered by timestamp descending.
    Capped at 500 events to stay within the Gemini Flash input token budget.
    """
    sql = build_window_query(
        project=project,
        dataset=dataset,
        table="events",
        window_minutes=window_minutes,
        max_rows=500,
    )
    logger.info("Fetching events: window=%d min", window_minutes)
    rows = list(client.query(sql).result())
    logger.info("Fetched %d events", len(rows))
    return [dict(row) for row in rows]


def write_summary(client: bigquery.Client, project: str, dataset: str,
                  summary_text: str, window_minutes: int,
                  event_count: int, token_count: int) -> None:
    """Insert a new summary record into the summaries table."""
    table_ref = f"{project}.{dataset}.summaries"
    row = {
        "summary_id": str(uuid.uuid4()),
        "summary_text": summary_text,
        "window_minutes": window_minutes,
        "event_count": event_count,
        "input_tokens": token_count,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    errors = client.insert_rows_json(table_ref, [row])
    if errors:
        logger.error("Failed to write summary: %s", errors)
        raise RuntimeError(f"BQ insert error: {errors}")
    logger.info("Summary written to %s (id=%s)", table_ref, row["summary_id"])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(args: argparse.Namespace) -> None:
    """Orchestrate: fetch events -> summarise -> write summary."""
    logger.info(
        "Starting summary build | project=%s dataset=%s window=%dmin",
        args.project, args.bq_dataset, args.window_minutes,
    )

    # Initialise clients
    vertexai.init(project=args.project, location=args.region)
    bq_client = bigquery.Client(project=args.project)
    summarizer = FlashSummarizer(
        model_name=args.model,
        max_input_tokens=args.max_input_tokens,
        max_output_tokens=args.max_output_tokens,
        temperature=args.temperature,
    )

    # Fetch events
    events = fetch_events_in_window(
        bq_client, args.project, args.bq_dataset, args.window_minutes
    )

    if not events:
        logger.info("No events in window. Skipping summary generation.")
        return

    # Summarise
    summary_result = summarizer.summarize(
        events=events,
        window_minutes=args.window_minutes,
    )

    logger.info(
        "Summary generated | tokens_in=%d tokens_out=%d",
        summary_result.input_tokens,
        summary_result.output_tokens,
    )

    # Write back to BQ
    write_summary(
        client=bq_client,
        project=args.project,
        dataset=args.bq_dataset,
        summary_text=summary_result.text,
        window_minutes=args.window_minutes,
        event_count=len(events),
        token_count=summary_result.input_tokens,
    )

    logger.info("Summary build complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build rolling operational summary")
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", default="us-central1")
    parser.add_argument("--bq_dataset", default="ops_intelligence")
    parser.add_argument("--window_minutes", type=int, default=15,
                        help="Rolling window size in minutes")
    parser.add_argument("--model", default="gemini-2.0-flash-001")
    parser.add_argument("--max_input_tokens", type=int, default=8000)
    parser.add_argument("--max_output_tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.1)
    main(parser.parse_args())
