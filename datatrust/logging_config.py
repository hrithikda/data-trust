"""Logging setup shared by the CLI, services and the Streamlit app."""

from __future__ import annotations

import logging

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging once; later calls only adjust the level."""
    global _CONFIGURED
    if not _CONFIGURED:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        _CONFIGURED = True
    logging.getLogger().setLevel(level)
    for noisy in ("urllib3", "watchdog", "fsevents"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
