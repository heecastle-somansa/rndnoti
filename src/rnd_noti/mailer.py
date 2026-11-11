"""메일 발송 (개발요청서 F10, 5.3, 7.1).

- 구독자마다 별도 메일 1통 (To에 본인 주소 하나만 → 수신자끼리 주소가 보이지 않음)
- HTML + 텍스트를 multipart/alternative로 함께 보낸다
- 서버·계정 정보는 모두 비밀값(환경변수)에서 읽는다
- 오류 메시지·로그에 메일 주소나 비밀번호를 남기지 않는다
"""
from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Mapping


class MailError(Exception):
    """메시지에는 주소·비밀번호를 넣지 않는다."""


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    user: str
    password: str
    from_name: str = "R&D Noti"
    reply_to: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "MailSettings":
        missing = [k for k in ("SMTP_USER", "SMTP_PASSWORD") if not env.get(k)]
        if missing:
            raise MailError(f"메일 발송 비밀값이 없습니다: {', '.join(missing)}")
        try:
            port = int(env.get("SMTP_PORT") or 587)
        except ValueError:
            raise MailError("SMTP_PORT는 숫자여야 합니다(예: 587)") from None
        return cls(
            host=env.get("SMTP_HOST") or "smtp.gmail.com", port=port,
            user=env["SMTP_USER"], password=env["SMTP_PASSWORD"],
            from_name=env.get("MAIL_FROM_NAME") or "R&D Noti",
            reply_to=env.get("MAIL_REPLY_TO") or "",
        )


def build_message(settings: MailSettings, to_addr: str, subject: str, html: str, text: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.from_name, settings.user))
    msg["To"] = to_addr                              # 한 통에 한 사람만
    msg["Reply-To"] = settings.reply_to or settings.user
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=settings.user.rsplit("@", 1)[-1] if "@" in settings.user else None)
    msg.set_content(text)                            # text/plain
    msg.add_alternative(html, subtype="html")        # → multipart/alternative
    return msg


class Mailer:
    """with Mailer(settings) as m: m.send(msg) — 연결 한 번으로 여러 통을 따로 보낸다."""

    def __init__(self, settings: MailSettings, smtp_factory=None) -> None:
        self.settings = settings
        self._factory = smtp_factory
        self._smtp: smtplib.SMTP | None = None

    def __enter__(self) -> "Mailer":
        s = self.settings
        try:
            if self._factory:
                self._smtp = self._factory(s.host, s.port)
            elif s.port == 465:
                self._smtp = smtplib.SMTP_SSL(s.host, s.port, timeout=60, context=ssl.create_default_context())
            else:
                self._smtp = smtplib.SMTP(s.host, s.port, timeout=60)
                self._smtp.starttls(context=ssl.create_default_context())
            self._smtp.login(s.user, s.password)
        except smtplib.SMTPAuthenticationError:
            raise MailError("메일 서버 로그인 실패(SMTP_USER·SMTP_PASSWORD, Gmail은 앱 비밀번호 확인)") from None
        except (smtplib.SMTPException, OSError) as exc:
            raise MailError(f"메일 서버 연결 실패({type(exc).__name__}, SMTP_HOST·SMTP_PORT 확인)") from None
        return self

    def send(self, msg: EmailMessage) -> None:
        assert self._smtp is not None
        try:
            refused = self._smtp.send_message(msg)
        except smtplib.SMTPRecipientsRefused:
            raise MailError("수신 주소가 거부됐습니다(주소 오타 확인)") from None
        except (smtplib.SMTPException, OSError) as exc:
            raise MailError(f"발송 실패({type(exc).__name__})") from None
        if refused:
            raise MailError("수신 주소가 거부됐습니다(주소 오타 확인)")

    def __exit__(self, *exc) -> None:
        if self._smtp is not None:
            try:
                self._smtp.quit()
            except (smtplib.SMTPException, OSError):
                pass
