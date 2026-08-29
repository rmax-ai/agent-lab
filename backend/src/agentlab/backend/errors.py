"""Shared HTTP error type for backend services.

Keeps the flat ``{"error": {"code", "description"}}`` envelope free of a hard
dependency from services back onto ``app.py`` (which imports the services).
"""

from __future__ import annotations


class BackendError(Exception):
    """A domain error rendered as the flat ``{"error": {...}}`` envelope."""

    def __init__(self, status_code: int, code: str, description: str) -> None:
        super().__init__(description)
        self.status_code = status_code
        self.code = code
        self.description = description
