import os
import io
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel
from typing import List
from sqlalchemy import create_engine, Column, Integer, String, Float, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, Session, relationship
from PIL import Image

# Importujemy funkcję wykonującą prompt do Gemini z pliku old_ai.py
from old_ai import scan_receipt

# Load environment variables
load_dotenv()
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")
if not SQLALCHEMY_DATABASE_URL:
    raise ValueError("Missing DATABASE_URL in .env file!")

engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ---------------------------------------------------------
# 1. SQLALCHEMY MODELS (MySQL Tables) - uproszczone, bez użytkowników!
# ---------------------------------------------------------
class DBReceipt(Base):
    __tablename__ = "receipts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)  # Zwykłe int na potrzeby testu

    # Establish relationship with items
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

    # Establish relationship back to receipt
    receipt = relationship("DBReceipt", back_populates="items")


# Create tables in the database automatically if they don't exist
Base.metadata.create_all(bind=engine)


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
    quantity: float
    unit_price: float
    discount: float
    final_price: float


class ReceiptPayload(BaseModel):
    user_id: int
    items: List[ReceiptItem]

class DBUser(Base):
    """Table storing users/friends who participate in sharing costs."""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), index=True)

# ---------------------------------------------------------
# 3. API ENDPOINTS
# ---------------------------------------------------------

@app.post("/analyze")
async def analyze_receipt_endpoint(file: UploadFile = File(...)):
    """
    Odbiera obraz (np. z aplikacji Android), konwertuje go i wywołuje
    funkcję scan_receipt() z pliku old_ai.py, aby odpytać Gemini.
    """
    try:
        image_bytes = await file.read()
        img = Image.open(io.BytesIO(image_bytes))
    except Exception:
        raise HTTPException(status_code=400, detail="Nieprawidłowy format obrazu.")

    # Korzystamy ze ściągniętej funkcji z old_ai.py!
    result = scan_receipt(img)

    if result is None:
        raise HTTPException(status_code=500, detail="AI nie zdołało przeanalizować paragonu.")

    return result


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
            quantity=item.quantity,
            unit_price=item.unit_price,
            discount=item.discount,
            final_price=item.final_price
        )
        db.add(db_item)

    db.commit()

    return {
        "status": "success",
        "message": "Saved to MySQL!",
        "receipt_id": new_receipt.id,
        "items_saved": len(payload.items)
    }



@app.get("/users")
def get_users(db: Session = Depends(get_db)):
    """
    Simple endpoint to retrieve all users from the database.
    Useful for testing and assigning items to users in the future.
    """
    users = db.query(DBUser).all()
    return users