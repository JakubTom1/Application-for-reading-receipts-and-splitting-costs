from pydantic import BaseModel
from typing import List, Optional

# ---------------------------------------------------------
# SCHEMAS FOR AI ANALYSIS (Output from Gemini)
# ---------------------------------------------------------
class ReceiptItemScanned(BaseModel):
    """Schema used to strictly validate the JSON coming from Gemini AI."""
    name: str
    quantity: float
    unit_price: float
    discount: float
    final_price: float

# ---------------------------------------------------------
# SCHEMAS FOR SAVING (Input from Android App)
# ---------------------------------------------------------
class SplitUserCreate(BaseModel):
    """Represents a user assigned to pay for a specific item."""
    user_id: int

class ReceiptItemCreate(ReceiptItemScanned):
    """Extends the scanned item with a list of users paying for it."""
    split_among: List[SplitUserCreate]

class ReceiptCreate(BaseModel):
    """Payload sent by the Android app when saving a verified receipt."""
    payer_id: int
    event_id: Optional[int] = None
    items: List[ReceiptItemCreate]

# ---------------------------------------------------------
# SCHEMAS FOR FETCHING (Output to Android App for GET requests)
# ---------------------------------------------------------
class UserResponse(BaseModel):
    id: int
    name: str
    class Config:
        from_attributes = True # Allows Pydantic to read from SQLAlchemy ORM models

class ItemSplitResponse(BaseModel):
    id: int
    user: UserResponse
    class Config:
        from_attributes = True

class ItemResponse(ReceiptItemScanned):
    id: int
    splits: List[ItemSplitResponse]
    class Config:
        from_attributes = True

class ReceiptResponse(BaseModel):
    id: int
    payer: UserResponse
    event_id: Optional[int]
    items: List[ItemResponse]
    class Config:
        from_attributes = True