use std::process::ExitCode;

use brotli::{BrotliDecompressStream, BrotliResult, BrotliState, HeapAlloc, HuffmanCode};

fn decode(comp: &[u8], orig_len: usize) -> Option<Vec<u8>> {
    let mut out = vec![0u8; orig_len];
    let mut state = BrotliState::new(
        HeapAlloc::<u8>::new(0),
        HeapAlloc::<u32>::new(0),
        HeapAlloc::<HuffmanCode>::new(HuffmanCode::default()),
    );
    let mut available_in = comp.len();
    let mut input_offset = 0;
    let mut available_out = orig_len;
    let mut output_offset = 0;
    let mut written = 0;
    let result = BrotliDecompressStream(
        &mut available_in,
        &mut input_offset,
        comp,
        &mut available_out,
        &mut output_offset,
        &mut out,
        &mut written,
        &mut state,
    );
    (matches!(result, BrotliResult::ResultSuccess) && written == orig_len).then_some(out)
}

fn main() -> ExitCode {
    brotli_rust_decode_bench::main_with("rust-brotli", decode)
}
