import os
import requests
import time
from flask import Flask, request, jsonify

app = Flask(__name__)

print("🚀 Flask app starting...", flush=True)

# ---------- ENV ----------
SHOP = os.getenv("SHOPIFY_SHOP")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2024-07")

CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")

print("🏪 SHOP:", SHOP, flush=True)

# ---------- TOKEN CACHE ----------
SHOPIFY_TOKEN = None
TOKEN_TIME = 0

def get_shopify_access_token():
    global SHOPIFY_TOKEN, TOKEN_TIME

    if SHOPIFY_TOKEN and time.time() - TOKEN_TIME < 3000:
        print("🔁 Using cached Shopify token", flush=True)
        return SHOPIFY_TOKEN

    print("🔐 Requesting Shopify access token...", flush=True)

    url = f"https://{SHOP}/admin/oauth/access_token"

    payload = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "client_credentials"
    }

    res = requests.post(url, json=payload)
    print("🔁 Token raw response:", res.text, flush=True)

    data = res.json()
    if not data.get("access_token"):
        raise Exception("❌ Token failed")

    SHOPIFY_TOKEN = data["access_token"]
    TOKEN_TIME = time.time()
    print("✅ Token received", flush=True)
    return SHOPIFY_TOKEN

# ---------- HELPERS ----------
def _json_headers():
    return {
        "X-Shopify-Access-Token": get_shopify_access_token(),
        "Content-Type": "application/json",
    }

def _graphql_url():
    return f"https://{SHOP}/admin/api/{API_VERSION}/graphql.json"

def _rest_url(path):
    return f"https://{SHOP}/admin/api/{API_VERSION}/{path}"

def _to_number(x):
    try:
        return float(x) if x not in (None, "") else None
    except:
        return None

# ---------- GRAPHQL ----------
def shopify_graphql(query, variables=None):
    print("📤 GraphQL Query:", query[:120], "...", flush=True)
    r = requests.post(_graphql_url(), headers=_json_headers(), json={"query": query, "variables": variables})
    print("📥 GraphQL status:", r.status_code, flush=True)
    r.raise_for_status()
    return r.json()

# ---------- VARIANT ----------
def get_variant_product_and_inventory_by_sku(sku):
    print("🔍 Searching variant for SKU:", sku, flush=True)

    QUERY = """
    query ($q: String!) {
      productVariants(first: 1, query: $q) {
        nodes { id product { id } }
      }
    }
    """

    res = shopify_graphql(QUERY, {"q": f"sku:{sku}"})
    nodes = res.get("data", {}).get("productVariants", {}).get("nodes", [])

    if not nodes:
        return None, None, None, None

    variant_gid = nodes[0]["id"]
    product_gid = nodes[0]["product"]["id"]

    variant_id = variant_gid.split("/")[-1]
    product_id = product_gid.split("/")[-1]

    r = requests.get(_rest_url(f"variants/{variant_id}.json"), headers=_json_headers())
    r.raise_for_status()

    inventory_item_id = r.json()["variant"]["inventory_item_id"]

    return variant_gid, variant_id, inventory_item_id, product_id

# ---------- UPDATE PRODUCT ----------
def update_product_title(product_id, title):
    if not title:
        return
    payload = {"product": {"id": int(product_id), "title": title}}
    print("📝 Updating product title:", payload, flush=True)

    r = requests.put(_rest_url(f"products/{product_id}.json"), headers=_json_headers(), json=payload)
    print("📥 Product title response:", r.text, flush=True)
    r.raise_for_status()

# ---------- UPDATE VARIANT ----------
def update_variant_barcode_and_size(variant_id, barcode, size):
    payload = {"variant": {"id": int(variant_id)}}

    if barcode:
        payload["variant"]["barcode"] = barcode
    if size:
        payload["variant"]["option1"] = size

    print("🏷 Updating variant barcode/size:", payload, flush=True)

    r = requests.put(_rest_url(f"variants/{variant_id}.json"), headers=_json_headers(), json=payload)
    print("📥 Variant response:", r.text, flush=True)
    r.raise_for_status()

# ---------- PRICE ----------
def update_variant_default_price(variant_id, price, compare_price=None):
    payload = {"variant": {"id": int(variant_id), "price": str(price)}}
    if compare_price:
        payload["variant"]["compare_at_price"] = str(compare_price)

    print("💲 Updating default price:", payload, flush=True)

    r = requests.put(_rest_url(f"variants/{variant_id}.json"), headers=_json_headers(), json=payload)
    print("📥 Default price response:", r.text, flush=True)
    r.raise_for_status()

# ---------- INVENTORY ----------
def get_primary_location_id():
    r = requests.get(_rest_url("locations.json"), headers=_json_headers())
    r.raise_for_status()
    return r.json()["locations"][0]["id"]

def set_inventory_absolute(inventory_item_id, location_id, quantity):
    payload = {
        "inventory_item_id": int(inventory_item_id),
        "location_id": int(location_id),
        "available": int(quantity),
    }
    print("📦 Updating inventory:", payload, flush=True)

    r = requests.post(_rest_url("inventory_levels/set.json"), headers=_json_headers(), json=payload)
    print("📥 Inventory response:", r.text, flush=True)
    r.raise_for_status()

# ---------- ROUTE ----------
@app.route("/airtable-webhook", methods=["POST"])
def airtable_webhook():
    print("\n🔔 WEBHOOK HIT", flush=True)

    if (request.headers.get("X-Secret-Token") or "").strip() != WEBHOOK_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}
    print("📦 Payload:", data, flush=True)

    sku = data.get("SKU")
    if not sku:
        return jsonify({"error": "SKU missing"}), 400

    variant_gid, variant_id, inventory_item_id, product_id = get_variant_product_and_inventory_by_sku(sku)
    if not variant_gid:
        return jsonify({"error": "Variant not found"}), 404

    # --- TEXT UPDATES ---
    update_product_title(product_id, data.get("Title"))
    update_variant_barcode_and_size(variant_id, data.get("Barcode"), data.get("Size"))

    # --- PRICE ---
    update_variant_default_price(
        variant_id,
        _to_number(data.get("UAE price")),
        _to_number(data.get("UAE Comparison Price"))
    )

    # --- INVENTORY ---
    qty = _to_number(data.get("Qty given in shopify"))
    if qty is not None:
        loc = get_primary_location_id()
        set_inventory_absolute(inventory_item_id, loc, qty)

    print("🎉 SYNC COMPLETE", flush=True)
    return jsonify({"status": "success"}), 200

# ---------- RUN ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
