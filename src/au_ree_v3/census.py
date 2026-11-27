"""Resource-column projection from accepted values in the requested release."""

import json

from .contracts import ResearchRelease
from .observations import VARIABLES
from .research_inputs import checked_research_inputs

COLUMNS = {
    "ore_mt": "礦石蘊藏量_Mt",
    "grade": "TREO品位_pct",
    "contained_treo_kt": "總TREO蘊藏量_kt",
}


def accepted_resource_rows(
    store, release: ResearchRelease, profile: str,
) -> tuple[list[dict], list[dict], list[dict]]:
    selected = {}
    evidence = []
    for entry in checked_research_inputs(store, release, purpose="CENSUS"):
        accepted, candidate, raw = entry.accepted, entry.candidate, entry.raw
        source, scope, anchor = entry.source, entry.scope, entry.anchor
        width = json.loads(raw.original_payload)["legacy_claim"]["rounding_half_width"]
        # This projection represents the historical V2 table contract. Native
        # PDF tokens and parsed semantics remain in the separate evidence rows.
        value = candidate.value + ".0" if raw.variable == "contained_treo_kt" else candidate.value
        selected[raw.project_id, raw.variable] = {
            "case_id": f"{profile}:observation:{raw.observation_id}",
            "values": {
                "project_id": raw.project_id, "observation_id": raw.observation_id,
                "statement_id": raw.statement_id, "scope": scope.name, "category": raw.category,
                "reporting_standard": None, "cutoff_grade": None, "effective_date": raw.effective_date,
                "effective_date_precision": "DAY", "publication_date": source.publication_date,
                "publication_date_precision": "DAY", "available_date": source.available_date,
                "acquired_at": source.acquired_at, "research_cutoff": release.cutoff,
                "selection_role": accepted.reason, "selected_for_census": True, "variable": raw.variable,
                "value": value, "unit": candidate.unit, "basis": candidate.chemical_basis,
                "material_basis": None, "denominator_basis": None, "locator": anchor.locator,
                "physical_page": anchor.page, "transform_version": "normalization-v1",
                "raw_value": None, "raw_unit": None,
                "rounding_half_width": str(width) if width is not None else None,
                "source_sha256": source.sha256, "source_url": source.url,
            },
            "v3_reference": f"release:{release.release_id}/anchor:{anchor.anchor_id}",
        }
        evidence.append({
            "case_id": selected[raw.project_id, raw.variable]["case_id"],
            "raw": raw.to_dict(),
            "anchor": anchor.to_dict(),
            "temporal_lineage": entry.lineage(),
        })
    observations = sorted(selected.values(), key=lambda item: item["case_id"])
    census = []
    for project in release.cohort:
        statement = [selected[project, variable]["values"] for variable in VARIABLES if (project, variable) in selected]
        for variable in VARIABLES:
            item = selected.get((project, variable))
            value = item["values"] if item else {}
            field = "treo_grade_pct" if variable == "grade" else variable
            census.append({
                "case_id": f"{profile}:census:{project}:{field}",
                "values": {
                    "project_id": project, "research_cutoff": release.cutoff, "field": field,
                    "value": value.get("value"), "value_state": "PRESENT" if item else "UNKNOWN",
                    "status": "VALUE_PRESENT" if item else "NO_SELECTED_VALUE",
                    "source_sha256": value.get("source_sha256"), "locator": value.get("locator"),
                    "observation_ids": [observation["observation_id"] for observation in statement],
                    "effective_date": value.get("effective_date"), "scope": value.get("scope"),
                },
                "v3_reference": f"release:{release.release_id}/census:{project}:{field}",
            })
    return observations, census, sorted(evidence, key=lambda item: item["case_id"])


def table_alternatives(observations: list[dict], evidence: list[dict], profile: str) -> list[dict]:
    """Expose the standalone table summary alongside the selected linked statement."""
    selected = {row["case_id"]: row["values"] for row in observations}
    alternatives = []
    for row in evidence:
        raw = row["raw"]
        accepted = selected[row["case_id"]]
        context = json.loads(raw["original_payload"])
        printed_tokens = context["table_tokens"]
        scope = context["table_title"]
        variable = raw["variable"]
        index = tuple(VARIABLES).index(variable)
        value = printed_tokens[index].replace(",", "")
        if variable == "contained_treo_kt":
            value += ".0"
        alternatives.append({
            "case_id": f"{profile}:table-alternative:{raw['project_id']}:reported_resource_{variable}",
            "values": {
                "project_id": raw["project_id"], "research_cutoff": accepted["research_cutoff"],
                "source_sha256": raw["source_sha"], "physical_page": row["anchor"]["page"],
                "variable": f"reported_resource_{variable}", "scope": scope,
                "value": value, "unit": accepted["unit"], "raw_tokens": printed_tokens,
                "selected_for_census": scope == accepted["scope"] and value == accepted["value"],
            },
            "v3_reference": f"v3-evidence.jsonl:{row['case_id']}",
        })
    return sorted(alternatives, key=lambda row: row["case_id"])
