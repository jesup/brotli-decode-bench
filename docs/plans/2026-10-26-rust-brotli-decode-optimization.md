# rust-brotli decode optimization plan

Goal: bring `brotli-decompressor` (dropbox/rust-brotli-decompressor, used by the
`brotli` crate) decode speed to parity with Google's C decoder, ideally past it,
on the web corpus at q4..q11 and on small streams, without format or API
regressions and with the work landing upstream.

## 0. Where things stand (2026-10-26)

Baseline must be `brotli-decompressor` 6.2.0 / `brotli` 9.1.0, not the 5.0.3
that `brotli` 8.x pulls in.  Upstream landed most of the obvious fixes in
Oct 2026 (fall-through command states, tree cache fixed, bit reader mask and
refill, overlap copy by doubling, dynamic ring buffer, smaller trees).  5.0.3
numbers in older reports are obsolete.

Spot check, q9 web corpus, plain Firefox-style builds, one pinned core
(geomean MB/s over 14 files):

| decoder | MB/s | vs C |
|---|--:|--:|
| google-brotli C (-O2, LTO) | 683 | 1.00 |
| brotli-decompressor 6.2.0 | 580 | 0.85 |
| burli 0.3.5 | 556 | 0.81 |
| brotli-decompressor 5.0.3 | 395 | 0.58 |

Full q0..q11 matrix (`results/report.md`), geomean MB/s and ratio to C:

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

Note the shape: rust-brotli sits at a flat 0.84..0.90x of C at every quality,
including q1..q3 where C itself drops to half its q4 speed on large files and
burli does not.  rust-brotli inherits C's per-metablock behaviour by
construction; whatever makes q1..q3 streams slow for C (many small
metablocks from the fast encoder, so table build and setup per metablock
dominate) is a target for phase 1 and a chance to beat C outright there.

Experiments already run on 6.2.0 (q9):

- `unsafe` feature (unchecked indexing everywhere): +1%.  Bounds checks are
  not the problem any more; do not spend effort there first.
- profile (perf, srcline): `ProcessCommands` 63% self, `HuffmanTreeGroup::init`
  5.9% (all zero-fill), `ReadHuffmanCode` 4.7%, `BrotliBuildHuffmanTable` 4.0%,
  libc memmove 4.4%, libc memset 3.6%, `HeapAlloc::alloc_cell` 1.6%.  Inside
  `ProcessCommands`: bit reader refill/peek lines ~12%, `fast!` indexing ~5%.
- small q11 stream (11 KB compressed): 479 MB/s vs C 1.0+ GB/s; table build
  and allocation dominate, not the command loop.

Remaining gap decomposes roughly as: per-metablock table alloc + zero + build
(~12%), per-literal checks and bit reader (~10%), ring buffer copy to output
and ring alloc (~8%), copy path (~4%).  Parity is plausible from these alone;
exceeding C needs the output-path rearchitecture in phase 4.

## 1. Constraints

- Format compliance: every change gated on the tests in section 3.  No
  behaviour change on malformed input except where C and Rust already differ
  intentionally (CVE-2026-111497 error code).
- API: `BrotliDecompressStream`, `BrotliState`, `Decompressor`,
  `DecompressorWriter`, FFI (`ffi-api`), dictionaries, metadata callback stay
  source compatible.  Allocator traits (`alloc-no-stdlib`) may gain methods
  with default impls only.
