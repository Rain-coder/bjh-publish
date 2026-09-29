# 百家号视频自动化流水线（bjh）设计文档

日期：2026-09-29
状态：已获用户批准

## 1. 目标与约束

构建一个本地运行的百家号视频内容流水线：关键词 → 抓取百家号热门文章 → 按热度选题并用 DeepSeek 写原创口播文案 → 本地合成图文口播视频 → 随机间隔自动发布到百家号。提供 CLI 与 Web 面板两种使用方式，并沉淀一个 Claude Code skill 封装百家号算法经验。

**硬性约束：**

- 单条视频成本 ≤ 0.3 元（实测预期 ≈0.02~0.05 元：edge-tts 免费、Pexels 免费、FFmpeg 本地、DeepSeek ≈0.02 元/篇）
- 视频形态：图文口播（AI 配音 + 配图 + 硬字幕），1080×1920 竖屏，60~90 秒
- 核心优化目标：评论区互动（选题、文案结构、结尾提问均围绕此设计）
- 内容必须原创生成，不做洗稿/搬运；同话题去重避免撞原创判定
- 平台现实：百家号单账号每日发文有硬性上限，发布队列必须尊重可配置日上限（默认 8）；高频机器发文是平台风控对象，操作需随机化

**范围：**

- 前期单账号；数据模型与 Cookie 管理预留多账号（矩阵）扩展
- 首版不做数字人、不做付费 TTS、不做第三方数据平台接入

## 2. 已确认的技术决策

| 环节 | 决策 |
|---|---|
| 热文数据源 | Playwright + 登录 Cookie 抓百家号站内搜索/热榜，取阅读量、评论数 |
| 选题+文案模型 | DeepSeek（chat completions，httpx 直连） |
| TTS | edge-tts（免费，晓晓/云希音色，指数退避重试） |
| 视频合成 | FFmpeg subprocess 本地合成；Pexels 免费图库配图，Pillow 模板大字图兜底 |
| 发布 | Playwright 复用登录态自动上传发布，每步随机延迟 |
| 运行形态 | Python CLI（typer）+ FastAPI Web 面板（Alpine.js 静态页，无构建） |
| 调度 | 自研 asyncio daemon：随机间隔（默认 25~50 分钟）、黄金时段优先、日上限 |
| 算法经验沉淀 | `skills/bjh-video/SKILL.md` 为规则唯一来源，流水线运行时读取其规则段渲染 system prompt |

## 3. 架构（方案 A：模块化 Python 单体）

```
bjh/
├── pyproject.toml
├── bjh/
│   ├── cli.py              # typer 子命令：login/fetch/create/publish/run/web
│   ├── config.py           # config.toml 加载（间隔、日上限、API key）
│   ├── db.py               # SQLite schema + 访问层
│   ├── models.py           # dataclass：Account/Topic/Draft/Video/Post
│   ├── fetcher/baijiahao.py
│   ├── writer/
│   │   ├── topic.py        # 热度打分选题
│   │   ├── deepseek.py     # API 客户端（重试/超时）
│   │   └── prompts.py      # 渲染 SKILL.md 规则段 → system prompt
│   ├── studio/
│   │   ├── tts.py          # edge-tts
│   │   ├── images.py       # Pexels 检索 + Pillow 兜底图
│   │   ├── subtitle.py     # 按句切分 → SRT/ASS 时间轴
│   │   └── compose.py      # FFmpeg 合成
│   ├── publisher/baijiahao.py
│   ├── scheduler.py        # 发布队列 daemon
│   ├── risk.py             # 日上限、随机化、敏感词过滤
│   └── server/             # FastAPI + static/（Alpine.js 面板）
├── skills/bjh-video/SKILL.md
├── tests/
└── data/                   # bjh.db、videos/、screenshots/
```

CLI 命令：

- `bjh login [--account NAME]`：打开浏览器扫码登录，storage_state 存 Cookie
- `bjh fetch --keyword KW [--pages N]`：抓热文入库
- `bjh create [--count N]`：选题→文案→合成视频（可指定 topic id）
- `bjh publish [--now | --daemon]`：发布队列（单发 / daemon 模式）；`--dry-run` 到提交前一步
- `bjh run --keyword KW`：fetch→create→publish 一键全流程
- `bjh web`：启动面板（默认 127.0.0.1:8787）

## 4. 数据流与数据模型

```
关键词 → fetcher(topics) → 热度打分 → DeepSeek 口播稿(drafts)
      → edge-tts + 字幕 + 配图 → FFmpeg(videos)
      → 发布队列(posts) → 随机间隔 Playwright 发布 → 回填百家号链接
```

SQLite 表：

- `accounts(id, name, storage_state_path, daily_limit, published_today, last_publish_at, status[active/expired])`
- `topics(id, keyword, title, url, source_author, reads, comments, heat_score, fetched_at, status[new/selected/skipped])`
- `drafts(id, topic_id, title, script, tags, hook, model, cost, status[ok/blocked/failed], created_at)`
- `videos(id, draft_id, path, duration_sec, size_bytes, cost, status[ok/failed], error, created_at)`
- `posts(id, video_id, account_id, status[queued/publishing/published/failed], scheduled_at, published_at, bjh_url, error, screenshot_path)`

