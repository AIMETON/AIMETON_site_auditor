import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


projection_module = _load(
    "validate_runner_contract_projection",
    ROOT / "scripts" / "validate_runner_contract_projection.py",
)
sys.modules["validate_runner_contract_projection"] = projection_module
verifier = _load("verify_runner_contract_runtime", ROOT / "scripts" / "verify_runner_contract_runtime.py")


class RunnerProjectionTests(unittest.TestCase):
    def projection_and_digest(self):
        projection = projection_module.load_projection()
        return projection, projection["generation"]["canonical_projection_sha256"]

    def test_generated_projection_has_exact_canonical_provenance(self):
        projection, digest = self.projection_and_digest()
        generation = projection["generation"]
        self.assertEqual(generation["infrastructure_repository"], "Dimar4713/aimeton-infrastructure")
        self.assertEqual(generation["infrastructure_source_sha"], "b43a43c593df61eadc8e74151dc1ad68b4d0fc8e")
        self.assertEqual(len(digest), 64)
        self.assertEqual(
            {source["path"] for source in generation["sources"]},
            {
                "ops/main-server/runners.json",
                "ops/burst-runner/site-auditor-runners.json",
                "ops/burst-runner/repository-pools.json",
            },
        )

    def test_burst_acceptance_allows_each_projected_identity(self):
        _, digest = self.projection_and_digest()
        for runner_name in ("aimeton-auditor-burst-stage-01", "aimeton-auditor-burst-stage-02"):
            with self.subTest(runner_name=runner_name):
                result = verifier.verify_runtime_identity(
                    "site-auditor-stage", runner_name, require_source="burst",
                    expected_projection_sha256=digest,
                    control_plane_projection_path=projection_module.PROJECTION_PATH,
                )
                self.assertEqual(result["runner_source"], "burst")
                self.assertTrue(result["identity_membership_verified"])
                self.assertFalse(result["runtime_labels_verified"])
                self.assertEqual(result["selector_authority"], "github-scheduler")
                self.assertTrue(result["projection_integrity_verified"])
                self.assertTrue(result["canonical_projection_match_verified"])

    def test_missing_or_stale_expected_digest_fails_closed(self):
        projection, _ = self.projection_and_digest()
        for proof in (None, "0" * 64):
            with self.subTest(proof=proof), self.assertRaises(projection_module.ProjectionError):
                projection_module.validate_projection_integrity(projection, proof)

    def test_burst_acceptance_rejects_persistent_and_unknown_identity(self):
        _, digest = self.projection_and_digest()
        for runner_name in ("aimeton-site-auditor-stage", "unregistered-runner"):
            with self.subTest(runner_name=runner_name), self.assertRaises(projection_module.ProjectionError):
                verifier.verify_runtime_identity(
                    "site-auditor-stage", runner_name, require_source="burst",
                    expected_projection_sha256=digest,
                    control_plane_projection_path=projection_module.PROJECTION_PATH,
                )

    def test_explicit_empty_runtime_labels_fail_closed(self):
        _, digest = self.projection_and_digest()
        with self.assertRaisesRegex(projection_module.ProjectionError, "runtime labels"):
            verifier.verify_runtime_identity(
                "site-auditor-stage",
                "aimeton-auditor-burst-stage-01",
                actual_labels=[],
                require_source="burst",
                expected_projection_sha256=digest,
                control_plane_projection_path=projection_module.PROJECTION_PATH,
            )

    def test_missing_or_stale_control_plane_projection_fails_closed(self):
        projection, _ = self.projection_and_digest()
        with self.assertRaises(projection_module.ProjectionError):
            projection_module.validate_control_plane_projection(projection, None)
        mutated = copy.deepcopy(projection)
        mutated["generation"]["infrastructure_source_sha"] = "0" * 40
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "control.json"
            path.write_text(json.dumps(mutated))
            with self.assertRaises(projection_module.ProjectionError):
                projection_module.validate_control_plane_projection(projection, path)

    def test_payload_mutation_fails_local_digest_validation(self):
        projection, _ = self.projection_and_digest()
        mutated = copy.deepcopy(projection)
        mutated["canonical_payload"]["contract"]["eligible_runners"][1]["runner_name"] = "drift"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "projection.json"
            path.write_text(json.dumps(mutated))
            with self.assertRaisesRegex(projection_module.ProjectionError, "digest"):
                projection_module.load_projection(path)

    def test_projection_has_no_independent_inventory_or_resolver(self):
        self.assertFalse((ROOT / "ops" / "runner-inventory.json").exists())
        self.assertFalse((ROOT / "scripts" / "resolve_runner_contract.py").exists())


if __name__ == "__main__":
    unittest.main()
