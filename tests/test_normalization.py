"""Independent arithmetic and forbidden chemistry transformations."""

from dataclasses import replace
from decimal import Decimal, localcontext
from fractions import Fraction
import json
from types import SimpleNamespace
import unittest

from au_ree_v3.comparability import _stated_treo_members, _treo_definitions, contained_mass_shadow
from au_ree_v3.normalization import normalize_measurement
from au_ree_v3.semantics import SemanticContext

# Hand-transcribed independent specimen definition, in publisher order.
OXIDES = (
    "La2O3", "CeO2", "Pr6O11", "Nd2O3", "Sm2O3", "Eu2O3", "Gd2O3", "Tb4O7",
    "Dy2O3", "Ho2O3", "Er2O3", "Tm2O3", "Yb2O3", "Lu2O3", "Y2O3",
)
SOURCE = "a" * 64
GRADE = SemanticContext(
    quantity_kind="GRADE", project_id="project", scope_id="scope", deposit_id="deposit",
    statement_id="statement", resource_class="MINERAL_RESOURCE",
    jorc_categories=("MEASURED", "INDICATED", "INFERRED"), cutoff_grade="500", cutoff_unit="ppm",
    cutoff_analyte="TREO", effective_date="2024-04-30", includes_stockpiles=False,
    material="WHOLE_ORE", denominator_basis="WHOLE_ORE", treo_members=OXIDES,
    temporal_status="ELIGIBLE", temporal_decision_id="b" * 64, research_cutoff="2026-11-28",
)
ORE = replace(GRADE, quantity_kind="ORE_MASS", denominator_basis=None, treo_members=())
CONTAINED = replace(GRADE, quantity_kind="CONTAINED_OXIDE_MASS", denominator_basis=None)
ELEMENT = replace(GRADE, quantity_kind="OXIDE_COMPOSITION", chemical_species=("Nd",), treo_members=())


def normalize(token, unit, target, context=GRADE, *, code="grade", source=SOURCE, **kwargs):
    return normalize_measurement(
        token, unit, target, source_context=context, observation_id=code,
        source_sha256=source, evidence_anchor_id=f"anchor-{code}", **kwargs,
    )


def ore(context=ORE, **kwargs):
    return normalize("100.0", "Mt", "Mt", context, code="ore", **kwargs)


def grade(context=GRADE, **kwargs):
    return normalize("1,500", "ppm", "%", context, **kwargs)


def reported(token, context=CONTAINED):
    return normalize(token, "kt", "kt TREO", context, code="reported")


