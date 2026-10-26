# brotli-decode-bench

Decode-speed comparison of Google Brotli (C, `third_party/brotli`), burli
(Rust, `third_party/burli`) and rust-brotli (the `brotli` crate from
crates.io, 9.1.0, which pulls brotli-decompressor 6.2.0) across Brotli qualities q0..q11, on the web corpus used by
burli's own benchmark harness (JS, CSS, JSON, HTML), with the Silesia corpus
available as an option.

Both decoders read the *same* streams: the corpus is compressed by the C
encoder (`BrotliEncoderCompress`, lgwin 22, generic mode) because burli's
encoder stops at q5.  Decoded output is verified against the original before
timing.

## Layout

| path | purpose |
|---|---|
| `c/bench.c`, `c/Makefile` | C harness; compiles brotli's `common/`, `dec/`, `enc/` sources plus the harness into one binary, Firefox-style flags |
| `rust/` | Rust harness crate: shared timing loop in `src/lib.rs`, one binary per decoder in `src/bin/`; burli by path, `brotli` from crates.io; Firefox-style release profile |
| `scripts/fetch_corpus.py` | downloads and sha256-verifies `corpus/manifest.tsv` (same files and hashes as burli's harness) |
| `scripts/run_bench.py` | compresses the corpus, runs both binaries over a jobs file, writes JSONL |
| `scripts/report.py` | renders JSONL results to Markdown tables |
| `Makefile` | orchestration: `corpus`, `bench`, `bench-pgo`, `report` |
| `third_party/brotli`, `third_party/burli` | pinned upstream checkouts (see `third_party/REVISIONS`) |

## Running

```
make corpus                 # web corpus (SET=silesia or SET=all for more)
make bench                  # plain Firefox-style builds  -> results/plain.jsonl
make bench-pgo              # PGO builds, trained on the same jobs -> results/pgo.jsonl
make report                 # results/report.md
```

Knobs (make variables): `SET`, `QUALITIES` (default `0-11`), `TARGET_MS`
(200), `ROUNDS` (3), `WARMUP` (1), `CPU` (taskset core, default 3; empty to
unpin), `TRAIN_SET` (PGO training corpus, defaults to `SET`), `OPT` / `PGO_OPT`
(C optimisation level), `LLVM_PROFDATA_RUST`.

Rust PGO needs the `llvm-profdata` matching rustc's LLVM:
`rustup component add llvm-tools`.  If that is not possible, unpack the
`llvm-tools-<ver>-x86_64-unknown-linux-gnu.tar.xz` from
static.rust-lang.org somewhere and point `LLVM_PROFDATA_RUST` at its
`llvm-profdata` (it needs the `libLLVM.so` from the same tarball next to it).

## Compile settings

Modelled on a Firefox Linux x86_64 release build (`build/moz.configure/*`,
`config/makefiles/rust.mk`, top-level `Cargo.toml`), without `-march`:

- C (clang 21, mold):
  `-O2 -fPIC -fno-common -fno-math-errno -ffunction-sections -fdata-sections
  -fstack-protector-strong -fstack-clash-protection -fno-omit-frame-pointer
  -funwind-tables -fvisibility=hidden -flto`, linked with
  `-flto -Wl,--gc-sections -Wl,--icf=safe`.
  PGO: `-O3` (Firefox forces -O3 for PGO builds), instrumented with
  `-fprofile-generate -mllvm -pgo-temporal-instrumentation`, then
  `-fprofile-use`.
- Rust (rustc 1.95): `opt-level=2`, `lto="fat"`, `codegen-units=1`,
  `panic="abort"`.  PGO: `-Cprofile-generate` / `-Cprofile-use`, opt-level
  unchanged (Firefox keeps RUSTC_OPT_LEVEL=2 under PGO).

Firefox's `modules/brotli/moz.build` still adds `-DBROTLI_BUILD_PORTABLE`;
upstream brotli no longer references that macro, so it is not passed here.

## Measurement

Same method as burli's `bench/examples/burli_bench.rs`: process CPU time
(`CLOCK_PROCESS_CPUTIME_ID`); each round repeats the decode until 200 ms has
elapsed and reports ns/op; best of 3 rounds after 1 warmup round.  Each op
allocates its output and uses the decoder's one-shot entry point with the
whole input and a correctly sized output buffer: C `calloc` +
`BrotliDecoderDecompress`; burli `decompress_with_limit`; rust-brotli
`BrotliDecompressStream` on a fresh `BrotliState` (not the 4 KiB-buffered
`Decompressor` reader burli's harness uses, which adds copies).  Throughput
is decoded bytes per second.

PGO training uses the benchmark jobs themselves (`TRAIN_SET` defaults to
`SET`), which is the optimistic case for both decoders.

## Results (2026-10-26, Threadripper PRO 5975WX, pinned core, web corpus)

Full tables in `results/report.md`.  Geometric mean over the 14 web files of
decode MB/s; ratios are relative to google-brotli in the same build.
rust-brotli is `brotli` 9.1.0 / `brotli-decompressor` 6.2.0 (an earlier run
with 8.0.4 / 5.0.3 measured 0.58x of C at q9; 6.x is a large upstream
rewrite of the hot path).

| q | google plain | burli plain | rust-brotli plain | burli | rust-brotli | google PGO | burli PGO | rust-brotli PGO | burli | rust-brotli |
|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| 0 | 515 | 404 | 454 | 0.78 | 0.88 | 533 | 474 | 481 | 0.89 | 0.90 |
| 1 | 393 | 437 | 353 | 1.11 | 0.90 | 408 | 512 | 372 | 1.26 | 0.91 |
| 2 | 484 | 457 | 423 | 0.94 | 0.87 | 500 | 515 | 454 | 1.03 | 0.91 |
| 3 | 540 | 543 | 481 | 1.01 | 0.89 | 560 | 619 | 508 | 1.10 | 0.91 |
| 4 | 637 | 529 | 546 | 0.83 | 0.86 | 659 | 593 | 579 | 0.90 | 0.88 |
| 5 | 656 | 532 | 563 | 0.81 | 0.86 | 675 | 603 | 592 | 0.89 | 0.88 |
| 6 | 662 | 545 | 571 | 0.82 | 0.86 | 686 | 617 | 601 | 0.90 | 0.88 |
| 7 | 666 | 550 | 573 | 0.83 | 0.86 | 690 | 622 | 603 | 0.90 | 0.87 |
| 8 | 672 | 554 | 577 | 0.83 | 0.86 | 694 | 626 | 607 | 0.90 | 0.87 |
| 9 | 672 | 554 | 577 | 0.83 | 0.86 | 696 | 625 | 608 | 0.90 | 0.87 |
| 10 | 573 | 449 | 482 | 0.78 | 0.84 | 579 | 491 | 496 | 0.85 | 0.86 |
| 11 | 599 | 467 | 500 | 0.78 | 0.84 | 606 | 515 | 514 | 0.85 | 0.85 |

Observations:

- On q4..q11 streams the C decoder is fastest on every file above ~100 KiB;
  rust-brotli runs at 0.84..0.88x of C and burli at 0.78..0.83x (0.85..0.91x
  with PGO).  Files under 10 KiB are within ~5% between C and burli;
  rust-brotli trails more on small inputs.
- On q1..q3 streams the C decoder and rust-brotli both slow down markedly on
  the larger inputs (whatwg source at q1 is about half its q4 speed), while
  burli does not and leads there.  rust-brotli is a transliteration of the C
  decoder and inherits the behaviour; json-citm at q1 is extreme.  All
  decoders read the same stream and the output is verified.
- PGO (same-corpus training) gains the C decoder 1..5%, burli 8..17% and
  rust-brotli 3..7%, narrowing but not closing the q4+ gaps.
