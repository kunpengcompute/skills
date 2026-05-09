---
name: perf-topdown
description: Combine devkit top-down and perf annotate data to diagnose CPU performance bottlenecks. Cross-validate "where" (perf annotate: which instructions stall) with "why" (top-down: which pipeline stage is blocked). Use this skill whenever the user wants to understand why a program doesn't reach theoretical peak performance, needs combined top-down + perf hotspot analysis, or asks about multi-level bottleneck diagnosis.
---

# Combined Top-Down + Perf Annotate Analysis

Use top-down (devkit) data to answer **why** the pipeline stalls, and perf annotate to answer **where** the cycles go. The two methods must cross-validate.

## Analysis Framework: Three-Level Diagnosis

```
Level 1: Macro        Level 2: Pipeline         Level 3: Instruction
─────────────         ────────────────          ────────────────────
Function hotspots     Top-down categories       Assembly instruction mix
(perf report)         (devkit top-down)         (perf annotate)
                                          
WHERE cycles go        WHY pipeline stalls     WHICH instruction stalls
```

## Data Collection

```bash
# 1. Top-down
devkit tuner top-down -L 0 ./benchmark 2>&1 | tee topdown.log

# 2. Perf record + annotate
perf record -e cycles:u --call-graph dwarf -o perf.data taskset -c 0 ./benchmark
perf report -i perf.data --stdio --no-children -n -g none | tee hotspot.log
perf annotate -i perf.data --stdio --no-demangle <hottest_function> | tee annotate.log
```

## Cross-Validation Matrix

The key insight: top-down and perf annotate must agree. If they don't, investigate why.

| perf annotate shows | top-down should show | If mismatch, check |
|---------------------|---------------------|--------------------|
| Load > 60% samples | Memory Bound high, L1/L2/L3 Bound up | perf annotate the wrong function; kernel not the real bottleneck |
| FMA > 60% samples | Core Bound high, 0-ports-busy high | Frequency scaling; measurement overhead |
| Store > 10% samples | FSU Stall up, Store Bound up | Write-combining buffer full |
| Branch > 10% samples | Bad Speculation up, br_mis_pred high | Branch predictor training |
| Wide scatter, no >2% single inst | Frontend Bound up | ICache pressure from large code |

### Instruction Mix Classification

```bash
perf annotate -i perf.data --stdio --no-demangle <function> | awk '
/^[[:space:]]*[0-9]+\.[0-9]+/{
    pct=$1; line=$0
    if (line ~ /ld1d|ld1rd|ldr/)        load+=pct
    else if (line ~ /fmla|fadd|fmul/)   fma+=pct
    else if (line ~ /st1|str/)          store+=pct
    else if (line ~ /b\.|bne|beq|blt|bgt|ble|bge|ret/) br+=pct
    else                                other+=pct
} END {
    printf "Load:   %5.1f%%\nFMA:    %5.1f%%\nStore:  %5.1f%%\nBranch: %5.1f%%\nOther:  %5.1f%%\n",
           load, fma, store, br, other
}'
```

## Single-Core vs Multi-Core Bottleneck Shift

When comparing 1-core vs N-core runs, check how the bottleneck **migrates**:

```
                1 Core                    8 Cores
                ──────                    ──────
IPC drop        baseline                  ↓↓↓ (if shared resource saturated)
Load/FMA mix    Load=N%, FMA=M%           Load ↑ (more cache contention)
Memory Bound    X%                        ↑↑↑ (shared L3/DRAM saturated)
L1 StrHaz       Y%                        ↑↑↑ (L1 ports contended by more cores)
L3/DRAM Bound   Z%                        ↑↑↑ (memory bandwidth wall)
FSU Stall       W%                        ↑ (more store conflict with FMA)
```

**Key rule**: If Memory Bound increases more than linearly with core count, the bottleneck has shifted from per-core resources (Load Pipe, L1) to shared resources (L3, DRAM bandwidth).

## Bottleneck Decision Tree

```
IPC < 1.0?
├── Yes → check Backend Bound %
│   ├── Memory Bound > 15%
│   │   ├── L3/DRAM Bound high → shared bandwidth wall (multi-core)
│   │   └── L1 Structure Hazard high → per-core cache port contention
│   └── Core Bound > 60%
│       ├── 0-ports-busy > 50% → compute saturated (good)
│       └── FSU Stall / Ptag stall high → microarch resource pressure
│
└── No (IPC > 1.5) → check Retiring %
    ├── Retiring > 40% → efficient execution, look at perf annotate for fine-tuning
    └── Bad Spec / Frontend > 10% → code layout or branch issues
```

## Comparison Table Template

When comparing two configurations, always produce this table:

```
指标              配置A         配置B        变化      诊断
──                ─────        ─────        ────      ────
IPC               X            Y            ±Δ
Retiring          X%           Y%
Backend Bound     X%           Y%
Memory Bound      X%           Y%           ←关键差异
L1 StrHaz         X%           Y%
L3/DRAM           X%           Y%
0-ports-busy      X%           Y%           ←计算饱和度
FSU Stall         X%           Y%
Load samples      X%           Y%           ←perf annotate
FMA samples       X%           Y%           ←perf annotate
```

## Key Thresholds (ARM AArch64)

| Metric | Healthy | Warning | Critical |
|--------|---------|---------|----------|
| IPC | > 1.5 | 1.0–1.5 | < 1.0 |
| Retiring | > 35% | 20–35% | < 20% |
| Backend Bound | < 50% | 50–70% | > 80% |
| Memory Bound | < 5% | 5–15% | > 15% |
| L3/DRAM Bound | < 2% | 2–8% | > 8% |
| 0-ports-busy (compute) | > 40% | 20–40% | < 20% |
| Frontend Bound | < 5% | 5–15% | > 15% |
| Load samples (perf) | < 30% | 30–60% | > 60% |
