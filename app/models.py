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
    handovers: Mapped[list["CleanHandover"]] = relationship(back_populates="vat")

    def latest_lot(self) -> Optional["DipLot"]:
        if not self.lots:
            return None
        return sorted(self.lots, key=lambda x: (x.dippedAt, x.id), reverse=True)[0]

    def active_handover(self) -> Optional["CleanHandover"]:
        """进行中的清缸交接卷（完工时间为空），同缸至多一张。"""
        for h in self.handovers:
            if h.finishedAt is None:
                return h
        return None


class DipLot(Base):
    __tablename__ = "dip_lots"

    id: Mapped[int] = mapped_column(primary_key=True)
    vat_id: Mapped[int] = mapped_column(ForeignKey("vats.id", ondelete="CASCADE"))
    dippedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    clothMeters: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    redoxMv: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2), nullable=True)

    vat: Mapped["Vat"] = relationship(back_populates="lots")


class CleanHandover(Base):
    """清缸交接卷：可染色缸退回闲置前的清缸交接凭证。

    完工时间 finishedAt 为空即进行中、非空即已完成，状态由该字段隐含；
    数据库层用部分唯一索引保证同缸进行中只许一张（并发双开至多一张成功）。
    """

    __tablename__ = "clean_handovers"
    __table_args__ = (
        Index(
            "uniq_active_handover_per_vat",
            "vat_id",
            unique=True,
            postgresql_where=text('"finishedAt" IS NULL'),
            sqlite_where=text('"finishedAt" IS NULL'),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    vat_id: Mapped[int] = mapped_column(ForeignKey("vats.id", ondelete="CASCADE"), index=True)
    opener_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    clearedMeters: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    receivingTeam: Mapped[str] = mapped_column(String(80))
    openedAt: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finishedAt: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    vat: Mapped["Vat"] = relationship(back_populates="handovers")
    opener: Mapped["User"] = relationship()

    @property
    def is_active(self) -> bool:
        return self.finishedAt is None
