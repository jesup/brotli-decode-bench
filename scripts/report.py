#!/usr/bin/env python3
"""Render benchmark JSONL results as Markdown tables.

  report.py results/plain.jsonl [results/pgo.jsonl ...]

For each results file: a per-quality summary (geometric mean of per-file
MB/s, aggregate MB/s = total bytes / total ns, each impl's ratio to
google-brotli), then a per-file matrix of each impl's speed ratio to
google-brotli.  With two or more files, a final table compares geomeans
across builds.
"""
import json
import math
import sys
from collections import defaultdict

BASELINE = "google-brotli"


def load(path):
    meta, rows = None, []
    with open(path) as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("meta"):
                meta = rec
            else:
                rows.append(rec)
    return meta, rows


def geomean(values):
    return math.exp(sum(math.log(v) for v in values) / len(values)) if values else float("nan")


def by_quality(rows):
    table = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        table[r["quality"]][r["input"]][r["impl"]] = r
    return table


def impl_names(rows):
    seen = []
    for r in rows:
        if r["impl"] not in seen:
            seen.append(r["impl"])
    return [BASELINE] + [i for i in seen if i != BASELINE] if BASELINE in seen else seen


def complete_files(files, impls):
    return {name: v for name, v in files.items() if all(i in v for i in impls)}


def summary(meta, rows):
    out = []
    out.append(f"### Build `{meta['build']}`\n")
    out.append(f"- CPU: {meta['cpu']}" + (f", pinned to cpu {meta['pinned_cpu']}" if meta.get("pinned_cpu") is not None else ""))
    out.append(f"- clang: {meta['clang']}; rustc: {meta['rustc']}")
    out.append(f"- C flags: `{meta['c_flags']}`")
    out.append(f"- Rust flags: `{meta['rust_flags']}`")
    out.append(f"- brotli {str(meta['brotli_rev'])[:12]}, burli {str(meta['burli_rev'])[:12]}")
    out.append(f"- timing: best of {meta['rounds']} rounds x {meta['target_ms']} ms, {meta['warmup']} warmup; corpus `{meta['corpus_set']}`\n")

    table = by_quality(rows)
    impls = impl_names(rows)
    others = [i for i in impls if i != BASELINE]
    head = "| q | files | ratio (compressed/original) | " + " | ".join(f"{i} geomean MB/s" for i in impls)
    head += " | " + " | ".join(f"{i}/{BASELINE}" for i in others)
    head += " | " + " | ".join(f"{i} aggregate MB/s" for i in impls) + " |"
    out.append(head)
    out.append("|--:|--:|--:|" + "--:|" * (2 * len(impls) + len(others)))
    for q in sorted(table):
        complete = complete_files(table[q], impls)
        if not complete:
            continue
        gm = {i: geomean([v[i]["mb_per_s"] for v in complete.values()]) for i in impls}
        agg = {}
        for i in impls:
            total_bytes = sum(v[i]["input_size"] for v in complete.values())
            total_ns = sum(v[i]["decompress_ns"] for v in complete.values())
            agg[i] = total_bytes / total_ns * 1000.0
        any_impl = impls[0]
        comp = sum(v[any_impl]["compressed_size"] for v in complete.values())
        orig = sum(v[any_impl]["input_size"] for v in complete.values())
        ratios = [geomean([v[i]["mb_per_s"] / v[BASELINE]["mb_per_s"] for v in complete.values()]) for i in others]
        row = f"| {q} | {len(complete)} | {comp / orig:.3f} | " + " | ".join(f"{gm[i]:.0f}" for i in impls)
        row += " | " + " | ".join(f"{r:.2f}x" for r in ratios)
        row += " | " + " | ".join(f"{agg[i]:.0f}" for i in impls) + " |"
        out.append(row)
    out.append("")

    qualities = sorted(table)
    names = sorted({n for q in table for n in table[q]},
                   key=lambda n: -max(table[q][n][i]["input_size"] for q in table for i in table[q].get(n, {})))
    for other in others:
        out.append(f"Per-file {other}/{BASELINE} decode speed ratio (>1 means {other} is faster):\n")
        out.append("| file | size | " + " | ".join(f"q{q}" for q in qualities) + " |")
        out.append("|---|--:|" + "--:|" * len(qualities))
        for n in names:
            cells = []
            size = None
            for q in qualities:
                v = table[q].get(n, {})
                if other in v and BASELINE in v:
                    size = v[other]["input_size"]
                    cells.append(f"{v[other]['mb_per_s'] / v[BASELINE]['mb_per_s']:.2f}")
                else:
                    cells.append("")
            out.append(f"| {n} | {fmt_size(size)} | " + " | ".join(cells) + " |")
        out.append("")

    out.append("Per-file decode throughput, MB/s (" + " / ".join(impls) + "):\n")
    out.append("| file | " + " | ".join(f"q{q}" for q in qualities) + " |")
    out.append("|---|" + "--:|" * len(qualities))
    for n in names:
        cells = []
        for q in qualities:
            v = table[q].get(n, {})
            if all(i in v for i in impls):
                cells.append(" / ".join(f"{v[i]['mb_per_s']:.0f}" for i in impls))
            else:
                cells.append("")
        out.append(f"| {n} | " + " | ".join(cells) + " |")
    out.append("")
    return out


def fmt_size(n):
    if n is None:
        return ""
    for unit in ("B", "KiB", "MiB"):
        if n < 1024 or unit == "MiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def cross_build(results):
    out = ["### Builds compared (geomean MB/s over files)\n"]
    builds = [m["build"] for m, _ in results]
    impls = impl_names([r for _, rows in results for r in rows])
    out.append("| q | " + " | ".join(f"{i} `{b}`" for i in impls for b in builds) + " |")
    out.append("|--:|" + "--:|" * (len(impls) * len(builds)))
    tables = [by_quality(rows) for _, rows in results]
    qualities = sorted(set.intersection(*(set(t) for t in tables)))
    for q in qualities:
        cells = []
        for i in impls:
            for t in tables:
                vals = [v[i]["mb_per_s"] for v in t[q].values() if i in v]
                cells.append(f"{geomean(vals):.0f}" if vals else "")
        out.append(f"| {q} | " + " | ".join(cells) + " |")
    out.append("")
    return out


def main():
    paths = sys.argv[1:]
    if not paths:
        raise SystemExit(__doc__)
    results = [load(p) for p in paths]
    lines = []
    for meta, rows in results:
        lines += summary(meta, rows)
    if len(results) > 1:
        lines += cross_build(results)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
