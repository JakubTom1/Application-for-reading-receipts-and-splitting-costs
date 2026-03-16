import os
from dotenv import load_dotenv
from google import genai
import json
from PIL import Image
import requests

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("No API key in .env! file")

client = genai.Client(api_key=API_KEY)

def scan_receipt(image_path):
    print(f"Loading image: {image_path}...")
    try:
        img = Image.open(image_path)
    except FileNotFoundError:
        return "File not found. Please check the path."

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
        3. Return ONLY pure JSON format as a list of dictionaries. Do not add any text before or after JSON (no ```json markers).
        4. Each dictionary MUST have exactly these keys: 
           - "name" (string)
           - "quantity" (float)
           - "unit_price" (float)
           - "discount" (float)
           - "final_price" (float - the total paid for these items AFTER discount)
        5. Skip the overall receipt total sum, amount paid, change, PTU/VAT summaries, and store details.
    """

    print("Sending for analysis (this will take a few seconds)...")

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[prompt, img]
        )

        raw_text = response.text.strip()

        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]

        json_data = json.loads(raw_text.strip())
        return json_data

    except json.JSONDecodeError:
        print("Model did not return valid JSON format.")
        print("Raw model response:\n", raw_text)
        return None
    except Exception as e:
        print(f"API communication error: {e}")
        return None


if __name__ == "__main__":
    # 1. Scan the receipt using Gemini
    result = scan_receipt("paragon.jpg")

    if result:
        print("\n=== SUCCESS! RECOGNIZED PRODUCTS ===")
        print(json.dumps(result, indent=4, ensure_ascii=False))

        # 2. Prepare the payload for the API
        # We hardcode user_id=1 for now, later the mobile app will provide the logged-in user
        api_payload = {
            "user_id": 1,
            "items": result
        }

        # 3. Send the data to your local FastAPI server
        print("\nSending data to the MySQL database via API...")
        try:
            response = requests.post("http://127.0.0.1:8000/receipts/save", json=api_payload)
            print("API Response:", response.json())
        except requests.exceptions.ConnectionError:
            print("Error: Cannot connect to API. Make sure uvicorn is running!")