- Safety: upstream policy since 6.0 is safe code by default; `unsafe` only as
  small primitives with contracts and `debug_assert!`, behind the existing
  `unsafe` feature or in a module with Miri coverage (burli's model).
- MSRV: `brotli` declares 1.59; const generics (1.51) are usable.  No new deps.
- `no_std` + `alloc` builds keep working (`cargo test --no-default-features`).
- Work happens on a fork of upstream master as a PR series; each PR carries
  before/after numbers from this harness.

## 2. Method

Harness: this repo.  `third_party/rust-brotli-decompressor` checkout, bench
crate patched to it with `[patch.crates-io]`.  Every experiment:

1. hypothesis + expected gain written down first;
2. `make bench` on q4..q11 web (geomean and per-file) and the small-stream
   set (`QUALITIES=11` on 224 B..30 KiB files), pinned core, 3x200 ms;
3. `perf record --call-graph dwarf` + `perf report --sort sym,srcline
   --full-source-path` on the q9 run (scripts in `scripts/profile.sh`, to add);
4. keep if geomean q4..q11 improves >= 1% with no file regressing > 2%, or if
   small-stream geomean improves >= 3%;
5. `cargo asm`/`perf annotate` on `ProcessCommands` when a change should alter
   codegen but the number does not move.

PGO run only at phase ends; it hides branch-layout work.

Add to the harness first (phase 1): per-quality counters from a debug build
(commands, literals, copies, metablocks, tree builds per MB) so gains can be
attributed to a path, and a `--chunked IN,OUT` mode that feeds the stream in
fixed input chunks with a fixed output buffer, to measure the streaming path
Firefox actually uses (nsHTTPCompressConv, 16..64 KiB chunks).

## 3. Compliance gates (run before every commit; all must pass)

- Upstream: `cargo test`, `cargo test --release`, `cargo test --features
  unsafe --release`, `cargo test --no-default-features`; 96 files in
  `testdata/`; `tests/*.rs` (bit_reader_streaming, pool_ring_buffer,
  dictionaries, ffi allocator).
- Oracle: decode every stream the harness generates (web + Silesia, q0..q11,
  lgwin 10..24, plus large-window 25..30 via the C `BROTLI_PARAM_LARGE_WINDOW`)
  and compare bytes to the C decoder.  Add to `scripts/run_bench.py verify`.
- Chunking: same streams, random input chunk sizes (1 B..64 KiB) and random
  output buffer sizes, including 1-byte output, asserting identical output and
  identical `NeedsMoreInput/Output` discipline as a reference run.  This is the
  main regression risk of touching the state machine.
- Fuzz: upstream `fuzz/` targets (decompress_into_fixed, into_vec,
  dictionaries, differential_dictionaries) plus a new differential target
  against libbrotli (C) for arbitrary bytes: both must agree on
  success/failure and output.  Run 1 h per phase, 8 h before each upstream PR.
- Miri on any module containing `unsafe`.
- Error paths: the metadata, padding, and distance error tests from 6.2.0.

## 4. Phases

### Phase 1: instrumentation and allocation (expected +8..12% q9, +30%+ small)

1. Counters and chunked mode in the harness (above).
2. Stop zero-filling tables.  `HuffmanTreeGroup::init` allocates
   `ntrees * 1080` entries per metablock through `HeapAlloc::alloc_cell`
   (`vec![default; len]`).  Builders write every entry they use.
   - Keep the `codes` and `htrees` buffers in `BrotliState` across
     metablocks; grow only (`reset` becomes "retain").
   - Size per tree from alphabet size (`kMaxHuffmanTableSize`, as C does)
     instead of the 1080 maximum.
   - Where zero-init is semantically required (sentinel checks), keep it on
     the slice actually used.
   Measure: `HuffmanTreeGroup::init` share -> ~0; memset share.
3. Same for `context_map`, `block_type_trees`, `block_len_trees`,
   `context_modes`: allocate once per state, reuse across metablocks.
4. Ring buffer: 6.1 made it dynamic; check that the first allocation for a
   known-final small stream is sized to the metablock (as C does) and that
   `alloc_cell` zeroing is not paid on a 4 MiB window for a 10 KiB stream.
   For `Vec`-backed alloc, add an `alloc_cell_uninit`-style default trait
   method that falls back to zeroing, and use it for the ring buffer (every
   byte read is a byte previously written, enforced by distance checks;
   verify with the oracle and chunking suites).

### Phase 2: hot loop, straight from the C decoder (expected +6..10%)

1. Batched literal loop.  C's `BrotliCopyPreloadedSymbolsToU8` computes once
   how many literals can be decoded with no per-literal check
   (min of insert length, block length, ring space, available input bits/15)
   and runs a check-free loop, then finishes with the checked loop.  6.2.0
   still does `CheckInputAmount` + `block_length == 0` + `pos == ring_end` on
   every literal in both the trivial-context and context-map variants.
   Implement with the same `num_steps` logic; the inner loop body is
   `ReadPreloadedSymbol` + store.  Verify with `perf annotate` that the inner
   loop has no compare against `avail_in`.
2. Bit reader guard.  Replace `avail_in >= 28` checks with a precomputed
   `guard_in` index (C: `guard_in = last_in - 28`); `CheckInputAmount` becomes
   one compare against a loop-invariant.  Keep `next_in: u32` indexing but
   load through `input.get(next_in..next_in+8)` only in the slow path; the
   fast path loads via `from_le_bytes` from a slice proven in bounds by the
   guard (safe code, one bounds check per refill, not per byte).
3. `HuffmanCode` as a packed `u32` (C `BROTLI_HC_FAST_LOAD`): one load gives
   bits and value; today it is `{u16, u8}` with padding and two loads.
   Changes table layout only; builders and FFI table export need updating.
4. `safe` as a const generic: `ProcessCommandsInternal<const SAFE: bool>` so
   the fast path cannot keep runtime `if safe` tests.  Check asm before and
   after; if LLVM already folds them, skip.
5. Context-map literal path: cache the 64 `context_trees` lazily (6.2.0 fills
   all 64 on slice change); measure on q11 streams where block switches are
   frequent.

### Phase 3: copies and distances (expected +2..4%)

1. `memcpy_within_slice` validates and copies; make the non-overlapping path a
   single `copy_within` on a slice pre-split at `pos` (no overlap check per
   call), overlap path keeps the doubling.
2. Fixed-size 16/32-byte copies through `[u8; 16]`/`[u8; 32]` moves with the
   ring slack guaranteeing room (slack is 42 + 24 bytes already).
3. Distance decode: `ReadDistanceInternal` reads the distance tree then extra
   bits with separate refills; peek once for both (C does one refill of 32
   bits).  Small gain, verify with counters.

### Phase 4: output path rearchitecture (expected +4..8%; required to pass C)

C and 6.2.0 decode into a ring buffer, then `WriteRingBuffer` memcpys to the
caller: every output byte is written twice and the ring competes with the
output for cache.  burli decodes straight into its output `Vec`, which is also
the LZ77 window, and that is where its remaining edge over C sits on large
files.

Design: a `Window` abstraction with two modes, chosen per
`BrotliDecompressStream` call from `available_out`:

- `Direct`: when `available_out >= meta_block_remaining_len` (always true for
  one-shot callers and for `Decompressor` with a big buffer), commands write
  into `output[output_offset..]`; back-references read from `output` for
  distances within the bytes written this call and from the ring (history)
  for older ones.  At call end, the last `window_size` bytes are copied back
  into the ring only if more input is expected (one memcpy of the tail, not
  of everything).
- `Ring`: current behaviour, used when output space is small.

Contiguous output also removes the ring wrap paths from the hot loop in
`Direct` mode.  Risks: distance checks across the ring/output boundary,
dictionary copies, uncompressed metablocks, `total_out` accounting, and the
state saved between calls.  The chunking suite is the gate.  Prototype on the
one-shot path first and measure before committing to the streaming variant.

### Phase 5: small-stream fixed costs (expected 1.5..2x on < 10 KiB)

1. `BrotliState::new` allocations: `context_map_table` (1080 entries) and
   block trees are allocated eagerly; make them lazy (6.1 did some of this,
   verify what is left with the 224 B and 717 B HTML files).
2. `BrotliBuildHuffmanTable`: compare against C's current builder (it uses
   `ReplicateValue` with step doubling); profile the q11 small stream, where
   table build is the top entry.
