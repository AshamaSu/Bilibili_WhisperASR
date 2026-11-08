# 发布交接（由维护者本人执行）

> 依据 `~/apps/CODEBUDDY.md` §3：AI 会话无 sudo，特权操作（`/etc/`、系统包、属主与权限变更）
> 写入本文件，交维护者用 `ubuntu` 执行。
> 依据 `README.md:125-126`：构建（含重建 `dist/`）由开发方完成；**发布由维护者本人执行**——
> rsync、切软链、拷配置、`systemctl reload nginx`、线上验证都不在构建范围里，构建完只报告产物与校验结果。
> `conn/` 内含 SSH 凭据且已 gitignore，切勿提交；本文件不含任何凭据值，`user@host` 为占位符。

## 一、本次产物（2026-11-08 已生成并校验）

| 季 | 目录 | 复习站 | 精听页 | 字幕 json | 音频 m4a | 总大小 | 校验 |
|---|---|---|---|---|---|---|---|
| season1 | `dist/server/BusinessEnglishCourse-Season1/` | 1.28 MB | 48 KB | 67 | 67（803 MB） | 809M | `--verify` 通过 |
| season2 | `dist/server/BusinessEnglishCourse-Season2/` | 1.33 MB | 50 KB | 81 | 81（1055 MB） | 1.1G | `--verify` 通过 |

- 本轮改动：对话练习提示词、复制全部自测、周末精听提示卡（仅 `webgen/template.html` 及重生成的 `output/*/review_site/index.html`）；`selfcheck.py` 两季各 17/17 通过。
- `dist/` 为 gitignore，不入库。
- 幂等重建命令：
  ```
  python3 webgen/build_server.py --season season1
  python3 webgen/build_server.py --season season2
  ```

## 二、当前构建形态（发布前先确认）

本次**未加 `--base-path`**，每季 `nginx.conf` 各自是独立 `server{}`：
`listen 80; server_name _; root /var/www/whisper/<SeasonKey>;`。
两份都监听 80 且 `server_name _`，**同一 IP 上不能同时启用**。

若两季要挂同一台机器的 80 端口，须先按 `README.md §6` 用子路径模式重建（重建后每季多出 `nginx-locations.conf`，nginx.conf 变为两季共用骨架，只保留一份）：
```
python3 webgen/build_server.py --season season1 --base-path /BusinessEnglishCourse-Season1
python3 webgen/build_server.py --season season2 --base-path /BusinessEnglishCourse-Season2
```

## 三、发布命令

### 形态 A：单季独立部署（当前 dist 可直接用）

```bash
STAMP=$(date +%Y%m%d-%H%M)
S=BusinessEnglishCourse-Season1
# ① 传到新目录，不要直接覆盖线上目录
rsync -av --delete dist/server/$S/ user@host:/var/www/whisper/releases/$STAMP/
# ② 切软链（原子替换，失败可秒回滚）
ssh user@host "ln -sfn /var/www/whisper/releases/$STAMP /var/www/whisper/current"
# ③ 首次部署上传配置，并确认 root 指向 /var/www/whisper/current
scp dist/server/$S/nginx.conf user@host:/etc/nginx/conf.d/whisper.conf
# ④ 校验并重载（特权步骤，需 ubuntu）
ssh user@host 'nginx -t && systemctl reload nginx'
```

> 注意：当前 `nginx.conf` 的 root 是 `/var/www/whisper/BusinessEnglishCourse-Season1`，与上例 `current` 软链不一致。
> 二选一：把 root 改成 `/var/www/whisper/current`，或按 root 直接 rsync 到 `/var/www/whisper/BusinessEnglishCourse-Season1/`。

### 形态 B：一机两季子路径（需先按 §二 重建）

```bash
for S in BusinessEnglishCourse-Season1 BusinessEnglishCourse-Season2; do
  rsync -av --delete dist/server/$S/ user@host:/var/www/whisper/$S/
done
scp dist/server/BusinessEnglishCourse-Season1/nginx.conf user@host:/etc/nginx/conf.d/whisper.conf
scp dist/server/BusinessEnglishCourse-Season1/nginx-locations.conf user@host:/etc/nginx/whisper/BusinessEnglishCourse-Season1.locations
scp dist/server/BusinessEnglishCourse-Season2/nginx-locations.conf user@host:/etc/nginx/whisper/BusinessEnglishCourse-Season2.locations
ssh user@host 'mkdir -p /etc/nginx/whisper && nginx -t && systemctl reload nginx'
```

## 四、线上验证

```bash
curl -I http://<IP>/                 # 200, gzip, Cache-Control: no-cache
curl -I http://<IP>/listen/          # 200
curl -I http://<IP>/listen/audio/01_Howtoexplainaprojectyouworkedon.m4a
# 期望 audio/mp4、Accept-Ranges: bytes、Cache-Control: immutable；Range 请求返回 206
```

浏览器里再实测三个新功能：自测页填答案 →「复制全部自测」粘贴确认含已填答案（`file://` 下走 `execCommand` 兜底）；
「复制对话提示词」→ 粘贴给大模型；「完成本集」→ 今日页出现对话练习卡片，周末出现「本周建议精听」卡。

## 五、风险提示

- `rsync -av --delete` 会删除远端目标目录里多余的文件，执行前务必核对 `$STAMP` / `$S` 路径。
- `full` 模式含课程原文例句，只放私有服务器；已设 `X-Robots-Tag: noindex` + `robots.txt`，请勿公开分享链接。
- 音频 URL 带 `?v=<日期>`，重新转码后必须重跑 `build_server.py` 换版本号，否则浏览器一直用旧文件。
- HTTP → HTTPS 会改变 origin，浏览器 localStorage 里的学习进度会全部失效。