class DecimalNormalizationTests(unittest.TestCase):
    def test_native_ppm_and_inverse_preserve_printed_precision(self):
        result = grade()
        self.assertEqual((result.status, result.value, result.rounding_interval),
                         ("NORMALIZED", "0.1500", ("0.14995", "0.15005")))
        self.assertEqual((result.original_token, result.original_unit, result.target_unit),
                         ("1,500", "ppm", "%"))
        self.assertEqual(result.transform["factor"], "0.0001")
        inverse = normalize("0.1500", "%", "ppm")
        self.assertEqual((inverse.value, inverse.rounding_interval), ("1500", ("1499.5", "1500.5")))
        self.assertTrue(result.verify())
        self.assertEqual(result.to_dict(), grade().to_dict())

    def test_mass_t_kt_and_mt_preserve_distinct_reported_precision(self):
        variants = (
            ("1,005,300", "t", "1.005300", ("1.0052995", "1.0053005")),
            ("1005.3", "kt", "1.0053", ("1.00525", "1.00535")),
            ("1.0053", "million tonnes", "1.0053", ("1.00525", "1.00535")),
        )
        for token, source, value, interval in variants:
            with self.subTest(source=source):
                actual = normalize(token, source, "Mt", ORE)
                self.assertEqual((actual.value, actual.rounding_interval), (value, interval))

    def test_scientific_notation_and_physical_mass_fraction_bounds(self):
        actual = normalize("1.50e3", "ppm", "%")
        self.assertEqual((actual.value, actual.rounding_interval), ("0.150", ("0.1495", "0.1505")))
        physical = normalize("100", "%", "ppm")
        self.assertEqual(physical.rounding_interval, ("995000", "1000000"))
        self.assertEqual(normalize("100.01", "%", "ppm").status, "SCIENTIFIC_CONFLICT")
        self.assertEqual(normalize("1,50", "ppm", "%").reason, "UNSUPPORTED_REPORTED_NUMBER_TOKEN")
        self.assertEqual(normalize("0.15", "%", "kt").status, "NOT_COMPARABLE")
        self.assertEqual(normalize("100", "t", "kt TREO", ORE).reason,
                         "UNIT_TREO_LABEL_REQUIRES_DEFINED_TREO_MASS")
        oxide_mass = replace(CONTAINED, chemical_species=("Nd2O3",), treo_members=())
        self.assertEqual(normalize("100", "t", "kt TREO", oxide_mass).status, "NOT_COMPARABLE")
        self.assertEqual(normalize("100", "t", "kt", oxide_mass).status, "NORMALIZED")

    def test_same_units_do_not_establish_same_material_or_measurement(self):
        for change in (
            {"material": "HEAVY_MINERAL", "denominator_basis": "HEAVY_MINERAL"},
            {"material": "CONCENTRATE", "denominator_basis": "CONCENTRATE"},
            {"quantity_kind": "OXIDE_COMPOSITION"},
            {"statement_id": "different"},
        ):
            with self.subTest(change=change):
                actual = grade(target_context=replace(GRADE, **change))
                self.assertEqual(actual.status, "NOT_COMPARABLE")
                self.assertIsNone(actual.value)

    def test_one_mass_basis_and_explicit_exact_oxide_members_required(self):
        for changed_members in (
            tuple(x for x in OXIDES if x != "Y2O3"),
            (*OXIDES, "Sc2O3"),
            tuple(x for x in OXIDES if x != "CeO2"),
        ):
            with self.subTest(members=changed_members):
                self.assertEqual(grade(target_context=replace(GRADE, treo_members=changed_members)).status,
                                 "NOT_COMPARABLE")
        self.assertEqual(grade(replace(GRADE, treo_members=())).status, "INSUFFICIENT_CONTEXT")
        for unknown in (None, "UNKNOWN"):
            self.assertEqual(grade(replace(GRADE, denominator_basis=unknown)).reason, "MASS_DENOMINATOR_UNKNOWN")
        unresolved_group = replace(ELEMENT, chemical_species=())
        defined_group = replace(ELEMENT, chemical_species=("Nd2O3", "Pr6O11"))
        self.assertEqual(normalize("82", "ppm", "%", unresolved_group, target_context=defined_group).status,
                         "INSUFFICIENT_CONTEXT")

    def test_measured_zero_and_absences_remain_distinct(self):
        zero = normalize("0", "ppm", "%")
        self.assertEqual((zero.status, zero.value, Decimal(zero.rounding_interval[0])),
                         ("NORMALIZED", "0.0000", Decimal(0)))
        self.assertEqual(zero.transform["value_state"], "MEASURED_ZERO")
        for state in ("NOT_DISCLOSED", "NOT_LOCATED", "NOT_APPLICABLE"):
            with self.subTest(state=state):
                result = normalize(None, "ppm", "%", replace(GRADE, value_state=state))
                self.assertEqual(result.original_context["value_state"], state)
                self.assertIsNone(result.value)
        self.assertIsNone(normalize(None, "ppm", "%").value)

    def test_missing_or_conflicting_temporal_evidence_fails_closed(self):
        no_pin = normalize("1", "ppm", "%", replace(GRADE, temporal_decision_id=None))
        conflict = normalize("1", "ppm", "%", replace(GRADE, temporal_status="CONFLICT"))
        self.assertEqual(no_pin.status, "INSUFFICIENT_CONTEXT")
        self.assertEqual(conflict.status, "SCIENTIFIC_CONFLICT")
        self.assertEqual(grade(source="wrong-sha").reason, "ORIGINAL_EVIDENCE_NOT_BOUND")


