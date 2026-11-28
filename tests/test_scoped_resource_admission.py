"""Exercise the independent Census-only Mineral Resource policy and R1 boundary."""

import json
from types import SimpleNamespace as Item
import unittest

from au_ree_v3.comparability import TOTAL_REO_15
from au_ree_v3.fresh_observations import _native_resource_verdict
from au_ree_v3.observations import CATEGORY
from au_ree_v3.research_inputs import CENSUS_RESOURCE_USE


_NATIVE_PROOF = {key: "original anchor" for key in ("value_anchor", "header_anchor", "unit_anchor", "caption_anchors")}


class ScopedResourceAdmissionTests(unittest.TestCase):
    @staticmethod
    def statement():
        measurements = tuple(Item(
            raw=Item(variable=field, material_basis="WHOLE_ORE", chemical_basis="TREO",
                     denominator_basis=denominator, original_payload=json.dumps({"native_table_binding": _NATIVE_PROOF})),
            anchor=Item(page=7, table="Table 2", row="Total", column=heading),
            validation=Item(outcome="VALIDATED"),
            candidate=Item(value=value, unit=unit),
        ) for field, heading, value, unit, denominator in (
            ("ore_mt", "Tonnes", "100", "Mt", None),
            ("grade", "TREO", "330", "ppm", "WHOLE_ORE"),
        ))
        return Item(
            research_use=CENSUS_RESOURCE_USE, measurements=measurements,
            decision=Item(status="ELIGIBLE", reason="EXACT_EDITION_PUBLIC_BY_CUTOFF"),
            statement=Item(primary=True, resource_class="MINERAL_RESOURCE", reporting_standard="JORC 2012",
                           category=CATEGORY, cutoff="300", cutoff_unit="ppm", cutoff_analyte="TREO",
                           treo_members=TOTAL_REO_15),
        )

    def test_authorizes_same_row_pair_only_for_census(self):
        report = self.statement()
        self.assertEqual(_native_resource_verdict(report),
                         ("ADMISSIBLE", "EXACT_DIRECT_CENSUS_RESOURCE_OBSERVATION"))
        report.research_use = "MCDA_R1/S1"
        self.assertEqual(_native_resource_verdict(report),
                         ("REJECTED", "EXACT_REPORTED_RESOURCE_BUNDLE_REQUIRED"))

    def test_incompatible_scope_chemistry_temporal_and_values_are_refused(self):
        checks = (
            ("statement", "resource_class", "ORE_RESERVE", "PRIMARY_JORC_MINERAL_RESOURCE_REQUIRED"),
            ("statement", "resource_class", "EXPLORATION_TARGET", "PRIMARY_JORC_MINERAL_RESOURCE_REQUIRED"),
            ("statement", "primary", False, "PRIMARY_JORC_MINERAL_RESOURCE_REQUIRED"),
            ("statement", "category", "INFERRED_TOTAL", "APPROVED_PROJECT_RESOURCE_CATEGORY_REQUIRED"),
            ("statement", "cutoff_analyte", "P2O5", "EXACT_PRIMARY_TREO_CUTOFF_REQUIRED"),
            ("statement", "cutoff_analyte", "TREO-CeO2", "EXACT_PRIMARY_TREO_CUTOFF_REQUIRED"),
            ("statement", "cutoff_analyte", "TREO+Sc2O3", "EXACT_PRIMARY_TREO_CUTOFF_REQUIRED"),
            ("statement", "cutoff_unit", "US$/t", "EXACT_PRIMARY_TREO_CUTOFF_REQUIRED"),
            ("statement", "treo_members", TOTAL_REO_15 + ("Er2O3",), "EXACT_TREO_MEMBER_SET_REQUIRED"),
            ("statement", "treo_members", (*TOTAL_REO_15[:-1], "Sc2O3"), "EXACT_TREO_MEMBER_SET_REQUIRED"),
            ("decision", "status", "CANDIDATE", "EXACT_EDITION_PUBLIC_BY_CUTOFF"),
            ("anchor", "page", 8, "EXACT_SAME_ROW_AND_DISTINCT_FIELDS_REQUIRED"),
            ("anchor", "row", "Subtotal", "EXACT_SAME_ROW_AND_DISTINCT_FIELDS_REQUIRED"),
            ("anchor", "column", "Tonnes", "EXACT_SAME_ROW_AND_DISTINCT_FIELDS_REQUIRED"),
            ("raw", "denominator_basis", "HEAVY_MINERAL_CONCENTRATE", "WHOLE_ORE_TREO_QUANTITY_REQUIRED"),
            ("raw", "chemical_basis", "Dy", "WHOLE_ORE_TREO_QUANTITY_REQUIRED"),
            ("candidate", "value", "1000001", "CENSUS_RESOURCE_QUANTITY_OUT_OF_RANGE"),
            ("validation", "outcome", "SCIENTIFIC_CONFLICT", "CONFLICTING_NATIVE_MEASUREMENT"),
        )
        for member, attr, incorrect, reason in checks:
            with self.subTest(member=member, attribute=attr, incorrect=incorrect):
                report = self.statement()
                parent = report if member in {"statement", "decision"} else report.measurements[1]
                setattr(getattr(parent, member), attr, incorrect)
                verdict, observed = _native_resource_verdict(report)
                self.assertNotEqual(verdict, "ADMISSIBLE")
                self.assertEqual(observed, reason)


if __name__ == "__main__":
    unittest.main()
