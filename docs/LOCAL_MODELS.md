# 本地模型指南

## 语音识别（ASR）

默认：FunASR `paraformer-zh` + `fsmn-vad` + `ct-punc`。

- 中文普通话会议场景识别质量好、CPU 可跑；
- 首次运行自动从 ModelScope 下载（约 1–2GB）；
- 可用 `JKINCO_ASR_MODEL_DIR` 指向已有模型目录，跳过重复下载。

## 大模型（LLM）

默认：Ollama `qwen2.5:7b-instruct`。

| 硬件 | 推荐模型 | 体验 |
|---|---|---|
| 8GB 内存 | `qwen2.5:3b` / `qwen3:4b` | 可用，生成偏慢 |
| 16GB 内存 | `qwen2.5:7b-instruct` | 推荐默认 |
| 32GB 内存 / 8GB+ 显存 | `qwen2.5:14b` / `qwen3:8b` | 质量明显更好 |
| 64GB 内存 / 24GB+ 显存 | `qwen2.5:32b` | 接近商用质量 |

换模型：

```bash
OLLAMA_MODEL=qwen2.5:14b docker compose up -d
# 或手动模式
ollama pull qwen2.5:14b
```

也可以接入任意 OpenAI 兼容端点（vLLM、llama.cpp server、LM Studio），
只需在 `.env` 修改：

```env
LLM_BASE_URL=http://127.0.0.1:8000/v1/chat/completions
LLM_MODEL_NAME=your-model-name
```

## 质量建议

- 专有名词（标段、甲方、人名）写入 `JKINCO_ASR_EXTRA_TERMS`，顿号分隔；
- 重要会议建议用 14B 以上模型；
- 生成后的人工校核是最终事实来源，系统保留校核稿与原文对照。
