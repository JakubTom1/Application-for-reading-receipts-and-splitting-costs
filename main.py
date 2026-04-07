from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from typing import List, Dict
from collections import defaultdict

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
    try:
        image_bytes = await file.read()
        validated_items = ai_service.analyze_image_with_gemini(image_bytes)
        return validated_items
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error during analysis.")


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


@app.get("/balances")
def calculate_balances(db: Session = Depends(get_db)):
    """
    NEW: Calculates debts.
    Algorithm: Final product price / number of assigned users.
    Every assigned user (except the payer) owes this amount to the payer.
    """
    receipts = db.query(models.DBReceipt).all()

    # Debt dictionary: balances[debtor][creditor] = amount
    balances = defaultdict(lambda: defaultdict(float))

    for receipt in receipts:
        payer_name = receipt.payer.name

        for item in receipt.items:
            # If no one is assigned, skip the product
            if not item.splits:
                continue

            # Divide the amount equally among assigned users
            split_amount = item.final_price / len(item.splits)

            for split in item.splits:
                debtor_name = split.user.name

                # User does not owe money to themselves
                if debtor_name != payer_name:
                    balances[debtor_name][payer_name] += split_amount

    # Format the result into readable JSON
    result = []
    for debtor, debts in balances.items():
        for creditor, amount in debts.items():
            if amount > 0:
                result.append({
                    "debtor": debtor,
                    "owes_to": creditor,
                    "amount": round(amount, 2)
                })

    return {"summary": result}