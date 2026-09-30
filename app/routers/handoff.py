"""清缸交接专页：进行中列表、开卷/改卷、主管完成。

可染色缸退回闲置前，必须先在此开清缸交接卷并由主管完工。
并发双开由 cleaning_tickets 的部分唯一索引在数据库层兜底。
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError

from app.auth import get_current_user
from app.db import get_db
from app.models import CleaningTicket, User, Vat
from app.services.vat_rules import (
    VatRuleError,
    assert_can_complete_ticket,
    assert_can_open_ticket,
    recent_cloth_total,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _need_login(request: Request, db: Session):
    return get_current_user(request, db)


def _render(request: Request, name: str, context: dict, status_code: int = 200):
    ctx = {k: v for k, v in context.items() if k != "request"}
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def _fmt_dt(value: Optional[datetime]) -> Optional[str]:
    return value.strftime("%Y-%m-%d %H:%M") if value else None


def _load_ready_vats(db: Session) -> list[Vat]:
    return (
        db.query(Vat)
        .options(
            joinedload(Vat.workshop),
            joinedload(Vat.lots),
            joinedload(Vat.tickets),
        )
        .filter(Vat.status == Vat.STATUS_READY)
        .order_by(Vat.code)
        .all()
    )


def _load_open_tickets(db: Session) -> list[CleaningTicket]:
    return (
        db.query(CleaningTicket)
        .options(
            joinedload(CleaningTicket.vat).joinedload(Vat.workshop),
            joinedload(CleaningTicket.vat).joinedload(Vat.lots),
            joinedload(CleaningTicket.opener),
            joinedload(CleaningTicket.completed_by),
        )
        .filter(CleaningTicket.completedAt.is_(None))
        .order_by(CleaningTicket.openedAt.desc(), CleaningTicket.id.desc())
        .all()
    )


def _load_done_tickets(db: Session, limit: int = 8) -> list[CleaningTicket]:
    return (
        db.query(CleaningTicket)
        .options(
            joinedload(CleaningTicket.vat).joinedload(Vat.workshop),
            joinedload(CleaningTicket.opener),
            joinedload(CleaningTicket.completed_by),
        )
        .filter(CleaningTicket.completedAt.is_not(None))
        .order_by(CleaningTicket.completedAt.desc(), CleaningTicket.id.desc())
        .limit(limit)
        .all()
    )


def _ticket_payload(ticket: CleaningTicket) -> dict:
    cap = recent_cloth_total(list(ticket.vat.lots))
    return {
        "id": ticket.id,
        "vatId": ticket.vat_id,
        "vatCode": ticket.vat.code,
        "workshopName": ticket.vat.workshop.name if ticket.vat.workshop else "",
        "clothMeters": float(ticket.clothMeters),
        "receiveTeam": ticket.receiveTeam,
        "openerName": ticket.opener.username if ticket.opener else "?",
        "openedAt": _fmt_dt(ticket.openedAt),
        "completedAt": _fmt_dt(ticket.completedAt),
        "completedByName": ticket.completed_by.username if ticket.completed_by else None,
        "cap": float(cap),
    }


def _handoff_context(
    request: Request,
    db: Session,
    user: User,
    error: Optional[str] = None,
    form: Optional[dict] = None,
) -> dict:
    ready_vats = _load_ready_vats(db)
    open_cards = []
    open_vat_ids: set[int] = set()
    for ticket in _load_open_tickets(db):
        open_cards.append(_ticket_payload(ticket))
        open_vat_ids.add(ticket.vat_id)

    openable = []
    for vat in ready_vats:
        openable.append(
            {
                "id": vat.id,
                "code": vat.code,
                "workshopName": vat.workshop.name if vat.workshop else "",
                "cap": float(recent_cloth_total(list(vat.lots))),
                "hasOpen": vat.id in open_vat_ids,
            }
        )

    done_cards = [_ticket_payload(t) for t in _load_done_tickets(db)]

    return {
        "request": request,
        "user": user,
        "is_supervisor": bool(user.is_superuser),
        "open_tickets": open_cards,
        "openable": openable,
        "done_tickets": done_cards,
        "error": error,
        "form": form or {"vat_id": "", "clothMeters": "", "receiveTeam": ""},
        "active": "handoff",
    }


@router.get("/handoff", response_class=HTMLResponse)
async def handoff_page(request: Request, db: Session = Depends(get_db)):
    user = _need_login(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return _render(request, "handoff.html", _handoff_context(request, db, user))


@router.post("/handoff/tickets", response_class=HTMLResponse)
async def handoff_create(
    request: Request,
    vat_id: str = Form(...),
    clothMeters: str = Form(...),
    receiveTeam: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _need_login(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    form = {"vat_id": vat_id, "clothMeters": clothMeters, "receiveTeam": receiveTeam}
    try:
        vat_pk = int(vat_id)
        meters = Decimal(clothMeters)
        team = receiveTeam.strip()
        if not team:
            raise VatRuleError("接收班组不能为空。")
        vat = (
            db.query(Vat)
            .options(joinedload(Vat.lots), joinedload(Vat.tickets))
            .filter(Vat.id == vat_pk)
            .first()
        )
        if vat is None:
            raise VatRuleError("所选染缸不存在。")
        # 新建与更新共用同缸进行中唯一与米数上限校验
        assert_can_open_ticket(vat, meters, list(vat.lots), vat.open_ticket())
        ticket = CleaningTicket(
            vat_id=vat.id,
            opener_id=user.id,
            clothMeters=meters,
            receiveTeam=team,
            openedAt=datetime.now(timezone.utc),
        )
        db.add(ticket)
        db.commit()
        return RedirectResponse("/handoff", status_code=303)
    except (VatRuleError, ValueError, InvalidOperation) as exc:
        db.rollback()
        message = exc.message if isinstance(exc, VatRuleError) else f"清出米数无效：{exc}"
    except IntegrityError:
        # 两人几乎同时给同一缸开卷：部分唯一索引只放行一张
        db.rollback()
        message = "该缸刚被他人开出进行中的交接卷，同缸同时只许一张，请刷新后重试。"
    return _render(
        request,
        "handoff.html",
        _handoff_context(request, db, user, message, form),
        status_code=400,
    )


@router.post("/handoff/tickets/{pk}/update", response_class=HTMLResponse)
async def handoff_update(
    pk: int,
    request: Request,
    clothMeters: str = Form(...),
    receiveTeam: str = Form(...),
    db: Session = Depends(get_db),
):
    user = _need_login(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    ticket = (
        db.query(CleaningTicket)
        .options(
            joinedload(CleaningTicket.vat).joinedload(Vat.lots),
            joinedload(CleaningTicket.vat).joinedload(Vat.tickets),
        )
        .filter(CleaningTicket.id == pk)
        .first()
    )
    if ticket is None:
        return RedirectResponse("/handoff", status_code=303)
    try:
        meters = Decimal(clothMeters)
        team = receiveTeam.strip()
        if not team:
            raise VatRuleError("接收班组不能为空。")
        other = (
            db.query(CleaningTicket)
            .filter(
                CleaningTicket.vat_id == ticket.vat_id,
                CleaningTicket.completedAt.is_(None),
                CleaningTicket.id != ticket.id,
            )
            .first()
        )
        # 与新建共用进行中唯一与米数上限校验；可染色门槛只在开卷时卡
        assert_can_open_ticket(
            ticket.vat, meters, list(ticket.vat.lots), other, require_ready=False
        )
        ticket.clothMeters = meters
        ticket.receiveTeam = team
        db.commit()
        return RedirectResponse("/handoff", status_code=303)
    except (VatRuleError, ValueError, InvalidOperation) as exc:
        db.rollback()
        message = exc.message if isinstance(exc, VatRuleError) else f"清出米数无效：{exc}"
    return _render(
        request,
        "handoff.html",
        _handoff_context(request, db, user, message),
        status_code=400,
    )


@router.post("/handoff/tickets/{pk}/complete", response_class=HTMLResponse)
async def handoff_complete(
    pk: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = _need_login(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    ticket = (
        db.query(CleaningTicket)
        .options(joinedload(CleaningTicket.vat).joinedload(Vat.lots))
        .filter(CleaningTicket.id == pk)
        .first()
    )
    if ticket is None:
        return RedirectResponse("/handoff", status_code=303)
    try:
        if not user.is_superuser:
            raise VatRuleError("仅主管可以完成清缸交接卷。")
        # 再次核对该缸最新浸染读数仍满足可染色电位门槛（<= -500 mV）
        assert_can_complete_ticket(ticket, ticket.vat, ticket.vat.latest_lot())
        ticket.completedAt = datetime.now(timezone.utc)
        ticket.completed_by_id = user.id
        db.commit()
        return RedirectResponse("/handoff", status_code=303)
    except VatRuleError as exc:
        db.rollback()
        message = exc.message
    return _render(
        request,
        "handoff.html",
        _handoff_context(request, db, user, message),
        status_code=400,
    )
