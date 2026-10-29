# 发射中心（Launch Kit）

这里存放的是把 `jkinco-listen-open` 推向 1000 star/天量级的全部弹药。
所有文案都可以直接复制粘贴，占位符用 `[...]` 标出。

## 两条固定入口

每条公开内容都要让人一眼看懂下一步去哪里：

- **正式 App / 产品介绍：** <https://jkincolisten.cloud>
- **三端下载与产品动效：** <https://jkincolisten.cloud/download/ios>
- **开源本地版：** <https://github.com/wenxuanzhang1209-cyber/jkinco-listen-open>

正式 App 面向“现在就想用”的人；开源版面向“要自托管、扩展和审计”的人。两个入口同时出现，
但不要混写数据边界。

## 发射前检查单（D-3）

- [ ] 仓库 CI 全绿（工作区与 Git 历史密钥扫描 / 977 测试 / 前端构建 / Docker 镜像构建）
- [ ] Release v0.2.0 已发布且含实时会议截图
- [ ] 60 秒通用演示已上传 YouTube / Bilibili：实时字幕 → 结论/待办 → 导出
- [x] 仓库 Homepage 已设为正式产品演示与三端下载页
- [x] 正式 App 图标社交预览已上传：仓库 Settings → Social preview（`docs/social-preview.png`）
- [x] README 首屏的正式站、三端下载、筑听 Z1、开源版入口都点得通
- [ ] GitHub Discussions 已开启
- [ ] 已把仓库置顶到个人主页（Settings → Pinned）
- [ ] 提前联系 5–10 位 KOL，确认发射日转发

## 发射日动作（D-0，按北京时间）

| 时间 | 动作 | 物料 |
|---|---|---|
| 00:00 | GitHub Release + 微博/X 首发（附演示视频） | x-twitter.md |
| 00:30 | 掘金 / 知乎 / CSDN 文章 | china.md |
| 08:00 | Reddit 四连发（美东晚高峰前） | reddit.md |
| 16:00 | Show HN（美西 01:00，最佳窗口） | show-hn.md |
| 17:00 | Product Hunt 上线（美西 00:00 卡位） | product-hunt.md |
| 20:00 | 国内社群同步 + 当天所有评论/Issue 清零 | — |

## 目录

- [Show HN 文案](show-hn.md)
- [Product Hunt 文案](product-hunt.md)
- 产品动效：`docs/production-download-tour.gif`；分镜素材：
  `production-recording.gif`、`production-generation.gif`、`production-sona.gif`
- 硬件产品卡：`docs/jkinco-z1-product-card.png`
- [Reddit 文案](reddit.md)
- [掘金 / 知乎 / CSDN 文案](china.md)
- [X / 微博 / 即刻文案](x-twitter.md)
- [60 秒演示视频脚本](demo-video-script.md)
- [Awesome 列表投稿清单](awesome-lists.md)
