# XJTU 体育签到脚本

支持现有 token 和官方 CAS 浏览器续登。体育平台 token 是短期凭据；只复制一次 token 并不能保证每日自动运行。旧 token 返回 401 后，脚本会通过官方 CAS 获取新 token，再用只读接口确认有效。

## GitHub 云端自动续登

1. 保留 `SPORT_USER` 和 `SPORT_PASSWORD` Secrets。
2. 首次在学校官方页面完成本人验证，选择学校提供的“可信客户端”。可使用已有 Edge，无需另下载 Chromium：

   ```powershell
   python -m pip install -r requirements-browser.txt
   python setup_login.py --browser-channel msedge
   ```

3. 将生成的会话保存到 `SPORT_BROWSER_STATE` Secret。由配置程序代为处理时，可以加 `--no-copy-ui`，直接从 `.local/auth-state.json` 上传 Secret，无需手动复制。
4. 手动运行 `Verify CAS login (no attendance)`，确认云端成功取得并校验新 token；本地登录成功不等于云端已经接受会话。

云端会恢复上次成功登录后的会话，取得新 token，并加密保存更新后的 Cookie 和浏览器存储，供下一次任务使用。缓存使用 AES-GCM 加密，密钥从 `SPORT_BROWSER_STATE` Secret 派生；缓存不包含明文会话。更换这个 Secret 会同时切换缓存密钥。

只有登录成功后才更新会话缓存。403、网络错误、格式错误不会触发反复密码登录。学校仍可能因登录环境变化、会话撤销或 MFA 策略要求本人再次验证；脚本会停止并明确提示，不能保证学校永久接受同一个会话。

## 最简单的配置：SPORT_TOKEN

1. 在浏览器中打开 https://ipahw.xjtu.edu.cn/ ，正常登录。
2. 按 F12 打开开发者工具，进入 **应用（Application）→ 本地存储（Local Storage）→ https://ipahw.xjtu.edu.cn**。
3. 找到 `token`（部分页面版本叫 `_token`），复制它的完整 Value（值），不要只复制解析后的某一个片段。如果显示为 JSON，也可以原样复制。
4. 在仓库 **Settings → Secrets and variables → Actions → Secrets → New repository secret** 添加：
   - **Name**：`SPORT_TOKEN`
   - **Secret**：刚复制的值
5. 在 **Actions → XJTU Sport Bot → Run workflow** 选择 `main`：
   - `verify-login`：只验证 token，不签到或签退。
   - `run`：签到，等待 31 分钟后签退。

现有 `SPORT_TOKEN` 有效时会优先使用，并跳过 Playwright 和 Chromium 安装；返回 401 时会安装浏览器并尝试官方 CAS 续登。要实现跨天自动续登，仍需配置上面的账号密码和可信会话。

token 的有效期由学校服务器决定。仅配置 token 而未配置续登会话时，过期后仍需本人重新登录。不要把 token、账号密码或浏览器会话内容发到聊天、issue 或提交到仓库。

定时任务保持每天北京时间 12:00 运行 `run`。GitHub 定时调度可能延迟。

## 其他可选设置

在 **Variables** 标签中配置：

| Name | 默认值 / 用途 |
| --- | --- |
| `SPORT_LONGITUDE` | `108.654387`，经度 |
| `SPORT_LATITUDE` | `34.257229`，纬度 |
| `SPORT_SEND_EMAIL` | 默认 `false`；设为 `true` 启用邮件通知 |

邮件通知需要额外设置 `SMTP_AUTH_CODE`、`EMAIL_SENDER`、`EMAIL_RECEIVER` 三个 Secrets。SMTP 使用 QQ 邮箱 SSL 服务及授权码。

## 备选：CAS 浏览器登录

没有配置 `SPORT_TOKEN` 或该 token 返回 401 时，程序使用 `SPORT_USER` 和 `SPORT_PASSWORD` Secrets，通过官方 CAS 页面登录；优先恢复已保存的学校会话。页面自身处理加密、动态表单和安全检查。学校要求本次登录完成 MFA 或验证码时，程序停止提示，不会伪造验证结果或反复重试密码。

需要保存本人完成验证后的会话时，可使用 `setup-local.cmd` 或 `setup_login.py`，把生成的配置保存到 `SPORT_BROWSER_STATE` Secret。会话只包含学校域名数据，保存在本地被 Git 忽略的 `.local/auth-state.json`。会话的有效期及能否跨网络使用仍由学校检查。

## 本地运行

建议使用 Python 3.11。只用 token 时：

```powershell
python -m pip install -r requirements.txt
$env:SPORT_TOKEN = "你的 token"
python sport_bot.py --auth-only
```

完整签到流程使用 `python sport_bot.py`。

使用 CAS 浏览器登录时另需：

```bash
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
```

本地显示浏览器可加 `--headed`。日志输出到终端和 `sport_bot.log`，不会记录密码、Cookie 值或 token。

## 验证与失败状态

- 退出码 `0`：本次所选模式成功。`--auth-only` 成功不代表已经打卡。
- 退出码 `1`：配置、token、网络或业务接口失败，按日志中的具体阶段处理。
- 退出码 `2`：CAS 登录需要本人完成验证码或 MFA。
- `--check-token` 专用退出码 `3`：当前无 token 或平台返回 401，需要安装浏览器继续认证；该预检查不会提交账号密码或打卡请求。

`Verify CAS login (no attendance)` 工作流运行离线单元测试后，根据已配置的 token 或会话执行只读验证。推送代码时若二者都未配置，则跳过真实认证；手动触发时会尝试所选认证方式。绿色的离线检查结果不代表真实账号已经验证。

单元测试：

```bash
python -m unittest discover -s tests -v
```

签到或签退失败不会自动重试写入，避免重复提交。签到成功后若签退失败，请及时在官方平台处理。

## 使用说明

请在符合学校规定和课程要求的场景中使用，确保记录与实际活动相符。
