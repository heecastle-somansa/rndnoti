"""메일 발송 (개발요청서 F10, 5.3, 7.1).

- 구독자마다 별도 메일 1통 (To에 본인 주소 하나만 → 수신자끼리 주소가 보이지 않음)
- HTML + 텍스트를 multipart/alternative로 함께 보낸다
- 서버·계정 정보는 모두 비밀값(환경변수)에서 읽는다
- 오류 메시지·로그에 메일 주소나 비밀번호를 남기지 않는다
"""
from __future__ import annotations

import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Mapping


class MailError(Exception):
    """메시지에는 주소·비밀번호를 넣지 않는다."""


_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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
        # Secrets에 붙여넣을 때 끝에 줄바꿈·빈칸이 섞여 들어가는 일이 흔해서 앞뒤 공백은 떼고 쓴다
        user = env["SMTP_USER"].strip()
        if not is_address(user) and len(find_addresses(user)) == 1:
            user = find_addresses(user)[0]             # 따옴표·이름이 섞여 있어도 주소 하나면 그것을 쓴다
        reply_to = (env.get("MAIL_REPLY_TO") or "").strip()
        for name, value in (("SMTP_USER", user), ("MAIL_REPLY_TO", reply_to)):
            if value and not is_address(value):
                raise MailError(f"{name} 값이 메일 주소 형식이 아닙니다({describe_bad_value(value)})")
        return cls(
            host=(env.get("SMTP_HOST") or "").strip() or "smtp.gmail.com", port=port,
            user=user, password=env["SMTP_PASSWORD"].strip(),
            from_name=(env.get("MAIL_FROM_NAME") or "").strip() or "R&D Noti",
            reply_to=reply_to,
        )


_ADDRESS_ANYWHERE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def find_addresses(value: str) -> list[str]:
    """값 안에서 메일 주소만 골라낸다(중복 제거).

    Secrets에 주소를 넣을 때 따옴표·꺾쇠·이름·줄바꿈이 섞이거나 여러 주소를 적는 경우가 있어,
    형식이 조금 달라도 주소를 찾아서 쓴다.  예) '"a@x.com"', '홍길동 <a@x.com>', 'a@x.com, b@x.com'
    """
    out: list[str] = []
    for addr in _ADDRESS_ANYWHERE.findall(value or ""):
        if addr not in out:
            out.append(addr)
    return out


def describe_bad_value(value: str) -> str:
    """주소를 못 찾았을 때, 값 자체는 드러내지 않고 원인을 짐작할 단서만 알려 준다."""
    hints = [f"글자 수 {len(value)}", "'@' 있음" if "@" in value else "'@' 없음"]
    if any(ord(c) > 127 for c in value):
        hints.append("한글 등 영문이 아닌 글자 포함")
    if value.lstrip().startswith(("[", "{")):
        hints.append("[ 또는 { 로 시작(구독자 목록을 넣은 것은 아닌지 확인)")
    return ", ".join(hints)


def is_address(value: str) -> bool:
    """메일 주소 한 개인지(빈칸·줄바꿈 없음)."""
    return bool(_ADDRESS.match(value))


def build_message(settings: MailSettings, to_addr: str, subject: str, html: str, text: str) -> EmailMessage:
    to_addr = to_addr.strip()
    if not is_address(to_addr):
        # 주소 자체는 메시지에 넣지 않는다(로그에 남으므로)
        raise MailError("받는 사람 주소가 메일 주소 형식이 아닙니다(중간의 줄바꿈·빈칸·오타 확인)")
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
