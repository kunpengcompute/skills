---
name: perf-hotspot
description: Use Linux perf tools (perf record, perf report, perf annotate) to collect and analyze CPU hotspot data. Identify hot functions, hot instructions, and instruction mix breakdown. Use this skill whenever the user mentions perf record, perf annotate, hotspot analysis, instruction-level profiling, or wants to know where a program spends its CPU cycles.
---

# Perf Record & Annotate Hotspot Analysis

Use `perf record` to collect CPU cycle samples and `perf report`/`perf annotate` to identify hot functions and instructions.

## Prerequisites

- `perf` installed (`yum install -f perf` or equivalent)
- Binary compiled with `-g` for source-level annotation (optional but helpful)
- For shared libraries, ensure symbols are not stripped (`nm <lib> | grep <func>` to verify)
- Pin to a single core with `taskset -c N` or `sched_setaffinity` in-code to get clean data

## Workflow

### 1. Record

```bash
# Basic: cycle sampling with callgraph
perf record -e cycles:u --call-graph dwarf -o perf.data taskset -c 0 ./benchmark

# For very short benchmarks (<100ms), increase sample frequency
perf record -e cycles:u -F 999 -o perf.data taskset -c 0 ./benchmark
```

Key options:
- `-e cycles:u` — user-space cycles only (exclude kernel)
- `--call-graph dwarf` — DWARF-based unwinding (works without frame pointer, essential for assembly-heavy code)
- `-o <file>` — output file name (useful when comparing multiple runs)
- `-F N` — sampling frequency in Hz (default ~4000, increase for short benchmarks)

### 2. Function-level Hotspots

```bash
# Flat profile (which functions take the most samples)
perf report -i perf.data --stdio --no-children -n -g none

# Callchain profile (how functions are reached)
perf report -i perf.data --stdio -g --no-children
```

Focus on:
- Top functions with highest `Overhead` %
- Callchain depth — deep chains may indicate layering overhead
- Library vs. application code split

### 3. Instruction-level Hotspots

```bash
# Annotate a specific function to see assembly-level hotspots
perf annotate -i perf.data --stdio --no-demangle <function_name>
```

The output shows each assembly instruction with its sample percentage. The hottest instructions reveal where cycles are actually spent.

**Critical distinction:**
- Instructions with **high sample %** = bottleneck (these stall the pipeline)
- Instructions with **low/zero %** = execute quickly and retire without stalls
- In a well-optimized loop, the *bottleneck* instructions (loads waiting for cache, long-latency ops) accumulate samples, while fast instructions (pure ALU) may show 0%

### 4. Instruction Mix Classification

Use awk to classify annotated samples by instruction type:

```bash
perf annotate -i perf.data --stdio --no-demangle <function> | awk '
/^[[:space:]]*[0-9]+\.[0-9]+/{
    pct=$1; line=$0
    if (line ~ /ld1|ldr/)        load+=pct
    else if (line ~ /fmla|fadd/) fma+=pct
    else if (line ~ /st1|str/)   store+=pct
    else if (line ~ /subs|cmp/)  cmp+=pct
    else if (line ~ /b\.|bne|beq|blt|bgt|ble|bge|ret/) br+=pct
    else if (line ~ /add|sub|mov|lsl|asr|mul|nop/) alu+=pct
    else                         other+=pct
} END {
    printf "Load:    %6.1f%%\n", load
    printf "FMA:     %6.1f%%\n", fma
    printf "Store:   %6.1f%%\n", store
    printf "ALU:     %6.1f%%\n", alu
    printf "Control: %6.1f%%\n", cmp+br
    printf "Other:   %6.1f%%\n", other
}'
```

**Interpreting the mix:**

| Load > 50% | Memory/cache bottleneck — execution units starved for data |
| FMA > 50%  | Compute-saturated — floating-point pipes fully utilized |
| Control > 20% | Branch-heavy code, unpredictable control flow |
| Store > 10% | Heavy write-back pressure, store port contention |

Also count static instruction instances (lines with non-zero unique addresses):
```bash
perf annotate -i perf.data --stdio --no-demangle <function> | \
  grep -oP 'ld1d|ld1rd|fmla|st1' | sort | uniq -c | sort -rn
```

## Comparison Workflow

When comparing two benchmarks (e.g., pure compute vs. full application):

```bash
# Collect
perf record -e cycles:u --call-graph dwarf -o perf_A.data taskset -c 0 ./bench_A
perf record -e cycles:u --call-graph dwarf -o perf_B.data taskset -c 0 ./bench_B

# Compare function hotspots
perf report -i perf_A.data --stdio -n -g none | head -20
perf report -i perf_B.data --stdio -n -g none | head -20

# Compare instruction mix
perf annotate -i perf_A.data --stdio <hot_func_A> | awk '...'
perf annotate -i perf_B.data --stdio <hot_func_B> | awk '...'
```

Present results as a side-by-side table. Key comparisons:
1. **IPC** — which has better instruction throughput?
2. **Function concentration** — is the hot function >90% or spread thin?
3. **Instruction mix shift** — where did the samples move? (e.g., FMA→Load means compute→memory bottleneck)
4. **Branch mispredict rate** — higher = more wasted work
5. **Frontend stall ratio** — higher = icache/decode issues

## Combined Analysis with Top-Down

perf record/annotate answers **"where"** (which instruction), top-down answers **"why"** (which pipeline stage). The two must agree:

- **Load-heavy annotate + high Memory Bound** → confirmed memory bottleneck
- **FMA-heavy annotate + high Core Bound/0-ports** → confirmed compute saturation
- **High Frontend Bound + annotate shows wide sample scatter** → icache pressure from large code

## Common Issues

- **"no such process"** — benchmark exited too fast. Increase workload size/iterations.
- **Samples too few (<1000)** — increase `-F` or lengthen benchmark.
- **All samples in `[unknown]`** — binary stripped or missing symbols. Rebuild with `-g` or check `nm`.
- **Callchain truncated** — add `--call-graph dwarf` for assembly-heavy code.
