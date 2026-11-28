"""Evidence-bound unit and oxide transforms for shadow scientific evaluation."""

from dataclasses import asdict, dataclass, replace
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, localcontext
from hashlib import sha256
import re
from .contracts import canonical_json
from .semantics import SemanticContext, vocabulary

SCHEMA_VERSION = "normalization-transform-v1"
UNIT_POLICY = "exact-decimal-unit-transforms-v1"
OXIDE_POLICY = "element-oxide-stoichiometry-v1"
ATOMIC_WEIGHTS_VERSION = "ciaaw-abridged-standard-atomic-weights-2024"
ATOMIC_WEIGHTS_SOURCE = "https://www.ciaaw.org/abridged-atomic-weights.htm"
# CIAAW 2024 abridged standard atomic weights and their published uncertainties.
_WEIGHTS = {"O": ("15.999", "0.001"), "Nd": ("144.24", "0.01"), "Pr": ("140.91", "0.01"),
            "Dy": ("162.50", "0.01"), "Tb": ("158.93", "0.01")}
_OXIDES = {"Nd2O3": ("Nd", 2, 3), "Pr6O11": ("Pr", 6, 11), "Dy2O3": ("Dy", 2, 3), "Tb4O7": ("Tb", 4, 7)}
# Powers of ten relative to the reference t or the dimensionless mass fraction.
_UNITS = {
    "t": ("MASS", 0), "kg": ("MASS", -3), "kt": ("MASS", 3), "Mt": ("MASS", 6), "Bt": ("MASS", 9),
    "million tonnes": ("MASS", 6), "kt TREO": ("MASS", 3), "‘000 tonnes": ("MASS", 3),
    "%": ("MASS_FRACTION", -2), "ppm": ("MASS_FRACTION", -6), "kg/t": ("MASS_FRACTION", -3),
}
_MASS_KINDS = {"ORE_MASS", "CONTAINED_OXIDE_MASS"}
_FRACTION_KINDS = {"GRADE", "OXIDE_COMPOSITION"}
KNOWN_OXIDE_SPECIES = frozenset(vocabulary()["oxide_species"])
_TOKEN = re.compile(r"[+]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]{1,3})?")
_SHA256 = re.compile(r"[a-f0-9]{64}")


def _digest(value: dict) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _shift(value: Decimal, power: int) -> Decimal:
    sign, digits, exponent = value.as_tuple()
    return Decimal((sign, digits, exponent + power))


def exact_unit_exponent(source_unit: str, target_unit: str) -> int:
    """Resolve a lossless decimal power for physically equivalent unit dimensions."""
    if source_unit not in _UNITS or target_unit not in _UNITS or _UNITS[source_unit][0] != _UNITS[target_unit][0]:
        raise ValueError("Unit transform is unsupported or dimensionally inconsistent")
    return _UNITS[source_unit][1] - _UNITS[target_unit][1]


def reported_interval(token: str, *, significant_figures: int | None = None) -> tuple[Decimal, Decimal, Decimal]:
    """Return central value and physical endpoints at the last printed digit."""
    if not isinstance(token, str) or not _TOKEN.fullmatch(token):
        raise ValueError("unsupported or non-numeric reported value")
    point = Decimal(token.replace(",", ""))
    exponent = point.as_tuple().exponent - 1
    if significant_figures is not None:
        if type(significant_figures) is not int or not 1 <= significant_figures <= 12:
            raise ValueError("unsupported publisher significant-figures policy")
        if point:
            exponent = max(exponent, point.adjusted() - significant_figures)
    half = Decimal((0, (5,), exponent))
    with localcontext() as context:
        context.prec = len(point.as_tuple().digits) + 3
        return point, max(Decimal(0), point - half), point + half


@dataclass(frozen=True)
class NormalizationResult:
    """One immutable calculation receipt or an explicit scientific rejection."""

    transform_id: str
    status: str
    reason: str
    source_sha256: str
    observation_id: str
    evidence_anchor_id: str
    original_token: str | None
    original_unit: str | None
    target_unit: str
    original_context: dict
    target_context: dict
    context_evidence: tuple[dict, ...]
    value: str | None
    rounding_interval: tuple[str, str] | None
    transform: dict | None
    schema_version: str = SCHEMA_VERSION
    layer: str = "SHADOW"

    def to_dict(self) -> dict:
        return asdict(self)

    def verify(self) -> bool:
        payload = self.to_dict()
        return payload.pop("transform_id") == _digest(payload)


def _stoichiometry(source: str, target: str) -> tuple[str, bool] | None:
    if target in _OXIDES and source == _OXIDES[target][0]:
        return target, False
    if source in _OXIDES and target == _OXIDES[source][0]:
        return source, True
    return None


