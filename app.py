```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import time
import subprocess
import requests

from datetime import datetime, timezone, timedelta
from seleniumbase import SB


# ============================================================
# 环境变量
# ============================================================

EMAIL = os.environ.get("EMAIL", "").strip()

# Bot-hosting SESSION token
SESSION_TOKEN = os.environ.get("SESSION_TOKEN", "").strip()

# GitHub PAT，用于自动更新 SESSION_TOKEN
GH_TOKEN = os.environ.get("GH_TOKEN", "").strip()

# Telegram
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()

# Proxy
IS_PROXY = os.environ.get("IS_PROXY", "false").lower() == "true"
PROXY_SERVER = (
    os.environ.get("PROXY_SERVER", "").strip()
    or "http://127.0.0.1:1080"
)

# 浏览器
HEADLESS = os.environ.get("HEADLESS", "false").lower() == "true"


# ============================================================
# 基本检查
# ============================================================

if not SESSION_TOKEN:
    print("❌ 未配置 SESSION_TOKEN")
    print("请在 GitHub Secrets / 环境变量中设置 SESSION_TOKEN")
    sys.exit(1)


# ============================================================
# 全局状态
# ============================================================

LOGIN_METHOD = "SESSION_TOKEN"


# ============================================================
# 时间
# ============================================================

UTC8 = timezone(timedelta(hours=8))


def now_local():
    return datetime.now(UTC8)


def now_string():
    return now_local().strftime("%Y-%m-%d %H:%M:%S")


# ============================================================
# Telegram
# ============================================================

def send_telegram_message(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram 未配置，跳过通知")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TG_BOT_TOKEN}/sendMessage"
    )

    try:
        response = requests.post(
            url,
            json={
                "chat_id": TG_CHAT_ID,
                "text": message,
            },
            timeout=10,
        )

        if response.ok:
            print("✅ Telegram 通知已发送")
            return True

        print(
            f"❌ Telegram HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )
        return False

    except Exception as e:
        print(f"❌ Telegram 发送失败: {e}")
        return False


# ============================================================
# 邮箱脱敏
# ============================================================

def mask_email(email: str) -> str:
    if not email:
        return "****"

    if "@" not in email:
        return email[:2] + "****"

    name, domain = email.split("@", 1)

    if len(name) <= 4:
        return f"{name}@{domain}"

    return f"{name[:2]}****{name[-2:]}@{domain}"


# ============================================================
# Telegram 通知格式
# ============================================================

def format_notification(
    status: str,
    extra: str = "",
    error: str = "",
    expiry_date: str = "",
) -> str:

    lines = [
        "🇫🇮 Bot-hosting 续期通知",
        "",
        status,
        f"👤 登录账户: {mask_email(EMAIL)}",
    ]

    if LOGIN_METHOD != "SESSION_TOKEN":
        lines.append(f"🔐 登录方式: {LOGIN_METHOD}")

    if expiry_date:
        lines.append(f"📅 到期时间: {expiry_date}")

    if extra:
        lines.append(extra)

    if error:
        lines.append(f"⚠️ 错误信息: {error}")

    lines.append(f"⏱️ 登录时间: {now_string()}")

    return "\n".join(lines)


# ============================================================
# 获取出口 IP
# ============================================================

def get_current_ip(proxy_server: str = "") -> str:
    proxies = None

    if proxy_server:
        proxies = {
            "http": proxy_server,
            "https": proxy_server,
        }

    response = requests.get(
        "https://api.ip.sb/ip",
        proxies=proxies,
        timeout=15,
    )

    response.raise_for_status()

    return response.text.strip()


# ============================================================
# Cookie
# ============================================================

def get_cookie_info(sb, name):
    try:
        cookies = sb.get_cookies()
    except Exception as e:
        print(f"⚠️ 获取 Cookie 失败: {e}")
        return None, None

    for cookie in cookies:
        if cookie.get("name") != name:
            continue

        value = cookie.get("value")
        expiry_ts = cookie.get("expiry")

        expiry_dt = None

        if expiry_ts:
            try:
                expiry_dt = datetime.fromtimestamp(
                    expiry_ts,
                    tz=timezone.utc,
                )
            except Exception:
                expiry_dt = None

        return value, expiry_dt

    return None, None


def should_update_cookie(
    new_value,
    old_value,
    expiry_dt,
    days_threshold=3,
):
    if not new_value:
        return False

    if new_value != old_value:
        return True

    if expiry_dt:
        remaining = (
            expiry_dt - datetime.now(timezone.utc)
        ).total_seconds()

        if remaining < days_threshold * 24 * 3600:
            return True

    return False


# ============================================================
# GitHub Secret
# ============================================================

def update_github_secret(secret_name, new_value):
    if not new_value:
        print(
            f"⚠️ 跳过更新 {secret_name}: "
            "新值为空"
        )
        return False

    masked = (
        new_value[:4] + "..." + new_value[-4:]
        if len(new_value) > 8
        else "***"
    )

    print(
        f"🔄 更新 GitHub Secret: "
        f"{secret_name} ({masked})"
    )

    try:
        env = os.environ.copy()

        if GH_TOKEN:
            env["GH_TOKEN"] = GH_TOKEN

        proc = subprocess.run(
            [
                "gh",
                "secret",
                "set",
                secret_name,
                "--body",
                new_value,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )

        if proc.returncode == 0:
            print(
                f"✅ GitHub Secret {secret_name} "
                "更新成功"
            )
            return True

        print(
            f"❌ GitHub Secret 更新失败: "
            f"{proc.stderr.strip()}"
        )

        return False

    except Exception as e:
        print(f"❌ GitHub Secret 更新异常: {e}")
        return False


# ============================================================
# 日期解析
# ============================================================

def parse_expiry_date(value):
    if not value:
        return None

    value = value.strip()

    formats = [
        "%Y/%m/%d",
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m-%d-%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(
                value,
                fmt,
            ).date()
        except ValueError:
            pass

    return None


def extract_expiry_date(page_source: str):
    if not page_source:
        return None

    patterns = [
        # Expires: 2026/09/04
        r"Expires\s*[:\-]?\s*(\d{4}/\d{2}/\d{2})",

        # Expires: 2026-09-04
        r"Expires\s*[:\-]?\s*(\d{4}-\d{2}-\d{2})",

        # Expires: 09/04/2026
        r"Expires\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})",

        # 2026/09/04 - renew
        r"(\d{4}/\d{2}/\d{2})\s*[-–]\s*renew",

        # 2026-09-04 - renew
        r"(\d{4}-\d{2}-\d{2})\s*[-–]\s*renew",

        # 09/04/2026 - renew
        r"(\d{2}/\d{2}/\d{4})\s*[-–]\s*renew",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            page_source,
            re.IGNORECASE,
        )

        if not match:
            continue

        date_str = match.group(1)

        parsed = parse_expiry_date(date_str)

        if parsed:
            return parsed.strftime("%Y/%m/%d")

    return None


# ============================================================
# 倒计时
# ============================================================

def extract_countdown(page_source: str):
    if not page_source:
        return None

    patterns = [
        r"Renew\s+in\s+(\d{2}:\d{2}:\d{2})",
        r"renew\s+in\s+(\d{2}:\d{2}:\d{2})",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            page_source,
            re.IGNORECASE,
        )

        if match:
            return match.group(1)

    return None


def format_countdown(countdown):
    if not countdown:
        return ""

    try:
        h, m, s = countdown.split(":")
        h = int(h)
        m = int(m)

        if h > 0:
            return f"{h}h{m}min"

        return f"{m}min"

    except Exception:
        return countdown


# ============================================================
# 页面诊断
# ============================================================

def get_page_diagnostic(sb, max_length=600):
    try:
        text = sb.get_text("body")
    except Exception:
        return ""

    text = re.sub(r"\s+", " ", text).strip()

    if len(text) > max_length:
        text = text[:max_length] + "..."

    return text


# ============================================================
# 登录
# ============================================================

def login_with_session(sb):
    print("🚀 使用 SESSION_TOKEN 登录...")

    try:
        sb.open("https://bot-hosting.net/")
        sb.wait_for_ready_state_complete()
        sb.sleep(2)

        print("📝 注入 SESSION_TOKEN Cookie...")

        cookies = {
            "session_token": SESSION_TOKEN,
            "login": "true",
            "theme": "system",
        }

        for name, value in cookies.items():
            if not value:
                continue

            sb.add_cookie(
                {
                    "name": name,
                    "value": value,
                    "domain": "bot-hosting.net",
                }
            )

        print(
            "🌐 打开 "
            "https://bot-hosting.net/a/billings"
        )

        sb.open(
            "https://bot-hosting.net/a/billings"
        )

        sb.wait_for_ready_state_complete()
        sb.sleep(3)

        current_url = sb.get_current_url()
        current_title = sb.get_title()

        print(f"📝 当前 URL: {current_url}")
        print(f"📝 当前 Title: {current_title}")

        if (
            "/a/billings" in current_url
            and "/login" not in current_url
            and "error=" not in current_url
        ):
            print(
                "✅ SESSION_TOKEN 登录成功"
            )
            return True

        print(
            "❌ SESSION_TOKEN 登录失败"
        )

        return False

    except Exception as e:
        print(f"❌ 登录异常: {e}")
        return False


# ============================================================
# 找 Renew 按钮
# ============================================================

def find_renew_control(sb):
    selectors = [
        'button:contains("Renew free plan")',
        'button:contains("Renew")',
        'a:contains("Renew free plan")',
        'a:contains("Renew")',
        '[class*="renew"]',
    ]

    countdown = None

    for selector in selectors:
        try:
            if not sb.is_element_visible(selector):
                continue

            text = sb.get_text(selector).strip()

            if not text:
                continue

            print(
                f"🔎 找到候选元素: "
                f"{text[:150]!r}"
            )

            countdown_match = re.search(
                r"Renew\s+in\s+(\d{2}:\d{2}:\d{2})",
                text,
                re.IGNORECASE,
            )

            if countdown_match:
                countdown = countdown_match.group(1)
                continue

            if "renew" in text.lower():
                return selector, countdown

        except Exception:
            continue

    return None, countdown


# ============================================================
# 等待验证/人工操作完成
# ============================================================

def wait_for_verification(sb, timeout=120):
    """
    不自动绕过 CAPTCHA / Turnstile。
    如果页面需要人工验证，则等待验证完成。
    """

    print(
        "🔒 如果页面出现验证，请在浏览器中完成验证。"
    )

    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            source = sb.get_page_source().lower()

            indicators = [
                "verify you are human",
                "确认您是真人",
                "just a moment",
                "turnstile",
            ]

            has_verification = any(
                item in source
                for item in indicators
            )

            if not has_verification:
                print(
                    "✅ 页面验证状态已离开验证页"
                )
                return True

        except Exception:
            pass

        sb.sleep(2)

    print(
        f"❌ 等待验证超过 {timeout} 秒"
    )

    return False


# ============================================================
# 点击外部 Renew
# ============================================================

def click_outer_renew(sb, selector):
    print("🔄 点击外部 Renew 按钮...")

    try:
        sb.click(
            selector,
            timeout=10,
        )

        print("✅ 外部 Renew 已点击")

        # 给弹窗/页面足够时间加载
        sb.sleep(3)

        return True

    except Exception as e:
        print(
            f"❌ 外部 Renew 点击失败: {e}"
        )
        return False


# ============================================================
# 点击最终 Renew
# ============================================================

def click_final_renew(sb):
    selectors = [
        'button:contains("Renew for 4 days")',
        'button:contains("Renew")',
    ]

    for selector in selectors:
        try:
            if not sb.is_element_visible(selector):
                continue

            text = sb.get_text(selector).strip()

            if "renew" not in text.lower():
                continue

            print(
                f"🔘 尝试点击最终按钮: "
                f"{text[:150]!r}"
            )

            sb.click(
                selector,
                timeout=10,
            )

            print("✅ 已点击最终 Renew 按钮")

            return True

        except Exception as e:
            print(
                f"⚠️ 点击 {selector} 失败: {e}"
            )

    return False


# ============================================================
# 确认续期结果
# ============================================================

def verify_renewal(
    sb,
    old_expiry,
    timeout=45,
):
    """
    核心逻辑：

    只有确认新的到期日期 > 旧日期，
    才认为续期成功。

    Renew in 倒计时只作为辅助信息。
    """

    old_date = parse_expiry_date(
        old_expiry
    )

    deadline = time.time() + timeout

    last_expiry = None
    last_countdown = None

    print(
        f"🔍 开始确认续期结果，"
        f"旧到期日期: {old_expiry}"
    )

    while time.time() < deadline:

        try:
            source = sb.get_page_source()

            last_expiry = extract_expiry_date(
                source
            )

            last_countdown = extract_countdown(
                source
            )

            print(
                f"🔎 当前状态: "
                f"expiry={last_expiry}, "
                f"countdown={last_countdown}"
            )

            # ----------------------------------------
            # 最可靠判断：新日期 > 旧日期
            # ----------------------------------------

            if old_date and last_expiry:
                new_date = parse_expiry_date(
                    last_expiry
                )

                if new_date and new_date > old_date:
                    print(
                        "✅ 确认续期成功"
                    )

                    print(
                        f"📈 {old_expiry} "
                        f"→ {last_expiry}"
                    )

                    return {
                        "success": True,
                        "expiry": last_expiry,
                        "countdown": last_countdown,
                    }

        except Exception as e:
            print(
                f"⚠️ 检查续期结果异常: {e}"
            )

        sb.sleep(2)

    print(
        "⚠️ 未能在规定时间内确认续期"
    )

    return {
        "success": False,
        "expiry": last_expiry,
        "countdown": last_countdown,
    }


# ============================================================
# 更新 SESSION_TOKEN
# ============================================================

def update_session_token(sb):
    print(
        "🔄 检查 SESSION_TOKEN 是否需要更新..."
    )

    try:
        new_token, token_expiry = (
            get_cookie_info(
                sb,
                "session_token",
            )
        )

        if not new_token:
            print(
                "⚠️ 页面中没有获取到新的 "
                "session_token"
            )
            return

        if should_update_cookie(
            new_token,
            SESSION_TOKEN,
            token_expiry,
        ):
            print(
                "🔄 SESSION_TOKEN 发生变化或即将过期"
            )

            if not GH_TOKEN:
                print(
                    "⚠️ 未配置 GH_TOKEN，"
                    "无法自动更新 GitHub Secret"
                )

                return

            if update_github_secret(
                "SESSION_TOKEN",
                new_token,
            ):
                print(
                    "✅ SESSION_TOKEN 已更新"
                )
            else:
                print(
                    "❌ SESSION_TOKEN 更新失败"
                )

        else:
            print(
                "✅ SESSION_TOKEN 无需更新"
            )

    except Exception as e:
        print(
            f"❌ SESSION_TOKEN 检查异常: {e}"
        )


# ============================================================
# 主流程
# ============================================================

def main():

    print("=" * 50)
    print("       Bot-hosting 自动续期")
    print("=" * 50)

    print(
        f"🕐 当前时间: {now_string()}"
    )

    # --------------------------------------------------------
    # SeleniumBase
    # --------------------------------------------------------

    sb_kwargs = {
        "uc": True,
        "headless": HEADLESS,
    }

    if IS_PROXY:
        print(
            f"🔗 使用代理: {PROXY_SERVER}"
        )

        sb_kwargs["proxy"] = PROXY_SERVER

    else:
        print("🍭 未使用代理，直连访问")

    # --------------------------------------------------------
    # 浏览器
    # --------------------------------------------------------

    with SB(**sb_kwargs) as sb:

        # ----------------------------------------------------
        # IP
        # ----------------------------------------------------

        try:
            ip = get_current_ip(
                PROXY_SERVER
                if IS_PROXY
                else ""
            )

            print(
                f"📍 当前出口 IP: {ip}"
            )

        except Exception as e:
            print(
                f"⚠️ 获取出口 IP 失败: {e}"
            )

        # ----------------------------------------------------
        # 登录
        # ----------------------------------------------------

        login_ok = login_with_session(sb)

        if not login_ok:

            send_telegram_message(
                format_notification(
                    "❌ 登录失败",
                    error=(
                        "SESSION_TOKEN 无效、"
                        "已过期或页面异常"
                    ),
                )
            )

            return

        # ----------------------------------------------------
        # 获取当前页面
        # ----------------------------------------------------

        print("🔎 获取当前账户状态...")

        sb.sleep(2)

        page_source = (
            sb.get_page_source()
        )

        current_expiry = (
            extract_expiry_date(
                page_source
            )
        )

        current_countdown = (
            extract_countdown(
                page_source
            )
        )

        if current_expiry:
            print(
                f"📅 当前到期日期: "
                f"{current_expiry}"
            )
        else:
            print(
                "⚠️ 未能读取当前到期日期"
            )

        if current_countdown:
            print(
                f"⏱️ 当前倒计时: "
                f"{current_countdown}"
            )

        # ----------------------------------------------------
        # 找 Renew
        # ----------------------------------------------------

        print("🔎 查找 Renew 控件...")

        renew_selector, countdown = (
            find_renew_control(sb)
        )

        # ----------------------------------------------------
        # 尚未到续期时间
        # ----------------------------------------------------

        if not renew_selector:

            if countdown:

                friendly = format_countdown(
                    countdown
                )

                print(
                    f"⏳ 尚未到续期时间: "
                    f"{countdown}"
                )

                send_telegram_message(
                    format_notification(
                        "⏳ 未到续期时间",
                        extra=(
                            f"⏱️ 可续期时间: "
                            f"{friendly} 后"
                        ),
                        expiry_date=(
                            current_expiry
                            or "（未获取到）"
                        ),
                    )
                )

            else:

                diagnostic = (
                    get_page_diagnostic(
                        sb
                    )
                )

                print(
                    "⚠️ 未找到 Renew "
                    "或倒计时"
                )

                send_telegram_message(
                    format_notification(
                        "⚠️ 无法确认续期状态",
                        extra=(
                            "未找到 Renew 按钮"
                        ),
                        expiry_date=(
                            current_expiry
                            or "（未获取到）"
                        ),
                        error=(
                            diagnostic
                            or "页面无可用诊断信息"
                        ),
                    )
                )

            update_session_token(sb)

            print("🏁 脚本执行完毕")
            return

        # ----------------------------------------------------
        # 可以续期
        # ----------------------------------------------------

        print(
            f"✅ 找到可用 Renew 控件: "
            f"{renew_selector}"
        )

        if not current_expiry:
            print(
                "⚠️ 无法读取续期前日期，"
                "仍会尝试续期，但成功确认能力降低"
            )

        # ----------------------------------------------------
        # 点击外部 Renew
        # ----------------------------------------------------

        if not click_outer_renew(
            sb,
            renew_selector,
        ):

            send_telegram_message(
                format_notification(
                    "❌ 续期失败",
                    error=(
                        "无法点击外部 Renew 按钮"
                    ),
                    expiry_date=(
                        current_expiry
                        or "（未获取到）"
                    ),
                )
            )

            return

        # ----------------------------------------------------
        # 验证
        # ----------------------------------------------------

        if not wait_for_verification(
            sb,
            timeout=120,
        ):

            diagnostic = (
                get_page_diagnostic(sb)
            )

            send_telegram_message(
                format_notification(
                    "❌ 续期失败",
                    error=(
                        "验证页面在规定时间内"
                        "没有完成"
                        + (
                            f"\n页面: {diagnostic}"
                            if diagnostic
                            else ""
                        )
                    ),
                    expiry_date=(
                        current_expiry
                        or "（未获取到）"
                    ),
                )
            )

            return

        # ----------------------------------------------------
        # 点击最终 Renew
        # ----------------------------------------------------

        print(
            "🔎 查找最终续期按钮..."
        )

        if not click_final_renew(sb):

            diagnostic = (
                get_page_diagnostic(sb)
            )

            send_telegram_message(
                format_notification(
                    "❌ 续期失败",
                    error=(
                        "找不到或无法点击 "
                        "Renew for 4 days"
                        + (
                            f"\n页面: {diagnostic}"
                            if diagnostic
                            else ""
                        )
                    ),
                    expiry_date=(
                        current_expiry
                        or "（未获取到）"
                    ),
                )
            )

            return

        # ----------------------------------------------------
        # 确认续期
        # ----------------------------------------------------

        result = verify_renewal(
            sb,
            current_expiry,
            timeout=45,
        )

        new_expiry = result.get(
            "expiry"
        )

        new_countdown = result.get(
            "countdown"
        )

        # ----------------------------------------------------
        # 成功
        # ----------------------------------------------------

        if result.get("success"):

            extra = "📈 到期日期已确认延长"

            if current_expiry and new_expiry:
                extra = (
                    f"📈 到期日期: "
                    f"{current_expiry} → "
                    f"{new_expiry}"
                )

            if new_countdown:
                extra += (
                    f"\n⏱️ 下次可续期: "
                    f"{format_countdown(new_countdown)} 后"
                )

            send_telegram_message(
                format_notification(
                    "✅ 续期成功",
                    extra=extra,
                    expiry_date=(
                        new_expiry
                        or "（未获取到）"
                    ),
                )
            )

        # ----------------------------------------------------
        # 无法确认
        # ----------------------------------------------------

        else:

            diagnostic = (
                get_page_diagnostic(sb)
            )

            extra = (
                "没有检测到明确的"
                "到期日期变化"
            )

            if new_countdown:
                extra += (
                    f"\n⏱️ 页面当前倒计时: "
                    f"{format_countdown(new_countdown)}"
                )

            send_telegram_message(
                format_notification(
                    "⚠️ 续期结果无法确认",
                    extra=extra,
                    error=(
                        diagnostic
                        or "页面没有返回明确结果"
                    ),
                    expiry_date=(
                        new_expiry
                        or current_expiry
                        or "（未获取到）"
                    ),
                )
            )

        # ----------------------------------------------------
        # SESSION_TOKEN
        # ----------------------------------------------------

        update_session_token(sb)

        print("=" * 50)
        print("🏁 脚本执行完毕")
        print("=" * 50)


# ============================================================
# Entry
# ============================================================

if __name__ == "__main__":
    main()
```
