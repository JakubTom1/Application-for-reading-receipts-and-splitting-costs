from sqlalchemy import Column, Integer, String, Float, ForeignKey
from sqlalchemy.orm import relationship
from database import Base

class DBUser(Base):
    """Table storing users/friends who participate in sharing costs."""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), index=True)

    # Relationships
    receipts_paid = relationship("DBReceipt", back_populates="payer")
    splits = relationship("DBItemSplit", back_populates="user")

class DBEvent(Base):
    """Table storing events (e.g., 'Trip to Mountains', 'Flat Expenses')."""
    __tablename__ = "events"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), index=True)

    # Relationships
    receipts = relationship("DBReceipt", back_populates="event")

class DBReceipt(Base):
    """Table storing main receipt metadata."""
    __tablename__ = "receipts"
    id = Column(Integer, primary_key=True, index=True)
    payer_id = Column(Integer, ForeignKey("users.id"))
    event_id = Column(Integer, ForeignKey("events.id"), nullable=True) # Nullable for quick, event-less receipts

    # Relationships
    payer = relationship("DBUser", back_populates="receipts_paid")
    event = relationship("DBEvent", back_populates="receipts")
    items = relationship("DBItem", back_populates="receipt")

class DBItem(Base):
    """Table storing individual products from a receipt."""
    __tablename__ = "items"
    id = Column(Integer, primary_key=True, index=True)
    receipt_id = Column(Integer, ForeignKey("receipts.id"))
    name = Column(String(255), index=True)
    quantity = Column(Float)
    unit_price = Column(Float)
    discount = Column(Float)
    final_price = Column(Float)

    # Relationships
    receipt = relationship("DBReceipt", back_populates="items")
    splits = relationship("DBItemSplit", back_populates="item")

class DBItemSplit(Base):
    """Join table indicating which users are paying for which items."""
    __tablename__ = "item_splits"
    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("items.id"))
    user_id = Column(Integer, ForeignKey("users.id"))

    # Relationships
    item = relationship("DBItem", back_populates="splits")
    user = relationship("DBUser", back_populates="splits")