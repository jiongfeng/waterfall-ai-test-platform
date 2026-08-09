"""Stable, content-aware completion checks for generated plan files."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import time
from typing import Callable

from test_plan_viewer.artifacts.naming import validate_plan_filename

from .cases import extract_case_index, normalize_case_filename, normalize_case_index_cases
from .prompts import ABSOLUTE_PLAN_MAX_CASES


@dataclass(frozen=True)
class PlanFileSnapshot:
    """A content snapshot used to distinguish old artifacts from new output."""

    exists: bool
    size: int = 0
    mtime_ns: int = 0
    sha256: str = ""


def _missing_snapshot() -> PlanFileSnapshot:
    return PlanFileSnapshot(exists=False)


class PlanCompletionProbe:
    """Return true only after a changed, valid plan has remained stable.

    The baseline is captured when the probe is constructed. Calling ``check``
    never accepts that unchanged baseline, even when the target already exists.
    A changed candidate must be observed twice with at least ``stable_interval``
    seconds between observations before its ``cases`` payload is validated.
    """

    def __init__(
        self,
        target_file: Path | str,
        *,
        stable_interval: float = 0.5,
        max_cases: int = ABSOLUTE_PLAN_MAX_CASES,
        clock: Callable[[], float] = time.monotonic,
        read_bytes: Callable[[Path], bytes] | None = None,
    ) -> None:
        if stable_interval < 0:
            raise ValueError("stable_interval must be non-negative.")
        if max_cases <= 0:
            raise ValueError("max_cases must be positive.")

        self.target_file = Path(target_file)
        self.stable_interval = float(stable_interval)
        self.max_cases = int(max_cases)
        self._clock = clock
        self._read_bytes = read_bytes or (lambda path: path.read_bytes())
        self.baseline = self._capture_snapshot()
        self.last_snapshot = self.baseline
        self.last_error = ""
        self.cases: list[dict] = []
        self._candidate_snapshot: PlanFileSnapshot | None = None
        self._candidate_first_seen_at: float | None = None
        self._completed_snapshot: PlanFileSnapshot | None = None

    def _capture_snapshot(self) -> PlanFileSnapshot:
        try:
            before = self.target_file.stat()
            if not self.target_file.is_file():
                return PlanFileSnapshot(
                    exists=True,
                    size=before.st_size,
                    mtime_ns=before.st_mtime_ns,
                )
            content = self._read_bytes(self.target_file)
            after = self.target_file.stat()
        except FileNotFoundError:
            return _missing_snapshot()

        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or len(content) != after.st_size
        ):
            raise OSError(f"Plan file changed while it was being read: {self.target_file}")

        return PlanFileSnapshot(
            exists=True,
            size=len(content),
            mtime_ns=after.st_mtime_ns,
            sha256=hashlib.sha256(content).hexdigest(),
        )

    def _content_changed_since_baseline(self, snapshot: PlanFileSnapshot) -> bool:
        if not snapshot.exists or snapshot.size <= 0:
            return False
        if not self.baseline.exists:
            return True
        if snapshot.sha256 and self.baseline.sha256:
            return snapshot.sha256 != self.baseline.sha256
        return (
            snapshot.size != self.baseline.size
            or snapshot.mtime_ns != self.baseline.mtime_ns
        )

    def _reset_candidate(self) -> None:
        self._candidate_snapshot = None
        self._candidate_first_seen_at = None
        self.cases = []

    def _validate_cases(self) -> list[dict]:
        content = self._read_bytes(self.target_file)
        if not content:
            raise ValueError("Plan file is empty.")
        markdown_text = content.decode("utf-8")
        parsed = extract_case_index(markdown_text)
        cases = normalize_case_index_cases(parsed)
        if len(cases) > self.max_cases:
            raise ValueError(
                f"Plan contains {len(cases)} cases; maximum is {self.max_cases}."
            )

        filenames = set()
        for index, raw_case in enumerate(cases, start=1):
            if not isinstance(raw_case, dict):
                raise ValueError(f"cases[{index - 1}] must be an object.")
            title = str(raw_case.get("title") or raw_case.get("name") or "").strip()
            raw_filename = str(raw_case.get("filename") or "").strip()
            if raw_filename:
                validate_plan_filename(raw_filename)
            if raw_filename == self.target_file.name or raw_filename.startswith("_"):
                raise ValueError(
                    f"Case filename is unsafe or duplicated: {raw_filename}"
                )
            filename = normalize_case_filename(raw_filename, title, index=index)
            validate_plan_filename(filename)
            if (
                filename == self.target_file.name
                or filename.startswith("_")
                or filename in filenames
            ):
                raise ValueError(f"Case filename is unsafe or duplicated: {filename}")
            filenames.add(filename)

        if not filenames:
            raise ValueError("Plan does not contain a usable case filename.")
        return cases

    def check(self) -> bool:
        """Observe the target once and report whether generation is complete."""

        if self._completed_snapshot is not None:
            return True

        try:
            snapshot = self._capture_snapshot()
        except (OSError, UnicodeError) as exc:
            self.last_error = str(exc)
            self._reset_candidate()
            return False

        self.last_snapshot = snapshot
        if not self._content_changed_since_baseline(snapshot):
            self.last_error = "Target plan has not changed since the task started."
            self._reset_candidate()
            return False

        now = self._clock()
        if snapshot != self._candidate_snapshot:
            self._candidate_snapshot = snapshot
            self._candidate_first_seen_at = now
            self.last_error = "Target plan is waiting for a stable second observation."
            self.cases = []
            return False

        first_seen_at = self._candidate_first_seen_at
        if first_seen_at is None or now - first_seen_at < self.stable_interval:
            self.last_error = "Target plan is waiting for a stable second observation."
            return False

        try:
            cases = self._validate_cases()
            verified_snapshot = self._capture_snapshot()
        except (OSError, UnicodeError, ValueError) as exc:
            self.last_error = str(exc)
            self.cases = []
            return False

        if verified_snapshot != snapshot:
            self._candidate_snapshot = verified_snapshot
            self._candidate_first_seen_at = now
            self.last_snapshot = verified_snapshot
            self.last_error = "Target plan changed during validation."
            self.cases = []
            return False

        self.cases = cases
        self.last_error = ""
        self._completed_snapshot = snapshot
        return True

    __call__ = check


__all__ = ["PlanCompletionProbe", "PlanFileSnapshot"]
