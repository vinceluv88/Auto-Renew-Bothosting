import os
import re
import time
import subprocess
from datetime import datetime, timezone, timedelta

import requests
from seleniumbase import SB


# ============================================================
# 配置
# ============================================================

EMAIL = os.environ.get("EMAIL", "").strip()
SESSION_TOKEN = os.environ.get("SESSION_TOKEN", "").strip()

GH_TOKEN = os.environ.get("GH_TOKEN", "").strip()

TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()

IS_PROXY = os.environ.get("IS_PROXY", "false").lower() == "true"
PROXY_SERVER = os.environ.get("PROXY_SERVER", "").strip()

HEADLESS = os.environ.get("HEADLESS", "true").lower() == "true"

BASE_URL = "https://bot-hosting.net/"
BILLING_URL = "https://bot-hosting.net/a/billings"

# GitHub Actions 仓库中保存的 Secret 名称
SESSION_SECRET_NAME = "SESSION_TOKEN"

# Singapore / UTC+8
UTC8 = timezone(timedelta(hours=8))


# ============================================================
# 基础工具
# ============================================================

def log(message):
    now = datetime.now(UTC8).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}", flush=True)


def mask_email(email):
    if not email:
        return "****"

    if "@" not in email:
        return "****"

    name, domain = email.split("@", 1)

    if len(name) <= 2:
        masked = name[0] + "*" * max(1, len(name) - 1)
    else:
        masked = name[0] + "*" * (len(name) - 2) + name[-1]

    return masked + "@" + domain


def get_current_ip():
    services = [
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
    ]

    for url in services:
        try:
            response = requests.get(url, timeout=8)
            if response.ok:
                ip = response.text.strip()
                if ip:
                    return ip
        except Exception:
            pass

    return "未知"


# ============================================================
# Telegram
# ============================================================

def send_telegram_message(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("ℹ️ 未配置 Telegram，跳过通知")
        return False

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TG_CHAT_ID,
        "text": text,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=15,
        )

        if response.ok:
            log("✅ Telegram 通知已发送")
            return True

        log(f"⚠️ Telegram 返回异常: {response.status_code}")
        return False

    except Exception as e:
        log(f"⚠️ Telegram 发送失败: {e}")
        return False


def format_notification(
    title,
    expiry_date=None,
    extra=None,
    error=None,
):
    lines = [
        f"🇫🇮 Bot-hosting 续期通知",
        "",
        title,
        f"👤 登录账户: {mask_email(EMAIL)}",
    ]

    if expiry_date:
        lines.append(f"📅 到期时间: {expiry_date}")

    if extra:
        lines.append(str(extra))

    if error:
        lines.append(f"❌ 错误: {error}")

    local_time = datetime.now(UTC8).strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"⏱️ 登录时间: {local_time}")

    return "\n".join(lines)


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
        "%Y.%m.%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%m/%d/%Y",
        "%m-%d-%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    return None


def extract_expiry_date(page_source):
    if not page_source:
        return None

    # 先处理常见的 Expires 格式
    patterns = [
        r"Expires\s*[:\-]?\s*(\d{4}/\d{2}/\d{2})",
        r"Expires\s*[:\-]?\s*(\d{4}-\d{2}-\d{2})",
        r"Expires\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})",
        r"Expires\s*[:\-]?\s*(\d{2}-\d{2}-\d{4})",

        r"expires\s*[:\-]?\s*(\d{4}/\d{2}/\d{2})",
        r"expires\s*[:\-]?\s*(\d{4}-\d{2}-\d{2})",
        r"expires\s*[:\-]?\s*(\d{2}/\d{2}/\d{4})",
        r"expires\s*[:\-]?\s*(\d{2}-\d{2}-\d{4})",
    ]

    for pattern in patterns:
        match = re.search(pattern, page_source, re.I)

        if match:
            return match.group(1)

    # 处理类似：
    # 2026/09/04 - renew
    patterns = [
        r"(\d{4}/\d{2}/\d{2})\s*[\-–—]\s*renew",
        r"(\d{4}-\d{2}-\d{2})\s*[\-–—]\s*renew",
        r"(\d{2}/\d{2}/\d{4})\s*[\-–—]\s*renew",
        r"(\d{2}-\d{2}-\d{4})\s*[\-–—]\s*renew",
    ]

    for pattern in patterns:
        match = re.search(pattern, page_source, re.I)

        if match:
            return match.group(1)

    # 最后寻找页面中可能出现的日期
    generic_patterns = [
        r"\b(20\d{2}/\d{2}/\d{2})\b",
        r"\b(20\d{2}-\d{2}-\d{2})\b",
    ]

    for pattern in generic_patterns:
        match = re.search(pattern, page_source)

        if match:
            return match.group(1)

    return None


