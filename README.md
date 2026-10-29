# XJTU 体育签到脚本

使用学校当前的 CAS 登录页面获取体育平台令牌，再执行签到及 31 分钟后的签退。认证由 Playwright Chromium 操作官方网页完成，页面自身处理 RSA 加密、动态表单参数、设备指纹与安全检查，不再依赖旧版 `route` 等 Cookie。

## GitHub Actions 配置

在 **Settings → Secrets and variables → Actions → Secrets** 中添加两个 Repository secrets：

| Name | Secret 值 |
| --- | --- |
| `SPORT_USER` | 学号 / 统一身份认证账号 |
| `SPORT_PASSWORD` | 统一身份认证密码原文，不加额外引号、不提前加密 |

打开 **Actions → XJTU Sport Bot → Run workflow**，选择 `main` 和运行模式：

- `verify-login`：只验证 CAS 登录与体育平台令牌交换，不提交签到或签退。这是手动运行的默认模式。
- `run`：执行签到，等待 31 分钟后尝试签退。

定时任务保持每天北京时间 12:00 执行 `run`。GitHub 的定时调度可能延迟。

新增的 **Verify CAS login (no attendance)** 工作流用于验证适配：先运行离线单元测试，再执行一次 `--auth-only` 登录。它不会执行体育签到或签退。**Auth entry diagnostics (no login)** 则只检查公开登录入口，不会发送账号密码。

## 可选参数

在 **Variables** 标签中配置：

| Name | 默认值 / 用途 |
| --- | --- |
| `SPORT_LONGITUDE` | `108.654387`，经度 |
| `SPORT_LATITUDE` | `34.257229`，纬度 |
| `SPORT_SEND_EMAIL` | 默认 `false`；设为 `true` 启用邮件通知 |

启用邮件通知时，还需要在 **Secrets** 中添加 `SMTP_AUTH_CODE`、`EMAIL_SENDER`、`EMAIL_RECEIVER`。SMTP 使用 QQ 邮箱 SSL 服务及邮箱授权码。

## 本地使用

建议使用 Python 3.11：

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
```

PowerShell 中设置当前会话的环境变量后，可先验证登录：

```powershell
$env:SPORT_USER = "你的学号"
$env:SPORT_PASSWORD = "你的密码"
python sport_bot.py --auth-only
```

执行完整流程：

```bash
python sport_bot.py
```

本地调试可加 `--headed` 显示浏览器窗口。日志输出到终端和 `sport_bot.log`，不会记录密码、Cookie 值或登录令牌。

## 失败状态

- 退出码 `0`：本次所选模式执行成功。`--auth-only` 成功不代表已经打卡。
- 退出码 `1`：配置、登录、网络或业务接口失败，按日志中的具体阶段排查。
- 退出码 `2`：学校要求本次登录完成验证码或 MFA。程序停止并明确提示，不会伪造验证结果或反复尝试密码。

脚本只提交一次登录操作，不会自动重试失败的签到或签退。签到成功后若签退失败，需要及时在官方平台处理。

离线单元测试：

```bash
python -m unittest discover -s tests -v
```

## 使用说明

请在符合学校规定和课程要求的场景中使用，确保记录与实际活动相符。账号、密码、邮箱授权码及浏览器会话信息均不应提交到仓库。
