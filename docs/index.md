# JKinco Listen · 筑听（开源本地版）

本地优先的 AI 会议纪要工作台：**录音转写 → 场景识别 → 结构化纪要 → DOCX/PDF 导出**，
全流程 100% 本地运行，数据不出本机。

- 🎙 本地语音识别：FunASR `paraformer-zh`
- 🧠 本地大模型：Ollama（默认 `qwen2.5:7b-instruct`）
- 🏗 规则证据门控的场景识别：工程例会 / 通用 / 个人 / 面试 / 客户拜访
- 📄 原版式 DOCX/PDF 导出
- 🔒 零 API Key、零云端、零遥测
- 🐳 Docker 一条命令部署

## 快速开始

```bash
git clone https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open.git
cd jkinco-listen-open
cp .env.example .env
docker compose up -d --build
```

访问 <http://localhost:8080>，默认账号 `admin / 123456`。

想先体验界面？设置 `JKINCO_DEMO_DATA=1` 即可获得两条示例纪要。

## 文档

- [项目主页（README）](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open)
- [架构](ARCHITECTURE.md)
- [模型指南](LOCAL_MODELS.md)
- [路线图](ROADMAP.md)
- [开源版说明](OPEN_EDITION.md)

## 社区

- [Discussions](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/discussions)
- [Issue](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/issues)

MIT License © 2026 JKinco