# ============================================================
# 倒计时
# ============================================================

def extract_countdown(page_source):
    if not page_source:
        return None

    patterns = [
        r"Renew\s+in\s+(\d{2}:\d{2}:\d{2})",
        r"renew\s+in\s+(\d{2}:\d{2}:\d{2})",
    ]

    for pattern in patterns:
        match = re.search(pattern, page_source, re.I)

        if match:
            return match.group(1)

    return None


# ============================================================
# 页面诊断
# ============================================================

def get_page_diagnostic(sb):
    try:
        text = sb.get_text("body")

        if not text:
            return ""

        text = re.sub(r"\s+", " ", text).strip()

        return text[:1000]

    except Exception as e:
        return f"无法读取页面: {e}"


# ============================================================
# Session Cookie
# ============================================================

def inject_session_cookie(sb):
    if not SESSION_TOKEN:
        log("❌ SESSION_TOKEN 未配置")
        return False

    try:
        sb.open(BASE_URL)

        sb.add_cookie(
            {
                "name": "session_token",
                "value": SESSION_TOKEN,
                "path": "/",
                "secure": True,
            }
        )

        sb.add_cookie(
            {
                "name": "login",
                "value": "true",
                "path": "/",
            }
        )

        sb.add_cookie(
            {
                "name": "theme",
                "value": "system",
                "path": "/",
            }
        )

        log("✅ Session Cookie 已注入")
        return True

    except Exception as e:
        log(f"❌ 注入 Session Cookie 失败: {e}")
        return False


# ============================================================
# 登录检查
# ============================================================

def login_with_session(sb):
    try:
        sb.open(BILLING_URL)
        sb.sleep(5)

        current_url = sb.get_current_url()

        log(f"🌐 当前 URL: {current_url}")

        if "/a/billings" in current_url and "/login" not in current_url:
            log("✅ Session 登录成功")
            return True

        if "error=" in current_url.lower():
            log("❌ URL 中发现登录错误")

        diagnostic = get_page_diagnostic(sb)

        if diagnostic:
            log(f"页面信息: {diagnostic[:500]}")

        return False

    except Exception as e:
        log(f"❌ 登录检查失败: {e}")
        return False


# ============================================================
# 查找外部续期按钮
# ============================================================

def find_renew_control(sb):
    selectors = [
        'button:contains("Renew free plan")',
        'button:contains("Renew")',
        'a:contains("Renew free plan")',
        'a:contains("Renew")',
        '[class*="renew"]',
        '[class*="Renew"]',
    ]

    for selector in selectors:
        try:
            if sb.is_element_visible(selector):
                text = sb.get_text(selector).strip()

                if text:
                    log(f"🔎 找到续期控件: {text[:150]}")

                return selector

        except Exception:
            continue

    return None


# ============================================================
# 等待验证页面结束
# ============================================================

