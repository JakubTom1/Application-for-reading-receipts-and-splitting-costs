from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
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
    existing_user = db.query(models.DBUser).filter(models.DBUser.username == user_data.username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="User with this name already exists")
    
    hashed_password = hash_password(user_data.password)
    new_user = models.DBUser(username=user_data.username, password_hash=hashed_password)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user

@app.post("/login", response_model=schemas.UserResponse)
def login_user(login_data: schemas.LoginRequest, db: Session = Depends(get_db)):
    """Authenticates a user with their password."""
    user = db.query(models.DBUser).filter(models.DBUser.username == login_data.username).first()
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
    max_attempts = 2


    t0 = time.time()
    image_bytes = await file.read()
    print(f"File read time: {time.time() - t0:.2f}s")

    try:
        from PIL import Image
        import io

        t_resize = time.time()

        img = Image.open(io.BytesIO(image_bytes))
        img.thumbnail((1024, 1024))

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        image_bytes = buf.getvalue()

        print(f"Resize time: {time.time() - t_resize:.2f}s")

    except Exception as e:
        print("Resize skipped:", e)

    for attempt in range(max_attempts):
        try:
            t1 = time.time()

            validated_items = await asyncio.to_thread(
                ai_service.analyze_image_with_gemini,
                image_bytes
            )

            print(f"Gemini time: {time.time() - t1:.2f}s")

            total_time = time.time() - total_start_time
            print(f"TOTAL /analyze time: {total_time:.2f}s\n")

            return validated_items

        except ValueError as e:
            print(f"DATA ERROR (400): {e}")
            raise HTTPException(status_code=400, detail=str(e))

        except Exception as e:
            error_msg = str(e)

            is_rate_limit = "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg
            is_overload = "503" in error_msg and "high demand" in error_msg

            print(f"Attempt {attempt+1} failed: {error_msg}")

            if (is_rate_limit or is_overload) and attempt < max_attempts - 1:
                wait_time = 5
                print(f"Retrying in {wait_time}s...\n")
                await asyncio.sleep(wait_time)
                continue

            print("FINAL ERROR:")
            traceback.print_exc()

            raise HTTPException(
                status_code=429 if is_rate_limit else 503 if is_overload else 500,
                detail="AI is currently unavailable. Try again."
            )

@app.get("/events/{event_id}/receipts", response_model=List[schemas.ReceiptResponse])
def get_event_receipts(event_id: int, db: Session = Depends(get_db)):
    return db.query(models.DBReceipt).filter(models.DBReceipt.event_id == event_id).all()

@app.post("/receipts/save")
def save_final_receipt(payload: schemas.ReceiptCreate, db: Session = Depends(get_db)):
    """
    STEP 2: The app sends the corrected receipt with assigned participants.
    We save the receipt, products, and create splits (which event participants pay for what).
    """
    new_receipt = models.DBReceipt(name=payload.name, payer_id=payload.payer_id, event_id=payload.event_id)
    db.add(new_receipt)
    db.commit()
    db.refresh(new_receipt)

    for item_data in payload.items:
        db_item = models.DBItem(
            receipt_id=new_receipt.id, name=item_data.name, quantity=item_data.quantity,
            unit_price=item_data.unit_price, discount=item_data.discount, final_price=item_data.final_price
        )
        db.add(db_item)
        db.commit()
        db.refresh(db_item)
        for split_data in item_data.split_among:
            db_split = models.DBItemSplit(item_id=db_item.id, participant_id=split_data.participant_id)
            db.add(db_split)
    db.commit()
    return {"status": "success"}


