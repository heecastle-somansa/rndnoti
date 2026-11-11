"""나라장터 사전규격 (조달청_나라장터 사전규격정보서비스).

마감일(deadline)은 의견등록 마감일이다. 메일에는 '의견 D-n'으로 표시된다.
"""
from __future__ import annotations

from typing import Any

from ..models import Notice
from .common import parse_amount, parse_date, title_is_revision
from .g2b_base import G2BAdapterBase
from .registry import register


@register("api_g2b_prespec")
class G2BPrespecAdapter(G2BAdapterBase):
    SERVICE_NAME = "조달청_나라장터 사전규격정보서비스"
    BASE_URL = "https://apis.data.go.kr/1230000/ao/HrcspSsstndrdInfoService"
    OPERATIONS = {
        "용역": "getPublicPrcureThngInfoServc",
        "물품": "getPublicPrcureThngInfoThng",
        "공사": "getPublicPrcureThngInfoCnstwk",
        "외자": "getPublicPrcureThngInfoFrgcpt",
    }

    def to_notice(self, item: dict[str, Any]) -> Notice | None:
        no = str(item.get("bfSpecRgstNo") or "").strip()
        title = str(item.get("prdctClsfcNoNm") or item.get("bfSpecNm") or "").strip()
        if not no or not title:
            return None
        return Notice(
            uid=f"g2b_pre:{no}",
            source=self.cfg.id,
            source_name=self.cfg.name,
            title=title,
            # 사전규격 API에는 상세 링크 필드가 확인되지 않아, 없으면 sources.yaml의 url(목록 화면)을 쓴다
            url=str(item.get("bfSpecDtlUrl") or self.cfg.url),
            agency_group=self.cfg.group,
            ministry=self.cfg.ministry,
            notice_type=self.cfg.notice_type,
            is_revision=title_is_revision(title),
            org=str(item.get("rlDminsttNm") or item.get("orderInsttNm") or "").strip(),
            budget_krw=parse_amount(item.get("asignBdgtAmt")),
            contract_info="",
            posted_at=parse_date(item.get("rcptDt") or item.get("rgstDt")),
            deadline=parse_date(item.get("opninRgstClseDt")),
        )