def wait_for_verification(sb, timeout=120):
    """
    不自动绕过 CAPTCHA / Turnstile。
    如果页面出现验证，则等待页面自行完成验证。
    """

    indicators = [
        "verify you are human",
        "确认您是真人",
        "just a moment",
        "checking your browser",
        "checking your connection",
        "cf-chl",
    ]

    start = time.time()

    log("🛡️ 检查页面验证状态...")

    while time.time() - start < timeout:
        try:
            source = sb.get_page_source().lower()

            found = False

            for indicator in indicators:
                if indicator in source:
                    found = True
                    break

            if not found:
                log("✅ 未检测到正在进行的验证页面")
                return True

            elapsed = int(time.time() - start)

            log(f"⏳ 页面仍在验证中... {elapsed}/{timeout}s")

        except Exception as e:
            log(f"⚠️ 检查验证状态失败: {e}")

        sb.sleep(3)

    log("⚠️ 验证等待超时")
    return False


# ============================================================
# 点击外部 Renew
# ============================================================

def click_outer_renew(sb, selector):
    try:
        log(f"🖱️ 点击续期控件: {selector}")

        sb.click(
            selector,
            timeout=10,
        )

        sb.sleep(5)

        log("✅ 外部续期控件点击完成")

        return True

    except Exception as e:
        log(f"❌ 外部续期控件点击失败: {e}")
        return False


# ============================================================
# 点击最终 Renew for 4 days
# ============================================================

def click_final_renew(sb):
    selectors = [
        'button:contains("Renew for 4 days")',
        'button:contains("Renew")',
    ]

    for selector in selectors:
        try:
            if sb.is_element_visible(selector):
                text = sb.get_text(selector).strip()

                log(f"🔎 找到最终续期按钮: {text[:150]}")

                sb.click(
                    selector,
                    timeout=10,
                )

                log("✅ 已点击最终续期按钮")

                return True

        except Exception as e:
            log(f"⚠️ 尝试按钮失败 {selector}: {e}")

    log("❌ 找不到最终续期按钮")

    return False


# ============================================================
# 验证续期结果
# ============================================================

def verify_renewal(sb, old_expiry, timeout=45):
    old_date = parse_expiry_date(old_expiry)

    if not old_date:
        log("⚠️ 原到期日期无法解析")

    deadline = time.time() + timeout

    last_expiry = None
    last_countdown = None

    log(f"🔍 开始检查续期结果，最多等待 {timeout} 秒")

    while time.time() < deadline:
        try:
            source = sb.get_page_source()

            current_expiry = extract_expiry_date(source)

            if current_expiry:
                last_expiry = current_expiry

            current_countdown = extract_countdown(source)

            if current_countdown:
                last_countdown = current_countdown

                log(
                    f"ℹ️ 检测到 Renew 倒计时: "
                    f"{current_countdown}"
                )

            # 最可靠的判断：
            # 新到期日期 > 原到期日期
            if old_date and current_expiry:
                new_date = parse_expiry_date(current_expiry)

                if new_date and new_date > old_date:
                    log(
                        f"✅ 确认续期成功: "
                        f"{old_expiry} -> {current_expiry}"
                    )

                    return True, current_expiry

        except Exception as e:
            log(f"⚠️ 检查续期结果时出错: {e}")

        sb.sleep(3)

    log("⚠️ 在规定时间内无法确认续期结果")

    if last_expiry:
        log(f"📅 当前检测到的到期时间: {last_expiry}")

    if last_countdown:
        log(f"⏱️ 当前检测到的倒计时: {last_countdown}")

    return False, last_expiry


# ============================================================
# Session Token 提取
# ============================================================

def get_session_cookie(sb):
    try:
        cookies = sb.get_cookies()

        for cookie in cookies:
            if cookie.get("name") == "session_token":
                value = cookie.get("value")

                if value:
                    return value

    except Exception as e:
        log(f"⚠️ 获取 Session Cookie 失败: {e}")

    return None


# ============================================================
# GitHub Secret 更新
# ============================================================