@app.put("/receipts/{receipt_id}")
def update_receipt(receipt_id: int, payload: schemas.ReceiptCreate, db: Session = Depends(get_db)):
    receipt = db.query(models.DBReceipt).filter(models.DBReceipt.id == receipt_id).first()
    if not receipt:
        raise HTTPException(404, "Receipt not found")

    receipt.name = payload.name
    receipt.payer_id = payload.payer_id

    # Czyszczenie starych produktów
    for item in receipt.items:
        db.query(models.DBItemSplit).filter(models.DBItemSplit.item_id == item.id).delete()
    db.query(models.DBItem).filter(models.DBItem.receipt_id == receipt_id).delete()

    # Wrzucenie nowych po edycji
    for item_data in payload.items:
        db_item = models.DBItem(
            receipt_id=receipt.id, name=item_data.name, quantity=item_data.quantity,
            unit_price=item_data.unit_price, discount=item_data.discount, final_price=item_data.final_price
        )
        db.add(db_item)
        db.commit()
        db.refresh(db_item)
        for split_data in item_data.split_among:
            db_split = models.DBItemSplit(item_id=db_item.id, participant_id=split_data.participant_id)
            db.add(db_split)
    db.commit()
    return {"status": "updated"}


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
def create_event(name: str, owner_id: int, participant_name: str, db: Session = Depends(get_db)):
    """Tworzy event i od razu przypisuje właścicielowi wybrane imię."""
    new_event = models.DBEvent(name=name, owner_id=owner_id)
    db.add(new_event)
    db.commit()
    db.refresh(new_event)

    # Tworzymy uczestnika dla właściciela z wybranym imieniem
    participant = models.DBEventParticipant(
        event_id=new_event.id,
        user_id=owner_id,
        name=participant_name
    )
    db.add(participant)
    db.commit()
    return new_event

@app.get("/events/{event_id}/participants", response_model=List[schemas.EventParticipantResponse])
def get_event_participants(event_id: int, db: Session = Depends(get_db)):
    event = db.query(models.DBEvent).filter(models.DBEvent.id == event_id).first()
    return event.participants if event else []

@app.post("/events/{event_id}/participants", response_model=schemas.EventParticipantResponse)
def create_event_participant(event_id: int, name: str, db: Session = Depends(get_db)):
    try:
        participant = models.DBEventParticipant(event_id=event_id, name=name)
        db.add(participant)
        db.commit()
        db.refresh(participant)
        return participant
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Person with this name already exists in this event!")

