from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base

class DBUser(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)

    events = relationship("DBEvent", back_populates="owner")
    event_access = relationship("DBEventAccess", back_populates="user")
    # Powiązanie z uczestnictwem w różnych eventach
    participations = relationship("DBEventParticipant", back_populates="user_account")

class DBEvent(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    owner = relationship("DBUser", back_populates="events")
    participants = relationship("DBEventParticipant", back_populates="event", cascade="all, delete-orphan")
    receipts = relationship("DBReceipt", back_populates="event")

class DBEventParticipant(Base):
    __tablename__ = "event_participants"
    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True) # Kto z zalogowanych tym jest
    name = Column(String(100), index=True)

    __table_args__ = (UniqueConstraint('event_id', 'name', name='uix_event_participant_name'),)

    event = relationship("DBEvent", back_populates="participants")
    user_account = relationship("DBUser", back_populates="participations")
    splits = relationship("DBItemSplit", back_populates="participant")
    receipts_paid = relationship("DBReceipt", back_populates="payer")

class DBReceipt(Base):
    __tablename__ = "receipts"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, default="Paragon")
    payer_id = Column(Integer, ForeignKey("event_participants.id"))
    event_id = Column(Integer, ForeignKey("events.id"), nullable=True)

    payer = relationship("DBEventParticipant", back_populates="receipts_paid")
    event = relationship("DBEvent", back_populates="receipts")
    items = relationship("DBItem", back_populates="receipt")

class DBItem(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True, index=True)
    receipt_id = Column(Integer, ForeignKey("receipts.id"))
    name = Column(String(255), index=True)
    quantity = Column(Float)
    unit_price = Column(Float)
    discount = Column(Float)
    final_price = Column(Float)

    receipt = relationship("DBReceipt", back_populates="items")
    splits = relationship("DBItemSplit", back_populates="item")

class DBItemSplit(Base):
    __tablename__ = "item_splits"
    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("items.id"))
    participant_id = Column(Integer, ForeignKey("event_participants.id"), nullable=True)

    item = relationship("DBItem", back_populates="splits")
    participant = relationship("DBEventParticipant", back_populates="splits")

class DBEventAccess(Base):
    __tablename__ = "event_access"
    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    access_code = Column(String(6), index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    event = relationship("DBEvent")
    user = relationship("DBUser", back_populates="event_access")


class DBSettlement(Base):
    __tablename__ = "settlements"
    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(Integer, ForeignKey("events.id"), nullable=False)
    from_participant_id = Column(Integer, ForeignKey("event_participants.id"), nullable=False)
    to_participant_id = Column(Integer, ForeignKey("event_participants.id"), nullable=False)
    amount = Column(Float, nullable=False)
    note = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    event = relationship("DBEvent")
    from_participant = relationship("DBEventParticipant", foreign_keys=[from_participant_id])
    to_participant = relationship("DBEventParticipant", foreign_keys=[to_participant_id])