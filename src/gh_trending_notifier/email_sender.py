from __future__ import annotations

import json
import os
import smtplib
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid

from gh_trending_notifier.models import Newsletter


# Some provider APIs (e.g. Resend) sit behind Cloudflare, which rejects the
# default urllib User-Agent ("Python-urllib/x.y") with a 403 / error code 1010.
# Send an explicit User-Agent so the request is not blocked.
USER_AGENT = "gh-trending-digest/0.1 (+https://github.com/bcanozgur/gh-trending-digest)"


class EmailError(RuntimeError):
    pass


@dataclass(frozen=True)
class SendResult:
    provider: str
    message_id: str
    recipients: list[str]


def parse_recipients(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def send_newsletter(newsletter: Newsletter, provider: str, recipients: list[str]) -> SendResult:
    if not recipients:
        raise EmailError("MAIL_TO must contain at least one recipient.")
    if provider == "smtp":
        return _send_smtp(newsletter, recipients)
    if provider == "resend":
        return _send_resend(newsletter, recipients)
    if provider == "brevo":
        return _send_brevo(newsletter, recipients)
    raise EmailError(f"Unsupported email provider: {provider}")


def _send_smtp(newsletter: Newsletter, recipients: list[str]) -> SendResult:
    host = _required_env("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    mail_from = _required_env("MAIL_FROM")
    message_id = make_msgid(domain=mail_from.split("@")[-1])

    message = EmailMessage()
    message["Subject"] = newsletter.subject
    message["From"] = mail_from
    message["To"] = ", ".join(recipients)
    message["Message-ID"] = message_id
    message.set_content(newsletter.text)
    message.add_alternative(newsletter.html, subtype="html")

    with smtplib.SMTP(host, port, timeout=30) as client:
        client.starttls()
        if username and password:
            client.login(username, password)
        client.send_message(message)
    return SendResult(provider="smtp", message_id=message_id, recipients=recipients)


def _send_resend(newsletter: Newsletter, recipients: list[str]) -> SendResult:
    api_key = _required_env("RESEND_API_KEY")
    mail_from = _required_env("MAIL_FROM")
    message_id = make_msgid(domain=mail_from.split("@")[-1])
    payload = {
        "from": mail_from,
        "to": recipients,
        "subject": newsletter.subject,
        "html": newsletter.html,
        "text": newsletter.text,
        "headers": {"Message-ID": message_id},
    }
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    _post_json(request, "resend")
    return SendResult(provider="resend", message_id=message_id, recipients=recipients)


def _send_brevo(newsletter: Newsletter, recipients: list[str]) -> SendResult:
    api_key = _required_env("BREVO_API_KEY")
    mail_from = _required_env("MAIL_FROM")
    sender_name = os.getenv("MAIL_FROM_NAME", "GitHub Trending Notifier")
    message_id = make_msgid(domain=mail_from.split("@")[-1])
    payload = {
        "sender": {"name": sender_name, "email": mail_from},
        "to": [{"email": recipient} for recipient in recipients],
        "subject": newsletter.subject,
        "htmlContent": newsletter.html,
        "textContent": newsletter.text,
        "headers": {"Message-ID": message_id},
    }
    request = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    _post_json(request, "brevo")
    return SendResult(provider="brevo", message_id=message_id, recipients=recipients)


def _post_json(request: urllib.request.Request, provider: str) -> None:
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status >= 300:
                raise EmailError(f"{provider} send failed: HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace").strip()
        except Exception:  # pragma: no cover - best-effort diagnostics
            pass
        detail = f": {body}" if body else ""
        raise EmailError(f"{provider} send failed: HTTP {exc.code}{detail}") from exc
    except OSError as exc:
        raise EmailError(f"{provider} send failed: {exc}") from exc


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise EmailError(f"{name} is required.")
    return value
