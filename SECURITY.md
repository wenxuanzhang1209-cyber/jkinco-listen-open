# Security Policy

*[中文版见下方](#安全策略)*

## Data boundary

This project is designed to run **entirely on your own machine**:

- Audio, transcripts, minutes, and the history database never leave the local host.
- No API key is required. DingTalk and LiveKit integrations disable themselves automatically
  when unconfigured.
- Once the models are downloaded, the whole system works with networking switched off.

## Repository boundaries

The following must never appear in this repository:

- Any cloud model service name, domain, or environment variable.
- Any API key, token, private key, or certificate.
- Any production hostname, internal IP, or filing/registration information.

`scripts/check_open_source_hygiene.py` enforces this in CI. Run it locally before you push:

```bash
python scripts/check_open_source_hygiene.py
```

## Deployment note

`.env.example` ships with `JKINCO_AUTH=admin:123456` so that a local trial works immediately.
The server listens on `0.0.0.0`, so **anyone who can reach the port can sign in with those
credentials**. Change `JKINCO_AUTH` before binding the service to anything other than
localhost. This is the one configuration mistake most likely to matter in practice.

## Reporting a vulnerability

Please do **not** post secrets or sensitive recordings in a public issue.

Report security problems through
[GitHub Security Advisories](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/security/advisories/new)
or by contacting the maintainer directly. We aim to acknowledge reports quickly and will
credit reporters unless they prefer otherwise.

## Supported versions

The `main` branch receives security fixes. Because deployments are self-hosted and local,
upgrading means pulling and rebuilding — there is no hosted service to patch on your behalf.

---

# 安全策略

## 数据边界

本项目的设计目标是 **100% 本地运行**：

- 音频、转写、纪要、历史库不出本机；
- 不要求任何 API Key；未配置钉钉 / LiveKit 时对应功能自动关闭；
- 模型下载完成后，可以完全断网使用。

## 仓库红线

本仓库严禁出现：

- 任何云端模型服务名称、域名、环境变量；
- 任何 API Key、Token、私钥、证书；
- 生产内网域名 / IP / 备案信息。

`scripts/check_open_source_hygiene.py` 在 CI 中强制执行以上红线，提交前请本地运行一次：

```bash
python scripts/check_open_source_hygiene.py
```

## 部署提醒

`.env.example` 里预置了 `JKINCO_AUTH=admin:123456`，是为了让本地试用能立刻跑起来。
服务监听的是 `0.0.0.0`——**只要能访问到端口的人，都能用这组默认口令登录**。
除 localhost 之外的任何绑定，请先改掉 `JKINCO_AUTH`。这是实际部署中最容易出事的一处。

## 报告漏洞

请**不要**在公开 Issue 中提交密钥或敏感录音。

请通过
[GitHub Security Advisory](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/security/advisories/new)
或直接联系维护者报告安全问题。我们会尽快响应，并在报告者不反对的前提下致谢。

## 支持的版本

安全修复只进 `main` 分支。由于部署都是自托管的本地实例，升级意味着你自己拉取并重新构建——
没有一个托管服务会替你打补丁。