class OriginalOxideDefinitionTests(unittest.TestCase):
    @staticmethod
    def members(label, *quotes):
        item = SimpleNamespace(raw=SimpleNamespace(original_payload=json.dumps({
            "reported_chemical_label": label,
            "statement_context": [{"physical_page": 3, "quote": quote} for quote in quotes],
        })))
        return _stated_treo_members(_treo_definitions(item, extended=True))

    def test_qualified_measurement_labels_follow_exact_oxide_declarations(self):
        base = "TREO = Total Rare Earth Oxides (CeO2, Nd2O3, Pr6O11)"
        y = "TREO + Y2O3 = Total Rare Earth Oxides (CeO2, Nd2O3, Pr6O11, Y2O3)"
        sc = "TREO+Sc2O3 = Total Rare Earth Oxides (CeO2, Nd2O3, Pr6O11, Sc2O3)"
        no_ce = "TREO − CeO2 = Total Rare Earth Oxides (Nd2O3, Pr6O11)"
        self.assertEqual(self.members(None, base, y), ("CeO2", "Nd2O3", "Pr6O11"))
        self.assertEqual(self.members("In-situ TREO + Y2O3", base, y), ("CeO2", "Nd2O3", "Pr6O11", "Y2O3"))
        self.assertEqual(self.members("TREO + Sc2O3", sc), ("CeO2", "Nd2O3", "Pr6O11", "Sc2O3"))
        self.assertEqual(self.members("TREO - CeO2", no_ce), ("Nd2O3", "Pr6O11"))
        self.assertEqual(self.members("TREO including Y2O3", base), ())

    def test_unknown_or_repeated_members_and_conflicting_lists_remain_unbound(self):
        self.assertEqual(self.members("In-situ TREO + Y2O3", "TREO = Total Rare Earth Oxides (Nd2O3, Y2O3)"), ())
        self.assertEqual(self.members("TREO + Y2O3", "TREO+Y2O3 = Total Rare Earth Oxides (Y2O3, Y2O3)"), ())
        self.assertEqual(self.members("TREO - CeO2", "TREO - CeO2 = Total Rare Earth Oxides (CeO2, Nd2O3)"), ())
        self.assertEqual(self.members(None, "TREO = Total Rare Earth Oxides (Nd2O3, NdPr)"), ())
        self.assertEqual(self.members(None, "TREO = Total Rare Earth Oxides (Nd2O3)",
                                      "TREO = Total Rare Earth Oxides (Nd2O3, Y2O3)"), ())


class StoichiometryTests(unittest.TestCase):
    def test_nd_oxide_absolute_ratio_arithmetic_and_documented_uncertainty(self):
        oxide = replace(ELEMENT, chemical_species=("Nd2O3",))
        tagged = normalize("100.00", "ppm", "ppm", ELEMENT, target_context=oxide,
                           target_species_evidence_anchor_id="explicit-oxide-label")
        self.assertEqual(tagged.status, "NORMALIZED")
        # Independent rational atoms from the published table; no calls into the
        # production molar calculation or Decimal interval helper.
        nd, oxygen = Fraction(14424, 100), Fraction(15999, 1000)
        ideal = 100 * (1 + 3 * oxygen / (2 * nd))
        independent_lower = Fraction(99995, 1000) * (1 + 3 * Fraction(15998, 1000) / (2 * Fraction(14425, 100)))
        independent_upper = Fraction(100005, 1000) * (1 + 3 * Fraction(16000, 1000) / (2 * Fraction(14423, 100)))
        self.assertLess(abs(Fraction(tagged.value) - ideal), Fraction(1, 10**45))
        low, high = map(Fraction, tagged.rounding_interval)
        self.assertLessEqual(low, independent_lower)
        self.assertGreaterEqual(high, independent_upper)
        self.assertLess(independent_lower - low, Fraction(1, 10**45))
        self.assertLess(high - independent_upper, Fraction(1, 10**45))
        self.assertEqual(tagged.transform["atomic_weights_version"],
                         "ciaaw-abridged-standard-atomic-weights-2024")
        self.assertEqual(tagged.transform["atomic_weights"]["Nd"], {"value": "144.24", "uncertainty": "0.01"})
        self.assertEqual(tagged.transform["target_species_evidence_anchor_id"], "explicit-oxide-label")
        inverse = normalize("100.00", "ppm", "ppm", oxide, target_context=ELEMENT,
                            target_species_evidence_anchor_id="explicit-element-label")
        self.assertLess(abs(Fraction(inverse.value) - Fraction(10000, 1) / ideal), Fraction(1, 10**45))

    def test_unverified_oxide_and_unapproved_species_conversion_are_refused(self):
        oxide = replace(ELEMENT, chemical_species=("Nd2O3",))
        unverified = normalize("100.00", "ppm", "ppm", ELEMENT, target_context=oxide)
        self.assertEqual(unverified.reason, "TRANSFORM_TARGET_SPECIES_EVIDENCE_REQUIRED")
        wrong = normalize("100.00", "ppm", "ppm", ELEMENT,
                          target_context=replace(oxide, chemical_species=("Pr6O11",)),
                          target_species_evidence_anchor_id="unrelated-oxide-label")
        self.assertEqual(wrong.status, "NOT_COMPARABLE")


