from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)


class Workshop(Base):
    __tablename__ = "workshops"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    region: Mapped[str] = mapped_column(String(80))
    notes: Mapped[str] = mapped_column(Text, default="")

    vats: Mapped[list["Vat"]] = relationship(back_populates="workshop")


class Vat(Base):
    __tablename__ = "vats"
    __table_args__ = (
        UniqueConstraint("workshop_id", "code", name="uniq_vat_code_per_workshop"),
    )

    STATUS_IDLE = "idle"
    STATUS_REDUCING = "reducing"
    STATUS_READY = "ready"

    id: Mapped[int] = mapped_column(primary_key=True)
    workshop_id: Mapped[int] = mapped_column(ForeignKey("workshops.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(40))
    dyeType: Mapped[str] = mapped_column(String(80))
    volumeL: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(20), default=STATUS_IDLE)

    workshop: Mapped["Workshop"] = relationship(back_populates="vats")
    lots: Mapped[list["DipLot"]] = relationship(back_populates="vat")
    tickets: Mapped[list["CleaningTicket"]] = relationship(back_populates="vat")

    def latest_lot(self) -> Optional["DipLot"]:
        if not self.lots:
            return None
        return sorted(self.lots, key=lambda x: (x.dippedAt, x.id), reverse=True)[0]

    def open_ticket(self) -> Optional["CleaningTicket"]:
        """同缸进行中（尚未完工）的交接卷，至多一张。"""
        open_ones = [t for t in self.tickets if t.completedAt is None]
        if not open_ones:
            return None
        return sorted(open_ones, key=lambda x: (x.openedAt, x.id), reverse=True)[0]


class DipLot(Base):
    __tablename__ = "dip_lots"

    id: Mapped[int] = mapped_column(primary_key=True)
    vat_id: Mapped[int] = mapped_column(ForeignKey("vats.id", ondelete="CASCADE"))
    dippedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    clothMeters: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    redoxMv: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2), nullable=True)

    vat: Mapped["Vat"] = relationship(back_populates="lots")


class CleaningTicket(Base):
    """清缸交接卷：可染色缸退回闲置前必须先开卷并由主管完工。

    状态隐含：completedAt 为空即「进行中」，落了完工时间即「已完成」。
    """

    __tablename__ = "cleaning_tickets"
    __table_args__ = (
        # 同缸进行中只许一张：数据库层兜底，挡住两人几乎同时开卷的并发
        Index(
            "uniq_open_ticket_per_vat",
            "vat_id",
            unique=True,
            postgresql_where=text('"completedAt" IS NULL'),
            sqlite_where=text('"completedAt" IS NULL'),
        ),
    )

    STATUS_OPEN = "open"
    STATUS_DONE = "done"

    id: Mapped[int] = mapped_column(primary_key=True)
    vat_id: Mapped[int] = mapped_column(ForeignKey("vats.id", ondelete="CASCADE"))
    opener_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    clothMeters: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    receiveTeam: Mapped[str] = mapped_column(String(80))
    openedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completedAt: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )

    vat: Mapped["Vat"] = relationship(back_populates="tickets")
    opener = relationship("User", foreign_keys=[opener_id])
    completed_by = relationship("User", foreign_keys=[completed_by_id])

    @property
    def status(self) -> str:
        return self.STATUS_DONE if self.completedAt is not None else self.STATUS_OPEN
