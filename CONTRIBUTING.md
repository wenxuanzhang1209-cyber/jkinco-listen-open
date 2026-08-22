# Contributing

*[中文版见下方](#贡献指南)*

Contributions of every kind are welcome: issues, documentation, translations, code, model
evaluations, and domain vocabulary for new industries.

## Before you open a pull request

```bash
python scripts/check_open_source_hygiene.py   # boundary scan (must pass)
python -m pytest tests-v2 -q                  # full test suite
cd frontend && npm run build                  # frontend must build
```

## Workflow

1. Fork the repository.
2. Create a branch: `feat/xxx` or `fix/xxx`.
3. Write commit messages in English or Chinese — either is fine. Explain **why** the change
   is needed, not just what changed.
4. Open a pull request. It merges once CI is green.

## Hard boundaries

These are enforced by CI, not just by convention:

- No secrets, tokens, or certificates in the repository.
- No traces of cloud model services (see [SECURITY.md](SECURITY.md)).
- Never commit `.env`.

## Good first issues

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for candidates: local live captioning, desktop
packaging, and evaluations of additional local models. Translations of the documentation are
also genuinely useful — the project started in Chinese and the English side is younger.

## What makes a change easy to accept

The codebase favors comments that explain **why** a piece of code exists, especially where the
obvious implementation would be wrong. If your change fixes a subtle bug, a short note about
what went wrong is worth more than a long description of the fix.

---

# 贡献指南

欢迎任何形式的贡献：Issue、文档、翻译、代码、模型评测、场景词库。

## 提交前

```bash
python scripts/check_open_source_hygiene.py   # 红线扫描（必过）
python -m pytest tests-v2 -q                  # 全部测试通过
cd frontend && npm run build                  # 前端可构建
```

## 开发流程

1. Fork 本仓库；
2. 新建分支：`feat/xxx` 或 `fix/xxx`；
3. 提交信息用中文或英文皆可，说明**「为什么改」**而不只是「改了什么」；
4. 发起 Pull Request，CI 全绿后合入。

## 红线

以下由 CI 强制执行，不是约定俗成：

- 不提交任何密钥、Token、证书；
- 不提交任何云端模型痕迹（见 [SECURITY.md](SECURITY.md)）；
- 不把 `.env` 加入版本库。

## 好上手的坑

看 [`docs/ROADMAP.md`](docs/ROADMAP.md) 里的候选：本地实时字幕、桌面打包、
更多本地模型评测。文档翻译同样很有价值——这个项目从中文起步，英文那一侧更年轻。

## 什么样的改动容易被接受

这个代码库偏好解释**「为什么」**的注释，尤其是在「看起来显然的写法其实是错的」那些地方。
如果你的改动修了一个隐蔽的 bug，一句「原先错在哪」比长篇描述修法更有价值。
