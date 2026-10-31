"""Authenticate through XJTU's current, unmodified CAS web interface."""

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import time
from urllib.parse import urlencode, urlparse


AUTH_HOST = "login.xjtu.edu.cn"
SPORT_HOST = "ipahw.xjtu.edu.cn"
TOKEN_PATH = "/szjy-boot/sso/codeLogin"
AUTHORIZE_URL = "https://org.xjtu.edu.cn/openplatform/oauth/authorize?" + urlencode({
    "appId": "1740",
    "redirectUri": "https://ipahw.xjtu.edu.cn/sso/callback",
    "responseType": "code",
    "scope": "user_info",
    "state": "1234",
})


class AuthenticationError(Exception):
    """A diagnostic message that contains no credentials or response body."""


class InteractionRequired(AuthenticationError):
    """The identity provider requires the user to complete a challenge."""


class CredentialsRejected(AuthenticationError):
    """The identity provider rejected the supplied credentials."""


def school_state(state):
    """Export only this application's school-domain browser state."""
    def allowed(host):
        return host == "xjtu.edu.cn" or host.endswith(".xjtu.edu.cn")

    cookies = [cookie for cookie in state.get("cookies", []) if allowed(str(cookie.get("domain", "")).lstrip("."))]
    origins = []
    for origin in state.get("origins", []):
        parsed = urlparse(origin.get("origin", ""))
        if parsed.scheme == "https" and allowed(parsed.hostname or ""):
            origins.append({
                "origin": origin["origin"],
                "localStorage": [item for item in origin.get("localStorage", []) if not any(word in item.get("name", "").lower() for word in ("password", "pwd"))],
            })
    return {"cookies": cookies, "origins": origins}


def load_browser_state(raw):
    if not raw:
        return None
    try:
        state = json.loads(raw)
        if not isinstance(state, dict) or not isinstance(state.get("cookies"), list) or not isinstance(state.get("origins"), list):
            raise ValueError
        filtered = school_state(state)
        if not filtered["cookies"] and not filtered["origins"]:
            raise ValueError
        return filtered
    except (ValueError, TypeError, KeyError, AttributeError):
        raise AuthenticationError("SPORT_BROWSER_STATE 不是有效的学校浏览器会话，请重新执行首次登录配置。") from None


def save_browser_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(school_state(state), ensure_ascii=True, separators=(",", ":"))
    if len(data.encode("utf-8")) > 60000:
        raise AuthenticationError("浏览器会话超过 Secrets 的单项容量，请使用专用登录窗口重新配置。")
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(data)
    os.chmod(path, 0o600)


def login_rejection(message):
    """Classify a server message without echoing potentially private content."""
    message = str(message).lower()
    if any(word in message for word in ("验证码", "captcha", "多因素", "安全验证", "otp")):
        return InteractionRequired("学校要求验证码或额外安全验证，需要本人完成验证后再运行。")
    if any(word in message for word in ("密码", "用户名", "账号", "口令", "password", "credential", "account", "username")):
        return CredentialsRejected("统一认证拒绝了账号或密码，请在官方登录页核实并更新 Actions Secrets。")
    return AuthenticationError("统一认证未接受本次登录；已停止，未自动重试。")


