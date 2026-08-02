"""RDAgent bridge client errors."""

from __future__ import annotations


class RdAgentBridgeError(Exception):
    """Raised when bridge configuration or HTTP calls fail."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = int(status_code)
        self.code = str(code)
        self.message = str(message)
        super().__init__(message)
