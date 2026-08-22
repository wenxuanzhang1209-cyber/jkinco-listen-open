# Support

*[中文版见下方](#支持与维护承诺)*

## Where to ask

| I want to… | Go to |
|---|---|
| Ask a question, share a setup, compare models | [Discussions](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/discussions) |
| Report a reproducible bug | [Open an issue](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/issues/new?template=bug_report.yml) |
| Propose a feature | [Open an issue](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/issues/new?template=feature_request.yml) |
| Report a security problem | [Security advisory](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/security/advisories/new) — see [SECURITY.md](SECURITY.md) |

Questions in English or Chinese are equally welcome.

## What the maintainer commits to

These are commitments, not aspirations. If one of them slips, opening an issue about it is
fair game.

- **First response to issues: within 5 working days.** A first response may be a question
  rather than a fix — but silence is not an acceptable outcome.
- **Security reports: acknowledged within 72 hours**, with an assessment of severity and a
  plan. Fixes for confirmed high-severity issues ship as a patch release.
- **Pull requests: reviewed within 7 working days.** If a change is going to be declined,
  you will be told why rather than left waiting.
- **Releases: as features land**, not on a fixed calendar. Every release gets a
  [CHANGELOG](CHANGELOG.md) entry and a tag.
- **`main` always passes CI.** The boundary scan, the full test suite, and the frontend
  build gate every merge.

## What is out of scope

Being honest about this saves everyone time:

- **Per-user deployment debugging** beyond a reproducible bug report. Include your OS, RAM,
  install method, and model, and the problem becomes something anyone can look at.
- **Model quality complaints without a reproduction.** "The minutes were bad" cannot be acted
  on; a transcript excerpt plus the model you ran can be.
- **Cloud features.** This edition runs locally by design. A feature that requires a hosted
  service does not belong here — see [docs/OPEN_EDITION.en.md](docs/OPEN_EDITION.en.md).
- **Languages other than Chinese**, for now. The ASR model and the domain lexicon are tuned
  for Mandarin meetings. Multi-language support is on the roadmap and contributions are
  welcome, but it is not currently supported.

## Supported versions

Security fixes land on `main` and in the next patch release. Because every deployment is
self-hosted, upgrading means pulling and rebuilding — there is no hosted service being
patched on your behalf.

---

# 支持与维护承诺

## 去哪儿问

| 我想… | 去这里 |
|---|---|
| 提问、分享部署方案、比较模型 | [Discussions](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/discussions) |
| 报告可复现的 bug | [提交 Issue](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/issues/new?template=bug_report.yml) |
| 提议新功能 | [提交 Issue](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/issues/new?template=feature_request.yml) |
| 报告安全问题 | [Security Advisory](https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open/security/advisories/new)，见 [SECURITY.md](SECURITY.md) |

中英文提问一视同仁。

## 维护者的承诺

这些是承诺，不是愿望。哪条没做到，欢迎开 Issue 指出来。

- **Issue 首次回应：5 个工作日内。** 首次回应可能是一个反问而不是修复——但沉默不算。
- **安全报告：72 小时内确认**，并给出严重程度判断与处理计划。确认的高危问题以补丁版本发布。
- **Pull Request：7 个工作日内评审。** 如果一个改动不会被接受，会告诉你原因，而不是让它挂着。
- **发布节奏：功能就绪即发**，不按日历。每个版本都有 [CHANGELOG](CHANGELOG.md) 条目和 tag。
- **`main` 分支永远是绿的。** 红线扫描、全部测试、前端构建三道关卡守着每一次合入。

## 不在支持范围内

把这个说清楚能省下双方的时间：

- **超出「可复现 bug 报告」的逐人部署排障。** 附上系统、内存、安装方式和所用模型，
  问题就变成了任何人都能看的东西。
- **没有复现材料的模型质量抱怨。**「纪要写得不好」无法处理；一段转写片段加上你用的模型可以。
- **云端功能。** 本版本按设计就是本地运行的。需要托管服务的功能不属于这里，
  见 [docs/OPEN_EDITION.md](docs/OPEN_EDITION.md)。
- **中文以外的语种**（目前）。ASR 模型与领域词库是针对普通话会议调的。多语种在路线图上，
  欢迎贡献，但现在还不支持。

## 支持的版本

安全修复进 `main` 并随下一个补丁版本发布。由于所有部署都是自托管的，升级意味着你自己
拉取并重新构建——没有一个托管服务会替你打补丁。