@app.get("/events/{event_id}/balances", response_model=schemas.EventBalancesResponse)
def get_event_balances(event_id: int, db: Session = Depends(get_db)):
    """
    Zwraca szczegółowe salda uczestników eventu z uwzględnieniem
    już dokonanych spłat (DBSettlement). Styl Tricount.
    """
    event = db.query(models.DBEvent).filter(models.DBEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # --- 1. Oblicz net balance z paragonów ---
    paid = defaultdict(float)    # ile kto wydał przy kasie
    consumed = defaultdict(float) # ile kto skonsumował

    participant_objects = {p.name: p for p in event.participants}

    for receipt in event.receipts:
        if not receipt.payer:
            continue
        receipt_total = sum(item.final_price for item in receipt.items)
        paid[receipt.payer.name] += receipt_total
        for item in receipt.items:
            if item.splits:
                share = item.final_price / len(item.splits)
                for split in item.splits:
                    if split.participant:
                        consumed[split.participant.name] += share

    # --- 2. Uwzględnij spłaty z DBSettlement ---
    settlements = db.query(models.DBSettlement).filter(
        models.DBSettlement.event_id == event_id
    ).all()

    net = defaultdict(float)
    for name in set(list(paid.keys()) + list(consumed.keys())):
        net[name] = paid.get(name, 0.0) - consumed.get(name, 0.0)

    for s in settlements:
        # from_participant spłacił to_participant — zmniejsza jego dług, zmniejsza należność
        net[s.from_participant.name] += s.amount
        net[s.to_participant.name] -= s.amount

    # --- 3. Buduj response balances ---
    all_names = set(list(paid.keys()) + list(consumed.keys()))
    balances = []
    for name in all_names:
        p = participant_objects.get(name)
        if p:
            balances.append(schemas.ParticipantBalance(
                participant=p,
                total_paid=round(paid.get(name, 0.0), 2),
                total_consumed=round(consumed.get(name, 0.0), 2),
                net_balance=round(net.get(name, 0.0), 2)
            ))

    # --- 4. Uproszczone transakcje (greedy) ---
    creditors = [{"name": n, "amount": v} for n, v in net.items() if v > 0.01]
    debtors   = [{"name": n, "amount": abs(v)} for n, v in net.items() if v < -0.01]
    creditors.sort(key=lambda x: x["amount"], reverse=True)
    debtors.sort(key=lambda x: x["amount"], reverse=True)

    transactions = []
    c_idx, d_idx = 0, 0
    while c_idx < len(creditors) and d_idx < len(debtors):
        c, d = creditors[c_idx], debtors[d_idx]
        amount = min(c["amount"], d["amount"])
        if amount > 0.01:
            transactions.append({"from": d["name"], "to": c["name"], "amount": round(amount, 2)})
        c["amount"] -= amount
        d["amount"] -= amount
        if c["amount"] < 0.01: c_idx += 1
        if d["amount"] < 0.01: d_idx += 1

    return schemas.EventBalancesResponse(
        balances=balances,
        settlements_history=settlements,
        suggested_transactions=transactions
    )


@app.post("/events/{event_id}/settlements", response_model=schemas.SettlementResponse)
def record_settlement(event_id: int, payload: schemas.SettlementCreate, db: Session = Depends(get_db)):
    """
    Zapisuje fakt spłaty — np. Kasia oddaje Norbertowi 45 zł.
    To przesuwa salda w kolejnym wywołaniu /balances.
    """
    event = db.query(models.DBEvent).filter(models.DBEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    # Walidacja uczestników
    from_p = db.query(models.DBEventParticipant).filter(
        models.DBEventParticipant.id == payload.from_participant_id,
        models.DBEventParticipant.event_id == event_id
    ).first()
    to_p = db.query(models.DBEventParticipant).filter(
        models.DBEventParticipant.id == payload.to_participant_id,
        models.DBEventParticipant.event_id == event_id
    ).first()

    if not from_p or not to_p:
        raise HTTPException(status_code=400, detail="Participant not found in this event")

    settlement = models.DBSettlement(
        event_id=event_id,
        from_participant_id=payload.from_participant_id,
        to_participant_id=payload.to_participant_id,
        amount=payload.amount,
        note=payload.note
    )
    db.add(settlement)
    db.commit()
    db.refresh(settlement)
    return settlement


@app.delete("/events/{event_id}/settlements/{settlement_id}", status_code=204)
def delete_settlement(event_id: int, settlement_id: int, db: Session = Depends(get_db)):
    """Usuwa spłatę (np. jeśli wpisano przez pomyłkę)."""
    s = db.query(models.DBSettlement).filter(
        models.DBSettlement.id == settlement_id,
        models.DBSettlement.event_id == event_id
    ).first()
    if not s:
        raise HTTPException(status_code=404, detail="Settlement not found")
    db.delete(s)
    db.commit()


# ---------------------------------------------------------
# 4. EVENT ACCESS SHARING
# ---------------------------------------------------------
import random, string

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
def join_event_with_code(join_data: schemas.JoinEventRequest, user_id: int, participant_name: str,
                         db: Session = Depends(get_db)):
    access = db.query(models.DBEventAccess).filter(models.DBEventAccess.access_code == join_data.access_code).first()
    if not access:
        raise HTTPException(status_code=404, detail="Event not found")

    event = access.event

    existing_access = db.query(models.DBEventAccess).filter(
        models.DBEventAccess.event_id == event.id, models.DBEventAccess.user_id == user_id
    ).first()
    if not existing_access:
        db.add(models.DBEventAccess(event_id=event.id, user_id=user_id, access_code=join_data.access_code))

    existing_p = db.query(models.DBEventParticipant).filter(
        models.DBEventParticipant.event_id == event.id, models.DBEventParticipant.user_id == user_id
    ).first()

    if not existing_p:
        target_participant = db.query(models.DBEventParticipant).filter(
            models.DBEventParticipant.event_id == event.id, models.DBEventParticipant.name == participant_name
        ).first()

        if target_participant:
            if target_participant.user_id is not None:
                raise HTTPException(status_code=400,
                                    detail="Name is already taken!")
            else:
                target_participant.user_id = user_id
        else:
            new_p = models.DBEventParticipant(event_id=event.id, user_id=user_id, name=participant_name)
            db.add(new_p)

    db.commit()
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
