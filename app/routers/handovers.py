from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user
from app.db import get_db
from app.models import CleanHandover, Vat
from app.services.vat_rules import (
    VatRuleError,
    assert_can_open_handover,
    complete_handover,
    handover_meters_cap,
    validate_handover_ticket,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

FMT = "%Y-%m-%d %H:%M"


def _fmt(dt) -> str:
    return dt.strftime(FMT) if dt else "—"


def _active_query(db: Session):
    return db.query(CleanHandover).filter(CleanHandover.finishedAt.is_(None))


def _get_active(db: Session, vat_id: int) -> Optional[CleanHandover]:
    return _active_query(db).filter(CleanHandover.vat_id == vat_id).first()


def _ticket_payload(t: CleanHandover) -> dict:
    vat = t.vat
    return {
        "id": t.id,
        "vatId": vat.id,
        "vatCode": vat.code,
        "workshopName": vat.workshop.name if vat.workshop else "",
        "dyeType": vat.dyeType,
        "opener": t.opener.username if t.opener else "",
        "clearedMeters": f"{Decimal(t.clearedMeters):.2f}",
        "cap": f"{handover_meters_cap(vat.lots):.2f}",
        "receivingTeam": t.receivingTeam,
        "openedAt": _fmt(t.openedAt),
        "finishedAt": _fmt(t.finishedAt),
    }


def _handover_context(
    request: Request,
    db: Session,
    user,
    error: Optional[str] = None,
):
    active = (
        _active_query(db)
        .options(
            joinedload(CleanHandover.vat).joinedload(Vat.workshop),
            joinedload(CleanHandover.vat).joinedload(Vat.lots),
            joinedload(CleanHandover.opener),
        )
        .order_by(CleanHandover.openedAt.desc(), CleanHandover.id.desc())
        .all()
    )
    busy_vat_ids = {t.vat_id for t in active}
    ready_vats = (
        db.query(Vat)
        .options(joinedload(Vat.workshop), joinedload(Vat.lots))
        .filter(Vat.status == Vat.STATUS_READY)
        .order_by(Vat.code)
        .all()
    )
    eligible = [
        {
            "id": v.id,
            "code": v.code,
            "workshopName": v.workshop.name if v.workshop else "",
            "cap": f"{handover_meters_cap(v.lots):.2f}",
        }
        for v in ready_vats
        if v.id not in busy_vat_ids
    ]
    completed = (
        db.query(CleanHandover)
        .options(
            joinedload(CleanHandover.vat).joinedload(Vat.workshop),
            joinedload(CleanHandover.vat).joinedload(Vat.lots),
            joinedload(CleanHandover.opener),
        )
        .filter(CleanHandover.finishedAt.isnot(None))
        .order_by(CleanHandover.finishedAt.desc(), CleanHandover.id.desc())
        .limit(10)
        .all()
    )
    return {
        "request": request,
        "user": user,
        "active_tickets": [_ticket_payload(t) for t in active],
        "eligible_vats": eligible,
        "completed_tickets": [_ticket_payload(t) for t in completed],
        "error": error,
        "active": "handovers",
    }


def _render(request: Request, db: Session, user, error=None, status_code: int = 200):
    ctx = _handover_context(request, db, user, error)
    return templates.TemplateResponse(request, "handover.html", ctx, status_code=status_code)


@router.get("/handovers", response_class=HTMLResponse)
async def handover_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    return _render(request, db, user)


@router.post("/handovers", response_class=HTMLResponse)
async def handover_open(
    request: Request,
    vat_id: int = Form(...),
    clearedMeters: str = Form(...),
    receivingTeam: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    # 锁缸行串行化「查重-开卷」；部分唯一索引兜底，并发双开至多一张成功
    vat = db.query(Vat).filter(Vat.id == vat_id).with_for_update(of=Vat).first()
    if not vat:
        return _render(request, db, user, "染缸不存在。", status_code=400)
    try:
        cleared = Decimal(clearedMeters.strip())
    except InvalidOperation:
        return _render(request, db, user, "清出米数不是有效数字。", status_code=400)
    team = receivingTeam.strip()
    if not team:
        return _render(request, db, user, "接收班组不能为空。", status_code=400)
    try:
        assert_can_open_handover(vat)
        validate_handover_ticket(vat, cleared, vat.lots, _get_active(db, vat.id))
        db.add(
            CleanHandover(
                vat_id=vat.id,
                opener_id=user.id,
                clearedMeters=cleared,
                receivingTeam=team[:80],
                openedAt=datetime.now(timezone.utc),
                finishedAt=None,
            )
        )
        db.commit()
        return RedirectResponse("/handovers", status_code=303)
    except VatRuleError as exc:
        db.rollback()
        return _render(request, db, user, exc.message, status_code=400)
    except IntegrityError:
        db.rollback()
        return _render(
            request,
            db,
            user,
            f"染缸 {vat.code} 刚由他人开出进行中交接卷，同缸进行中只许一张。",
            status_code=400,
        )


@router.post("/handovers/{ticket_id}/edit", response_class=HTMLResponse)
async def handover_edit(
    ticket_id: int,
    request: Request,
    clearedMeters: str = Form(...),
    receivingTeam: str = Form(...),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    ticket = db.get(CleanHandover, ticket_id)
    if not ticket or ticket.finishedAt is not None:
        return _render(request, db, user, "交接卷不存在或已完成，不能修改。", status_code=400)
    try:
        cleared = Decimal(clearedMeters.strip())
    except InvalidOperation:
        return _render(request, db, user, "清出米数不是有效数字。", status_code=400)
    team = receivingTeam.strip()
    if not team:
        return _render(request, db, user, "接收班组不能为空。", status_code=400)
    vat = ticket.vat
    try:
        # 与开卷新建共用同缸进行中唯一与米数上限校验（排除本卷）
        validate_handover_ticket(
            vat, cleared, vat.lots, _get_active(db, vat.id), self_id=ticket.id
        )
        ticket.clearedMeters = cleared
        ticket.receivingTeam = team[:80]
        db.commit()
        return RedirectResponse("/handovers", status_code=303)
    except VatRuleError as exc:
        db.rollback()
        return _render(request, db, user, exc.message, status_code=400)


@router.post("/handovers/{ticket_id}/complete", response_class=HTMLResponse)
async def handover_complete(
    ticket_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not user.is_superuser:
        return _render(request, db, user, "仅主管可完成清缸交接卷。", status_code=403)
    ticket = db.get(CleanHandover, ticket_id)
    if not ticket:
        return _render(request, db, user, "交接卷不存在。", status_code=400)
    try:
        # 完成检查与改状态入口读同一电位门槛函数 assert_can_mark_ready
        complete_handover(ticket, ticket.vat.latest_lot(), datetime.now(timezone.utc))
        db.commit()
        return RedirectResponse("/handovers", status_code=303)
    except VatRuleError as exc:
        db.rollback()
        return _render(request, db, user, exc.message, status_code=400)
