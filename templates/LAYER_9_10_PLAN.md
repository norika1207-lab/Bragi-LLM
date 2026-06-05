# Layer 9-10 Plan (need env work, defer or do in parallel)

## Layer 9: Embedding model distillation

Current: `paraphrase-multilingual-MiniLM-L12-v2` = 118 MB.
Target: ~30 MB embedding model with similar retrieval quality.

Candidates (already public):
- `paraphrase-multilingual-MiniLM-L6-v2` = 88 MB (half the layers)
- `sentence-transformers/all-MiniLM-L6-v2` = 80 MB (English only, doesn't fit our Chinese-mix queries)
- `BAAI/bge-small-zh-v1.5` = 95 MB (Chinese strong, English meh)
- `intfloat/multilingual-e5-small` = 119 MB
- `BAAI/bge-micro-v2` = **32 MB** (English only)

The smallest multilingual we can get without dropping below 100 MB is
MiniLM-L6 at 88 MB. To hit ~30 MB and keep multilingual:

Path A — DIY distill:
- Use current MiniLM-L12 as teacher
- Train a 4-layer student on (intent_zh + intent_en) corpus from our 1642 templates
- Should land at ~25-35 MB with maybe 1-3% retrieval drop
- 1-2 days training on gx10

Path B — switch language regime:
- Translate all intent_zh to English at index-build time
- Use bge-micro-v2 (32 MB, English only) for retrieval
- All queries go through Bragi translation step first
- Adds ~1s per query but saves ~85 MB

Recommendation: Path A in background (gx10 GPU), keep MiniLM-L12 as default.

## Layer 10: Metal GPU acceleration for Bragi on Mac mini

Current: Bragi llama-server starts with `-ngl 0` (CPU only) per start-bragi.sh.
On Mac mini M4 Pro with Metal, `-ngl 99` should give 3-5x speedup.

Test:
```bash
LLAMA_BIN=~/Dropbox/Code\ Tree/runtime/bragi/llama-server \
NGL=99 \
~/Documents/Bragi-LLM/start-bragi.sh
```

If llama-server build supports Metal (Code Tree DMG includes one), this just
works. If not, rebuild llama.cpp with `LLAMA_METAL=1 make`.

Expected per-query time at -ngl 99:
- single Bragi call: 5-15s → 1-3s
- multi-pass (3-5 calls): 26s → ~7s

That's transformative for UX, but: bench was on CPU. If we re-run on Metal,
the numbers above change.

For honest comparison, we should:
- Document current numbers under CPU
- Document Metal numbers separately
- Don't claim Metal numbers without re-bench

## Both layers are real, both are work, neither is in scope for "verify pct"
The 10-layer sprint is "raise verify% via test-time compute". Layers 9-10
are about FOOTPRINT and SPEED, not verify%. We'll defer to a follow-up.
