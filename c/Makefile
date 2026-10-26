# Builds the Google Brotli C decode benchmark with flags modelled on a
# Firefox Linux x86_64 release build (clang, -O2, full LTO; -O3 for PGO).
#
#   make VARIANT=plain
#   make VARIANT=pgo-gen PROFDIR=/abs/path
#   make VARIANT=pgo PROFDATA=/abs/path/c.profdata

CC := clang
LINKER ?= mold
VARIANT ?= plain
BROTLI ?= ../third_party/brotli/c
BUILD := build/$(VARIANT)

OPT ?= -O2
PGO_OPT ?= -O3

FIREFOX_CFLAGS := -fPIC -fno-common -fno-math-errno -ffunction-sections \
  -fdata-sections -fstack-protector-strong -fstack-clash-protection \
  -fno-omit-frame-pointer -funwind-tables -fvisibility=hidden -flto
FIREFOX_LDFLAGS := -flto -fuse-ld=$(LINKER) -Wl,--gc-sections -Wl,--icf=safe \
  -fstack-protector-strong

ifeq ($(VARIANT),plain)
  OPTFLAGS := $(OPT)
else ifeq ($(VARIANT),pgo-gen)
  OPTFLAGS := $(PGO_OPT) -fprofile-generate=$(PROFDIR)
  CEXTRA := -mllvm -pgo-temporal-instrumentation
  LDEXTRA := -fprofile-generate=$(PROFDIR)
else ifeq ($(VARIANT),pgo)
  OPTFLAGS := $(PGO_OPT) -fprofile-use=$(PROFDATA) -Wno-profile-instr-unprofiled \
    -Wno-profile-instr-out-of-date
else
  $(error unknown VARIANT $(VARIANT))
endif

CFLAGS := $(OPTFLAGS) $(CEXTRA) $(FIREFOX_CFLAGS) $(EXTRA_CFLAGS) -I$(BROTLI)/include -w
LDFLAGS := $(OPTFLAGS) $(FIREFOX_LDFLAGS) $(LDEXTRA)

LIB_SRCS := $(wildcard $(BROTLI)/common/*.c $(BROTLI)/dec/*.c $(BROTLI)/enc/*.c)
OBJS := $(patsubst $(BROTLI)/%.c,$(BUILD)/%.o,$(LIB_SRCS)) $(BUILD)/bench.o

all: $(BUILD)/bench

$(BUILD)/bench: $(OBJS)
	$(CC) $(LDFLAGS) -o $@ $^ -lm

$(BUILD)/%.o: $(BROTLI)/%.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c -o $@ $<

$(BUILD)/bench.o: bench.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) -c -o $@ $<

flags:
	@echo "CC=$(CC) ($$($(CC) --version | head -1))"
	@echo "CFLAGS=$(CFLAGS)"
	@echo "LDFLAGS=$(LDFLAGS)"

clean:
	rm -rf build

.PHONY: all clean flags
