"""Authenticate through XJTU's current, unmodified CAS web interface."""

import asyncio
import logging
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


def login_rejection(message):
    """Classify a server message without echoing potentially private content."""
    message = str(message).lower()
    if any(word in message for word in ("验证码", "captcha", "多因素", "安全验证", "otp")):
        return InteractionRequired("学校要求验证码或额外安全验证，需要本人完成验证后再运行。")
    if any(word in message for word in ("密码", "用户名", "账号", "口令", "password", "credential", "account", "username")):
        return CredentialsRejected("统一认证拒绝了账号或密码，请在官方登录页核实并更新 Actions Secrets。")
    return AuthenticationError("统一认证未接受本次登录；已停止，未自动重试。")


class AuthObserver:
    def __init__(self):
        self.token = None
        self.error = None
        self.finished = asyncio.Event()
        self.mfa_checked = False
        self.password_submitted = False

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
        try:
            payload = await response.json()
        except Exception:
            self.fail(AuthenticationError("认证接口返回了非 JSON 数据，登录未完成。"))
            return
        if not isinstance(payload, dict):
            self.fail(AuthenticationError("认证接口响应格式不符合预期。"))
            return
        if is_mfa:
            self.mfa_checked = True
            logging.info("已执行学校页面的登录安全检查。")
            if response.status != 200 or payload.get("code") not in (0, "0"):
                self.fail(login_rejection(payload.get("message", payload.get("msg", ""))))
                return
            data = payload.get("data")
            if not isinstance(data, dict):
                self.fail(AuthenticationError("登录安全检查没有返回有效结果。"))
            elif data.get("need") is True or str(data.get("need")).lower() == "true":
                self.fail(InteractionRequired("学校对本次云端登录要求额外安全验证（MFA）；需要本人完成验证。"))
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


async def authenticate_async(username, password, *, headless=True, timeout_seconds=90):
    # Import lazily so pure unit tests do not need a browser installation.
    try:
        from playwright.async_api import async_playwright, Error, TimeoutError
    except ImportError as error:
        raise AuthenticationError("缺少 Playwright，请安装 requirements.txt 并安装 Chromium。") from error

    observer = AuthObserver()
    browser = None
    stage = "启动浏览器"
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=headless)
            context = await browser.new_context(locale="zh-CN")

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
            stage = "切换账号密码登录"
            if not await form.is_visible():
                menu = page.get_by_role("menuitem", name=re.compile("账号密码|帳號密碼|Account|Password", re.I)).first
                await menu.click(timeout=45000)
            await form.wait_for(state="visible", timeout=45000)
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
                if await page.locator(".captcha-dialog-wrapper:visible").count():
                    observer.fail(InteractionRequired("学校登录页面要求图形验证码，需要本人完成后再运行。"))
                    break
                rejection = page.locator(".login-error:visible")
                if await rejection.count():
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