def _weight_factors(oxide: str, reverse: bool) -> tuple[Decimal, Decimal, Decimal]:
    element, atoms, oxygen_atoms = _OXIDES[oxide]
    weight, uncertainty = map(Decimal, _WEIGHTS[element])
    oxygen, oxygen_uncertainty = map(Decimal, _WEIGHTS["O"])
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN

        def factor(element_weight: Decimal, oxygen_weight: Decimal) -> Decimal:
            elemental = atoms * element_weight
            mass = elemental + oxygen_atoms * oxygen_weight
            return elemental / mass if reverse else mass / elemental

        point = factor(weight, oxygen)
        context.rounding = ROUND_FLOOR
        low = (factor(weight - uncertainty, oxygen + oxygen_uncertainty) if reverse
               else factor(weight + uncertainty, oxygen - oxygen_uncertainty))
        context.rounding = ROUND_CEILING
        high = (factor(weight + uncertainty, oxygen - oxygen_uncertainty) if reverse
                else factor(weight - uncertainty, oxygen + oxygen_uncertainty))
    return point, low, high


def normalize_measurement(
    token: str | None, source_unit: str | None, target_unit: str, *,
    source_context: SemanticContext, target_context: SemanticContext | None = None,
    observation_id: str, source_sha256: str, evidence_anchor_id: str,
    context_evidence: tuple[dict, ...] = (), target_species_evidence_anchor_id: str | None = None,
    reported_significant_figures: int | None = None,
) -> NormalizationResult:
    """Apply only a dimensionally and chemically evidenced measurement transform."""
    source_context = replace(source_context, unit=source_unit) if source_context.unit is None else source_context
    target_context = (replace(source_context, unit=target_unit) if target_context is None else
                      replace(target_context, unit=target_unit) if target_context.unit is None else target_context)
    original = asdict(source_context)
    target = asdict(target_context)
    common = {
        "source_sha256": source_sha256, "observation_id": observation_id,
        "evidence_anchor_id": evidence_anchor_id, "original_token": token,
        "original_unit": source_unit, "target_unit": target_unit,
        "original_context": original, "target_context": target, "context_evidence": context_evidence,
    }

    def result(status: str, reason: str, amount=None, interval=None, transform=None) -> NormalizationResult:
        payload = {"schema_version": SCHEMA_VERSION, "layer": "SHADOW", **common, "status": status,
                   "reason": reason, "value": amount, "rounding_interval": interval, "transform": transform}
        return NormalizationResult(_digest(payload), **payload)

    if not observation_id or not evidence_anchor_id or not _SHA256.fullmatch(source_sha256 or ""):
        return result("INSUFFICIENT_CONTEXT", "ORIGINAL_EVIDENCE_NOT_BOUND")
    value_state = source_context.value_state
    if value_state == "CONFLICT":
        return result("SCIENTIFIC_CONFLICT", "REPORTED_VALUE_STATE_CONFLICT")
    if value_state in {"SOURCE_UNAVAILABLE", "AFTER_CUTOFF", "NOT_APPLICABLE"}:
        return result(value_state, "SOURCE_VALUE_STATE_PRECLUDES_NORMALIZATION")
    if value_state != "PRESENT" or token is None:
        return result("INSUFFICIENT_CONTEXT", "NO_REPORTED_NUMERIC_VALUE")
    if source_context.temporal_status in {"INELIGIBLE", "AFTER_CUTOFF"}:
        return result("AFTER_CUTOFF", "TEMPORAL_DECISION_EXCLUDES_MEASUREMENT")
    if source_context.temporal_status == "CONFLICT":
        return result("SCIENTIFIC_CONFLICT", "TEMPORAL_MEASUREMENT_CONFLICT")
    if source_context.temporal_status != "ELIGIBLE" or not source_context.temporal_decision_id:
        return result("INSUFFICIENT_CONTEXT", "TEMPORAL_ELIGIBILITY_NOT_BOUND")
    if not (source_context.project_id and source_context.scope_id and source_context.material):
        return result("INSUFFICIENT_CONTEXT", "MEASUREMENT_SCOPE_OR_MATERIAL_UNKNOWN")
    if source_context.unit != source_unit or target_context.unit != target_unit:
        return result("NOT_COMPARABLE", "MEASUREMENT_UNIT_CONTEXT_CONFLICT")
    unlike = (key for key in original if key not in {"chemical_species", "unit"} and original[key] != target[key])
    if any(unlike):
        return result("NOT_COMPARABLE", "MEASUREMENT_CONTEXT_CHANGED")
    source_dim, source_power = _UNITS.get(source_unit, (None, 0))
    target_dim, target_power = _UNITS.get(target_unit, (None, 0))
    kind = source_context.quantity_kind
    required = "MASS" if kind in _MASS_KINDS else "MASS_FRACTION" if kind in _FRACTION_KINDS else None
    if source_dim is None or target_dim is None:
        return result("INSUFFICIENT_CONTEXT", "MEASUREMENT_UNIT_UNKNOWN")
    if required is None or source_dim != target_dim or source_dim != required:
        return result("NOT_COMPARABLE", "MEASUREMENT_DIMENSION_OR_QUANTITY_KIND_MISMATCH")
    denominator = (source_context.denominator_basis or "").strip().upper()
    if required == "MASS_FRACTION" and denominator in {"", "UNKNOWN", "NOT_DISCLOSED", "NOT_LOCATED"}:
        return result("INSUFFICIENT_CONTEXT", "MASS_DENOMINATOR_UNKNOWN")
    before, after = source_context.chemical_species, target_context.chemical_species
    members = source_context.treo_members
    if members and not set(members).issubset(KNOWN_OXIDE_SPECIES):
        return result("INSUFFICIENT_CONTEXT", "OXIDE_MEMBER_UNDEFINED")
    if "kt TREO" in {source_unit, target_unit} and (
        kind != "CONTAINED_OXIDE_MASS" or before not in {(), ("TREO",), members}
        or after not in {(), ("TREO",), members}
    ):
        return result("NOT_COMPARABLE", "UNIT_TREO_LABEL_REQUIRES_DEFINED_TREO_MASS")
    known_species = (set(_WEIGHTS) - {"O"}) | KNOWN_OXIDE_SPECIES
    explicit_species = bool(before) and set(before).issubset(known_species)
    if kind != "ORE_MASS" and not members and not explicit_species:
        return result("INSUFFICIENT_CONTEXT", "CHEMICAL_SPECIES_OR_TREO_MEMBERS_UNKNOWN")
    try:
        point, low, high = reported_interval(token, significant_figures=reported_significant_figures)
    except (InvalidOperation, ValueError):
        return result("INSUFFICIENT_CONTEXT", "UNSUPPORTED_REPORTED_NUMBER_TOKEN")
    if required == "MASS_FRACTION":
        if _shift(point, source_power) > 1:
            return result("SCIENTIFIC_CONFLICT", "MASS_FRACTION_OUTSIDE_PHYSICAL_RANGE")
        high = min(high, _shift(Decimal(1), -source_power))
    exponent = exact_unit_exponent(source_unit, target_unit)
    unit_factor = _shift(Decimal(1), exponent)
    point, low, high = (_shift(value, exponent) for value in (point, low, high))
    transform = {"policy_version": UNIT_POLICY, "factor": str(unit_factor),
                 "formula": "reported_token × exact_unit_factor", "dimension": required,
                 "source_precision": "last-printed-digit ±0.5; nonnegative physical bounds",
                 "value_state": "MEASURED_ZERO" if point.is_zero() else "PRESENT"}
    if reported_significant_figures is not None:
        transform["reported_significant_figures"] = reported_significant_figures
        transform["source_precision"] = "publisher significant figures subject to original printed resolution"
    if before != after:
        stoich = _stoichiometry(before[0], after[0]) if len(before) == len(after) == 1 and not members else None
        if stoich is None or kind == "ORE_MASS":
            return result("NOT_COMPARABLE", "ELEMENT_OXIDE_TRANSFORM_UNAUTHORIZED")
        if not target_species_evidence_anchor_id:
            return result("INSUFFICIENT_CONTEXT", "TRANSFORM_TARGET_SPECIES_EVIDENCE_REQUIRED")
        oxide, reverse = stoich
        atomic, lower, upper = _weight_factors(oxide, reverse)
        with localcontext() as context:
            context.prec = max(len(point.as_tuple().digits) + 50, 50)
            context.rounding = ROUND_HALF_EVEN
            point *= atomic
            context.rounding = ROUND_FLOOR
            low *= lower
            context.rounding = ROUND_CEILING
            high *= upper
        if required == "MASS_FRACTION":
            if _shift(point, target_power) > 1:
                return result("SCIENTIFIC_CONFLICT", "TRANSFORMED_MASS_FRACTION_OUTSIDE_PHYSICAL_RANGE")
            high = min(high, _shift(Decimal(1), -target_power))
        element, atoms, oxygen_atoms = _OXIDES[oxide]
        transform = {
            **transform, "policy_version": OXIDE_POLICY,
            "unit_policy_version": UNIT_POLICY, "stoichiometric_factor": str(atomic),
            "stoichiometric_factor_interval": [str(lower), str(upper)],
            "formula": ("M(element atoms) / M(oxide)" if reverse else "M(oxide) / M(element atoms)"),
            "element": element, "oxide": oxide, "element_atoms": atoms, "oxygen_atoms": oxygen_atoms,
            "target_species_evidence_anchor_id": target_species_evidence_anchor_id,
            "atomic_weights_version": ATOMIC_WEIGHTS_VERSION, "atomic_weights_source": ATOMIC_WEIGHTS_SOURCE,
            "atomic_weights": {symbol: {"value": _WEIGHTS[symbol][0], "uncertainty": _WEIGHTS[symbol][1]}
                               for symbol in (element, "O")},
            "uncertainty": "printed rounding and CIAAW atomic-weight bounds; 50-digit molar-ratio evaluation",
        }
    return result("NORMALIZED", "EVIDENCE_BOUND_NUMERIC_TRANSFORM", format(point, "f"),
                  (format(low, "f"), format(high, "f")), transform)
