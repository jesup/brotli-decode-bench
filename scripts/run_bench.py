#!/usr/bin/env python3
"""Generate compressed streams and run the decode benchmarks.

  run_bench.py streams [--set web|silesia|all] [--qualities 0-11]
  run_bench.py bench --build NAME --impl NAME=PATH [--impl NAME=PATH ...]
                     [--set ...] [--qualities ...] [--target-ms N]
                     [--rounds N] [--warmup N] [--cpu N] [--out FILE]
  run_bench.py jobs  [--set ...] [--qualities ...]   (print jobs.tsv path)

Streams are produced by the C encoder (BrotliEncoderCompress, lgwin 22,
generic mode) so both decoders read identical input.  Results are JSONL,
one line per (impl, input, quality), plus one leading "meta" line.
"""
import argparse
import json
import os
import platform
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CORPUS = os.path.join(ROOT, "corpus")
STREAMS = os.path.join(ROOT, "streams")

sys.path.insert(0, HERE)
from fetch_corpus import read_manifest  # noqa: E402


def parse_qualities(spec):
    out = []
    for part in spec.split(","):
        if "-" in part:
            lo, hi = part.split("-")
            out.extend(range(int(lo), int(hi) + 1))
        else:
            out.append(int(part))
    return out


def entries_for(set_name):
    entries = read_manifest(os.path.join(CORPUS, "manifest.tsv"))
    return [e for e in entries if set_name == "all" or e["set"] == set_name]


def stream_path(entry, q):
    return os.path.join(STREAMS, f"{entry['label']}.q{q}.br")


def make_streams(args):
    os.makedirs(STREAMS, exist_ok=True)
    c_bin = args.c_bin or os.path.join(ROOT, "c", "build", "plain", "bench")
    for e in entries_for(args.set):
        src = os.path.join(CORPUS, e["path"])
        if not os.path.exists(src):
            raise SystemExit(f"missing corpus file {src}; run fetch_corpus.py")
        for q in parse_qualities(args.qualities):
            dst = stream_path(e, q)
            if os.path.exists(dst):
                continue
            t0 = time.time()
            subprocess.run([c_bin, "compress", str(q), src, dst], check=True)
            print(f"q{q:<2} {e['label']:28} {os.path.getsize(dst):>9} B  {time.time() - t0:5.1f}s",
                  file=sys.stderr)


def write_jobs(args):
    jobs = os.path.join(STREAMS, f"jobs-{args.set}-{args.qualities.replace(',', '_')}.tsv")
    with open(jobs, "w") as f:
        for e in entries_for(args.set):
            for q in parse_qualities(args.qualities):
                dst = stream_path(e, q)
                if not os.path.exists(dst):
                    raise SystemExit(f"missing stream {dst}; run `streams` first")
                f.write(f"{e['label']}\t{q}\t{os.path.join(CORPUS, e['path'])}\t{dst}\n")
    return jobs


def git_rev(path):
    try:
        return subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def tool_version(cmd):
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.splitlines()[0]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def cpu_model():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()


def run_bench(args):
    impls = []
    for spec in args.impl:
        name, _, path = spec.partition("=")
        if not path:
            raise SystemExit(f"--impl needs NAME=PATH, got {spec}")
        impls.append((name, path))
    jobs = write_jobs(args)
    prefix = ["taskset", "-c", str(args.cpu)] if args.cpu is not None else []
    out_path = args.out or os.path.join(ROOT, "results", f"{args.build}.jsonl")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    meta = {
        "meta": True,
        "build": args.build,
        "timestamp": int(time.time()),
        "cpu": cpu_model(),
        "pinned_cpu": args.cpu,
        "target_ms": args.target_ms,
        "rounds": args.rounds,
        "warmup": args.warmup,
        "corpus_set": args.set,
        "qualities": args.qualities,
        "impls": {name: os.path.relpath(path, ROOT) for name, path in impls},
        "c_flags": args.c_flags,
        "rust_flags": args.rust_flags,
        "clang": tool_version(["clang", "--version"]),
        "rustc": tool_version(["rustc", "--version"]),
        "brotli_rev": git_rev(os.path.join(ROOT, "third_party", "brotli")),
        "burli_rev": git_rev(os.path.join(ROOT, "third_party", "burli")),
    }
    with open(out_path, "w") as out:
        out.write(json.dumps(meta) + "\n")
        for impl, binary in impls:
            cmd = prefix + [binary, "decode", jobs, str(args.target_ms), str(args.rounds),
                            str(args.warmup)]
            print(f"running {impl}: {' '.join(cmd)}", file=sys.stderr)
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
            for line in proc.stdout:
                rec = json.loads(line)
                rec["build"] = args.build
                rec["mb_per_s"] = rec["input_size"] / rec["decompress_ns"] * 1000.0
                out.write(json.dumps(rec) + "\n")
                out.flush()
                print(f"  {impl:14} q{rec['quality']:<2} {rec['input']:28} {rec['mb_per_s']:8.1f} MB/s",
                      file=sys.stderr)
            if proc.wait() != 0:
                raise SystemExit(f"{impl} benchmark failed")
    print(f"wrote {out_path}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--set", default="web", choices=["web", "silesia", "all"])
    common.add_argument("--qualities", default="0-11")

    p = sub.add_parser("streams", parents=[common])
    p.add_argument("--c-bin")
    p.set_defaults(func=make_streams)

    p = sub.add_parser("jobs", parents=[common])
    p.set_defaults(func=lambda a: print(write_jobs(a)))

    p = sub.add_parser("bench", parents=[common])
    p.add_argument("--build", required=True, help="label for this build variant (plain, pgo, ...)")
    p.add_argument("--impl", action="append", required=True, metavar="NAME=PATH",
                   help="decoder binary to run; repeatable")
    p.add_argument("--c-flags", default="")
    p.add_argument("--rust-flags", default="")
    p.add_argument("--target-ms", type=int, default=200)
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--warmup", type=int, default=1)
    p.add_argument("--cpu", type=int, help="pin to this CPU with taskset")
    p.add_argument("--out")
    p.set_defaults(func=run_bench)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
