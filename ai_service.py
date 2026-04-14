import os
import io
import json
from google import genai
from PIL import Image
from schemas import ReceiptItemScanned
from pydantic import ValidationError
import time
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from google.genai import errors

# Initialize Gemini Client
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("No API key in .env! file")

ai_client = genai.Client(api_key=API_KEY)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=10),
    retry=retry_if_exception_type(errors.ServerError),
    reraise=True
)

def analyze_image_with_gemini(image_bytes: bytes) -> list[ReceiptItemScanned]:
    """
    Sends image to Gemini, retrieves JSON, and strictly validates it
    using Pydantic models before returning.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception as e:
        raise ValueError("Invalid image format.")

    prompt = """
        You are an expert expense analysis assistant specializing in Polish fiscal receipts (paragon fiskalny). 
        Analyze this receipt. Extract the list of purchased products, their quantities, unit prices, discounts applied to them, and their FINAL paid prices.
    
        CRITICAL RULES FOR POLISH RECEIPTS:
        1. MULTIPLIERS & UNIT PRICE: Look for quantity multipliers (e.g., "12 x2,39 28,68"). The quantity is 12, the unit_price is 2.39. If no multiplier is present, default quantity to 1 and unit_price equals the line price.
        2. DISCOUNTS (OPUST): Discounts are printed BELOW the product (e.g., "OPUST -4,50" or just "-4,50"). You MUST account for them as a positive float in the "discount" field (e.g., 4.50). If no discount is applied, set "discount" to 0.0. The final price is usually printed directly below the "OPUST" line.
           Example of a discounted item on receipt:
           SokTymbarkJabłko1l     3 x5,49 16,47
           OPUST                         -4,50
                                         11,97
           Correct Extraction -> "name": "SokTymbarkJabłko1l", "quantity": 3.0, "unit_price": 5.49, "discount": 4.50, "final_price": 11.97
        3. If There is no final proce after discount you have to subtract discount from the unit price to get final price.
           Example of a discounted item without final price line:
           KawaKrupica250g   1 x19,99 19,99
           OPUST                      -3,00
           (final price not listed, so calculate: 19.99 - 3.00 = 16.99)
        4. Return ONLY pure JSON format as a list of dictionaries. Do not add any text before or after JSON (no ```json markers).
        5. Each dictionary MUST have exactly these keys: 
           - "name" (string)
           - "quantity" (float)
           - "unit_price" (float)
           - "discount" (float)
           - "final_price" (float - the total paid for these items AFTER discount)
        6. Skip the overall receipt total sum, amount paid, change, PTU/VAT summaries, and store details.
    """
    start_time = time.time()

    # Call Gemini API
    response = ai_client.models.generate_content(
        model='gemini-2.5-flash',
        contents=[prompt, img]
    )
    gemini_time = time.time() - start_time
    print(f"⏱️ GEMINI API time: {gemini_time:.2f} seconds")

    raw_text = response.text.strip()

    # Clean up potential markdown formatting from Gemini
    if raw_text.startswith("```json"):
        raw_text = raw_text[7:]
    if raw_text.endswith("```"):
        raw_text = raw_text[:-3]

    # 1. Check if it's valid JSON
    try:
        json_data = json.loads(raw_text.strip())
    except json.JSONDecodeError:
        raise ValueError("Gemini did not return valid JSON.")

    # 2. STRICT VALIDATION: Check if JSON matches our Pydantic schema
    validated_items = []
    try:
        for item in json_data:
            # This line will crash (throw ValidationError) if types are wrong or keys are missing
            valid_item = ReceiptItemScanned(**item)
            validated_items.append(valid_item)
    except ValidationError as e:
        raise ValueError(f"Gemini returned JSON with invalid structure: {e}")

    return validated_items