from __future__ import annotations

import unittest
from pathlib import Path

from replication_r1.sealing import assert_current_seal


class ReplicationArchiveGuardTest(unittest.TestCase):
    def test_executed_negative_replication_is_validated_as_an_immutable_archive(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]

        assert_current_seal(repository_root)

    def test_runner_contains_an_explicit_reexecution_barrier(self) -> None:
        repository_root = Path(__file__).resolve().parents[3]
        source = (repository_root / "experiments" / "aporia-lacuna" / "replication_r1" / "runner.py").read_text(encoding="utf-8")

        self.assertIn('raise RuntimeError("replication_confirmatory_already_executed")', source)


if __name__ == "__main__":
    unittest.main()
