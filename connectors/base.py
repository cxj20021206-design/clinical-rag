"""连接器基类：把某个外部源的原始返回映射成 ExternalStandard 记录。"""
from __future__ import annotations
import sys, os
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from schema import ExternalStandard  # noqa: E402

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")


class Connector:
    source_id: str = "base"
    issuing_body: str = ""
    source_role: str = ""
    tier: int | None = None
    machine_access: str = "api"

    def search(self, query_context: dict, submission_date: str | None = None,
               limit: int = 5) -> list[ExternalStandard]:
        """query_context: 结构化 PICO/Intended-Use (population/condition/
        intervention/comparator/outcome/setting/region)。返回 ExternalStandard 列表。"""
        raise NotImplementedError

    @staticmethod
    def today() -> str:
        return date.today().isoformat()
