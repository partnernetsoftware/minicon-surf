#!/usr/bin/env python3
"""Fit retained-above-empty against sequential cycle count across retention receipts."""

import argparse
import json
import pathlib
import statistics


def least_squares(points):
    xs = [x for x, _ in points]
    if len(set(xs)) < 2:
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(y for _, y in points)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator
    return {"intercept_bytes": mean_y - slope * mean_x, "slope_bytes_per_cycle": slope}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("receipts", nargs="+")
    parser.add_argument("--output")
    args = parser.parse_args()
    per_candidate = {}
    sources = []
    for path in args.receipts:
        receipt = json.loads(pathlib.Path(path).read_text())
        cycles = receipt["workload"]["sequential_target_cycles"]
        sources.append({"receipt": pathlib.Path(path).name, "cycles": cycles})
        for name, candidate in receipt["measurement"]["candidates"].items():
            retained = candidate["post_all_closes_minus_empty_resident_bytes"]
            entry = per_candidate.setdefault(name, {"version": candidate.get("version"), "points": [], "medians": {}})
            entry["points"].extend((cycles, value) for value in retained)
            entry["medians"][str(cycles)] = int(statistics.median(retained))
    result = {
        "schema": "minicon-surf.target-retention-slope-receipt/0.0.1",
        "status": "incomplete",
        "semantic": "retained = intercept + slope * cycles fitted over every run of every receipt; "
                    "intercept approximates one-time warm-up, slope approximates per-cycle accumulation of summed RSS",
        "sources": sources,
        "candidates": {
            name: {
                "version": entry["version"],
                "retained_median_by_cycles": entry["medians"],
                "least_squares_over_runs": least_squares(entry["points"]),
            }
            for name, entry in per_candidate.items()
        },
        "limitations": [
            "summed RSS is neither private memory nor PSS",
            "linear fit over three cycle counts; non-linear retention is not modelled",
            "one fixture, one platform, one machine",
        ],
    }
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        pathlib.Path(args.output).write_text(encoded)
    print(encoded, end="")


if __name__ == "__main__":
    main()
