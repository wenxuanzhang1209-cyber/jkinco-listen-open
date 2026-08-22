# Local model guide

*[中文版 / Chinese version](LOCAL_MODELS.md)*

## Speech recognition (ASR)

Default: FunASR `paraformer-zh` + `fsmn-vad` + `ct-punc`.

- Strong quality on Mandarin meeting audio, and it runs on CPU.
- Downloaded automatically from ModelScope on first run (~1–2 GB).
- Point `JKINCO_ASR_MODEL_DIR` at an existing model directory to skip the download.

## Language model (LLM)

Default: Ollama `qwen2.5:7b-instruct`.

| Hardware | Recommended model | What to expect |
|---|---|---|
| 8 GB RAM | `qwen2.5:3b` / `qwen3:4b` | Usable, noticeably slow generation |
| 16 GB RAM | `qwen2.5:7b-instruct` | The recommended default |
| 32 GB RAM / 8 GB+ VRAM | `qwen2.5:14b` / `qwen3:8b` | Clearly better output quality |
| 64 GB RAM / 24 GB+ VRAM | `qwen2.5:32b` | Approaching commercial quality |

Switching models:

```bash
OLLAMA_MODEL=qwen2.5:14b docker compose up -d
# or, in manual mode
ollama pull qwen2.5:14b
```

Any OpenAI-compatible endpoint works — vLLM, llama.cpp server, LM Studio. Point `.env` at it:

```env
LLM_BASE_URL=http://127.0.0.1:8000/v1/chat/completions
LLM_MODEL_NAME=your-model-name
```

## Getting better output

- Put proper nouns — contract sections, client organizations, recurring participant names —
  into `JKINCO_ASR_EXTRA_TERMS`, separated by `、`. These are the words a general-purpose ASR
  model has never seen, and they are also the words whose errors are most visible in a
  finished document.
- For meetings that matter, use a 14B model or larger. The gap between 7B and 14B shows up
  mostly in whether action items keep their owners attached.
- Human review is the final source of truth. The system keeps the reviewed version alongside
  the original transcript so you can always see what changed.

## A note on quantization

Ollama's default quantization (Q4_K_M) is a reasonable trade-off for this workload. Minutes
generation is a summarization task rather than a reasoning-heavy one, so the quality loss from
4-bit quantization is smaller here than it would be for code or math. If you have the memory
headroom, moving up a model size buys more than moving up a quantization level.
