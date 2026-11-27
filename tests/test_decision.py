import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from au_ree_v3.decision import RankChange, average_ranks, leave_one_out, rank_panel
from au_ree_v3.migration.verify_decision import verify_leave_one_out, verify_point_mcda


class PointDecisionTest(unittest.TestCase):
    def test_fixed_ideal_methods_and_exact_tie_groups(self):
        matrix = {
            "alpha": {"inventory": 1.0, "grade": 0.0},
            "beta": {"inventory": 0.0, "grade": 1.0},
            "gamma": {"inventory": 0.0, "grade": 0.0},
        }
        results = rank_panel(matrix, {"inventory": 2.0, "grade": 2.0}, 0.2)
        for method, points in results.items():
            with self.subTest(method=method):
                self.assertEqual(points["alpha"].score, points["beta"].score)
                self.assertEqual(points["alpha"].rank, 1.5)
                self.assertEqual(points["beta"].rank, 1.5)
                self.assertEqual(points["gamma"].rank, 3.0)
        self.assertEqual(results["promethee"]["alpha"].score, 0.25)
        self.assertEqual(results["promethee"]["gamma"].score, -0.5)
        self.assertEqual(results["saw"]["alpha"].score, 0.5)
        self.assertEqual(results["topsis"]["alpha"].score, 0.5)
        self.assertEqual(results["topsis"]["gamma"].score, 0.0)

    def test_fixed_anchors_do_not_vary_with_cohort(self):
        original = {"p1": {"criterion": 0.6}, "p2": {"criterion": 0.4}}
        enlarged = {**original, "p3": {"criterion": 1.0}}
        weights = {"criterion": 1.0}
        first = rank_panel(original, weights, 0.5)
        second = rank_panel(enlarged, weights, 0.5)
        self.assertEqual(first["saw"]["p1"].score, second["saw"]["p1"].score)
        self.assertEqual(first["topsis"]["p1"].score, second["topsis"]["p1"].score)
        self.assertNotEqual(first["promethee"]["p1"].score, second["promethee"]["p1"].score)

    def test_rank_does_not_invent_ties_for_nearby_scores(self):
        self.assertEqual(average_ranks((0.5, 0.5000000000000001, 0.5)), (2.5, 1.0, 2.5))

    def test_malformed_inputs_are_rejected(self):
        valid = {"a": {"criterion": 0.9}, "b": {"criterion": 0.1}}
        for matrix, weights, threshold in (
            ({"a": {"criterion": 0.9}}, {"criterion": 1.0}, 0.1),
            (valid, {"criterion": 1.0}, 0.0),
            (valid, {"criterion": 0.0}, 0.1),
            (valid, {"criterion": -1.0}, 0.1),
            (valid, {"unrelated": 2.0}, 0.1),
            ({"a": {"criterion": float("nan")}, **{"b": valid["b"]}}, {"criterion": 1.0}, 0.1),
        ):
            with self.subTest(matrix=matrix, weights=weights, threshold=threshold):
                with self.assertRaises(ValueError):
                    rank_panel(matrix, weights, threshold)

    def test_absent_frozen_reference_reports_blocked(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)
            report = verify_point_mcda(path / "missing-v2", path / "report")
            self.assertEqual(report["status"], "BLOCKED")
            self.assertEqual(report["oracle_status"], "NOT_RUN")
            self.assertEqual(report["compared_ranking_rows"], 0)
            self.assertTrue((path / "report" / "point_mcda_verification.json").is_file())

    def test_leave_out_recomputes_tied_ranks_and_baseline_changes(self):
        matrix = {
            "alpha": {"inventory": 1.0, "grade": 0.0, "aux": 0.0},
            "beta": {"inventory": 0.0, "grade": 1.0, "aux": 0.0},
            "gamma": {"inventory": 0.0, "grade": 0.0, "aux": 0.0},
        }
        domains = {"inventory": "stock", "grade": "quality", "aux": "stock"}
        weights = {"inventory": 2.0, "grade": 1.0, "aux": 0.0}
        results = leave_one_out(matrix, weights, domains, 0.2)
        expected = {
            "alpha": RankChange(2.5, 1.5),
            "beta": RankChange(1.0, -1.0),
            "gamma": RankChange(2.5, -0.5),
        }
        self.assertEqual(results[("criterion", "inventory")], expected)
        self.assertEqual(results[("domain", "stock")], expected)
        self.assertEqual(results[("criterion", "aux")]["alpha"], RankChange(1.0, 0.0))

    def test_leave_out_skips_removals_with_no_positive_weight(self):
        matrix = {
            "alpha": {"inventory": 1.0, "grade": 0.0},
            "beta": {"inventory": 0.0, "grade": 1.0},
        }
        weights = {"inventory": 1.0, "grade": 0.0}
        domains = {"inventory": "stock", "grade": "stock"}
        results = leave_one_out(matrix, weights, domains, 0.2)
        self.assertEqual(set(results), {("criterion", "grade")})
        with self.assertRaisesRegex(ValueError, "declared domain"):
            leave_one_out(matrix, weights, {"inventory": "stock"}, 0.2)

    def test_leave_out_reports_absent_frozen_reference(self):
        with TemporaryDirectory() as folder:
            path = Path(folder)
            report = verify_leave_one_out(path / "missing-v2", path / "report")
            self.assertEqual(report["status"], "BLOCKED")
            self.assertEqual(report["oracle_status"], "NOT_RUN")
            self.assertEqual(report["compared_ranking_rows"], 0)
            self.assertTrue((path / "report" / "leave_out_verification.json").is_file())
