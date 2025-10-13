from flask import Flask, request, jsonify
import requests
import os
import time
import json
import hashlib
import uuid
import math

app = Flask(__name__)

BITUNIX_API_KEY = os.getenv("BITUNIX_API_KEY")
BITUNIX_API_SECRET = os.getenv("BITUNIX_API_SECRET")
BASE_URL = "https://fapi.bitunix.com"
LEVERAGE = 50
RETRY_DELAY = 0.5
MAX_RETRIES = 5

# Minimum order size per asset (adjust as needed)
MIN_ORDER_QTY = {
    "SOLUSDT": 0.1,
    "BTCUSDT": 0.0001,
    # add more symbols here
}

# Decimal precision per asset (adjust as needed)
ASSET_PRECISION = {
    "SOLUSDT": 4,
    "BTCUSDT": 5,
    # add more symbols here
}

# --------------------- Signature ---------------------
def generate_signature(api_key, secret_key, query_params=None, body=None):
    """
    Bitunix signature (debug-enhanced version)

    Formula:
        digest = SHA256(nonce + timestamp + apiKey + queryParams + body).upper()
        sign   = SHA256(digest + secretKey).upper()
    """

    import hashlib, uuid, json, time

    nonce = str(uuid.uuid4()).replace("-", "")[:32]
    timestamp = str(int(time.time() * 1000))

    # ---- Build query string (alphabetical order) ----
    sorted_query = ""
    if query_params:
        sorted_items = sorted(query_params.items(), key=lambda x: x[0])
        sorted_query = "".join(f"{k}{v}" for k, v in sorted_items)

    # ---- Compact JSON body ----
    body_str = ""
    if body:
        body_str = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    # ---- Step 1: digest ----
    digest_input_str = nonce + timestamp + api_key + sorted_query + body_str
    digest_hex = hashlib.sha256(digest_input_str.encode("utf-8")).hexdigest().upper()

    # ---- Step 2: sign ----
    sign_input_str = digest_hex + secret_key
    sign_hex = hashlib.sha256(sign_input_str.encode("utf-8")).hexdigest().upper()

    # ---- Debugging output ----
    print("\n===== Bitunix Signature Debug =====")
    print("Nonce:", nonce)
    print("Timestamp:", timestamp)
    print("Sorted query:", sorted_query)
    print("Body string:", body_str)
    print("Digest input (nonce+timestamp+apiKey+query+body):", digest_input_str)
    print("Digest HEX (uppercase):", digest_hex)
    print("Sign input (digest+secret):", sign_input_str)
    print("Final SIGN (uppercase):", sign_hex)
    print("===================================\n")

    headers = {
        "api-key": api_key,
        "nonce": nonce,
        "timestamp": timestamp,
        "sign": sign_hex,
        "Content-Type": "application/json"
    }
    return headers


# --------------------- Request Helper ---------------------
def send_request(method, endpoint, body=None, query=None):
    url = f"{BASE_URL}{endpoint}"
    for attempt in range(1, MAX_RETRIES + 1):
        # generate fresh headers each attempt (nonce/timestamp must be fresh)
        headers = generate_signature(BITUNIX_API_KEY, BITUNIX_API_SECRET, query, body)
        try:
            r = requests.request(method, url,
                                 headers=headers,
                                 json=body if method.upper() in ["POST", "DELETE"] else None,
                                 params=query, timeout=10)
            # Some endpoints return HTTP 200 even with API-level error, so handle both
            # raise_for_status for network/HTTP errors
            r.raise_for_status()
            try:
                resp_json = r.json()
            except ValueError:
                print(f"Non-JSON response (attempt {attempt}): {r.text}")
                resp_json = None

            # If we got a dict with 'code' key, follow doc pattern
            if isinstance(resp_json, dict):
                code = resp_json.get("code")
                if code == 0:
                    return resp_json
                else:
                    # Log API-level error but allow retry
                    print(f"Bitunix API error (attempt {attempt}): code={code}, msg={resp_json.get('msg')}, full={resp_json}")
            else:
                # Not a dict - log and return raw content (no .get usage)
                print(f"Unexpected response shape (attempt {attempt}): {resp_json if resp_json is not None else r.text}")
                # Return raw - caller should handle
                return resp_json
        except requests.exceptions.RequestException as e:
            print(f"Request error attempt {attempt}: {e}")
        time.sleep(RETRY_DELAY)

    return None

# --------------------- Open Positions ---------------------
def get_open_positions(symbol):
    resp = send_request("GET", "/api/v1/futures/trade/positions", query={"symbol": symbol})
    if isinstance(resp, dict) and "data" in resp:
        # adapt to returned structure: docs show list under "data"
        try:
            return [p for p in resp["data"] if float(p.get("positionAmt", 0) or p.get("qty", 0) or 0) != 0]
        except Exception:
            # fallback - return data as-is if can't parse
            return resp.get("data", [])
    return []

