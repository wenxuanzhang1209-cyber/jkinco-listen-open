# 增长作战手册：向千星日发起冲击

> 目标不是“慢慢涨”，而是把项目打磨到**顶级开源项目的水准**，
> 再用一次高密度发射制造速度。GitHub Trending 不看绝对 star 数，
> 看的是**单位时间速度** —— 这是 1000 star/天这类爆发的物理基础。

## 1. 顶级项目对照清单（已对齐）

| 维度 | 顶级项目做法 | 本仓库现状 |
|---|---|---|
| README 即落地页 | Demo 优先、15 秒讲清价值、徽章、架构图 | ✅ 双语文案 + 截图 + 架构图 + Star History |
| 开箱即用 | 一条命令跑起来 | ✅ `docker compose up` |
| 可信度 | CI 全绿、测试数量、许可证、安全声明 | ✅ 891 测试 + 红线扫描 + MIT |
| 持续迭代 | Roadmap、Issue 模板、Contributing | ✅ 文档齐备 |
| 差异化叙事 | 一个让人记住的“为什么” | ✅ “工程监理级中文纪要，数据不出门” |

## 2. 定位一句话

> **本地方案里的“工程会议纪要工具”**：FunASR + Ollama，中文工程场景，
> 原版式 Word/PDF，零云零密钥。

这条定位同时踩中三个大池子：

- self-hosted / privacy（faster-whisper、whisper.cpp 用户群）；
- Chinese NLP / FunASR 生态（ModelScope 开发者）；
- AI 办公效率（会议纪要工具，天然适合短视频传播）。

## 3. 发射计划（D-7 → D+90）

> 所有文案、分镜、投稿清单已就绪，见 [docs/launch/](launch/README.md)，
> 可直接复制使用。

### D-7：蓄水

- [ ] 发布 `v0.1.0` Release，写清变更与截图；
- [ ] 录制 60 秒演示视频（录音→纪要→导出），中英双语字幕；
- [ ] 写 3 篇技术文章：掘金 / 知乎 / CSDN（“怎么把会议纪要全放本地”）；
- [ ] 预热社群：微信 / 知识星球 / Discord / 本地 LLM 群；
- [ ] 联系 5–10 位中文技术 KOL（异步，发体验链接）。

### D-0：同一天高密度发射（Stacked Launch）

- [ ] 00:00 UTC+8：GitHub Release + 置顶 tweet/微博（演示视频优先）；
- [ ] Show HN（标题带“engineering supervision meeting minutes, 100% local”）；
- [ ] Reddit：`r/selfhosted`、`r/LocalLLaMA`、`r/opensource`、`r/ChineseLanguage`；
- [ ] Product Hunt（美西 0 点对应北京时间下午，卡位 TOP 5）；
- [ ] 掘金 / 知乎 / V2EX / 即刻同步发布；
- [ ] 12 小时内回复所有评论、issues、PR，**互动率是算法分发的燃料**。

### D+1 ~ D+14：速度放大器

- [ ] 每天 1 条 build-in-public 进展（修 issue、加功能、晒 star 曲线）；
- [ ] 提交 awesome 列表：`awesome-selfhosted`、`awesome-llm`、
      `awesome-chinese-nlp`、`selfh.st`、`osalt.dev`；
- [ ] 发布 3 个“换皮”演示：工地例会 / 面试 / 个人复盘，各 30 秒；
- [ ] 邀请 3–5 位社区用户写评测并联动转发。

### D+15 ~ D+90：复利

- [ ] 每周一个功能迭代（先做实时字幕 / 说话人分离，戳中刚需）；
- [ ] 每月一份“被 Star 的项目”复盘：哪些渠道转化最高；
- [ ] 冲 GitHub Trending（中/美区语言榜）；
- [ ] 目标里程碑：D+30 达 1k，D+90 达 5k–10k。

## 4. 诚实说明

1000 star/天是**爆款量级**，决定权在外部传播，不在代码本身。
我能承诺的是：把代码、仓库、演示、发布动作全部做到顶级水准，
并让每一次曝光都具备“高转化”的钩子。爆款需要一次运气，
但**长期复利是设计出来的**：内容不断、迭代不断、社区不断，
10k star 是执行结果，不是碰运气。

## 5. 每日执行清单

- [ ] 回复所有新增 issue / PR / 评论（当日清零）；
- [ ] 检查 star 曲线，记录当日来源（渠道归因）；
- [ ] 输出 1 条公开进展（视频 / 文章 / 推文）；
- [ ] 跑一次红线扫描 + 测试，保持 CI 全绿。
