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
    You are an expense analysis assistant. Analyze this receipt (Polish or English). 
    Extract the list of purchased products and their gross prices.

    RULES:
    1. Return ONLY pure JSON format as a list of dictionaries.
    2. Each dictionary must have exactly two keys: "name" (string) and "price" (float).
    3. Skip total sum, amount paid, change, discounts, register numbers, and store details.
    4. Do not add any text before or after JSON (no ```json markers).
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