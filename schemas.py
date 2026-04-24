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
    """Represents an event participant assigned to pay for a specific item."""
    participant_id: int

class ReceiptItemCreate(ReceiptItemScanned):
    """Extends the scanned item with a list of users paying for it."""
    split_among: List[SplitUserCreate]

class ReceiptCreate(BaseModel):
    """Payload sent by the Android app when saving a verified receipt."""
    payer_id: int
    event_id: Optional[int] = None
    items: List[ReceiptItemCreate]

# ---------------------------------------------------------
# SCHEMAS FOR AUTHENTICATION
# ---------------------------------------------------------
class UserCreate(BaseModel):
    """Schema for creating a new user with a password."""
    name: str
    password: str

class LoginRequest(BaseModel):
    """Schema for user login request."""
    user_id: int
    password: str

class UserResponse(BaseModel):
    id: int
    name: str
    class Config:
        from_attributes = True # Allows Pydantic to read from SQLAlchemy ORM models

class EventParticipantResponse(BaseModel):
    id: int
    name: str
    class Config:
        from_attributes = True

class EventResponse(BaseModel):
    id: int
    name: str
    owner_id: int
    participants: List[EventParticipantResponse] = []
    class Config:
        from_attributes = True

class ItemSplitResponse(BaseModel):
    id: int
    participant: EventParticipantResponse
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