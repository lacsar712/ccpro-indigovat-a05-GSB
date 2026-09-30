"""染缸状态业务规则。"""

from decimal import Decimal
from typing import Optional, Sequence

from app.models import CleanHandover, DipLot, Vat

READY_REDOX_THRESHOLD = Decimal("-500")
HANDOVER_LOT_WINDOW = 5


class VatRuleError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def assert_can_mark_ready(latest: Optional[DipLot]) -> None:
    """不能将染缸标为 ready，除非最新浸染批次 redoxMv 已填且 <= -500。

    该函数同时是「改状态为可染色」与「清缸交接卷完成复核」共用的电位门槛。
    """
    if latest is None or latest.redoxMv is None or Decimal(latest.redoxMv) > READY_REDOX_THRESHOLD:
        raise VatRuleError(
            "无法设为可染色：最新浸染批次的氧化还原电位为空或高于 -500 mV。"
        )


def assert_can_mark_idle(
    vat: Vat,
    active_handover: Optional[CleanHandover],
    last_handover: Optional[CleanHandover],
) -> None:
    """改闲置门槛：进行中禁闲置；可染色缸须先完成一张清缸交接卷。"""
    if active_handover is not None:
        raise VatRuleError(
            f"染缸 {vat.code} 的清缸交接卷进行中，禁止改闲置；请先在交接专页完成交接。"
        )
    if vat.status == Vat.STATUS_READY and (
        last_handover is None or last_handover.finishedAt is None
    ):
        raise VatRuleError(
            f"可染色缸 {vat.code} 退回闲置前，必须先在清缸交接专页开卷并完成交接。"
        )


def validate_vat_status_change(
    vat: Vat,
    new_status: str,
    latest: Optional[DipLot],
    active_handover: Optional[CleanHandover] = None,
    last_handover: Optional[CleanHandover] = None,
) -> None:
    """改状态统一入口：可染色电位门槛 + 改闲置的交接卷条件，同一函数校验。"""
    if new_status == Vat.STATUS_READY:
        assert_can_mark_ready(latest)
    if new_status == Vat.STATUS_IDLE:
        assert_can_mark_idle(vat, active_handover, last_handover)


def handover_meters_cap(lots: Sequence[DipLot]) -> Decimal:
    """清出米数上限：该缸最近 5 笔浸染布米合计。"""
    recent = sorted(lots, key=lambda x: (x.dippedAt, x.id), reverse=True)[:HANDOVER_LOT_WINDOW]
    return sum((Decimal(l.clothMeters) for l in recent), Decimal("0"))


def assert_can_open_handover(vat: Vat) -> None:
    """仅可染色缸可开清缸交接卷。"""
    if vat.status != Vat.STATUS_READY:
        raise VatRuleError(
            f"染缸 {vat.code} 当前不是可染色状态，仅可染色缸可开清缸交接卷。"
        )


def validate_handover_ticket(
    vat: Vat,
    cleared_meters: Decimal,
    lots: Sequence[DipLot],
    active_handover: Optional[CleanHandover],
    self_id: Optional[int] = None,
) -> None:
    """开卷新建与更新共用：同缸进行中唯一 + 清出米数为正且不超上限。"""
    if active_handover is not None and active_handover.id != self_id:
        raise VatRuleError(
            f"染缸 {vat.code} 已有进行中的交接卷（#{active_handover.id}），同缸进行中只许一张。"
        )
    if cleared_meters is None or cleared_meters <= 0:
        raise VatRuleError("清出米数须为正数。")
    cap = handover_meters_cap(lots)
    if cleared_meters > cap:
        raise VatRuleError(
            f"清出米数 {cleared_meters} m 超出上限：该缸最近 {HANDOVER_LOT_WINDOW} 笔浸染布米合计 {cap} m。"
        )


def complete_handover(ticket: CleanHandover, latest: Optional[DipLot], finished_at) -> None:
    """主管完成：落下完工时间，并再次核对最新浸染读数仍满足可染色电位门槛。

    电位复核与改状态入口读同一函数 assert_can_mark_ready。
    """
    if ticket.finishedAt is not None:
        raise VatRuleError(f"交接卷 #{ticket.id} 已完成，不能重复完工。")
    assert_can_mark_ready(latest)
    ticket.finishedAt = finished_at
