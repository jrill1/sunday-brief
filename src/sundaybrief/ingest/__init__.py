"""Dispatch table mapping a source `type` to its ingest function.

Every ingester has the same shape: (source, window_start, window_end) -> list[Event].
"""
from .ical import ingest_ical
from .scrape import (
    ingest_cityspark_events,
    ingest_headless,
    ingest_libnet_events,
    ingest_mec_events,
    ingest_rss,
    ingest_wp_events,
)

INGESTERS = {
    "ical": ingest_ical,
    "rss": ingest_rss,
    "wp-events": ingest_wp_events,
    "libnet-events": ingest_libnet_events,
    "mec-events": ingest_mec_events,
    "cityspark-events": ingest_cityspark_events,
    "headless": ingest_headless,
}

__all__ = [
    "INGESTERS", "ingest_ical", "ingest_rss", "ingest_wp_events",
    "ingest_libnet_events", "ingest_mec_events", "ingest_cityspark_events",
    "ingest_headless",
]
