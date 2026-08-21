## JKinco Listen Open Edition v0.1.0

100% 本地运行的智能会议纪要工作台：录音转写 → 场景识别 → 结构化纪要 → DOCX/PDF 导出，数据不出本机。

![demo](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/raw/main/docs/demo.gif)

### 亮点

- 本地 FunASR 中文识别 + Ollama 本地大模型，零 API Key
- 工程例会 / 通用纪要 / 个人备忘 / 面试记录 / 客户拜访五类场景
- 规则证据门控：模型不能仅凭泛词套工程模板
- 原版式 DOCX/PDF 导出 + 自定义模板
- 本地历史知识库与“问筑听”会议问答
- Docker 一键部署
- CI 全绿：红线扫描 / 894 测试 / 前端构建 / Docker 镜像构建

### 快速开始

```bash
docker compose up -d --build
```

访问 http://localhost:8080（admin / 123456）
