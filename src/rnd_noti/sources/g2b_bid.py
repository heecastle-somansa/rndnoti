"""나라장터 입찰공고 (조달청_나라장터 입찰공고정보서비스).

uid는 공고번호(차수 제외)로 만들어, 같은 공고의 정정 차수가 하나로 묶이게 한다(F4).
취소공고는 수집하지 않는다.
"""
from __future__ import annotations

from typing import Any

from ..models import Notice
from .common import parse_amount, parse_date, title_is_revision
from .g2b_base import G2BAdapterBase
from .registry import register


@register("api_g2b_bid")
class G2BBidAdapter(G2BAdapterBase):
    SERVICE_NAME = "조달청_나라장터 입찰공고정보서비스"
    BASE_URL = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"
    OPERATIONS = {
        "용역": "getBidPblancListInfoServc",
        "물품": "getBidPblancListInfoThng",
        "공사": "getBidPblancListInfoCnstwk",
        "외자": "getBidPblancListInfoFrgcpt",
    }

    def to_notice(self, item: dict[str, Any]) -> Notice | None:
        no = str(item.get("bidNtceNo") or "").strip()
        title = str(item.get("bidNtceNm") or "").strip()
        if not no or not title:
            return None
        kind = str(item.get("ntceKindNm") or "")
        if "취소" in kind:
            return None
        try:
            order = int(str(item.get("bidNtceOrd") or "0"))
        except ValueError:
            order = 0
        is_revision = (order > 0 or any(k in kind for k in ("재공고", "변경", "정정"))
                       or title_is_revision(title))
        budget = parse_amount(item.get("asignBdgtAmt")) or parse_amount(item.get("presmptPrce"))
        return Notice(
            uid=f"g2b:{no}",
            source=self.cfg.id,
            source_name=self.cfg.name,
            title=title,
            url=str(item.get("bidNtceDtlUrl") or item.get("bidNtceUrl") or self.cfg.url),
            agency_group=self.cfg.group,
            ministry=self.cfg.ministry,
            notice_type=self.cfg.notice_type,
            is_revision=is_revision,
            revision_no=order,
            org=str(item.get("dminsttNm") or item.get("ntceInsttNm") or "").strip(),
            budget_krw=budget,
            contract_info=str(item.get("cntrctCnclsMthdNm") or "").strip(),
            posted_at=parse_date(item.get("bidNtceDt")),
            deadline=parse_date(item.get("bidClseDt")),
        )