3. Reusable decoder: expose `BrotliState::reset()` that keeps buffers, so
   `Decompressor`-style callers pay allocation once; Firefox-style callers
   (one decoder per response) still need 1 and 2.

### Phase 6: PGO/LTO check and upstreaming

1. Rerun plain and PGO matrices; PGO should now give <= 5% as it does for C.
   If PGO still gives > 10%, branch layout is wrong somewhere: find the
   function with the largest PGO delta via `perf diff`.
2. PRs to dropbox/rust-brotli-decompressor in this order: phase 1 (alloc),
   phase 2.1/2.2 (literal loop, guard), 2.3 (table layout), 3, 5, 4.
   Each PR: numbers, fuzz hours, Miri status.  Then bump `brotli` to pick up
   the release and rerun this harness.

## 5. Ordering and effort

| step | gain (q9, est.) | risk | size |
|---|--:|---|---|
| 1.2 table reuse, no zero-fill | 6-8% | low | S |
| 2.1 batched literal loop | 4-7% | medium (streaming) | M |
| 2.2 bit reader guard | 2-4% | low | S |
| 1.4 ring alloc for small streams | small-stream 20%+ | low | S |
| 2.3 packed HuffmanCode | 1-3% | low (FFI layout) | M |
| 4 direct output window | 4-8% | high | L |
| 3 copies | 2-4% | low | S |
| 5 small-stream init | small-stream 1.5-2x | low | M |

Decision point after phase 2: if q4..q11 geomean >= 0.97x C, phase 4 is
optional for parity and should be judged on the streaming numbers only.

## 6. Unresolved questions

1. Is the target the one-shot API, the chunked streaming API (what Firefox
   uses), or both weighted equally?  Phase 4's value depends on this.
2. Does Firefox intend to switch from C brotli to a Rust decoder, or is this
   to inform that decision?  If switching, the `unsafe` feature policy and
   the `brotli` crate's edition-2015/MSRV 1.59 are things to raise upstream.
3. Should burli be the vehicle instead?  It is already structured as phase 4
   describes and is at 0.81x; its gap is in the hot loop (phases 2-3), and it
   lacks large-window and dictionary support that rust-brotli has.
4. Upstream appetite: dropbox merged AI-authored optimizations in Oct 2026 and
   requires fuzz/test guardrails; confirm with the maintainer before phase 4
   since it changes the state machine they just reworked.
5. Which machines matter?  Numbers here are Zen 3 desktop; the literal loop
   and bit reader trade-offs differ on ARM (Android).  Add an aarch64 run
   before claiming parity.
