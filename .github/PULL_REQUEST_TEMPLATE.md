<!-- English or Chinese are both fine. 中英文皆可。 -->

## What does this change, and why?
## 改了什么，为什么改？

<!--
Explain the problem first, then the fix. If this fixes a subtle bug, a sentence about what
went wrong is worth more than a long description of the fix.
先说问题，再说修法。如果修的是一个隐蔽的 bug，一句「原先错在哪」比长篇描述修法更有价值。
-->

Closes #

## How did you verify it?
## 你是怎么验证的？

<!-- Which test did you add or run? What did you observe before and after? -->
<!-- 加了/跑了哪个测试？改动前后你分别看到了什么？ -->

## Checklist / 自查

- [ ] `python scripts/check_open_source_hygiene.py` passes / 红线扫描通过
- [ ] `python -m pytest tests-v2 -q` passes / 全部测试通过
- [ ] `cd frontend && npm run build` succeeds / 前端可构建
- [ ] No secrets, tokens, or `.env` in the diff / 改动里没有密钥、Token 或 `.env`
- [ ] This change runs 100% locally and adds no cloud dependency / 该改动 100% 本地运行，不引入云端依赖
- [ ] Documentation updated if behavior changed / 行为变了的话，文档也更新了
