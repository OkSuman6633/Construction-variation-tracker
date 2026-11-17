"""Malware-scan gate. Uploaded files stay quarantined until a scanner clears them.

DECISION OUTSTANDING (assessment risk 4): production scanner (ClamAV job vs third party) is not
selected. Until one is wired in, production uses UnavailableScanner: files remain QUARANTINED and
cannot be analysed. DevNoopScanner is for local/test only and is refused in staging/production.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ScanResult:
    scanned: bool
    clean: bool
    detail: str


class Scanner(Protocol):
    def scan(self, data: bytes, mime: str) -> ScanResult: ...


class DevNoopScanner:
    """DEV/TEST ONLY: performs NO malware scan."""

    def scan(self, data: bytes, mime: str) -> ScanResult:
        return ScanResult(True, True, "dev-only: no malware scan performed")


class UnavailableScanner:
    def scan(self, data: bytes, mime: str) -> ScanResult:
        return ScanResult(False, False, "no malware scanner configured; file remains quarantined")
