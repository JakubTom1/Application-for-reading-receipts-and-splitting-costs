from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
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
    name: str
    payer_id: int
    event_id: Optional[int] = None
    items: List[ReceiptItemCreate]

# ---------------------------------------------------------
# SCHEMAS FOR AUTHENTICATION
# ---------------------------------------------------------
class UserCreate(BaseModel):
    """Schema for creating a new user with a password."""
    username: str
    password: str

class LoginRequest(BaseModel):
    """Schema for user login request."""
    username: str
    password: str

class UserResponse(BaseModel):
    id: int
    username: str
    class Config:
        from_attributes = True

class EventAccessResponse(BaseModel):
    """Response for event access code."""
    event_id: int
    access_code: str
    class Config:
        from_attributes = True

class JoinEventRequest(BaseModel):
    """Request to join an event with access code."""
    access_code: str


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
    name: str  # Tego brakowało
    payer: EventParticipantResponse 
    event_id: Optional[int]
    items: List[ItemResponse]
    class Config:
        from_attributes = True

class SettlementCreate(BaseModel):
    from_participant_id: int
    to_participant_id: int
    amount: float
    note: Optional[str] = None

class SettlementResponse(BaseModel):
    id: int
    from_participant: EventParticipantResponse
    to_participant: EventParticipantResponse
    amount: float
    note: Optional[str]
    created_at: datetime
    class Config:
        from_attributes = True

class ParticipantBalance(BaseModel):
    participant: EventParticipantResponse
    total_paid: float      # ile zapłacił przy kasie
    total_consumed: float  # ile skonsumował
    net_balance: float     # paid - consumed (+ = należy mu się, - = jest winien)

class EventBalancesResponse(BaseModel):
    balances: List[ParticipantBalance]
    settlements_history: List[SettlementResponse]
    suggested_transactions: List[dict]  # [{from, to, amount}] - uproszczone długi do spłaty