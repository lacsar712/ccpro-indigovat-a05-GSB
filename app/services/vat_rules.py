"""染缸状态与清缸交接卷业务规则。"""

from decimal import Decimal
from typing import Optional

from app.models import CleaningTicket, DipLot, Vat


class VatRuleError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def recent_cloth_total(lots: list[DipLot], limit: int = 5) -> Decimal:
    """最近 limit 笔浸染布米合计，作为清出米数上限；无记录时为 0。"""
    ordered = sorted(lots, key=lambda x: (x.dippedAt, x.id), reverse=True)[:limit]
    return sum((Decimal(l.clothMeters) for l in ordered), Decimal("0"))


def assert_can_mark_ready(latest: Optional[DipLot]) -> None:
    """不能将染缸标为 ready，除非最新浸染批次 redoxMv 已填且 <= -500。"""
    if latest is None or latest.redoxMv is None or Decimal(latest.redoxMv) > Decimal("-500"):
        raise VatRuleError(
            "无法设为可染色：最新浸染批次的氧化还原电位为空或高于 -500 mV。"
        )


def assert_can_open_ticket(
    vat: Vat,
    cloth_meters: Decimal,
    latest_lots: list[DipLot],
    other_open_ticket: Optional[CleaningTicket] = None,
    require_ready: bool = True,
) -> Decimal:
    """开卷（新建）与改卷（更新）共用的校验。

    - 仅可染色（ready）缸可开卷（更新已存在的卷时 require_ready=False）；
    - 同缸进行中卷至多一张（更新自身时 other_open_ticket 传 None）；
    - 清出米数须为正，且不超过最近 5 笔浸染布米合计。

    通过时返回米数上限，便于调用方展示。
    """
    if require_ready and vat.status != Vat.STATUS_READY:
        raise VatRuleError("仅可染色状态的染缸可以开清缸交接卷。")
    if other_open_ticket is not None:
        raise VatRuleError("该缸已有进行中的清缸交接卷，同缸同时只许一张。")
    cap = recent_cloth_total(latest_lots)
    if cloth_meters <= 0:
        raise VatRuleError("清出米数须为正数。")
    if cloth_meters > cap:
        raise VatRuleError(
            f"清出米数超出上限：最近 5 笔浸染布米合计为 {cap} 米。"
        )
    return cap


def assert_can_complete_ticket(
    ticket: CleaningTicket, vat: Vat, latest: Optional[DipLot]
) -> None:
    """主管完成：卷仍须进行中，且最新浸染读数仍满足可染色电位门槛。"""
    if ticket.completedAt is not None:
        raise VatRuleError("该交接卷已完工，无需重复完成。")
    assert_can_mark_ready(latest)


def validate_vat_status_change(
    vat: Vat,
    new_status: str,
    latest: Optional[DipLot],
    open_ticket: Optional[CleaningTicket] = None,
) -> None:
    """改缸状态的唯一校验入口（还原台与各表单共用）。

    - 设为可染色：最新浸染批次 redoxMv 须已填且 <= -500；
    - 退回闲置：该缸不得有进行中的清缸交接卷。
    """
    if new_status == Vat.STATUS_READY:
        assert_can_mark_ready(latest)
    if new_status == Vat.STATUS_IDLE and open_ticket is not None:
        raise VatRuleError(
            "该缸有进行中的清缸交接卷，须由主管完成交接卷后才可退回闲置。"
        )