class AuthObserver:
    def __init__(self, interactive=False):
        self.token = None
        self.error = None
        self.finished = asyncio.Event()
        self.mfa_checked = False
        self.password_submitted = False
        self.interactive = interactive

    def fail(self, error):
        if not self.token and not self.error:
            self.error = error
            self.finished.set()

    async def response(self, response):
        parsed = urlparse(response.url)
        is_mfa = parsed.scheme == "https" and parsed.hostname == AUTH_HOST and parsed.path == "/cas/mfa/detect"
        is_token = parsed.scheme == "https" and parsed.hostname == SPORT_HOST and parsed.path == TOKEN_PATH
        if not (is_mfa or is_token):
            return
        if 300 <= response.status < 400:
            # A redirect has no JSON body; wait for the final callback instead.
            return
        try:
            payload = await response.json()
        except Exception:
            if self.interactive:
                logging.warning("认证接口暂未返回 JSON（%s，HTTP %s）；请在学校页面核对，配置窗口继续等待。", "安全检查" if is_mfa else "平台回调", response.status)
                return
            self.fail(AuthenticationError("认证接口返回了非 JSON 数据，登录未完成。"))
            return
        if not isinstance(payload, dict):
            self.fail(AuthenticationError("认证接口响应格式不符合预期。"))
            return
        if is_mfa:
            self.mfa_checked = True
            logging.info("已执行学校页面的登录安全检查。")
            if response.status != 200 or payload.get("code") not in (0, "0"):
                if self.interactive:
                    logging.warning("本次登录未通过，请在浏览器中核对并完成登录。")
                else:
                    self.fail(login_rejection(payload.get("message", payload.get("msg", ""))))
                return
            data = payload.get("data")
            if not isinstance(data, dict):
                self.fail(AuthenticationError("登录安全检查没有返回有效结果。"))
            elif data.get("need") is True or str(data.get("need")).lower() == "true":
                if self.interactive:
                    logging.warning("请在浏览器中本人完成学校的额外安全验证。")
                else:
                    self.fail(InteractionRequired("学校对本次云端登录要求额外安全验证（MFA）；请使用首次登录配置生成会话。"))
        else:
            data = payload.get("data")
            token = data.get("token") if isinstance(data, dict) else None
            if response.status == 200 and isinstance(token, str) and token:
                self.token = token
                self.finished.set()
            else:
                self.fail(AuthenticationError("CAS 回调已到达体育平台，但平台没有返回登录令牌。"))

    def request(self, request):
        parsed = urlparse(request.url)
        if parsed.hostname == AUTH_HOST and parsed.path == "/cas/login" and request.method == "POST":
            self.password_submitted = True
            logging.info("学校页面已提交一次密码登录表单。")