def close_all_positions(symbol):
    positions = get_open_positions(symbol)
    if not positions:
        return {"code": 1, "msg": "No open positions"}
    return send_request("POST", "/api/v1/futures/trade/close_all_position", body={"symbol": symbol})

# --------------------- Place Limit Order ---------------------
def place_limit_order(symbol, side, quantity, sl=None, tp=None, guaranteed_sl=False):
    # Clean symbol prefix if any (TradingView often sends EXCHANGE:SYM)
    if ":" in symbol:
        symbol = symbol.split(":")[-1]

    # Round quantity to asset precision and enforce minimum
    precision = ASSET_PRECISION.get(symbol, 3)
    min_qty = MIN_ORDER_QTY.get(symbol, 0.001)
    # Round to correct precision
    quantity = max(round(float(quantity), precision), min_qty)
    qty_str = f"{quantity:.{precision}f}"

    # Fetch book to compute a conservative limit price near mid price
    ticker_resp = send_request("GET", "/api/v1/futures/market/depth", query={"symbol": symbol, "limit": 1})
    if not isinstance(ticker_resp, dict) or "data" not in ticker_resp:
        print("❌ Failed to fetch order book")
        return None
    bids = ticker_resp["data"].get("bids", [])
    asks = ticker_resp["data"].get("asks", [])
    if not bids or not asks:
        print("❌ Empty bids or asks")
        return None
    try:
        # bids/asks elements may be [price, qty]
        last_price = (float(bids[0][0]) + float(asks[0][0])) / 2
        # --- ADDED/MODIFIED LINE HERE ---
        # Explicitly round the price to a sufficient number of decimal places before converting to string
        # Use 8 decimal places as a robust default, or consult Bitunix docs for specific symbol precision
        last_price_str = f"{last_price:.4f}" 
        # --- END OF MODIFIED LINE ---

    except Exception as e:
        print("Error parsing book prices:", e)
        return None

    # Map side to API expected uppercase
    side_up = side.upper() if isinstance(side, str) else str(side).upper()
    if side_up not in ("BUY", "SELL"):
        # Attempt to map common synonyms
        side_up = "BUY" if side in ("buy", "long", "LONG") else "SELL"

    # Build order body according to Bitunix place_order API
    order_body = {
        "symbol": symbol,
        "side": side_up,         # BUY or SELL
        "orderType": "LIMIT",    # LIMIT order
        "price": last_price_str, # Use the explicitly formatted price string
        "qty": qty_str,          # 'qty' expected by API
        "effect": "GTC",
        "leverage": LEVERAGE,
        "tradeSide": "OPEN"      # open order by default (use CLOSE for closing in hedge-mode)
    }

    # Attach TP/SL in API parameter names
    if sl is not None:
        order_body["slPrice"] = f"{sl:.4f}"
    if tp is not None:
        order_body["tpPrice"] = f"{tp:.4f}"

    # Keep guaranteed stop if available flag (some endpoints accept it)
    if guaranteed_sl:
        order_body["guaranteedStopLoss"] = True

    # Remove None values (already guarded)
    print("Placing order:", order_body)
    return send_request("POST", "/api/v1/futures/trade/place_order", body=order_body)

# ... rest of your code ...
# --------------------- Webhook ---------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    #i removed this:if not request.is_json:
    #and this    return jsonify({"status": "error", "message": "Content-Type must be application/json"}), 415
    try:
        #and this: data = request.get_json(force=True)
        data = json.loads(request.get_data())
        print("Received Webhook Data:", data) # <-- Add this line 
        symbol = data.get("symbol")
        side = data.get("side")
        quantity = data.get("quantity")
        sl = data.get("sl")
        tp = data.get("tp")
        guaranteed_sl = data.get("guaranteed_stop_loss", False)

        if not symbol or not side or quantity is None:
            return jsonify({"status": "error", "message": "Missing required fields (symbol, side, quantity)"}), 400

        # If quantity is 0 -> close all positions
        if float(quantity) == 0.0:
            resp = close_all_positions(symbol)
            if isinstance(resp, dict) and resp.get("code") == 0:
                return jsonify({"status": "success", "response": resp})
            else:
                return jsonify({"status": "failed", "message": resp or "Failed to close positions"}), 500

        resp = place_limit_order(symbol, side, quantity, sl, tp, guaranteed_sl)
        if resp:
            return jsonify({"status": "success", "response": resp})
        else:
            return jsonify({"status": "failed", "message": "Failed to place order"}), 500

    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({"status": "failed", "message": str(e)}), 500

# --------------------- Run ---------------------
if __name__ == "__main__":
    if not BITUNIX_API_KEY or not BITUNIX_API_SECRET:
        print("⚠️ BITUNIX_API_KEY or BITUNIX_API_SECRET not set")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
