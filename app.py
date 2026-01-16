import os, requests
from flask import Flask, request, jsonify

app = Flask(__name__)

print("🚀 Flask app starting...", flush=True)

SHOP = os.getenv("SHOPIFY_SHOP")
TOKEN = os.getenv("SHOPIFY_API_TOKEN")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2024-07")

MARKET_NAMES = {
    "UAE": "United Arab Emirates",
    "Asia": "Asia Market with 55 rate",
    "America": "America catlog",
}

CACHED_PRICE_LISTS = None

def headers():
    return {
        "X-Shopify-Access-Token": TOKEN,
        "Content-Type": "application/json",
    }

def rest(path):
    return f"https://{SHOP}/admin/api/{API_VERSION}/{path}"

def graphql():
    return f"https://{SHOP}/admin/api/{API_VERSION}/graphql.json"

def to_number(x):
    try:
        return float(x) if x not in ("", None) else None
    except:
        return None

# ---------------- GRAPHQL ----------------
def shopify_graphql(query, variables=None):
    r = requests.post(graphql(), headers=headers(), json={"query": query, "variables": variables})
    r.raise_for_status()
    return r.json()

# ---------------- PRICE LISTS ----------------
def get_market_price_lists():
    global CACHED_PRICE_LISTS
    if CACHED_PRICE_LISTS:
        return CACHED_PRICE_LISTS

    QUERY = """
    query {
      catalogs(first: 20, type: MARKET) {
        nodes {
          title
          status
          priceList { id currency }
        }
      }
    }
    """

    res = shopify_graphql(QUERY)
    price_lists = {}

    for c in res["data"]["catalogs"]["nodes"]:
        if c["status"] == "ACTIVE" and c["priceList"]:
            price_lists[c["title"]] = c["priceList"]

    print("📊 Price lists:", price_lists, flush=True)
    CACHED_PRICE_LISTS = price_lists
    return price_lists

# ---------------- VARIANT BY SKU ----------------
def get_variant_by_sku(sku):
    QUERY = """
    query ($q: String!) {
      productVariants(first: 1, query: $q) {
        nodes { id }
      }
    }
    """

    res = shopify_graphql(QUERY, {"q": f"sku:{sku}"})
    nodes = res["data"]["productVariants"]["nodes"]

    if not nodes:
        return None, None, None

    gid = nodes[0]["id"]
    vid = gid.split("/")[-1]

    r = requests.get(rest(f"variants/{vid}.json"), headers=headers())
    r.raise_for_status()

    inventory_item_id = r.json()["variant"]["inventory_item_id"]
    return gid, vid, inventory_item_id

# ---------------- INVENTORY ----------------
def get_location():
    r = requests.get(rest("locations.json"), headers=headers())
    r.raise_for_status()
    return r.json()["locations"][0]["id"]

def set_inventory(inventory_item_id, location_id, qty):
    print("📦 Inventory update →", qty, flush=True)
    requests.post(
        rest("inventory_levels/set.json"),
        headers=headers(),
        json={
            "inventory_item_id": int(inventory_item_id),
            "location_id": int(location_id),
            "available": int(qty),
        },
    ).raise_for_status()

# ---------------- DEFAULT PRICE ----------------
def update_default_price(variant_id, price, compare=None):
    payload = {"variant": {"id": int(variant_id), "price": str(price)}}
    if compare is not None:
        payload["variant"]["compare_at_price"] = str(compare)

    print("💲 Default price:", payload, flush=True)
    requests.put(rest(f"variants/{variant_id}.json"), headers=headers(), json=payload).raise_for_status()

# ---------------- MARKET PRICE ----------------
def update_price_list(price_list_id, variant_gid, price, currency, compare=None):
    price_input = {
        "variantId": variant_gid,
        "price": {"amount": str(price), "currencyCode": currency},
    }

    if compare is not None:
        price_input["compareAtPrice"] = {
            "amount": str(compare),
            "currencyCode": currency,
        }

    MUTATION = """
    mutation ($pl: ID!, $prices: [PriceListPriceInput!]!) {
      priceListFixedPricesAdd(priceListId: $pl, prices: $prices) {
        userErrors { message }
      }
    }
    """

    print("➡️ Market price update", price_input, flush=True)

    shopify_graphql(MUTATION, {"pl": price_list_id, "prices": [price_input]})

# ---------------- ROUTES ----------------
@app.route("/")
def home():
    return "✅ Airtable Shopify Sync Running"

@app.route("/airtable-webhook", methods=["POST"])
def webhook():
    print("\n🔔 WEBHOOK HIT", flush=True)

    if request.headers.get("X-Secret-Token") != WEBHOOK_SECRET:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json or {}

    sku = data.get("SKU")
    if not sku:
        return jsonify({"error": "SKU missing"}), 400

    prices = {
        "UAE": to_number(data.get("UAE price")),
        "Asia": to_number(data.get("Asia Price")),
        "America": to_number(data.get("America Price")),
    }

    compares = {
        "UAE": to_number(data.get("UAE Comparison Price")),
        "Asia": to_number(data.get("Asia Comparison Price")),
        "America": to_number(data.get("America Comparison Price")),
    }

    qty = to_number(data.get("Qty given in shopify"))

    gid, vid, inventory_item_id = get_variant_by_sku(sku)
    if not gid:
        return jsonify({"error": "Variant not found"}), 404

    # Default price = UAE
    if prices["UAE"] is not None:
        update_default_price(vid, prices["UAE"], compares["UAE"])

    # Inventory
    if qty is not None:
        location = get_location()
        set_inventory(inventory_item_id, location, qty)

    price_lists = get_market_price_lists()

    for market, price in prices.items():
        if price is None:
            continue

        pl = price_lists.get(MARKET_NAMES.get(market))
        if not pl:
            continue

        update_price_list(pl["id"], gid, price, pl["currency"], compares.get(market))

    print("🎉 SYNC COMPLETE", flush=True)
    return jsonify({"status": "success"})