def update_github_secret(secret_name, new_value):
    if not GH_TOKEN:
        log("ℹ️ GH_TOKEN 未配置，跳过 GitHub Secret 更新")
        return False

    if not new_value:
        log("⚠️ 新 Session Token 为空，拒绝更新")
        return False

    env = os.environ.copy()
    env["GH_TOKEN"] = GH_TOKEN

    try:
        result = subprocess.run(
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
            env=env,
        )

        if result.returncode == 0:
            log(f"✅ GitHub Secret 已更新: {secret_name}")
            return True

        error_text = result.stderr.strip()

        log(
            f"❌ GitHub Secret 更新失败: "
            f"{error_text[:500]}"
        )

        return False

    except FileNotFoundError:
        log("❌ 找不到 gh 命令，请确认 GitHub CLI 已安装")
        return False

    except Exception as e:
        log(f"❌ 更新 GitHub Secret 异常: {e}")
        return False


# ============================================================
# Session Token 更新逻辑
# ============================================================

def update_session_token(sb, current_expiry):
    new_token = get_session_cookie(sb)

    if not new_token:
        log("⚠️ 页面没有获取到新的 session_token")
        return False

    if not SESSION_TOKEN:
        log("ℹ️ 当前没有旧 SESSION_TOKEN")
        return update_github_secret(
            SESSION_SECRET_NAME,
            new_token,
        )

    if new_token != SESSION_TOKEN:
        log("🔄 检测到新的 Session Token")
        return update_github_secret(
            SESSION_SECRET_NAME,
            new_token,
        )

    expiry_date = parse_expiry_date(current_expiry)

    if expiry_date:
        today = datetime.now(UTC8).date()
        remaining_days = (expiry_date - today).days

        log(f"📅 Session 到期剩余约 {remaining_days} 天")

        if remaining_days <= 3:
            log("🔄 Session 即将到期，重新写入 GitHub Secret")

            return update_github_secret(
                SESSION_SECRET_NAME,
                new_token,
            )

    log("ℹ️ Session Token 没有变化，无需更新")

    return True


# ============================================================
# 主程序
# ============================================================

