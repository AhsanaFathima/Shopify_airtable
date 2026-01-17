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

#------------ UPDATE BARCODE-------------------
def update_variant_details(variant_gid, title=None, barcode=None):
    if not (title or barcode):
        return None
    var_num = variant_gid.split("/")[-1]
    url = _rest_url(f"variants/{var_num}.json")
    vdata = {"id": int(var_num)}
    if title:   vdata["title"] = title
    if barcode: vdata["barcode"] = barcode
    payload = {"variant": vdata}
    print(f"[REST] PUT variant details {url} payload={payload}", flush=True)
    resp = requests.put(url, headers=_json_headers(), json=payload)
    print("[REST] variant details resp:", resp.status_code, resp.text, flush=True)
    resp.raise_for_status()
    return resp.json()

# ---------- UPDATE PRODUCT TITLE ----------
def update_product_title(product_gid, new_title):
    pid = product_gid.split("/")[-1]
    url = _rest_url(f"products/{pid}.json")
    payload = {"product": {"id": int(pid), "title": new_title}}
    print(f"[REST] PUT product title {url} payload={payload}", flush=True)
    resp = requests.put(url, headers=_json_headers(), json=payload)
    print("[REST] product title resp:", resp.status_code, resp.text, flush=True)
    resp.raise_for_status()
    return resp.json()


# ---------- Metafields ----------
def set_metafield(owner_id_gid, namespace, key, mtype, value):
    METAFIELDS_SET = """
    mutation metafieldsSet($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) {
        metafields { id namespace key type value }
        userErrors { field message }
      }
    }
    """
    variables = {
        "metafields": [{
            "ownerId": owner_id_gid,
            "namespace": namespace,
            "key": key,
            "type": mtype,
            "value": str(value)
        }]
    }
    print(f"Setting metafield {namespace}.{key}={value} on {owner_id_gid}", flush=True)
    result = shopify_graphql(METAFIELDS_SET, variables)
    try:
        errs = result["data"]["metafieldsSet"]["userErrors"]
        if errs:
            print("Metafield userErrors:", errs, flush=True)
    except Exception as e:
        print("Error reading metafield userErrors:", e, flush=True)
    return result

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
