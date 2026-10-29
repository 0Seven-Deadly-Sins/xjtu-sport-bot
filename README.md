# XJTU 西安交大研究生体育打卡脚本

## 简介

此脚本用于西安交通大学研究生体美劳体育打卡系统的签到与签退，支持本地运行和 GitHub Actions 定时运行。脚本会先签到，等待 31 分钟后尝试签退，并可选发送邮件通知。

## 环境配置

本地建议使用 Python 3.11，并安装以下依赖：

```bash
python -m pip install -r requirements.txt
```

账号信息通过环境变量读取，不要把学号、密码或邮箱授权码写入代码或提交到仓库。

| 环境变量 | 用途 | 必需 |
| --- | --- | --- |
| `SPORT_USER` | 学号 | 是 |
| `SPORT_PASSWORD` | 统一身份认证密码 | 是 |
| `SPORT_LONGITUDE` | 签到经度；默认 `108.654387` | 否 |
| `SPORT_LATITUDE` | 签到纬度；默认 `34.257229` | 否 |
| `SPORT_SEND_EMAIL` | 设为 `true` 启用邮件通知；默认关闭 | 否 |
| `SMTP_AUTH_CODE` | QQ 邮箱 SMTP 授权码 | 邮件通知启用时 |
| `EMAIL_SENDER` | 发件人邮箱 | 邮件通知启用时 |
| `EMAIL_RECEIVER` | 收件人邮箱 | 邮件通知启用时 |

## GitHub Actions 定时运行

工作流位于 `.github/workflows/sport-bot.yml`，默认每天北京时间 12:00 运行，也可以从 Actions 页面手动触发。GitHub 的计划任务偶尔会因负载延迟启动。

1. 打开仓库的 **Settings → Secrets and variables → Actions**。
2. 在 **Repository secrets** 中添加：
   - `SPORT_USER`
   - `SPORT_PASSWORD`
3. 如需邮件通知，再添加以下 secrets：
   - `SMTP_AUTH_CODE`
   - `EMAIL_SENDER`
   - `EMAIL_RECEIVER`
4. 如需更改默认坐标，在 **Variables** 中添加 `SPORT_LONGITUDE` 和 `SPORT_LATITUDE`。
5. 如需启用邮件通知，在 **Variables** 中添加 `SPORT_SEND_EMAIL`，值设为 `true`。
6. 打开 **Actions → XJTU Sport Bot → Run workflow** 可手动运行一次；之后定时任务会按计划运行。

工作流成功与否会反映在 Actions 运行结果中。不要在 issue、提交记录或聊天中发送账号密码和邮箱授权码。

## 本地运行

macOS / Linux：

```bash
export SPORT_USER="你的学号"
export SPORT_PASSWORD="你的密码"
python sport_bot.py
```

PowerShell：

```powershell
$env:SPORT_USER = "你的学号"
$env:SPORT_PASSWORD = "你的密码"
python .\sport_bot.py
```

运行日志写入 `sport_bot.log`，同时会输出到终端。

## 免责声明

请仅在符合学校规定及课程要求的场景中使用，并确保打卡记录与实际活动相符。使用此脚本所产生的任何后果由使用者自行承担。