def main():
    log("=" * 60)
    log("🇫🇮 Bot-hosting 自动续期程序启动")
    log("=" * 60)

    if not SESSION_TOKEN:
        log("❌ SESSION_TOKEN 未配置")
        send_telegram_message(
            format_notification(
                "❌ 自动续期失败",
                error="SESSION_TOKEN 未配置",
            )
        )
        return 1

    log(f"👤 账户: {mask_email(EMAIL)}")
    log(f"🌐 当前 IP: {get_current_ip()}")
    log(f"🖥️ Headless: {HEADLESS}")
    log(f"🔀 Proxy: {IS_PROXY}")

    proxy = None

    if IS_PROXY:
        if PROXY_SERVER:
            proxy = PROXY_SERVER
            log(f"🔀 使用代理: {proxy}")
        else:
            log("⚠️ IS_PROXY=true，但 PROXY_SERVER 为空")

    sb_kwargs = {
        "uc": True,
        "headless": HEADLESS,
    }

    if proxy:
        sb_kwargs["proxy"] = proxy

    current_expiry = None

    try:
        with SB(**sb_kwargs) as sb:
            log("🌐 浏览器启动成功")

            # ------------------------------------------------
            # 注入 Session
            # ------------------------------------------------

            if not inject_session_cookie(sb):
                send_telegram_message(
                    format_notification(
                        "❌ 自动续期失败",
                        error="Session Cookie 注入失败",
                    )
                )
                return 1

            # ------------------------------------------------
            # 登录
            # ------------------------------------------------

            if not login_with_session(sb):
                log("❌ Session 登录失败")

                send_telegram_message(
                    format_notification(
                        "❌ 自动续期失败",
                        error="Session 登录失败，请检查 SESSION_TOKEN",
                    )
                )

                return 1

            # ------------------------------------------------
            # 获取当前到期时间
            # ------------------------------------------------

            try:
                page_source = sb.get_page_source()

                current_expiry = extract_expiry_date(
                    page_source
                )

            except Exception as e:
                log(f"⚠️ 获取到期时间失败: {e}")

            if current_expiry:
                log(f"📅 当前到期时间: {current_expiry}")
            else:
                log("⚠️ 无法识别当前到期时间")

            # ------------------------------------------------
            # 查找 Renew
            # ------------------------------------------------

            renew_selector = find_renew_control(sb)

            if not renew_selector:
                diagnostic = get_page_diagnostic(sb)

                if diagnostic:
                    log(
                        "📄 页面诊断:\n"
                        + diagnostic[:1000]
                    )

                send_telegram_message(
                    format_notification(
                        "⚠️ 暂未找到续期按钮",
                        expiry_date=current_expiry or "未知",
                        error="页面可能尚未到续期时间，或页面结构发生变化",
                    )
                )

                # 即使没有续期按钮，也尝试更新 Session
                update_session_token(
                    sb,
                    current_expiry,
                )

                return 0

            # ------------------------------------------------
            # 检查是否处于倒计时
            # ------------------------------------------------

            try:
                source = sb.get_page_source()
                countdown = extract_countdown(source)

                if countdown:
                    log(f"⏱️ 当前不能立即续期，倒计时: {countdown}")

                    send_telegram_message(
                        format_notification(
                            "⏳ 暂未到续期时间",
                            expiry_date=current_expiry or "未知",
                            extra=f"⏱️ Renew in {countdown}",
                        )
                    )

                    update_session_token(
                        sb,
                        current_expiry,
                    )

                    return 0

            except Exception:
                pass

            # ------------------------------------------------
            # 点击外部续期
            # ------------------------------------------------

            if not click_outer_renew(
                sb,
                renew_selector,
            ):
                send_telegram_message(
                    format_notification(
                        "❌ 续期失败",
                        expiry_date=current_expiry or "未知",
                        error="无法点击 Renew 控件",
                    )
                )
                return 1

            # ------------------------------------------------
            # 等待页面验证
            # ------------------------------------------------

            if not wait_for_verification(
                sb,
                timeout=120,
            ):
                diagnostic = get_page_diagnostic(sb)

                send_telegram_message(
                    format_notification(
                        "⚠️ 续期结果无法确认",
                        expiry_date=current_expiry or "未知",
                        error=(
                            "页面验证等待超时。"
                            f" 页面信息: {diagnostic[:300]}"
                        ),
                    )
                )

                return 1

            # ------------------------------------------------
            # 点击最终 Renew
            # ------------------------------------------------

            if not click_final_renew(sb):
                diagnostic = get_page_diagnostic(sb)

                send_telegram_message(
                    format_notification(
                        "❌ 续期失败",
                        expiry_date=current_expiry or "未知",
                        error=(
                            "找不到最终 Renew for 4 days 按钮。"
                            f" 页面: {diagnostic[:300]}"
                        ),
                    )
                )

                return 1

            # ------------------------------------------------
            # 验证结果
            # ------------------------------------------------

            renewed, new_expiry = verify_renewal(
                sb,
                current_expiry,
                timeout=45,
            )

            if renewed:
                log("🎉 续期成功")

                send_telegram_message(
                    format_notification(
                        "✅ 续期成功",
                        expiry_date=new_expiry,
                        extra=(
                            f"📈 到期时间: "
                            f"{current_expiry or '未知'} → {new_expiry}"
                        ),
                    )
                )

                current_expiry = new_expiry

            else:
                diagnostic = get_page_diagnostic(sb)

                log("⚠️ 无法确认续期成功")

                send_telegram_message(
                    format_notification(
                        "⚠️ 续期可能未成功",
                        expiry_date=new_expiry or current_expiry or "未知",
                        error=(
                            "页面在等待时间内没有确认到期日期变化。"
                            f" 页面: {diagnostic[:500]}"
                        ),
                    )
                )

            # ------------------------------------------------
            # 更新 Session Token
            # ------------------------------------------------

            update_session_token(
                sb,
                current_expiry,
            )

            log("=" * 60)
            log("🏁 脚本执行完毕")
            log("=" * 60)

            return 0

    except Exception as e:
        log("=" * 60)
        log(f"💥 程序发生未处理异常: {e}")
        log("=" * 60)

        send_telegram_message(
            format_notification(
                "❌ 自动续期程序异常",
                expiry_date=current_expiry or "未知",
                error=str(e)[:500],
            )
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
