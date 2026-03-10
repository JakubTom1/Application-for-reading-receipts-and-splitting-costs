import os
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import List
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, Session, relationship

# Load environment variables
load_dotenv()
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")
if not SQLALCHEMY_DATABASE_URL:
    raise ValueError("Missing DATABASE_URL in .env file!")

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ---------------------------------------------------------
# 1. SQLALCHEMY MODELS (MySQL Tables)
# ---------------------------------------------------------
class DBReceipt(Base):
    __tablename__ = "receipts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)

    # Establish relationship with items
    items = relationship("DBItem", back_populates="receipt")


class DBItem(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True, index=True)
    receipt_id = Column(Integer, ForeignKey("receipts.id"))
    name = Column(String(255), index=True)
    price = Column(Float)

    # Establish relationship back to receipt
    receipt = relationship("DBReceipt", back_populates="items")


# Create tables in the database automatically if they don't exist
Base.metadata.create_all(bind=engine)


# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------
# 2. FASTAPI SETUP & PYDANTIC MODELS
# ---------------------------------------------------------
app = FastAPI(title="Receipt Splitter API")


class ReceiptItem(BaseModel):
    name: str
    price: float


class ReceiptPayload(BaseModel):
    user_id: int
    items: List[ReceiptItem]


# ---------------------------------------------------------
# 3. API ENDPOINTS
# ---------------------------------------------------------
@app.post("/receipts/save")
def save_receipt(payload: ReceiptPayload, db: Session = Depends(get_db)):
    """
    Receives JSON from the mobile app, creates a receipt record,
    and saves all individual items linked to that receipt.
    """
    # Create the main receipt record
    new_receipt = DBReceipt(user_id=payload.user_id)
    db.add(new_receipt)
    db.commit()
    db.refresh(new_receipt)  # Get the generated ID

    # Create records for each item
    for item in payload.items:
        db_item = DBItem(
            receipt_id=new_receipt.id,
            name=item.name,
            price=item.price
        )
        db.add(db_item)

    db.commit()

    return {
        "status": "success",
        "message": "Saved to MySQL!",
        "receipt_id": new_receipt.id,
        "items_saved": len(payload.items)
    }