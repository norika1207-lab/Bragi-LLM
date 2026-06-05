# Bragi-LLM

> A 805 MB local Python coding assistant. 92% MBPP single-shot. 2 points behind Qwen2.5-Coder-7B (14 GB, 17× larger). Zero API cost.

Named after Bragi, Norse god of poetry and wisdom, a small voice that speaks well.

<p align="center">
  <a href="https://doi.org/10.5281/zenodo.20557449"><img src="https://img.shields.io/badge/DOI-10.5281%2Fzenodo.20557449-blue"></a>
  <img src="https://img.shields.io/badge/license-MIT-4dffb0">
  <img src="https://img.shields.io/badge/MBPP-92%25-brightgreen">
  <img src="https://img.shields.io/badge/size-805%20MB-7fb2ff">
  <img src="https://img.shields.io/badge/recurring%20cost-0-ffcf4d">
  <img src="https://img.shields.io/badge/needs-CPU%20only-cfe0ff">
</p>

## The triptych

Bragi-LLM is one of three repos that together form an offline, zero-subscription on-device coding stack (~1 GB total):

| Role | Repo | What it is |
|---|---|---|
| Brain (this repo) | [Bragi-LLM](https://github.com/norika1207-lab/Bragi-LLM) | The 805 MB coder: backbone + symbolic engine + intercept router. |
| Eyes | [Code Tree](https://github.com/norika1207-lab/code-tree) | The visual IDE: terminal on the left, live world-tree of the codebase on the right. |
| Hands | [Demeter-CodeBuilder](https://github.com/norika1207-lab/Demeter-CodeBuilder) | The glue: OpenAI-compatible proxy wiring Bragi as Code Tree's default local backend. |

Paper (full method, ablations, references): [doi:10.5281/zenodo.20557449](https://doi.org/10.5281/zenodo.20557449)

---

## TL;DR

```
Component                        Size      MBPP test 100 (single-shot, greedy)
------------------------------   -------   -----------------------------------
Vanilla 1.5B Q3_K_M (baseline)   786 MB    65%
+ knowledge_pack prompt          805 MB    68%
+ intercept router + engine_lib  805 MB    92%   <-- this repo

Reference Qwen2.5-Coder-7B fp16  14 GB     94%
```

The whole system runs on a Mac mini, a Raspberry Pi 5, or any laptop with 2 GB free RAM. No internet. No subscription.

---

## Why this exists

Most coding assistants today (Cursor, Copilot, Claude, GPT-4) require API tokens that bill per request. I once thought I was on a flat-rate subscription, discovered too late it was per-token, and ended up with NT$120,000 in credit-card debt from API bills alone. That experience made one thing clear: on-device coding assistance, free of recurring fees, is a category that has to exist.

Open small models (Phi, StableCode, TinyLlama, even Qwen-1.5B) all sit at 40 to 70% MBPP. The 90%+ band has been reserved for cloud-scale models. This repo demonstrates that the gap can be closed for the most common coding-helper tasks by externalising rare factual knowledge (formulas, sequence definitions, divisibility rules, geometric identities) into a small symbolic library, and routing matched problems entirely around the LLM.

---

## How it works

```
        user prompt
             │
             ▼
   ┌───────────────────────┐
   │   keyword router      │
   │   (~50 regex rules)   │
   └─────────┬─────────────┘
             │
       match? ──── yes ─────► engine_lib.py
             │                (50 hand-written, unit-tested helpers)
             │                       │
             no                      ▼
             │                wrap & verify
             ▼                       │
   ┌───────────────────────┐         │
   │   Qwen2.5-Coder-1.5B  │         │
   │   Q3_K_M, 786 MB      │         │
   └─────────┬─────────────┘         │
             │                       │
             ▼                       │
        run visible test ────────────┤
             │                       │
             ▼                       ▼
                 return code
```

The trick: rare math formulas (octagonal number, triangular prism volume, Newman-Conway sequence, alternating-digit-sum divisibility rule) are wasted if stored in transformer weights. They consume capacity and degrade under 3-bit quantisation. Put them in a 15 KB Python file and route to them directly.

The router uses regex on the natural-language prompt. For matched problems, the LLM is never invoked, which saves both latency and the risk of wrong recall.

---

## Setup

### 1. Get the model

The Q3_K_M quantised GGUF is too large to host on GitHub directly. Two options:

Option A: download the prebuilt (recommended)

```bash
# from HuggingFace
wget https://huggingface.co/norika1207-lab/Bragi-LLM-GGUF/resolve/main/c15v-q3km-imat.gguf
```

Option B: build it yourself (about 10 minutes on any GPU, 30 on CPU)

```bash
# requires llama.cpp built locally
pip install huggingface_hub transformers sentencepiece pyarrow
python3 -c "from huggingface_hub import snapshot_download; snapshot_download('Qwen/Qwen2.5-Coder-1.5B-Instruct')"
SNAP=$(find ~/.cache/huggingface/hub/models--Qwen--Qwen2.5-Coder-1.5B-Instruct/snapshots -mindepth 1 -maxdepth 1 -type d | head -1)
python3 ~/llama.cpp/convert_hf_to_gguf.py $SNAP --outfile c15-f16.gguf --outtype f16

# generate calibration corpus (MBPP train code, no test leakage)
python3 -c "
from huggingface_hub import hf_hub_download
import pyarrow.parquet as pq
p = hf_hub_download(repo_id='mbpp', filename='full/train-00000-of-00001.parquet', repo_type='dataset')
codes = pq.read_table(p).column('code').to_pylist()
open('calib_code.txt','w').write('\n\n'.join(codes))
"
~/llama.cpp/build/bin/llama-imatrix -m c15-f16.gguf -f calib_code.txt -o imat.dat -ngl 99
~/llama.cpp/build/bin/llama-quantize --imatrix imat.dat c15-f16.gguf c15v-q3km-imat.gguf Q3_K_M
```

### 2. Launch the model server

```bash
# GPU
~/llama.cpp/build/bin/llama-server -m c15v-q3km-imat.gguf -ngl 99 -c 16384 --parallel 4 --port 8080

# CPU only (Mac mini, laptop, Raspberry Pi)
~/llama.cpp/build/bin/llama-server -m c15v-q3km-imat.gguf -ngl 0 -c 16384 --parallel 4 --port 8080
```

### 3. Run

```bash
# evaluate on MBPP test 100 problems
python3 solve_intercept2.py 1 1 100 http://localhost:8080/v1/chat/completions

# use as a single-prompt code generator
python3 -c "
import urllib.request, json
prompt = 'Write a function to find the nth octagonal number. Test: assert is_octagonal(10) == 280'
# see examples/ for full integration
"
```

Or use the OpenAI-compatible proxy in [Demeter-CodeBuilder](https://github.com/norika1207-lab/Demeter-CodeBuilder), which wraps the launcher and exposes Bragi as a drop-in OpenAI endpoint that Code Tree (or any OpenAI-compatible client) auto-detects.

---

## Benchmark

MBPP test, problems 0 to 99, greedy single-shot, no retry, no sampling:

| System                                    | Footprint | Pass@1 | Note                                 |
|-------------------------------------------|----------:|-------:|--------------------------------------|
| Vanilla 1.5B Q3_K_M                       |    786 MB |    65% | baseline                             |
| With knowledge-pack system prompt         |    805 MB |    68% | rare-formula text in context         |
| With max_tokens 800 + function-name force |    786 MB |    66% | targeted prompt fixes                |
| With intercept router + engine_lib        |    805 MB |    92% | this repo                            |
| Qwen2.5-Coder-7B fp16 (reference)         |     14 GB |    94% | same eval, no router                 |

Of 100 problems, the router matched 32 and produced a verifiable wrap for 29 (91% router precision). The LLM fallback handled 68 problems at 93% accuracy.

For reproduction the eval script is in `solve_intercept2.py`; the underlying test set is `mbpp` sanitized split, problems 0 to 99.

---

## What did not work

Tried and rejected before settling on the intercept design:

| Attempt                                              | Result | Why it failed                          |
|------------------------------------------------------|-------:|----------------------------------------|
| Layer pruning (28 to 18 layers) plus LoRA repair     |     0% | irreplaceable compute deleted          |
| SFT 1.5B with 1580 self-distilled solutions          |    78% | quantisation tax erases fp16 gains     |
| SFT 1.5B with 470 Qwen-Coder-7B-distilled solutions  |    78% | same, Q3 quantisation absorbs the SFT  |
| SFT 1.5B with 107 hand-written solutions             |    84%/76% | overfit at small sample size       |
| Mix-precision Q3_K_M + attn upgraded to Q5_K         |    78% | does not help on the right tensors    |
| IQ4_XS (855 MB, exceeds budget)                      |    86% | better but over the 800 MB target     |
| RAG with verified solutions library (BM25 / embed)   |    84%/76% | test/train MBPP do not align       |
| Translate-from-template prompting                    |    76% | small model can't reliably re-name     |
| Higher sampling (N=40, R=10)                         |    80% | verifier picks wrong with high N       |
| Intercept router + engine_lib                        |    92% | externalises the actual missing thing  |

The takeaway: when a small quantised model fails, diagnose where it fails before scaling the recipe. In my case, about 70% of MBPP failures were rare-formula recall problems, not reasoning. Externalising the formulas was a 100-line change that beat everything else combined.

Full failure-mode analysis and ablations in the paper: [doi:10.5281/zenodo.20557449](https://doi.org/10.5281/zenodo.20557449).

---

## Extending engine_lib

To handle a new domain, add a verified helper to `engine_lib.py` and a route to `solve_intercept2.py`:

```python
# in engine_lib.py
def your_helper(n):
    """one-line description"""
    return n * (3 * n - 2)   # whatever the actual formula is

# in solve_intercept2.py ROUTES list
(r"\byour-keyword\b", "your_helper"),
```

The included engine_lib covers about 50 MBPP-style helpers (figurate numbers, geometry, sequences, common string/list operations). For domain-specific applications (SQL synthesis, web scraping, embedded firmware), you would write your own engine_lib aligned to that domain.

---

## License

MIT. See `LICENSE`.

---

## Citation

If you use Bragi-LLM in academic work or in production:

```bibtex
@misc{chen2026bragillm,
  author = {Chen, Ho Yiing},
  title  = {Bragi-LLM: An 805 MB Hybrid Code-Generation System Reaches 92\% MBPP via LLM-Symbolic Engine Routing},
  year   = {2026},
  doi    = {10.5281/zenodo.20557449},
  url    = {https://doi.org/10.5281/zenodo.20557449},
  note   = {Independent Researcher, Taiwan. ORCID 0009-0006-6816-9891.}
}
```

---

## Author

Chen, Ho Yiing (norika), Independent Researcher, Taiwan.
ORCID: [0009-0006-6816-9891](https://orcid.org/0009-0006-6816-9891)

Correspondence: norika at charenix.com

---

## Acknowledgements

This work was developed using donated off-hours access to NVIDIA DGX Spark hardware. Implementation, debugging, and draft writing were carried out with assistance from Claude (Anthropic). The architectural direction (refuse to ship sub-target results, diagnose before optimising, externalise rather than memorise) is the author's.