class ContainedMassShadowTests(unittest.TestCase):
    def test_arithmetic_precision_use_contract_and_derived_only_identity(self):
        actual = contained_mass_shadow(ore(), grade())
        self.assertEqual((actual.status, Decimal(actual.value), actual.layer),
                         ("COMPARABLE_DERIVED", Decimal(150), "SHADOW"))
        self.assertEqual(tuple(map(Decimal, actual.derivation["rounding_interval_kt"])),
                         (Decimal("149.875025"), Decimal("150.125025")))
        self.assertEqual(actual.input_observation_ids, ("ore", "grade"))
        self.assertEqual(actual.evidence_anchor_ids, ("anchor-ore", "anchor-grade"))
        self.assertEqual(actual.derivation["result_role"], "DERIVED")
        self.assertFalse(actual.derivation["formal_admission"])
        self.assertEqual(actual.context["source_sha256"], SOURCE)
        self.assertEqual(actual.to_dict(), contained_mass_shadow(ore(), grade()).to_dict())

    def test_reported_printed_interval_compared_without_overwriting_reported_mass(self):
        actual = contained_mass_shadow(ore(), grade(), reported=reported("150.13"))
        self.assertEqual(actual.status, "COMPARABLE_DERIVED")
        self.assertEqual(actual.derivation["reported_agreement"], "ROUNDING_INTERVALS_OVERLAP")
        original = actual.derivation["input_transforms"][2]
        self.assertEqual(original["original_token"], "150.13")
        mismatch = contained_mass_shadow(ore(), grade(), reported=reported("150.14"))
        self.assertEqual((mismatch.status, mismatch.reason, mismatch.value),
                         ("SCIENTIFIC_CONFLICT", "REPORTED_CONTAINED_MASS_CONTRADICTS_DERIVATION", None))

    def test_wrong_scope_or_basis_does_not_derive(self):
        variants = (
            ({"cutoff_grade": "400"}, "RESOURCE_CATEGORY_OR_CUTOFF_MISMATCH"),
            ({"effective_date": "2025-04-30"}, "RESOURCE_STATEMENT_SCOPE_OR_SOURCE_MISMATCH"),
            ({"deposit_id": "other"}, "RESOURCE_STATEMENT_SCOPE_OR_SOURCE_MISMATCH"),
            ({"combined_deposit_ids": ("deposit-a", "deposit-b")}, "RESOURCE_STATEMENT_SCOPE_OR_SOURCE_MISMATCH"),
            ({"observation_role": "FORECAST"}, "HISTORICAL_MEASUREMENT_REQUIRED"),
            ({"jorc_categories": ("INDICATED",)}, "RESOURCE_CATEGORY_OR_CUTOFF_MISMATCH"),
            ({"resource_class": "ORE_RESERVE", "jorc_categories": ()}, "JORC_MINERAL_RESOURCE_REQUIRED"),
            ({"resource_class": "EXPLORATION_TARGET", "jorc_categories": ()}, "JORC_MINERAL_RESOURCE_REQUIRED"),
            ({"material": "HEAVY_MINERAL", "denominator_basis": "HEAVY_MINERAL"},
             "RESOURCE_WHOLE_ORE_MATERIAL_AND_DENOMINATOR_REQUIRED"),
        )
        for context, reason in variants:
            with self.subTest(context=context):
                result = contained_mass_shadow(ore(), grade(replace(GRADE, **context)))
                self.assertEqual((result.status, result.reason, result.value), ("NOT_COMPARABLE", reason, None))
        self.assertEqual(contained_mass_shadow(ore(), grade(source="c" * 64)).reason,
                         "RESOURCE_STATEMENT_SCOPE_OR_SOURCE_MISMATCH")
        self.assertEqual(contained_mass_shadow(ore(), grade(replace(GRADE, treo_members=()))).status,
                         "INSUFFICIENT_CONTEXT")
        incompatible = reported("150.0", replace(CONTAINED, treo_members=(*OXIDES, "Sc2O3")))
        self.assertEqual(contained_mass_shadow(ore(), grade(), reported=incompatible).reason,
                         "TREO_MEMBER_DEFINITIONS_DISAGREE")

    def test_damaged_transform_receipt_does_not_derive(self):
        tampered = replace(grade(), value="0.5")
        self.assertEqual(contained_mass_shadow(ore(), tampered).reason, "INPUT_TRANSFORM_RECEIPT_DIGEST_MISMATCH")

    def test_decimal_context_does_not_change_result(self):
        reference = contained_mass_shadow(ore(), grade()).to_dict()
        with localcontext() as precision:
            precision.prec = 5
            self.assertEqual(contained_mass_shadow(ore(), grade()).to_dict(), reference)


if __name__ == "__main__":
    unittest.main()
