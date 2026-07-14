#!/usr/bin/env python3
"""Fresh Phoenix V3 tier tuning batch — micro / medium / large from scratch.

Runs medium first so micro and large can seed from the new medium best tuning.

Usage::
    py -3 scripts/run_tier_tuning_batch.py --trials 25000 --workers 8
    py -3 scripts/run_tier_tuning_batch.py --tier large --trials 5000
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3.tier_profiles import TIER_PROFILES, TierCartridgeProfile
from designs.phoenix_v3_optimizer import (
    default_workers,
    export_best_config,
    print_optimization_report,
    run_optimization,
    search_bounds_for_tier,
)

# Medium before dependents — phase 0 seeds micro/large from medium best JSON.
TIER_RUN_ORDER: tuple[str, ...] = ("medium", "micro", "large")


def _profile(key: str) -> TierCartridgeProfile:
    for profile in TIER_PROFILES:
        if profile.key == key:
            return profile
    raise ValueError(f"Unknown tier {key!r}")


def _wipe_tier_artifacts(profile: TierCartridgeProfile, *, keep_logs: bool = False) -> list[Path]:
    removed: list[Path] = []
    for path in (profile.corpus_path, profile.best_tuning_path):
        if path.is_file():
            path.unlink()
            removed.append(path)
    log_path = profile.corpus_path.parent / f"phoenix_v3_optimize_{profile.key}.log"
    if not keep_logs and log_path.is_file():
        log_path.unlink()
        removed.append(log_path)
    return removed


def _run_tier(
    profile: TierCartridgeProfile,
    *,
    trials: int,
    workers: int,
    cycles: int,
    seed: int,
    population: int,
    log_dir: Path,
    resume: bool = False,
) -> None:
    log_path = log_dir / f"phoenix_v3_optimize_{profile.key}.log"
    freq = next(
        b for b in search_bounds_for_tier(profile.tier_index) if b.name == "frequency_hz"
    )

    header = (
        f"=== Phoenix V3 tier batch: {profile.name} "
        f"({profile.total_displacement_cc:.0f} cc) ===\n"
        f"trials={trials} workers={workers} cycles={cycles} seed={seed} resume={resume}\n"
        f"frequency_hz search: {freq.lo:.1f}-{freq.hi:.1f}\n"
        f"corpus: {profile.corpus_path.name}\n"
        f"export: {profile.best_tuning_path.name}\n"
    )
    print(header, flush=True)
    log_path.write_text(header, encoding="utf-8")

    t0 = time.perf_counter()
    report = run_optimization(
        trials=trials,
        cycles=cycles,
        seed=seed,
        population=population,
        workers=workers,
        resume=resume,
        corpus_path=profile.corpus_path,
        tier_profile=profile,
        verbose=True,
    )
    export_best_config(
        report,
        path=profile.best_tuning_path,
        search_version="v3",
        validate=True,
        tier_profile=profile,
    )

    elapsed = time.perf_counter() - t0
    summary = (
        f"\nCompleted {profile.key} in {elapsed / 60:.1f} min "
        f"({report.new_trials} trials, corpus={report.total_corpus})\n"
        f"Best net efficiency: {report.best_stable.net_efficiency:.1%}\n"
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_optimization_report(report)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(buf.getvalue())
        log.write(summary)
    print(buf.getvalue(), end="", flush=True)
    print(summary, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trials",
        type=int,
        default=25_000,
        help="Trials per tier (default 25000)",
    )
    parser.add_argument("--cycles", type=int, default=8, help="Simulation cycles per trial")
    parser.add_argument(
        "--workers",
        type=int,
        default=min(12, default_workers()),
        help="Parallel worker processes",
    )
    parser.add_argument("--population", type=int, default=48, help="GA population size")
    parser.add_argument(
        "--tier",
        choices=TIER_RUN_ORDER,
        action="append",
        dest="tiers",
        help="Run only selected tier(s); default all three in medium->micro->large order",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Append trials to existing corpus (implies --no-wipe)",
    )
    parser.add_argument(
        "--no-wipe",
        action="store_true",
        help="Keep existing corpus/JSON",
    )
    args = parser.parse_args()

    if args.resume:
        args.no_wipe = True

    selected = args.tiers or list(TIER_RUN_ORDER)
    order = [t for t in TIER_RUN_ORDER if t in selected]
    log_dir = _REPO / "designs"

    mode = "resume" if args.resume else "fresh start"
    print(f"Phoenix V3 tier tuning batch ({mode})", flush=True)
    print(f"  Order: {' -> '.join(order)}", flush=True)
    print(f"  Trials/tier: {args.trials:,}  workers: {args.workers}", flush=True)

    batch_t0 = time.perf_counter()
    for key in order:
        profile = _profile(key)
        if not args.no_wipe:
            removed = _wipe_tier_artifacts(profile)
            if removed:
                names = ", ".join(p.name for p in removed)
                print(f"  Removed stale artifacts for {key}: {names}", flush=True)
        seed = 42 + profile.tier_index
        _run_tier(
            profile,
            trials=args.trials,
            workers=args.workers,
            cycles=args.cycles,
            seed=seed,
            population=args.population,
            log_dir=log_dir,
            resume=args.resume,
        )

    total_min = (time.perf_counter() - batch_t0) / 60.0
    print(f"\nBatch complete - {len(order)} tier(s) in {total_min:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
