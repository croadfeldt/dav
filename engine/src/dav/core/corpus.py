"""
DCM self-test corpus loader.

Loads use cases from the corpus git repo (currently
`croadfeldt/dav-content-corpus`, migrates to `dcm-project/...`
once validated). Corpus layout:

    corpus_root/
    ├── schema/
    │   └── use_case.schema.json
    ├── use_cases/
    │   ├── compute/
    │   │   ├── uc-foo.yaml
    │   │   └── ...
    │   ├── networking/
    │   └── ...
    └── baselines/
        └── dcm-<version>/
            └── <use_case_uuid>.baseline.yaml

Responsibilities:
  - Walk the use_cases/ tree, parse YAML, return UseCase objects
  - Filter by tag / domain / profile
  - Load/save baselines for regression diffing
  - Validate each case against the canonical schema on load
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator
import yaml

from .use_case_schema import UseCase, Analysis

log = logging.getLogger(__name__)

class CorpusError(Exception):
    pass

class Corpus:
    """A loaded (or mounted) corpus directory."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        if not self.root.is_dir():
            raise CorpusError(f"corpus root does not exist: {self.root}")
        self.use_cases_dir = self.root / "use_cases"
        self.baselines_dir = self.root / "baselines"

    def load_all(self) -> list[UseCase]:
        """Load every use case, skipping entries that fail validation."""
        cases = []
        for uc_path in self._iter_use_case_files():
            try:
                cases.append(self._load_one(uc_path))
            except Exception as e:
                log.warning("skipping %s: %s", uc_path, e)
        return cases

    def iter_all(self) -> Iterator[UseCase]:
        """Stream use cases lazily."""
        for uc_path in self._iter_use_case_files():
            try:
                yield self._load_one(uc_path)
            except Exception as e:
                log.warning("skipping %s: %s", uc_path, e)

    def load_by_uuid(self, use_case_uuid: str) -> UseCase | None:
        for uc in self.iter_all():
            if uc.uuid == use_case_uuid:
                return uc
        return None

    def filter_by_tags(self, tags: list[str]) -> list[UseCase]:
        """Return cases matching ALL provided tags."""
        tag_set = set(tags)
        return [uc for uc in self.iter_all() if tag_set.issubset(set(uc.tags))]

    def filter_by_domain(self, domain: str) -> list[UseCase]:
        """Return cases from a specific domain subdirectory."""
        domain_dir = self.use_cases_dir / domain
        if not domain_dir.is_dir():
            return []
        results = []
        for path in sorted(domain_dir.glob("*.yaml")):
            try:
                results.append(self._load_one(path))
            except Exception as e:
                log.warning("skipping %s: %s", path, e)
        return results

    def filter_by_profile(self, profile: str) -> list[UseCase]:
        return [uc for uc in self.iter_all() if uc.scenario.profile == profile]

    def write_use_case(self, use_case: UseCase, domain: str) -> Path:
        """Write a new use case into the corpus tree."""
        errors = use_case.validate()
        if errors:
            raise CorpusError(f"use case failed validation: {errors}")
        domain_dir = self.use_cases_dir / domain
        domain_dir.mkdir(parents=True, exist_ok=True)
        # Filename convention: <handle's trailing segment>.yaml
        filename = f"{use_case.handle.split('/')[-1]}.yaml"
        path = domain_dir / filename
        with path.open("w") as f:
            yaml.safe_dump(use_case.to_dict(), f, sort_keys=False, default_flow_style=False)
        log.info("wrote use case %s to %s", use_case.uuid, path)
        return path

    def load_baseline(self, use_case_uuid: str, dcm_version: str) -> Analysis | None:
        """Load a use case's baseline analysis for a given DCM spec version."""
        baseline_path = self.baselines_dir / f"dcm-{dcm_version}" / f"{use_case_uuid}.baseline.yaml"
        if not baseline_path.exists():
            return None
        with baseline_path.open() as f:
            data = yaml.safe_load(f)
        return Analysis.from_dict(data)

    def save_baseline(self, use_case_uuid: str, dcm_version: str,
                      analysis: Analysis) -> Path:
        """Persist a baseline analysis."""
        baseline_dir = self.baselines_dir / f"dcm-{dcm_version}"
        baseline_dir.mkdir(parents=True, exist_ok=True)
        baseline_path = baseline_dir / f"{use_case_uuid}.baseline.yaml"
        with baseline_path.open("w") as f:
            yaml.safe_dump(analysis.to_dict(), f, sort_keys=False, default_flow_style=False)
        log.info("saved baseline for %s at %s", use_case_uuid, baseline_path)
        return baseline_path

    def _iter_use_case_files(self) -> Iterator[Path]:
        if not self.use_cases_dir.is_dir():
            return
        for path in sorted(self.use_cases_dir.rglob("*.yaml")):
            yield path

    def _load_one(self, path: Path) -> UseCase:
        with path.open() as f:
            data = yaml.safe_load(f)
        uc = UseCase.from_dict(data)
        errors = uc.validate()
        if errors:
            raise CorpusError(f"{path}: {errors}")
        return uc
