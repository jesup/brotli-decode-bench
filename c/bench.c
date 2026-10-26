/* Decode benchmark harness for Google Brotli (C).
 *
 *   bench compress <quality> <input> <output>
 *   bench decode <jobs.tsv> [target_ms] [rounds] [warmup]
 *
 * jobs.tsv lines: label<TAB>quality<TAB>original_path<TAB>compressed_path
 * Timing mirrors burli's bench harness: per round, repeat the op until
 * target_ms of process CPU time has elapsed, report ns/op, keep the best
 * round.  Each op allocates its output buffer, as burli's harness does.
 */
#include <brotli/decode.h>
#include <brotli/encode.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define WINDOW_BITS 22

static uint8_t* read_file(const char* path, size_t* len) {
  FILE* f = fopen(path, "rb");
  if (!f) {
    perror(path);
    exit(1);
  }
  fseek(f, 0, SEEK_END);
  long n = ftell(f);
  fseek(f, 0, SEEK_SET);
  uint8_t* buf = malloc(n ? (size_t)n : 1);
  if (n && fread(buf, 1, (size_t)n, f) != (size_t)n) {
    perror(path);
    exit(1);
  }
  fclose(f);
  *len = (size_t)n;
  return buf;
}

static uint64_t cpu_nanos(void) {
  struct timespec ts;
  clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &ts);
  return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static int do_compress(int quality, const char* in_path, const char* out_path) {
  size_t in_len;
  uint8_t* in = read_file(in_path, &in_len);
  size_t out_len = BrotliEncoderMaxCompressedSize(in_len);
  uint8_t* out = malloc(out_len);
  if (!BrotliEncoderCompress(quality, WINDOW_BITS, BROTLI_MODE_GENERIC, in_len,
                             in, &out_len, out)) {
    fprintf(stderr, "compress failed: %s q%d\n", in_path, quality);
    return 1;
  }
  FILE* f = fopen(out_path, "wb");
  if (!f || fwrite(out, 1, out_len, f) != out_len) {
    perror(out_path);
    return 1;
  }
  fclose(f);
  free(in);
  free(out);
  return 0;
}

static volatile int sink;

static int decode_once(const uint8_t* comp, size_t comp_len, size_t orig_len,
                       uint8_t** out_ret) {
  size_t out_len = orig_len;
  uint8_t* out = calloc(1, orig_len ? orig_len : 1);
  BrotliDecoderResult r =
      BrotliDecoderDecompress(comp_len, comp, &out_len, out);
  sink = out[0];
  if (r != BROTLI_DECODER_RESULT_SUCCESS || out_len != orig_len) {
    free(out);
    return 0;
  }
  if (out_ret) {
    *out_ret = out;
  } else {
    free(out);
  }
  return 1;
}

static int do_decode(const char* jobs_path, uint64_t target_ns, int rounds,
                     int warmup) {
  FILE* jobs = fopen(jobs_path, "r");
  if (!jobs) {
    perror(jobs_path);
    return 1;
  }
  char line[8192];
  while (fgets(line, sizeof line, jobs)) {
    line[strcspn(line, "\n")] = 0;
    if (!line[0] || line[0] == '#') continue;
    char* label = strtok(line, "\t");
    char* quality = strtok(NULL, "\t");
    char* orig_path = strtok(NULL, "\t");
    char* comp_path = strtok(NULL, "\t");
    if (!label || !quality || !orig_path || !comp_path) {
      fprintf(stderr, "bad job line\n");
      return 1;
    }
    size_t orig_len, comp_len;
    uint8_t* orig = read_file(orig_path, &orig_len);
    uint8_t* comp = read_file(comp_path, &comp_len);

    uint8_t* decoded;
    if (!decode_once(comp, comp_len, orig_len, &decoded) ||
        memcmp(decoded, orig, orig_len) != 0) {
      fprintf(stderr, "decode mismatch: %s q%s\n", label, quality);
      return 1;
    }
    free(decoded);

    for (int i = 0; i < warmup; i++) {
      decode_once(comp, comp_len, orig_len, NULL);
    }
    double best = 1e300;
    for (int r = 0; r < rounds; r++) {
      uint64_t iters = 0;
      uint64_t start = cpu_nanos();
      uint64_t elapsed;
      do {
        decode_once(comp, comp_len, orig_len, NULL);
        iters++;
        elapsed = cpu_nanos() - start;
      } while (elapsed < target_ns);
      double ns_per_op = (double)elapsed / (double)iters;
      if (ns_per_op < best) best = ns_per_op;
    }
    printf(
        "{\"impl\":\"google-brotli\",\"input\":\"%s\",\"quality\":%s,"
        "\"input_size\":%zu,\"compressed_size\":%zu,\"decompress_ns\":%.1f}\n",
        label, quality, orig_len, comp_len, best);
    fflush(stdout);
    free(orig);
    free(comp);
  }
  fclose(jobs);
  return 0;
}

int main(int argc, char** argv) {
  if (argc >= 5 && strcmp(argv[1], "compress") == 0) {
    return do_compress(atoi(argv[2]), argv[3], argv[4]);
  }
  if (argc >= 3 && strcmp(argv[1], "decode") == 0) {
    uint64_t target_ms = argc > 3 ? strtoull(argv[3], NULL, 10) : 200;
    int rounds = argc > 4 ? atoi(argv[4]) : 3;
    int warmup = argc > 5 ? atoi(argv[5]) : 1;
    return do_decode(argv[2], target_ms * 1000000ull, rounds, warmup);
  }
  fprintf(stderr,
          "usage: %s compress <quality> <in> <out>\n"
          "       %s decode <jobs.tsv> [target_ms] [rounds] [warmup]\n",
          argv[0], argv[0]);
  return 2;
}
