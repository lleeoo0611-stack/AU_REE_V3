"""Verify state and Commonwealth point candidates and corroborate prior production shadows."""

import argparse
from hashlib import sha256
from pathlib import Path

from au_ree_v3.contracts import canonical_json
from au_ree_v3.forecast import review_production_forecasts, verify_forecast_context
from au_ree_v3.official_geography import review_official_point_candidates


def _hash(path):
    return sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("profile", "fixed-sources", "fixed-assets", "expanded-sources", "expanded-assets",
                 "point-bindings", "forecast-bindings", "forecast-oracle", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if (args.out.exists() and any(args.out.iterdir())) or any(
        args.out.resolve().is_relative_to(path.resolve()) for path in (args.fixed_assets, args.expanded_assets)
    ):
        raise ValueError("Context audit requires a new output directory separate from both source stores")
    args.out.mkdir(parents=True, exist_ok=True)
    production = review_production_forecasts(args.profile, args.fixed_sources, args.fixed_assets, args.forecast_bindings,
                                            args.out / "production_shadows.json")
    forecast = verify_forecast_context(production, args.fixed_sources, args.fixed_assets, args.forecast_oracle)
    forecast_path = args.out / "production_forecast_context_oracle.json"
    forecast_path.write_text(canonical_json(forecast) + "\n")
    points = args.out / "government_native_points_shadow.json"
    geography = review_official_point_candidates(args.point_bindings, args.fixed_sources, args.fixed_assets,
                                                 args.expanded_sources, args.expanded_assets, points)
    print(canonical_json({"forecast": {"status": forecast["status"], "receipt_sha256": _hash(forecast_path)},
                          "geography": {"status": geography["status"], "receipt_sha256": _hash(points)}}))


if __name__ == "__main__":
    main()
