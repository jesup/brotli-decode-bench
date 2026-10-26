//! Shared decode benchmark harness for the Rust decoders.
//!
//!   <bin> decode <jobs.tsv> [target_ms] [rounds] [warmup]
//!
//! Same job format and timing method as c/bench.c: per round, repeat the op
//! until target_ms of process CPU time has elapsed, report ns/op, keep the
//! best round.  Each op allocates its output, as burli's own harness does.

use std::fs;
use std::hint::black_box;
use std::process::ExitCode;

#[repr(C)]
struct Timespec {
    tv_sec: i64,
    tv_nsec: i64,
}

extern "C" {
    fn clock_gettime(clk: i32, ts: *mut Timespec) -> i32;
}

const CLOCK_PROCESS_CPUTIME_ID: i32 = 2;

fn cpu_nanos() -> u64 {
    let mut ts = Timespec { tv_sec: 0, tv_nsec: 0 };
    unsafe { clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &mut ts) };
    ts.tv_sec as u64 * 1_000_000_000 + ts.tv_nsec as u64
}

/// `decode(compressed, original_len)` returns the decoded bytes, or None on
/// any failure (including a length mismatch).
pub type DecodeFn = fn(&[u8], usize) -> Option<Vec<u8>>;

fn run(
    name: &str,
    decode: DecodeFn,
    jobs_path: &str,
    target_ns: u64,
    rounds: usize,
    warmup: usize,
) -> Result<(), String> {
    let jobs = fs::read_to_string(jobs_path).map_err(|e| format!("{jobs_path}: {e}"))?;
    for line in jobs.lines() {
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let cols: Vec<&str> = line.split('\t').collect();
        if cols.len() < 4 {
            return Err(format!("bad job line: {line}"));
        }
        let (label, quality, orig_path, comp_path) = (cols[0], cols[1], cols[2], cols[3]);
        let orig = fs::read(orig_path).map_err(|e| format!("{orig_path}: {e}"))?;
        let comp = fs::read(comp_path).map_err(|e| format!("{comp_path}: {e}"))?;

        match decode(&comp, orig.len()) {
            Some(out) if out == orig => {}
            _ => return Err(format!("decode mismatch: {label} q{quality}")),
        }

        for _ in 0..warmup {
            black_box(decode(black_box(&comp), orig.len()));
        }
        let mut best = f64::MAX;
        for _ in 0..rounds {
            let mut iters = 0u64;
            let start = cpu_nanos();
            let elapsed = loop {
                black_box(decode(black_box(&comp), orig.len()));
                iters += 1;
                let elapsed = cpu_nanos() - start;
                if elapsed >= target_ns {
                    break elapsed;
                }
            };
            best = best.min(elapsed as f64 / iters as f64);
        }
        println!(
            "{{\"impl\":\"{name}\",\"input\":\"{label}\",\"quality\":{quality},\
             \"input_size\":{},\"compressed_size\":{},\"decompress_ns\":{best:.1}}}",
            orig.len(),
            comp.len()
        );
    }
    Ok(())
}

pub fn main_with(name: &str, decode: DecodeFn) -> ExitCode {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 3 || args[1] != "decode" {
        eprintln!("usage: {} decode <jobs.tsv> [target_ms] [rounds] [warmup]", args[0]);
        return ExitCode::from(2);
    }
    let target_ms: u64 = args.get(3).map_or(200, |s| s.parse().unwrap());
    let rounds: usize = args.get(4).map_or(3, |s| s.parse().unwrap());
    let warmup: usize = args.get(5).map_or(1, |s| s.parse().unwrap());
    match run(name, decode, &args[2], target_ms * 1_000_000, rounds, warmup) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("{e}");
            ExitCode::FAILURE
        }
    }
}
