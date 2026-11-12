"""나라장터(조달청 공공데이터포털 API) 공통 처리.

- 업무구분(용역·물품·공사·외자)별 오퍼레이션을 차례로 호출
- 수집 기간을 7일 단위로 나눠 조회 (API의 조회 기간 제한 대비)
- 모든 페이지 순회 (F2)
- 공공데이터포털 오류(인증키 미등록, 트래픽 초과 등)를 한국어로 바꿔 알림

[주의] 엔드포인트·필드명은 공공데이터포털 문서 기준이며 실제 호출은 아직 확인 전이다.
       주소가 바뀌면 sources.yaml의 options.base_url로 코드 수정 없이 바꿀 수 있다.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any, Iterator, Mapping

from ..config import SourceConfig
from ..models import Notice
from .base import SourceAdapter, SourceError
from .http import HttpClient

KEY_NAME = "DATA_GO_KR_KEY"
PAGE_SIZE = 500              # 한 번에 받는 건수. 클수록 호출 수가 줄어 하루 한도를 아낀다 (options.page_size로 변경)
MAX_DAYS_PER_QUERY = 7

_ERROR_HINTS = {
    "SERVICE_KEY_IS_NOT_REGISTERED_ERROR": "인증키가 등록되지 않았거나 활용신청 승인 전입니다",
    "SERVICE_ACCESS_DENIED_ERROR": "이 API에 대한 활용신청이 없거나 접근이 거부됐습니다",
    "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR": "하루 호출 한도를 넘었습니다",
    "DEADLINE_HAS_EXPIRED_ERROR": "활용 기간이 만료됐습니다",
    "UNREGISTERED_IP_ERROR": "등록되지 않은 IP에서 호출했습니다(해외 IP 차단 가능성)",
}


class G2BAdapterBase(SourceAdapter):
    """하위 클래스는 BASE_URL, OPERATIONS, to_notice()를 정한다."""

    BASE_URL: str = ""
    SERVICE_NAME: str = ""                     # 공공데이터포털에서 활용신청하는 API 이름
    OPERATIONS: dict[str, str] = {}            # 업무구분 이름 → 오퍼레이션명
    DEFAULT_OPERATIONS: tuple[str, ...] = ("용역", "물품")

    def __init__(self, cfg: SourceConfig, secrets: Mapping[str, str] | None = None,
                 http: HttpClient | None = None) -> None:
        super().__init__(cfg, secrets)
        self.key = self.secrets.get(KEY_NAME, "")
        self.http = http or HttpClient(secrets=[self.key])
        self.page_size = max(1, min(int(self.cfg.options.get("page_size") or PAGE_SIZE), 999))

    # ----- 하위 클래스 구현 -----
    def to_notice(self, item: dict[str, Any]) -> Notice | None:
        raise NotImplementedError

    # ----- 공통 흐름 -----
    def _operations(self) -> list[str]:
        names = self.cfg.options.get("operations") or list(self.DEFAULT_OPERATIONS)
        unknown = [n for n in names if n not in self.OPERATIONS]
        if unknown:
            raise SourceError(
                f"options.operations에 알 수 없는 업무구분: {', '.join(unknown)} "
                f"(가능: {', '.join(self.OPERATIONS)})")
        # options.operation_names = {용역: 새이름} : 포털에서 기능(오퍼레이션) 이름이 바뀌었을 때 코드 수정 없이 덮어쓴다
        renamed = self.cfg.options.get("operation_names") or {}
        return [str(renamed.get(n) or self.OPERATIONS[n]) for n in names]

    def fetch(self, since: date, until: date) -> list[Notice]:
        if not self.key:
            raise SourceError(f"인증키({KEY_NAME})가 Secrets에 등록되지 않았습니다")
        base = str(self.cfg.options.get("base_url") or self.BASE_URL).rstrip("/")
        notices: list[Notice] = []
        op = ""
        raw_count, sample_keys = 0, ""
        try:
            for op in self._operations():
                for start, end in _split_period(since, until, MAX_DAYS_PER_QUERY):
                    for item in self._iter_items(f"{base}/{op}", start, end):
                        raw_count += 1
                        sample_keys = sample_keys or ", ".join(list(item)[:12])
                        notice = self.to_notice(item)
                        if notice is not None:
                            notices.append(notice)
        except SourceError as exc:
            if "404" in str(exc):
                raise SourceError(
                    f"서버 응답 404: 호출한 주소가 없습니다: {base}/{op} · 포털의 API 상세 화면에서 기능별 "
                    "'요청주소'를 확인해, 주소 앞부분이 다르면 sources.yaml의 options.base_url에, 맨 끝 기능 이름이 "
                    "다르면 options.operation_names(예: {용역: 새이름})에 적어 주세요") from None
            if "403" in str(exc):
                # 공공데이터포털의 'Forbidden' = 이 인증키로 이 주소의 활용신청 내역을 찾지 못했다는 뜻
                raise SourceError(
                    "서버 응답 403(Forbidden): 공공데이터포털이 '이 인증키로는 이 API의 활용신청 내역을 찾지 못했다'고 "
                    f"답했습니다. 호출한 주소: {base}/{op} · 확인 순서: ① [마이페이지 → 활용신청 현황]에서 "
                    f"'{self.SERVICE_NAME}'가 [승인]인지 ② 그 상세 화면의 End Point(요청주소)가 위 주소와 같은지"
                    "(이름이 비슷한 다른 API를 신청한 경우가 많습니다) ③ 상세 화면의 [미리보기]가 포털 안에서 "
                    "성공하는지 ④ Secrets의 DATA_GO_KR_KEY가 그 신청을 한 계정의 인증키인지. "
                    "주소가 다르면 sources.yaml의 options.base_url에 포털의 주소를 적으면 됩니다. "
                    "방금 승인됐다면 반영까지 1~2시간 걸릴 수 있습니다") from None
            raise
        if raw_count and not notices:
            # 받긴 받았는데 한 건도 읽지 못함 = 응답 항목 이름이 바뀐 것. 조용히 0건으로 넘어가지 않게 알린다
            raise SourceError(f"{raw_count}건을 받았지만 공고번호·공고명 항목을 찾지 못했습니다(응답 형식 변경 추정). "
                              f"받은 항목 이름: {sample_keys}")
        return notices

    def _iter_items(self, url: str, start: date, end: date) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            params = {
                "serviceKey": self.key,
                "pageNo": page,
                "numOfRows": self.page_size,
                "inqryDiv": 1,                                  # 1 = 등록(게시)일시 기준
                "inqryBgnDt": start.strftime("%Y%m%d") + "0000",
                "inqryEndDt": end.strftime("%Y%m%d") + "2359",
                "type": "json",
            }
            resp = self.http.get(url, params)
            items, total = parse_response(resp.text)
            yield from items
            if not items or page * self.page_size >= total:
                return
            page += 1


def _split_period(since: date, until: date, days: int) -> Iterator[tuple[date, date]]:
    start = since
    while start <= until:
        end = min(start + timedelta(days=days - 1), until)
        yield start, end
        start = end + timedelta(days=1)


def parse_response(text: str) -> tuple[list[dict[str, Any]], int]:
    """공공데이터포털 응답 → (항목 목록, 전체 건수). 오류 응답이면 SourceError."""
    try:
        data = json.loads(text)
    except ValueError:
        # 인증 오류 등은 type=json이어도 XML로 오는 경우가 있다
        code = _xml_value(text, "returnAuthMsg") or _xml_value(text, "resultMsg") or _xml_value(text, "errMsg")
        raise SourceError(_explain(code or "응답을 해석할 수 없습니다(JSON 아님)")) from None

    resp = data.get("response", data)
    header = resp.get("header", {}) or {}
    code = str(header.get("resultCode", "00"))
    if code not in ("00", "0", "000"):
        raise SourceError(_explain(str(header.get("resultMsg") or code)))
    body = resp.get("body", {}) or {}
    items = body.get("items") or []
    if isinstance(items, dict):
        items = items.get("item") or []
    if isinstance(items, dict):
        items = [items]
    total = int(body.get("totalCount") or 0)
    return list(items), total


def _xml_value(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\s*([^<]+?)\s*</{tag}>", text)
    return m.group(1) if m else ""


def _explain(code: str) -> str:
    for key, hint in _ERROR_HINTS.items():
        if key in code:
            return f"공공데이터포털 오류: {hint} ({key})"
    return f"공공데이터포털 오류: {code[:200]}"
