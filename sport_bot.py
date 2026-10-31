import argparse
import logging
import os
from pathlib import Path
import smtplib
from email.mime.text import MIMEText
import time

import requests

from cas_auth import authenticate, AuthenticationError, InteractionRequired
from token_auth import TokenExpired, parse_token, verify_token


class Config:
    TOKEN = os.getenv("SPORT_TOKEN", "")
    USER = os.getenv("SPORT_USER", "")
    PASSWORD = os.getenv("SPORT_PASSWORD", "")
    BROWSER_STATE = os.getenv("SPORT_BROWSER_STATE", "")
    LONGITUDE = os.getenv("SPORT_LONGITUDE") or "108.654387"
    LATITUDE = os.getenv("SPORT_LATITUDE") or "34.257229"
    SEND_EMAIL = os.getenv("SPORT_SEND_EMAIL", "false").strip().lower() in {"1", "true", "yes", "on"}
    SMTP_AUTH_CODE = os.getenv("SMTP_AUTH_CODE", "")
    EMAIL_SENDER = os.getenv("EMAIL_SENDER", "")
    EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER", "")


class RedactSecrets(logging.Filter):
    def __init__(self, values):
        super().__init__()
        self.values = [value for value in values if value]

    def filter(self, record):
        message = record.getMessage()
        for value in sorted(self.values, key=len, reverse=True):
            message = message.replace(value, "***")
        record.msg, record.args = message, ()
        return True


def configure_logging():
    redactor = RedactSecrets([Config.TOKEN, Config.USER, Config.PASSWORD, Config.SMTP_AUTH_CODE])
    handlers = [logging.StreamHandler(), logging.FileHandler(Path(__file__).with_name("sport_bot.log"), encoding="utf-8")]
    for handler in handlers:
        handler.addFilter(redactor)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", handlers=handlers, force=True)
    return redactor


def sign_operation(url, payload, token, operation_name):
    try:
        response = requests.post(
            url,
            headers={
                "token": token,
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_4 like Mac OS X) AppleWebKit/537.36 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/537.36",
            },
            json=payload,
            timeout=30,
        )
        logging.info("%s请求：HTTP %s", operation_name, response.status_code)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and (data.get("success") is True or data.get("success") == "true"):
            logging.info("%s成功", operation_name)
            return True
        message = data.get("msg", "平台未返回成功状态") if isinstance(data, dict) else "响应格式错误"
        logging.error("%s失败：%s", operation_name, message)
    except requests.RequestException:
        logging.error("%s的网络请求失败；不会自动重试写入操作。", operation_name)
    except ValueError:
        logging.error("%s接口返回了非 JSON 数据。", operation_name)
    return False


def send_email(content):
    try:
        if not all([Config.SMTP_AUTH_CODE, Config.EMAIL_SENDER, Config.EMAIL_RECEIVER]):
            logging.error("邮件配置不完整，通知未发送。")
            return
        message = MIMEText(content, "plain", "utf-8")
        message["From"] = Config.EMAIL_SENDER
        message["To"] = Config.EMAIL_RECEIVER
        message["Subject"] = "运动打卡通知"
        with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=30) as smtp:
            smtp.login(Config.EMAIL_SENDER, Config.SMTP_AUTH_CODE)
            smtp.sendmail(Config.EMAIL_SENDER, [Config.EMAIL_RECEIVER], message.as_string())
        logging.info("邮件发送成功")
    except (OSError, smtplib.SMTPException):
        logging.error("邮件发送失败，请检查邮箱 SMTP 配置。")


def check_saved_token():
    """0: valid token; 3: install browser for renewal; 1: other failure."""
    if not Config.TOKEN:
        return 3
    try:
        verify_token(parse_token(Config.TOKEN))
        logging.info("现有 token 有效，本次无需安装浏览器。")
        return 0
    except TokenExpired as error:
        logging.info("%s 将准备官方浏览器续登。", error)
        return 3
    except AuthenticationError as error:
        logging.error("%s", error)
        return 1


def obtain_token(*, headed=False):
    if Config.TOKEN:
        token = parse_token(Config.TOKEN)
        try:
            verify_token(token)
            logging.info("SPORT_TOKEN 已通过体育平台只读接口校验，无需浏览器登录。")
            return token
        except TokenExpired as error:
            logging.warning("%s 正在通过官方 CAS 重新获取 token。", error)
    token = authenticate(Config.USER, Config.PASSWORD, headless=not headed)
    verify_token(token)
    logging.info("官方登录取得的新 token 已通过体育平台只读校验。")
    return token


def run(*, auth_only=False, headed=False):
    if not Config.TOKEN and not Config.BROWSER_STATE and (not Config.USER or not Config.PASSWORD):
        logging.error("请配置 SPORT_TOKEN，或 SPORT_BROWSER_STATE / SPORT_USER / SPORT_PASSWORD 登录方案。")
        return 1
    try:
        longitude, latitude = float(Config.LONGITUDE), float(Config.LATITUDE)
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise ValueError
    except ValueError:
        logging.error("坐标格式或范围错误，请检查 SPORT_LONGITUDE / SPORT_LATITUDE。")
        return 1
    try:
        token = obtain_token(headed=headed)
    except InteractionRequired as error:
        logging.error("%s", error)
        return 2
    except AuthenticationError as error:
        logging.error("%s", error)
        return 1
    if auth_only:
        logging.info("登录或 token 验证通过；本模式不会提交签到或签退。")
        return 0
    for handler in logging.getLogger().handlers:
        for filter_ in handler.filters:
            if isinstance(filter_, RedactSecrets):
                filter_.values.append(token)
    signed_in = sign_operation(
        "https://ipahw.xjtu.edu.cn/szjy-boot/api/v1/sportActa/signRun",
        {"sportType": 2, "longitude": longitude, "latitude": latitude, "courseInfoId": "null"},
        token, "签到",
    )
    if not signed_in:
        if Config.SEND_EMAIL:
            send_email("签到失败，请查看 Actions 日志。")
        return 1
    logging.info("等待 31 分钟后签退。")
    time.sleep(31 * 60)
    signed_out = sign_operation(
        "https://ipahw.xjtu.edu.cn/szjy-boot/api/v1/sportActa/signOutTrain",
        {"longitude": longitude, "latitude": latitude}, token, "签退",
    )
    if Config.SEND_EMAIL:
        send_email("打卡成功" if signed_out else "签到成功但签退失败，请及时手动处理。")
    return 0 if signed_out else 1


def main():
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--auth-only", action="store_true", help="只验证登录，不提交签到或签退")
    modes.add_argument("--check-token", action="store_true", help="只检查已有 token；退出码 3 表示需要浏览器续登")
    parser.add_argument("--headed", action="store_true", help="本地显示 Chromium 窗口")
    args = parser.parse_args()
    configure_logging()
    if args.check_token:
        return check_saved_token()
    return run(auth_only=args.auth_only, headed=args.headed)


if __name__ == "__main__":
    raise SystemExit(main())