async def authenticate_async(username, password, *, headless=True, timeout_seconds=90, interactive=False, save_state_path=None, browser_channel=None):
    # Import lazily so pure unit tests do not need a browser installation.
    try:
        from playwright.async_api import async_playwright, Error, TimeoutError
    except ImportError as error:
        raise AuthenticationError("缺少 Playwright，请安装 requirements-browser.txt 并安装 Chromium。") from error

    observer = AuthObserver(interactive=interactive)
    browser = None
    stage = "启动浏览器"
    try:
        async with async_playwright() as playwright:
            from session_cache import persist_state, restore_state
            launch_options = {"headless": headless}
            if browser_channel in {"msedge", "chrome"}:
                launch_options["channel"] = browser_channel
            browser = await playwright.chromium.launch(**launch_options)
            seed = os.getenv("SPORT_BROWSER_STATE", "")
            cache_path = os.getenv("SPORT_SESSION_CACHE", "")
            state = restore_state(seed, cache_path)
            context = await browser.new_context(locale="zh-CN", storage_state=state)
            if state:
                logging.info("已载入配置的学校会话，将由服务器检查会话是否仍有效。")

            async def block_attendance(route):
                # Authentication must never create or close an activity record.
                await route.abort()

            await context.route("**/api/v1/sportActa/signRun**", block_attendance)
            await context.route("**/api/v1/sportActa/signOutTrain**", block_attendance)
            page = await context.new_page()
            page.on("response", observer.response)
            page.on("request", observer.request)
            stage = "打开认证页面"
            await page.goto(AUTHORIZE_URL, wait_until="domcontentloaded", timeout=60000)
            form = page.locator(".login-form:visible").filter(
                has=page.locator('input[type="password"]')
            ).first
            stage = "等待已有会话或密码登录表单"
            prepare_deadline = time.monotonic() + 60
            selected_password_menu = False
            while not await form.is_visible() and not observer.finished.is_set():
                if time.monotonic() >= prepare_deadline:
                    raise AuthenticationError("认证页面未显示密码登录表单，也未完成已有会话的令牌交换。")
                menu = page.get_by_role("menuitem", name=re.compile("账号密码|帳號密碼|Account|Password", re.I)).first
                if not selected_password_menu and await menu.is_visible():
                    await menu.click()
                    selected_password_menu = True
                try:
                    await asyncio.wait_for(observer.finished.wait(), timeout=0.25)
                except asyncio.TimeoutError:
                    pass
            if observer.error:
                raise observer.error
            if observer.token:
                persist_state(seed, cache_path, await context.storage_state())
                if save_state_path:
                    save_browser_state(save_state_path, await context.storage_state())
                logging.info("已有学校会话认证及体育平台令牌交换成功。")
                return observer.token
            parsed = urlparse(page.url)
            if parsed.scheme != "https" or parsed.hostname != AUTH_HOST:
                raise AuthenticationError("登录页面不在学校认证域名，已停止提交。")
            logging.info("当前使用 CAS 浏览器登录，页面主机=%s。", parsed.hostname)
            stage = "等待官方页面加密与设备指纹初始化"
            await page.wait_for_function(
                """() => {
                    const fp = document.querySelector('#fm1 [name="fpVisitorId"]');
                    const rsaReady = !window.encryptEnabled ||
                        (window.encrypt && window.encrypt.key && window.encrypt.key.n);
                    return fp && fp.value && rsaReady;
                }""",
                timeout=20000,
            )
            if interactive:
                stage = "等待本人在浏览器中完成登录与验证"
                logging.info("请在打开的学校浏览器窗口中填写账号密码并完成本人验证，程序将等待最多 30 分钟。")
            else:
                if not username or not password:
                    raise AuthenticationError("已有会话未能完成续登，且未配置 SPORT_USER / SPORT_PASSWORD。")
                username_input = form.locator('input:not([type="password"]):visible').first
                password_input = form.locator('input[type="password"]:visible').first
                stage = "填写官方登录表单"
                await username_input.fill(username)
                await password_input.fill(password)
                # The official page performs RSA encryption, execution-token handling,
                # browser fingerprinting and the MFA check. Do not forge these values.
                stage = "提交一次登录并等待平台回调"
                await form.locator("button.login-btn").click()
            deadline = time.monotonic() + timeout_seconds
            while not observer.finished.is_set() and time.monotonic() < deadline:
                if interactive:
                    trust = page.get_by_role("checkbox", name="登录成功后，设为可信客户端")
                    if await trust.count() and await trust.is_visible() and not await trust.is_checked():
                        await trust.check()
                        logging.info("已选择学校提供的可信客户端选项。")
                if await page.locator(".captcha-dialog-wrapper:visible").count():
                    if not interactive:
                        observer.fail(InteractionRequired("学校登录页面要求图形验证码，需要本人完成后再运行。"))
                        break
                rejection = page.locator(".login-error:visible")
                if not interactive and await rejection.count():
                    message = (await rejection.first.inner_text()).strip()
                    if message:
                        observer.fail(login_rejection(message))
                        break
                try:
                    await asyncio.wait_for(observer.finished.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
            if observer.error:
                raise observer.error
            if observer.token:
                persist_state(seed, cache_path, await context.storage_state())
                if save_state_path:
                    save_browser_state(save_state_path, await context.storage_state())
                logging.info("CAS 登录和体育平台令牌交换成功。")
                return observer.token
            raise AuthenticationError(
                "登录未在规定时间内完成；安全检查=%s，表单提交=%s。"
                % (observer.mfa_checked, observer.password_submitted)
            )
    except (AuthenticationError, InteractionRequired):
        raise
    except TimeoutError:
        # Playwright exception text may include fill arguments. Never log it.
        raise AuthenticationError("认证页面加载超时，阶段=%s；未自动重试账号登录。" % stage) from None
    except Error:
        raise AuthenticationError("浏览器认证失败；请确认 Chromium 已安装且学校页面可以访问。") from None
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass


def authenticate(username, password, *, headless=True):
    return asyncio.run(authenticate_async(username, password, headless=headless))


def setup_login(output_path, *, browser_channel=None):
    return asyncio.run(authenticate_async("", "", headless=False, interactive=True, timeout_seconds=1800, save_state_path=output_path, browser_channel=browser_channel))
