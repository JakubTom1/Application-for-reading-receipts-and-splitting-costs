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
    try:
        image_bytes = await file.read()
        validated_items = await asyncio.to_thread(ai_service.analyze_image_with_gemini, image_bytes)
        total_time = time.time() - total_start_time
        print(f"🚀 Overall /analyze endpoint time: {total_time:.2f} seconds\n")
        return validated_items
    except ValueError as e:
        print(f"--- DATA ERROR (400) ---: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        error_msg = str(e)
        if "503" in error_msg and "high demand" in error_msg:
            print("--- GOOGLE API OVERLOAD (503) ---")
            raise HTTPException(
                status_code=503,
                detail="AI servers are currently overloaded. Wait a few seconds and try again."
            )
        else:
            print("--- CRITICAL SERVER ERROR (500) ---")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=error_msg)


@app.post("/receipts/save")
def save_final_receipt(payload: schemas.ReceiptCreate, db: Session = Depends(get_db)):
    """
    STEP 2: The app sends the corrected receipt with assigned users.
    We save the receipt, products, and create splits (who pays for what).
    """
    # Check if the payer exists
    payer = db.query(models.DBUser).filter(models.DBUser.id == payload.payer_id).first()
    if not payer:
        raise HTTPException(status_code=404, detail="Payer user not found")

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
            db_split = models.DBItemSplit(
                item_id=db_item.id,
                user_id=split_data.user_id
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
        net_balances[receipt.payer_id] += receipt_total

        # Each item consumer gets a "debt" share
        for item in receipt.items:
            if item.splits:
                share = item.final_price / len(item.splits)
                for split in item.splits:
                    net_balances[split.user_id] -= share

    # Step 2: Separate into creditors and debtors
    creditors = []  # people who should receive money
    debtors = []  # people who must pay

    for user_id, balance in net_balances.items():
        user = db.query(models.DBUser).get(user_id)
        if balance > 0.01:
            creditors.append({"name": user.name, "amount": balance})
        elif balance < -0.01:
            debtors.append({"name": user.name, "amount": abs(balance)})

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