# 开源版说明

> 想直接使用成熟移动端产品？
> [打开筑听](https://jkincolisten.cloud) ·
> [iOS / Android / 鸿蒙三端下载](https://jkincolisten.cloud/download/ios)

本仓库是筑听的**开源本地版（Open Edition）**，定位是：

- 100% 本地运行，无需任何 API Key；
- 实时会议 / 录音 → 本地转写 → 场景识别 → 纪要 → 导出完整闭环；
- 模型、模板、历史数据全部属于使用者自己。

## 边界

- 本仓库只包含开源本地版；
- 开源版内置自托管 LiveKit 实时会议与本地流式字幕，不依赖云端 ASR
  或托管会议服务；
- 与任何商业/内部版本相互独立，互不共享代码与配置；
- 本仓库的 CI 红线扫描保证云端模型痕迹、密钥、生产信息永不进入开源仓库。

## 想参与？

看 [CONTRIBUTING.md](../CONTRIBUTING.md) 与 [ROADMAP.md](ROADMAP.md)。
