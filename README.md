# bjh — 百家号视频自动化流水线

关键词 → 抓百家号热文（阅读/评论） → DeepSeek 原创口播文案 → edge-tts 配音 + FFmpeg 合成竖屏视频 → 随机间隔自动发布。单条现金成本 ≈0.02~0.05 元（仅 DeepSeek）。

## 安装

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium
brew install ffmpeg-full      # macOS 必须用 ffmpeg-full（或自带 libass 的构建）
cp config.example.toml config.toml   # 填 deepseek_api_key（https://platform.deepseek.com）
```

> **FFmpeg 坑**：brew 默认的 ffmpeg（slim）不带 libass，`subtitles` 滤镜不可用，字幕会失败。必须 `brew install ffmpeg-full`，Linux 下需安装带 libass 的 ffmpeg 包。

密钥也可以用环境变量：`BJH_DEEPSEEK_API_KEY`、`BJH_PEXELS_API_KEY`（Pexels 可选，无 Key 时用本地模板图）。

## 使用

```bash
bjh login                                    # 打开非无头浏览器，扫码/登录后在终端按 Enter 确认（Cookie 存 data/accounts/default.json）
bjh fetch "职场焦虑"                        # 抓热文入库
bjh create --count 1                         # 生成文案+视频
bjh publish --dry-run                        # 发布演练（截图确认，不真发）
bjh publish --now                            # 立即真发 1 条
bjh publish --daemon                         # 常驻调度（日上限+随机间隔+黄金时段）
bjh run "职场焦虑" --count 3                 # 一键全流程（抓→做→入队）
bjh web                                      # 管理面板 http://127.0.0.1:8787
```

## 真机端到端冒烟（首次部署建议顺序）

```bash
bjh login
bjh fetch "职场焦虑" --pages 1               # 确认抓到热文
bjh create --count 1                         # 确认 data/videos/draft_N/N.mp4 可播放、字幕正确
bjh web                                      # 面板核对
bjh publish --dry-run                        # data/screenshots/dryrun_*.png 核对表单已填好
bjh publish --now                            # 真发 1 条，面板确认 posts.published + bjh_url
```

## Claude Code 半自动模式

skills/bjh-video/SKILL.md 封装了选题/标题/文案/视频/发布节奏规则；在 Claude Code 里可逐步驱动上述 CLI 并人工审查每步产出。

## 风控注意

- 百家号单账号有日发文上限：daily_limit 默认 8（面板可调）；平台对高频机器发文有风控，间隔默认 25~50 分钟随机，发布集中在黄金时段（7-9 / 12-14 / 18-22 点）
- Cookie 失效在面板"设置"页红字提示，重新 `bjh login` 即可
- 内容必须原创；敏感词命中会自动重写/拦截（drafts.status=blocked）

## 成本

| 项 | 成本 |
|---|---|
| edge-tts 配音 | 0 |
| Pexels 配图（无 Key 用模板图） | 0 |
| FFmpeg 合成 | 0（本地） |
| DeepSeek 文案 | ≈0.02 元/条 |

合计 ≈0.02~0.05 元/条，远低于 0.3 元上限。

## 开发

```bash
.venv/bin/python -m pytest -v   # 全量回归
```
