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
3. 提交信息用中文或英文皆可，说明“为什么改”；
4. 发起 Pull Request，CI 全绿后合入。

## 红线

- 不提交任何密钥、Token、证书；
- 不提交任何云端模型痕迹（见 `SECURITY.md`）；
- 不把 `.env` 加入版本库。

## 好用的首坑

看 `docs/ROADMAP.md` 里 `good first issue` 候选：本地实时字幕、桌面打包、
更多语言模型评测。
