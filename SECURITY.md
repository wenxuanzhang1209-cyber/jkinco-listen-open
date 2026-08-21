# 安全策略

## 数据边界

- 本项目的设计目标是 **100% 本地运行**：音频、转写、纪要、历史库不出本机；
- 不要求任何 API Key；未配置钉钉 / LiveKit 时对应功能自动关闭；
- 模型下载完成后，可以完全断网使用。

## 红线

本仓库严禁出现：

- 任何云端模型服务名称、域名、环境变量；
- 任何 API Key、Token、私钥、证书；
- 生产内网域名 / IP / 备案信息。

`scripts/check_open_source_hygiene.py` 在 CI 中强制执行以上红线，
提交前请本地运行一次：

```bash
python scripts/check_open_source_hygiene.py
```

## 报告漏洞

请勿在公开 Issue 中提交密钥或敏感录音。发现安全问题请通过 GitHub
Security Advisory 或邮件联系维护者，我们会尽快响应。
