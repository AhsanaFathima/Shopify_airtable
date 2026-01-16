from flask import Flask, request, jsonify
import requests
import os
from dotenv import load_dotenv
import time

load_dotenv()

app = Flask(__name__)

print("🚀 Starting Airtable → Shopify Sync API")

# ---------- CONFIG ----------
SHOP = os.getenv("SHOPIFY_DOMAIN")   # devfragrantsouq
CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")

UAE_CATALOG_ID = os.getenv("UAE_CATALOG_ID")
ASIA_CATALOG_ID = os.getenv("ASIA_CATALOG_ID")
AMERICA_CATALOG_ID = os.getenv("AMERICA_CATALOG_ID")

print("Shop:", SHOP)
print("UAE Catalog:", UAE_CATALOG_ID)
print("Asia Catalog:", ASIA_CATALOG_ID)
print("America Catalog:", AMERICA_CATALOG_ID)

# ---------- TOKEN CACHE ----------
SHOPIFY_TOKEN = None
TOKEN_TIME = 0

def get_shopify_access_token():
    global SHOPIFY_TOKEN, TOKEN_TIME

    if SHOPIFY_TOKEN and time.time() - TOKEN_TIME < 3000:
        print("🔁 Using cached token")
        return SHOPIFY_TOKEN

    print("🔐 Requesting Shopify access token...")

    url = f"https://{SHOP}.myshopify.com/admin/oauth/access_token"

    payload = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "client_credentials"
    }

    res = requests.post(url, json=payload)

    print("🔁 Token raw response:", res.text)

    data = res.json()

    if "access_token" not in data:
        raise Exception("❌ Token failed")

    SHOPIFY_TOKEN = data["access_token"]
    TOKEN_TIME = time.time()

    print("✅ Token received")

    return SHOPIFY_TOKEN


def get_headers():
    return {
        "X-Shopify-Access-Token": get_shopify_access_token(),
        "Content-Type": "application/json"
    }

# ---------- HELPERS ----------

def update_product(product_id, title):
    print("➡️ Updating product title")

    url = f"https://{SHOP}.myshopify.com/admin/api/2025-01/products/{product_id}.json"
    payload = {"product": {"id": product_id, "title": title}}

    res = requests.put(url, json=payload, headers=get_headers())
    print("Product update response:", res.text)
    return res.json()

def update_variant_inventory(variant_id, qty):
    print("➡️ Updating inventory")

    url = f"https://{SHOP}.myshopify.com/admin/api/2025-01/variants/{variant_id}.json"
    payload = {"variant": {"id": variant_id, "inventory_quantity": qty}}

    res = requests.put(url, json=payload, headers=get_headers())
    print("Inventory update response:", res.text)
    return res.json()

def update_catalog_price(catalog_id, variant_id, price, compare_price):
    print(f"➡️ Updating catalog {catalog_id} price")

    url = f"https://{SHOP}.myshopify.com/admin/api/2025-01/catalogs/{catalog_id}/prices.json"

    payload = {
        "prices": [
            {
                "variant_id": variant_id,
                "price": price,
                "compare_at_price": compare_price
            }
        ]
    }

    res = requests.post(url, json=payload, headers=get_headers())
    print("Catalog price response:", res.text)
    return res.json()

# ---------- WEBHOOK ----------

@app.route("/webhook", methods=["POST"])
def webhook():
    print("\n🔥 Webhook triggered")

    data = request.json
    print("Payload received:", data)

    # ---- Airtable inputs ----
    product_id = data.get("product_id")
    title = data.get("title")
    sku = data.get("sku")
    barcode = data.get("barcode")
    size = data.get("size")
    quantity = data.get("quantity")

    uae_price = data.get("uae_price")
    asia_price = data.get("asia_price")
    america_price = data.get("america_price")

    uae_compare = data.get("uae_compare_price")
    asia_compare = data.get("asia_compare_price")
    america_compare = data.get("america_compare_price")

    print("Product ID:", product_id)
    print("SKU:", sku)
    print("Barcode:", barcode)
    print("Size:", size)
    print("Quantity:", quantity)

    # ---- Fetch product ----
    print("Fetching product from Shopify...")

    product_url = f"https://{SHOP}.myshopify.com/admin/api/2025-01/products/{product_id}.json"
    product_data = requests.get(product_url, headers=get_headers()).json()

    variant_id = None

    for v in product_data["product"]["variants"]:
        print("Checking variant SKU:", v["sku"])
        if v["sku"] == sku:
            variant_id = v["id"]

    if not variant_id:
        print("❌ Variant not found")
        return jsonify({"error": "Variant not found"}), 400

    print("✅ Variant found:", variant_id)

    # ---- Shopify Updates ----
    update_product(product_id, title)
    update_variant_inventory(variant_id, quantity)

    update_catalog_price(UAE_CATALOG_ID, variant_id, uae_price, uae_compare)
    update_catalog_price(ASIA_CATALOG_ID, variant_id, asia_price, asia_compare)
    update_catalog_price(AMERICA_CATALOG_ID, variant_id, america_price, america_compare)

    print("🎉 Shopify update completed")

    return jsonify({"status": "Shopify updated successfully"})

# ---------- HOME ----------

@app.route("/")
def home():
    return "Airtable → Shopify Sync API Running"

# ---------- RUN ----------

if __name__ == "__main__":
    app.run(debug=True)
