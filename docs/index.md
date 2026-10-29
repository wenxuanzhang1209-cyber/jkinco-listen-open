<p align="center">
  <a href="https://jkincolisten.cloud">
    <img src="https://raw.githubusercontent.com/wenxuanzhang1209-cyber/jkinco-listen-open/main/frontend/public/jkinco-listen-logo.png"
         width="480" alt="JKinco Listen | 筑听" />
  </a>
</p>

# JKinco Listen · 筑听（开源本地版）

一个能直接开会的私有会议工作台：**自托管实时会议 → 本机流式字幕 → 场景识别 →
结构化纪要 → DOCX/PDF 导出**。不调用云端模型，不依赖云端 API Key。

*[English](en.md)*

> **正式产品已经上线。**<br>
> [打开筑听](https://jkincolisten.cloud) ·
> [产品演示、三端下载与筑听 Z1](https://jkincolisten.cloud/download/ios)

![正式产品：现场采集、自动成稿与会议问答](production-download-tour.gif)

<sub>动图录制自公开下载页；公开产品介绍目前以中文为主，本开源仓库与贡献文档采用英文优先。
[静态海报](production-download-tour.png) · [全流程总览](production-app-demo.gif)</sub>

### 随身录音卡：筑听 Z1

正式产品同时支持 **筑听 Z1**：约 44 g、磁吸随身，双全向麦克风加 1 个骨传导麦克风，
8 / 16 / 32 / 64 GB 存储，支持 Android / iOS，并可识别 120+ 种语言和方言。在正式 App
中，Z1 与手机录音并列，作为一等的采集入口。

[![官方筑听 Z1 产品卡](jkinco-z1-product-card.png)](https://jkincolisten.cloud/download/ios)

<sub>具体在售状态、配置与价格以正式产品页为准。</sub>

<video src="demo.mp4" controls muted loop playsinline width="100%" poster="demo-minutes.png"
       style="border-radius:12px;box-shadow:0 8px 28px rgba(15,23,42,.18);margin:20px 0">
  你的浏览器不支持内嵌视频，<a href="demo.mp4">点这里下载演示</a>。
</video>

- 🎥 自托管实时会议：LiveKit 房间、屏幕共享、聊天与本机流式字幕
- 🎙 本地语音识别：FunASR `paraformer-zh`
- 🧠 本地大模型：Ollama（默认 `qwen2.5:7b-instruct`）
- 🏗 规则证据门控的场景识别：工程例会 / 通用 / 个人 / 面试 / 客户拜访
- 📄 原版式 DOCX/PDF 导出
- 🔒 零 API Key、零云端、零遥测
- 🐳 Docker 一条命令部署

## 一个产品，两种用法

| | 正式 App | 开源本地版 |
|---|---|---|
| 适合谁 | 想立即使用成熟产品的人 | 需要自托管、扩展和审计的人 |
| 使用入口 | iOS、Android、鸿蒙 | 浏览器 + Docker Compose |
| 核心流程 | 一键录音、自动成稿、Sona 会议问答 | LiveKit 实时会议、本地字幕、本地大模型、DOCX/PDF |
| 数据边界 | 以正式产品服务及其条款为准 | 运行在你控制的基础设施上 |

正式移动端与开源仓库共享同一个产品方向：现场记录要实时有用，散会后要留下结构化、
可检索、可导出的会议资产。

## 产出物长这样

会议概述、流程、结论、待办事项——直接可归档，右侧是从录音到导出的完整流水线。

![结构化纪要](demo-minutes.png)

## 快速开始

```bash
git clone https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open.git
cd jkinco-listen-open
cp .env.example .env
docker compose up -d --build
```

访问 <http://localhost:8080>。

从实时会议大厅创建第一场会议。首次打开本地字幕会下载流式 ASR 模型，之后识别在运行
服务的机器上完成。

> ⚠️ **本地试用凭据故意写成一眼可见。** 网页登录默认是 `admin:123456`，LiveKit 使用
> 开发模式。开放局域网或公网前必须更换两套凭据、配置可达的 `LIVEKIT_PUBLIC_URL`，
> 并在前置代理启用 TLS。

想先体验界面、不下载模型？设置 `JKINCO_DEMO_DATA=1` 即可获得两条示例纪要，
导出、历史检索、模板都能直接玩。

## 和别的方案比

|  | 筑听开源版 | 云端会议助手 | 直接用 Whisper |
|---|---|---|---|
| 音频是否出本机 | **从不** | 是 | 从不 |
| 需要 API Key | **不需要** | 需要 | 不需要 |
| 产出物 | **排好版的 DOCX/PDF** | 转写 + 摘要 | 纯转写 |
| 会议类型区分 | **五类，证据门控** | 单一格式 | 无 |

只要转写的话，直接用 Whisper 更简单。这个项目要解决的是**转写之后**那一段：
把它变成一份能归档、能发出去的文档。

## 文档

- [项目主页（README）](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open)
- [**领域词识别基准**](BENCHMARK.md) —— 31/36 → 36/36，附可复现脚本
- [架构](ARCHITECTURE.md)
- [模型指南](LOCAL_MODELS.md)
- [路线图](ROADMAP.md)
- [开源版说明](OPEN_EDITION.md)

## 社区

- [Discussions](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/discussions)
- [Issue](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/issues)

如果它帮你省下了手动整理纪要的时间，请去仓库点个 ⭐ Star。

MIT License © 2026 JKinco
