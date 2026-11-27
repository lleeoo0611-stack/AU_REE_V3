"""Point MCDA and deterministic sensitivity for prepared benefit matrices."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import groupby
from math import fsum, isfinite, sqrt


POINT_METHODS = ("promethee", "saw", "topsis")
POINT_METHOD_VERSION = "point-mcda-v1"
LEAVE_OUT_VERSION = "promethee-leave-out-v1"


@dataclass(frozen=True)
class PointResult:
    score: float
    rank: float


@dataclass(frozen=True)
class RankChange:
    rank: float
    rank_change: float


def average_ranks(scores: Sequence[float]) -> tuple[float, ...]:
    """Return descending average ranks, with ties based on exact score equality."""
    ranks = [0.0] * len(scores)
    ranked_indices = sorted(range(len(scores)), key=scores.__getitem__, reverse=True)
    next_rank = 1
    for _, tied in groupby(ranked_indices, key=scores.__getitem__):
        indices = tuple(tied)
        rank = next_rank + (len(indices) - 1) / 2
        for index in indices:
            ranks[index] = rank
        next_rank += len(indices)
    return tuple(ranks)


def rank_panel(
    matrix: Mapping[str, Mapping[str, float]],
    weights: Mapping[str, float],
    threshold: float,
) -> dict[str, dict[str, PointResult]]:
    """Rank a panel by PROMETHEE II, SAW and fixed-ideal TOPSIS."""
    project_ids, _, values, weights_vector = _panel_values(matrix, weights, threshold)
    normalized = _normalize_weights(weights_vector)
    weighted = tuple(tuple(value * weight for value, weight in zip(row, normalized))
                     for row in values)
    scores = {
        "promethee": _net_flows(values, normalized, threshold),
        "saw": tuple(fsum(row) for row in weighted),
        "topsis": tuple(_fixed_ideal_closeness(row, normalized) for row in weighted),
    }
    return {
        method: {
            project_id: PointResult(score, rank)
            for project_id, score, rank in zip(project_ids, points, average_ranks(points))
        }
        for method, points in scores.items()
    }


def leave_one_out(
    matrix: Mapping[str, Mapping[str, float]],
    weights: Mapping[str, float],
    domains: Mapping[str, str],
    threshold: float,
) -> dict[tuple[str, str], dict[str, RankChange]]:
    """Recompute PROMETHEE II ranks after removing a criterion or criterion domain."""
    project_ids, criteria, values, weights_vector = _panel_values(matrix, weights, threshold)
    if domains.keys() != weights.keys() or any(not domain for domain in domains.values()):
        raise ValueError("Each weighted criterion requires one declared domain")
    baseline = average_ranks(_net_flows(values, _normalize_weights(weights_vector), threshold))
    alternatives = {
        ("criterion", criterion): (index,)
        for index, criterion in enumerate(criteria)
    }
    for domain in sorted(set(domains.values())):
        alternatives[("domain", domain)] = tuple(
            index for index, criterion in enumerate(criteria) if domains[criterion] == domain
        )
    results = {}
    for removal, excluded in alternatives.items():
        kept = tuple(index for index in range(len(criteria)) if index not in excluded)
        remaining_weights = tuple(weights_vector[index] for index in kept)
        if not any(remaining_weights):
            continue
        reduced = tuple(tuple(row[index] for index in kept) for row in values)
        flows = _net_flows(reduced, _normalize_weights(remaining_weights), threshold)
        results[removal] = {
            project_id: RankChange(rank, rank - original_rank)
            for project_id, rank, original_rank in zip(project_ids, average_ranks(flows), baseline)
        }
    return results


def _panel_values(matrix, weights, threshold):
    if len(matrix) < 2 or not weights or not isfinite(threshold) or threshold <= 0:
        raise ValueError("Point MCDA requires at least two projects, criteria and a positive threshold")
    project_ids = tuple(sorted(matrix))
    criteria = tuple(sorted(weights))
    weight_vector = tuple(weights[criterion] for criterion in criteria)
    if any(not isfinite(weight) or weight < 0 for weight in weight_vector):
        raise ValueError("Weights must be finite and nonnegative")
    if any(matrix[project_id].keys() != weights.keys() for project_id in project_ids):
        raise ValueError("Project criterion sets must match the scenario weights")
    values = tuple(tuple(matrix[project_id][criterion] for criterion in criteria)
                   for project_id in project_ids)
    if any(not 0 <= score <= 1 for row in values for score in row):
        raise ValueError("Prepared benefit scores must be finite and in [0, 1]")
    return project_ids, criteria, values, weight_vector


def _normalize_weights(weights):
    try:
        weight_sum = fsum(weights)
    except OverflowError as error:
        raise ValueError("Total weight must be finite") from error
    if not isfinite(weight_sum) or weight_sum <= 0:
        raise ValueError("Total weight must be finite and positive")
    return tuple(weight / weight_sum for weight in weights)


def _net_flows(values, normalized, threshold):
    preferences = tuple(
        tuple(
            _preference(reference, other, normalized, threshold)
            for other in values
        )
        for reference in values
    )
    return tuple((fsum(outgoing) - fsum(incoming)) / (len(values) - 1)
                 for outgoing, incoming in zip(preferences, zip(*preferences)))


def _preference(reference, other, normalized, threshold):
    total = 0.0
    for first, second, weight in zip(reference, other, normalized):
        total += weight * min(1.0, max(0.0, (first - second) / threshold))
    return total


def _fixed_ideal_closeness(row, weights):
    lo = sqrt(fsum(value * value for value in row))
    hi = sqrt(fsum((value - ideal) * (value - ideal) for value, ideal in zip(row, weights)))
    return lo / (hi + lo)