**热度打分**：`heat = ln(reads+1) + 2.2 × ln(comments+1)`。评论权重加倍以对齐互动目标；同话题标题相似度去重（简单分词 Jaccard），防撞原创。

## 5. 模块设计要点

### fetcher
- storage_state 登录态；抓关键词搜索结果列表 + 逐篇打开取阅读/评论数
- Cookie 失效检测 → 账号标记 `expired`，面板红字提醒重扫码

### writer
- `topic.py`：按 heat 排序、去重、过滤已用选题
- `prompts.py`：解析 SKILL.md 规则段 → system prompt；user prompt 含热门文章标题+数据+评论区高赞视角
- 产出 JSON：`{titles:[3], title, script, tags, hook, closing_question}`；标题择优规则在 SKILL.md
- 敏感词本地词表过滤，命中 → 换角度重写一次 → 再命中跳过选题（draft.status=blocked）

### studio
- tts：edge-tts，输出 mp3 + 字级时间戳（word boundary）供字幕对齐
- subtitle：按句切分（。！？…），每句 ≤15 字换行，生成 ASS 硬字幕样式（大字、描边、居中偏下）
- images：优先 Pexels API（免费 Key）按文案关键词取图；失败/无 Key → Pillow 生成渐变底+大字标题模板图
- compose：FFmpeg —— 图片 Ken Burns 缓放 + mp3 + ASS 字幕烧录 → 1080×1920 H.264 MP4；逐段估算但以 mp3 实际时长为准

### publisher
- 流程：创作平台 → 视频发布页 → 上传文件 → 等待转码 → 填标题/封面/分类 → 提交
- 每步随机 2~6s；失败截图 + 存 HTML 到 data/screenshots/
- `--dry-run`：走到提交前一步停，截图确认

### scheduler
- asyncio 循环：到点取 `queued` → 查账号日上限 → 发布 → 回填
- 间隔：配置范围内随机（默认 25~50 分钟）；黄金时段（7-9/12-14/18-22）优先调度
- `published_today` 按自然日重置

### risk
- 日上限校验、间隔随机化、敏感词表、发布前标题/文案最终检查

### server（Web 面板）
- 页面：热榜（关键词检索+热度排序）、草稿/视频（预览、改标题、重生成）、发布队列（状态、失败截图）、配置（间隔/日上限/账号）、统计（今日产量、累计成本）
- FastAPI 只做薄 API 层，复用同一套 service 函数；前端 Alpine.js + Tailwind CDN 单页

## 6. 百家号算法 skill（skills/bjh-video/SKILL.md）

规则唯一来源，两个消费方：Claude Code 用户手动调用（半自动精细模式：人审每步）；流水线 prompts.py 运行时读取规则段渲染 system prompt。内容五块：

1. **选题**：评论比>1% 优先；争议性/身份代入感话题；热点时效窗口；避同质化
2. **标题**：悬念/数字/身份代入三模板，≤28 字，不含夸张违禁词
3. **文案结构**：3 秒钩子 → 冲突递进 → 口语化短句 → 结尾开放式提问（驱动评论）
4. **视频规范**：9:16 竖屏、60~90s、大字硬字幕、封面三要素（大字标题/人物或主体/对比色）、前 3 秒信息点前置
5. **发布节奏**：黄金时段、频率上限、避坑（同质化检测、搬运判定、敏感词、机器发文特征）

SKILL.md 结构：`<rules>` 机器可读规则段（供 prompts.py 提取）+ 面向 Claude 的操作说明（如何调用 bjh CLI）。

## 7. 错误处理

| 风险 | 对策 |
|---|---|
| 单账号高频风控 | 日上限默认 8 + 随机间隔 + 步间随机延迟 |
| Cookie 失效 | 账号 expired → 抓取/发布暂停 + 面板提醒 |
| 页面选择器变更 | 失败自动截图+存 HTML，错误指明卡在哪步 |
| edge-tts 限流 | 指数退避重试 3 次 |
| DeepSeek 超时/拦截 | 超时重试 2 次；内容拦截换角度重写 1 次，再失败跳过 |
| FFmpeg 失败 | 保留中间产物，video.status=failed 不入队 |
| 发布失败 | 截图入库，post=failed，不阻塞后续队列 |

## 8. 测试策略

- 单元：热度打分、标题相似度去重、字幕切分/时间轴、敏感词过滤、prompt 渲染、db 层
- 集成：studio 真实 FFmpeg 合成 5s 样片（CI 可跑）；DeepSeek mock
- 真账号冒烟（手动执行，不进自动测试）：`bjh fetch` 抓 1 页、`bjh publish --dry-run`、真实发布 1 条

## 9. 里程碑（供实现计划参考）

1. 骨架：pyproject、db、models、config、cli 空壳
2. fetcher：login + 抓取 + 热度打分
3. writer：SKILL.md 规则 + prompts + DeepSeek 客户端 + 敏感词
4. studio：tts → subtitle → images → compose 全链路出片
5. publisher + scheduler：dry-run → 真发 1 条 → daemon
6. web 面板
7. 端到端 `bjh run` + skill 手动模式打通
