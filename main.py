from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from typing import List, Dict
from collections import defaultdict
import traceback
import time
import asyncio

# Imports from your files
from database import engine, get_db
import models
import schemas
import ai_service

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
def create_user(name: str, db: Session = Depends(get_db)):
    """Adds a new person to the settlements."""
    new_user = models.DBUser(name=name)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


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
    """Pobiera listę wydarzeń należących do danego właściciela."""
    query = db.query(models.DBEvent)
    if owner_id is not None:
        query = query.filter(models.DBEvent.owner_id == owner_id)
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
                    net_balances[split.participant.name] -= share

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