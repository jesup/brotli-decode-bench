use std::process::ExitCode;

fn decode(comp: &[u8], orig_len: usize) -> Option<Vec<u8>> {
    let out = burli::decompress_with_limit(comp, orig_len).ok()?;
    (out.len() == orig_len).then_some(out)
}

fn main() -> ExitCode {
    brotli_rust_decode_bench::main_with("burli", decode)
}
