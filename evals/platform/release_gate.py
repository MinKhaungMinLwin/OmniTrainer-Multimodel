import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GateResult:
    passed: bool
    failures: list[str]
    scorecard: dict


def evaluate_release(baseline: dict, candidate: dict, thresholds: dict) -> GateResult:
    failures: list[str] = []
    metrics = candidate.get("metrics", {})
    baseline_metrics = baseline.get("metrics", {})
    samples = int(candidate.get("samples", 0))
    if samples < int(thresholds["minimum_samples"]):
        failures.append(f"samples: {samples} < {thresholds['minimum_samples']}")
    if not candidate.get("segments"):
        failures.append("segments: at least one quality segment is required")
    for name, minimum in thresholds.get("minimum", {}).items():
        value = metrics.get(name)
        if value is None or value < minimum:
            failures.append(f"{name}: {value} < {minimum}")
    for name, maximum in thresholds.get("maximum", {}).items():
        value = metrics.get(name)
        if value is None or value > maximum:
            failures.append(f"{name}: {value} > {maximum}")
    for name, tolerance in thresholds.get("maximum_regression", {}).items():
        if name in metrics and name in baseline_metrics:
            regression = baseline_metrics[name] - metrics[name]
            if regression > tolerance:
                failures.append(f"{name}: regression {regression:.4f} > {tolerance}")
    fingerprint = hashlib.sha256(json.dumps(candidate, sort_keys=True).encode()).hexdigest()
    scorecard = {
        "candidate": candidate.get("version"),
        "baseline": baseline.get("version"),
        "artifact_fingerprint": fingerprint,
        "passed": not failures,
        "failures": failures,
        "metrics": metrics,
        "segments": candidate.get("segments", {}),
    }
    return GateResult(passed=not failures, failures=failures, scorecard=scorecard)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description="Gate an Omni release against quality, safety, cost, and latency")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--thresholds", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate_release(load_json(args.baseline), load_json(args.candidate), load_json(args.thresholds))
    rendered = json.dumps(result.scorecard, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")
    raise SystemExit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
