# Google Brotli (C) vs burli (Rust) decode benchmark.
#
#   make corpus       fetch the web corpus (SET=silesia or SET=all for more)
#   make bench        build both decoders Firefox-style, compress, run -> results/plain.jsonl
#   make bench-pgo    same with PGO (instrument, train on TRAIN_SET, rebuild) -> results/pgo.jsonl
#   make report       results/report.md from whatever results exist
#
# Knobs: SET, QUALITIES, TARGET_MS, ROUNDS, WARMUP, CPU (taskset core, empty to
# disable), TRAIN_SET, OPT / PGO_OPT (C optimisation level), LLVM_PROFDATA_RUST.

ROOT := $(abspath .)
PYTHON ?= python3 -I
SET ?= web
TRAIN_SET ?= $(SET)
QUALITIES ?= 0-11
TARGET_MS ?= 200
ROUNDS ?= 3
WARMUP ?= 1
CPU ?= 3
PGO_DIR := $(ROOT)/pgo
RESULTS := $(ROOT)/results

RUST_SYSROOT := $(shell rustc --print sysroot)
LLVM_PROFDATA_RUST ?= $(RUST_SYSROOT)/lib/rustlib/x86_64-unknown-linux-gnu/bin/llvm-profdata
LLVM_PROFDATA_C ?= llvm-profdata

C_PLAIN := $(ROOT)/c/build/plain/bench
C_PGO_GEN := $(ROOT)/c/build/pgo-gen/bench
C_PGO := $(ROOT)/c/build/pgo/bench
RUST_BINS := burli-decode-bench rust-brotli-decode-bench
RUST_PLAIN_DIR := $(ROOT)/rust/target/release
RUST_PGO_GEN_DIR := $(ROOT)/rust/target/pgo-gen/release
RUST_PGO_DIR := $(ROOT)/rust/target/pgo/release

RUST_PROFILE_FLAGS := opt-level=2 lto=fat codegen-units=1 panic=abort
RUST_PGO_GEN_FLAGS := -Cprofile-generate=$(PGO_DIR)/rust-raw
RUST_PGO_USE_FLAGS := -Cprofile-use=$(PGO_DIR)/rust.profdata

CPU_ARG := $(if $(CPU),--cpu $(CPU),)
BENCH_ARGS := --set $(SET) --qualities $(QUALITIES) --target-ms $(TARGET_MS) \
  --rounds $(ROUNDS) --warmup $(WARMUP) $(CPU_ARG)
TRAIN_JOBS = $(shell $(PYTHON) scripts/run_bench.py jobs --set $(TRAIN_SET) --qualities $(QUALITIES))

.PHONY: all corpus build build-c build-rust streams bench pgo-c pgo-rust bench-pgo report clean

all: bench

corpus:
	$(PYTHON) scripts/fetch_corpus.py --set $(SET)

build-c:
	$(MAKE) -C c VARIANT=plain

build-rust:
	cd rust && cargo build --release

build: build-c build-rust

streams: build-c
	$(PYTHON) scripts/run_bench.py streams --set $(SET) --qualities $(QUALITIES) --c-bin $(C_PLAIN)

bench: build streams
	$(PYTHON) scripts/run_bench.py bench --build plain --impl google-brotli=$(C_PLAIN) \
	  --impl burli=$(RUST_PLAIN_DIR)/burli-decode-bench \
	  --impl rust-brotli=$(RUST_PLAIN_DIR)/rust-brotli-decode-bench \
	  --c-flags "$$($(MAKE) -s -C c flags VARIANT=plain | grep CFLAGS=)" \
	  --rust-flags "$(RUST_PROFILE_FLAGS)" $(BENCH_ARGS) --out $(RESULTS)/plain.jsonl

pgo-c: streams
	rm -rf $(PGO_DIR)/c-raw c/build/pgo-gen c/build/pgo
	$(MAKE) -C c VARIANT=pgo-gen PROFDIR=$(PGO_DIR)/c-raw
	$(C_PGO_GEN) decode $(TRAIN_JOBS) 50 1 0 > /dev/null
	$(LLVM_PROFDATA_C) merge -o $(PGO_DIR)/c.profdata $(PGO_DIR)/c-raw
	$(MAKE) -C c VARIANT=pgo PROFDATA=$(PGO_DIR)/c.profdata

pgo-rust: streams
	rm -rf $(PGO_DIR)/rust-raw rust/target/pgo-gen rust/target/pgo
	cd rust && RUSTFLAGS="$(RUST_PGO_GEN_FLAGS)" cargo build --release --target-dir target/pgo-gen
	for b in $(RUST_BINS); do $(RUST_PGO_GEN_DIR)/$$b decode $(TRAIN_JOBS) 50 1 0 > /dev/null; done
	$(LLVM_PROFDATA_RUST) merge -o $(PGO_DIR)/rust.profdata $(PGO_DIR)/rust-raw
	cd rust && RUSTFLAGS="$(RUST_PGO_USE_FLAGS)" cargo build --release --target-dir target/pgo

bench-pgo: pgo-c pgo-rust
	$(PYTHON) scripts/run_bench.py bench --build pgo --impl google-brotli=$(C_PGO) \
	  --impl burli=$(RUST_PGO_DIR)/burli-decode-bench \
	  --impl rust-brotli=$(RUST_PGO_DIR)/rust-brotli-decode-bench \
	  --c-flags "$$($(MAKE) -s -C c flags VARIANT=pgo PROFDATA=$(PGO_DIR)/c.profdata | grep CFLAGS=)" \
	  --rust-flags "$(RUST_PROFILE_FLAGS) $(RUST_PGO_USE_FLAGS)" $(BENCH_ARGS) --out $(RESULTS)/pgo.jsonl

report:
	$(PYTHON) scripts/report.py $(wildcard $(RESULTS)/*.jsonl) > $(RESULTS)/report.md
	@echo wrote $(RESULTS)/report.md

clean:
	rm -rf c/build rust/target $(PGO_DIR) streams
