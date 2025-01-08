"""Logging setup, with a hard rule about what must never be logged.

This service stores patient-identifiable clinical data. ``patient_id``,
``created_by`` and ``note_content`` are PHI and must not reach the logs, an
error payload, or a traceback repr. The original code printed whole result
sets and whole stats dicts on every request, and returned ``str(exception)``
to the client in a 500 body.

The rules:

* Log identifiers that are safe to correlate on -- tenant id, facility id,
  note id, row counts, durations.
* Never log a note's content, its patient, or its author.
* Never return an internal exception message to a client.
"""

from __future__ import annotations

import logging
import sys

#: Field names that must never be written to a log record or error response.
SENSITIVE_FIELDS = frozenset({"patient_id", "created_by", "note_content"})


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging once, to stdout."""
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level.upper())
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s | %(message)s"))
    root.addHandler(handler)
    root.setLevel(level.upper())


def scrub(payload: dict) -> dict:
    """Return ``payload`` with PHI fields replaced by a redaction marker.

    For the rare case where a dict genuinely has to be logged.
    """
    return {
        key: ("[redacted]" if key in SENSITIVE_FIELDS else value) for key, value in payload.items()
    }
