from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from typing import List, Dict
from collections import defaultdict
import traceback
import time
import asyncio
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError

# Imports from your files
from database import engine, get_db
import models
import schemas
import ai_service

# Password hashing configuration with Argon2
ph = PasswordHasher()

def hash_password(password: str) -> str:
    """Hash a password for secure storage using Argon2."""
    return ph.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a hashed password."""
    try:
        ph.verify(hashed_password, plain_password)
        return True
    except (VerifyMismatchError, VerificationError):
        return False

# Create database tables (if they don't exist)
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Receipt Splitter API",
    description="Complete backend for scanning receipts and settling expenses among friends."
)


# ---------------------------------------------------------
# 1. USER MANAGEMENT
# ---------------------------------------------------------
@app.get("/users", response_model=List[schemas.UserResponse])
def get_users(db: Session = Depends(get_db)):
    """Fetches the list of all friends from the database."""
    return db.query(models.DBUser).all()


@app.post("/users", response_model=schemas.UserResponse)
def create_user(user_data: schemas.UserCreate, db: Session = Depends(get_db)):
    """Adds a new person to the settlements with a password."""
    # Check if user with this name already exists
    existing_user = db.query(models.DBUser).filter(models.DBUser.name == user_data.name).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="User with this name already exists")
    
    hashed_password = hash_password(user_data.password)
    new_user = models.DBUser(name=user_data.name, password_hash=hashed_password)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@app.post("/login", response_model=schemas.UserResponse)
def login_user(login_data: schemas.LoginRequest, db: Session = Depends(get_db)):
    """Authenticates a user with their password."""
    user = db.query(models.DBUser).filter(models.DBUser.id == login_data.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if not verify_password(login_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect password")
    
    return user


# ---------------------------------------------------------
# 2. RECEIPT SCANNING AND SAVING
# ---------------------------------------------------------
@app.post("/analyze", response_model=List[schemas.ReceiptItemScanned])
async def analyze_receipt(file: UploadFile = File(...)):
    """
    STEP 1: Sends the image to Gemini (via ai_service.py) and returns
    the recognized products to the app for user verification.
    """
    total_start_time = time.time()
    max_attempts = 2  # 🔽 było 3 (mniej retry = mniej czekania)

    # -----------------------------
    # 1. READ FILE (z pomiarem)
    # -----------------------------
    t0 = time.time()
    image_bytes = await file.read()
    print(f"📥 File read time: {time.time() - t0:.2f}s")

    # -----------------------------
    # 2. (OPCJONALNIE) RESIZE OBRAZU
    # -----------------------------
    try:
        from PIL import Image
        import io

        t_resize = time.time()

        img = Image.open(io.BytesIO(image_bytes))
        img.thumbnail((1024, 1024))  # 🔥 ogromny boost dla AI

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        image_bytes = buf.getvalue()

        print(f"🖼️ Resize time: {time.time() - t_resize:.2f}s")

    except Exception as e:
        print("⚠️ Resize skipped:", e)

    # -----------------------------
    # 3. GEMINI CALL + RETRY
    # -----------------------------
    for attempt in range(max_attempts):
        try:
            t1 = time.time()

            validated_items = await asyncio.to_thread(
                ai_service.analyze_image_with_gemini,
                image_bytes
            )

            print(f"🤖 Gemini time: {time.time() - t1:.2f}s")

            total_time = time.time() - total_start_time
            print(f"🚀 TOTAL /analyze time: {total_time:.2f}s\n")

            return validated_items

        except ValueError as e:
            print(f"❌ DATA ERROR (400): {e}")
            raise HTTPException(status_code=400, detail=str(e))

        except Exception as e:
            error_msg = str(e)

            is_rate_limit = "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg
            is_overload = "503" in error_msg and "high demand" in error_msg

            print(f"⚠️ Attempt {attempt+1} failed: {error_msg}")

            # 🔥 DUŻA ZMIANA: krótszy retry
            if (is_rate_limit or is_overload) and attempt < max_attempts - 1:
                wait_time = 5  # 🔽 było 38 sekund
                print(f"⏳ Retrying in {wait_time}s...\n")
                await asyncio.sleep(wait_time)
                continue

            print("💥 FINAL ERROR:")
            traceback.print_exc()

            raise HTTPException(
                status_code=429 if is_rate_limit else 503 if is_overload else 500,
                detail="AI is currently unavailable. Try again."
            )


@app.post("/receipts/save")
def save_final_receipt(payload: schemas.ReceiptCreate, db: Session = Depends(get_db)):
    """
    STEP 2: The app sends the corrected receipt with assigned participants.
    We save the receipt, products, and create splits (which event participants pay for what).
    """
    # Check if the payer exists
    payer = db.query(models.DBUser).filter(models.DBUser.id == payload.payer_id).first()
    if not payer:
        raise HTTPException(status_code=404, detail="Payer user not found")

    # Validate the event if provided
    event = None
    if payload.event_id is not None:
        event = db.query(models.DBEvent).filter(models.DBEvent.id == payload.event_id).first()
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")

    # 1. Create the main receipt entry (with info on who paid upfront)
    new_receipt = models.DBReceipt(
        payer_id=payload.payer_id,
        event_id=payload.event_id
    )
    db.add(new_receipt)
    db.commit()
    db.refresh(new_receipt)

    # 2. Saving individual products
    for item_data in payload.items:
        db_item = models.DBItem(
            receipt_id=new_receipt.id,
            name=item_data.name,
            quantity=item_data.quantity,
            unit_price=item_data.unit_price,
            discount=item_data.discount,
            final_price=item_data.final_price
        )
        db.add(db_item)
        db.commit()
        db.refresh(db_item)

        # 3. Saving SPLITS (Who participates in the cost of this product)
        for split_data in item_data.split_among:
            participant = db.query(models.DBEventParticipant).filter(
                models.DBEventParticipant.id == split_data.participant_id
            ).first()
            if not participant:
                raise HTTPException(status_code=404, detail="Event participant not found")
            if event and participant.event_id != event.id:
                raise HTTPException(status_code=400, detail="Participant does not belong to the receipt event")

            db_split = models.DBItemSplit(
                item_id=db_item.id,
                participant_id=split_data.participant_id
            )
            db.add(db_split)

    db.commit()
    return {"status": "success", "receipt_id": new_receipt.id}


# ---------------------------------------------------------
# 3. SETTLEMENTS (Who owes whom?)
# ---------------------------------------------------------
@app.get("/receipts", response_model=List[schemas.ReceiptResponse])
def get_all_receipts(db: Session = Depends(get_db)):
    """Fetches the entire receipt history with all details."""
    return db.query(models.DBReceipt).all()

@app.get("/events", response_model=List[schemas.EventResponse])
def get_all_events(owner_id: int = None, db: Session = Depends(get_db)):
    """Pobiera eventy, których user jest właścicielem albo do których ma dostęp przez kod."""
    query = db.query(models.DBEvent)

    if owner_id is not None:
        query = query.outerjoin(
            models.DBEventAccess,
            models.DBEventAccess.event_id == models.DBEvent.id
        ).filter(
            (models.DBEvent.owner_id == owner_id) |
            (models.DBEventAccess.user_id == owner_id)
        ).distinct()

    return query.all()

@app.post("/events", response_model=schemas.EventResponse)
def create_event(name: str, owner_id: int, db: Session = Depends(get_db)):
    """Tworzy nowe wydarzenie do rozliczeń dla konkretnego właściciela."""
    owner = db.query(models.DBUser).filter(models.DBUser.id == owner_id).first()
    if not owner:
        raise HTTPException(status_code=404, detail="Event owner not found")

    new_event = models.DBEvent(name=name, owner_id=owner_id)
    db.add(new_event)
    db.commit()
    db.refresh(new_event)
    return new_event

@app.get("/events/{event_id}/participants", response_model=List[schemas.EventParticipantResponse])
def get_event_participants(event_id: int, db: Session = Depends(get_db)):
    event = db.query(models.DBEvent).filter(models.DBEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event.participants

@app.post("/events/{event_id}/participants", response_model=schemas.EventParticipantResponse)
def create_event_participant(event_id: int, name: str, db: Session = Depends(get_db)):
    event = db.query(models.DBEvent).filter(models.DBEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    participant = models.DBEventParticipant(event_id=event.id, name=name)
    db.add(participant)
    db.commit()
    db.refresh(participant)
    return participant

@app.get("/events/{event_id}/balances")
def get_event_balances(event_id: int, db: Session = Depends(get_db)):
    """
    Calculates simplified debts for a specific event (Tricount style).
    Algorithm: Net Balance = Total Paid - Total Consumed.
    """
    event = db.query(models.DBEvent).filter(models.DBEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Step 1: Calculate net balance for each user in this event
    # positive = someone is owed money, negative = someone owes money
    net_balances = defaultdict(float)

    for receipt in event.receipts:
        # Payer gets credit for the total amount
        receipt_total = sum(item.final_price for item in receipt.items)
        net_balances[receipt.payer.name] += receipt_total

        # Each item consumer gets a "debt" share
        for item in receipt.items:
            if item.splits:
                share = item.final_price / len(item.splits)
                for split in item.splits:
                    participant_name = None
                    if split.participant is not None:
                        participant_name = split.participant.name
                    elif split.legacy_user is not None:
                        participant_name = split.legacy_user.name
                    if not participant_name:
                        continue
                    net_balances[participant_name] -= share

    # Step 2: Separate into creditors and debtors
    creditors = []  # people who should receive money
    debtors = []  # people who must pay

    for name, balance in net_balances.items():
        if balance > 0.01:
            creditors.append({"name": name, "amount": balance})
        elif balance < -0.01:
            debtors.append({"name": name, "amount": abs(balance)})

    # Step 3: Greedy algorithm to minimize transactions
    transactions = []
    creditors.sort(key=lambda x: x["amount"], reverse=True)
    debtors.sort(key=lambda x: x["amount"], reverse=True)

    c_idx, d_idx = 0, 0
    while c_idx < len(creditors) and d_idx < len(debtors):
        c = creditors[c_idx]
        d = debtors[d_idx]

        amount = min(c["amount"], d["amount"])
        if amount > 0.01:
            transactions.append({
                "from": d["name"],
                "to": c["name"],
                "amount": round(amount, 2)
            })

        c["amount"] -= amount
        d["amount"] -= amount

        if c["amount"] < 0.01: c_idx += 1
        if d["amount"] < 0.01: d_idx += 1

    return {"summary": transactions}


# ---------------------------------------------------------
# 4. EVENT ACCESS SHARING
# ---------------------------------------------------------
import random
import string

def generate_access_code() -> str:
    """Generate a random 6-digit access code."""
    return ''.join(random.choices(string.digits, k=6))

@app.post("/events/{event_id}/access-code", response_model=schemas.EventAccessResponse)
def generate_event_access_code(event_id: int, user_id: int, db: Session = Depends(get_db)):
    """
    Generate a 6-digit access code for an event (only owner can do this).
    Other users can use this code to join the event.
    """
    event = db.query(models.DBEvent).filter(models.DBEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    if event.owner_id != user_id:
        raise HTTPException(status_code=403, detail="Only event owner can generate access code")
    
    # Generate new code
    access_code = generate_access_code()
    
    # Remove old access codes for this event
    db.query(models.DBEventAccess).filter(
        models.DBEventAccess.event_id == event_id
    ).delete()
    
    # Create new access code entry
    new_access = models.DBEventAccess(
        event_id=event_id,
        user_id=user_id,
        access_code=access_code
    )
    db.add(new_access)
    db.commit()
    db.refresh(new_access)
    
    return new_access

@app.post("/events/join", response_model=schemas.EventResponse)
def join_event_with_code(join_data: schemas.JoinEventRequest, user_id: int, db: Session = Depends(get_db)):
    """
    Join an event using a 6-digit access code.
    """
    # Find event access record by code
    access = db.query(models.DBEventAccess).filter(
        models.DBEventAccess.access_code == join_data.access_code
    ).first()
    
    if not access:
        raise HTTPException(status_code=404, detail="Invalid access code")
    
    event = access.event
    
    # Check if user already has access
    existing_access = db.query(models.DBEventAccess).filter(
        models.DBEventAccess.event_id == event.id,
        models.DBEventAccess.user_id == user_id
    ).first()
    
    if existing_access:
        return event
    
    # Grant access to this user
    user_access = models.DBEventAccess(
        event_id=event.id,
        user_id=user_id,
        access_code=join_data.access_code
    )
    db.add(user_access)
    db.commit()
    db.refresh(user_access)
    
    return event

@app.get("/events/{event_id}/users", response_model=List[schemas.UserResponse])
def get_event_users(event_id: int, db: Session = Depends(get_db)):
    """
    Get all users who have access to this event (owner + shared users).
    """
    event = db.query(models.DBEvent).filter(models.DBEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    
    # Get all users with access via DBEventAccess
    users_with_access = db.query(models.DBUser).join(
        models.DBEventAccess,
        models.DBEventAccess.user_id == models.DBUser.id
    ).filter(
        models.DBEventAccess.event_id == event_id
    ).all()
    
    # Add owner
    result = [event.owner] if event.owner not in users_with_access else []
    result.extend(users_with_access)
    
    return list(set(result))  # Remove duplicates
