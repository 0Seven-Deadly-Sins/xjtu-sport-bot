"""Use an existing platform token without starting a browser or logging in."""

import json
import requests

from cas_auth import AuthenticationError


VERIFY_URL = "https://ipahw.xjtu.edu.cn/szjy-boot/api/v1/system/user/getInfo"


def parse_token(value):
    value = value.strip()
    # uni-app stores _token either as text or as a typed JSON value. Accept
    # both so the user can copy the entire browser storage value unchanged.
    if value.startswith(('"', '{')):
        try:
            value = json.loads(value)
        except ValueError:
            raise AuthenticationError("SPORT_TOKEN 格式错误，请复制学校网页 _token 的完整值。") from None
        if isinstance(value, dict):
            value = value.get("token", value.get("data"))
    if not isinstance(value, str):
        raise AuthenticationError("SPORT_TOKEN 没有包含有效的 token 字符串。")
    value = value.strip()
    if value.lower().startswith("bearer "):
        value = value[7:].strip()
    if not value or value.lower() in {"null", "undefined"} or any(char.isspace() for char in value):
        raise AuthenticationError("SPORT_TOKEN 为空或包含多余内容，请只填写 token 值。")
    return value


def verify_token(token):
    try:
        response = requests.get(VERIFY_URL, headers={"token": token}, timeout=30)
    except requests.RequestException:
        raise AuthenticationError("无法连接体育平台的只读验证接口，请稍后重试。") from None
    if response.status_code in (401, 403):
        raise AuthenticationError("SPORT_TOKEN 无效或已过期，请重新登录学校网页并更新这个 Secret。")
    if response.status_code != 200:
        raise AuthenticationError("体育平台的只读验证接口返回了非成功 HTTP 状态。")
    try:
        payload = response.json()
    except ValueError:
        raise AuthenticationError("体育平台的只读验证接口未返回 JSON。") from None
    if not isinstance(payload, dict):
        raise AuthenticationError("体育平台的只读验证响应格式异常。")
    if payload.get("code") in (401, 403, "401", "403"):
        raise AuthenticationError("SPORT_TOKEN 无效或已过期，请重新登录学校网页并更新这个 Secret。")
    if payload.get("success") is not True and payload.get("success") != "true":
        raise AuthenticationError("体育平台未接受该 token，请重新复制 _token 并更新 Secret。")
    return token
