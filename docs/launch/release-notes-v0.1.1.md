## JKinco Listen Open Edition v0.1.1

在 v0.1.0 基础上新增：

### 🎉 新功能

- **示例数据模式**：`JKINCO_DEMO_DATA=1`，不下载模型也能体验完整界面、历史纪要、导出与检索
- **实验性本地实时字幕**：`JKINCO_REALTIME_LOCAL_ASR=1`，会议/录音面板走本机
  `paraformer-zh-streaming`，数据依旧不出门
- **一键安装脚本**：`bash scripts/install.sh && bash scripts/start.sh`（非 Docker 路径）
- **文档站**：https://wenxuanzhang1209-cyber.github.io/jkinco-listen-open/

### 🚀 工程化

- 测试从 888 增至 894，CI 全绿（红线扫描 / 后端测试 / 前端构建 / Docker 镜像构建）
- GitHub Discussions 社区上线：公告 / 投票 / 晒单
- Star 里程碑自动庆祝工作流

### ⚡ 快速开始

```bash
docker compose up -d --build
```

访问 http://localhost:8080（admin / 123456）
