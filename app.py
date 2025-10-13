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
#Use decimals values(e.g., 0.0002 = 0.02%)
STOP_LOSS_PERCENTAGE = 0.0001
TAKE_PROFIT_PERCENTAGE = 0.005

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
    import hashlib, uuid, json, time

    nonce = str(uuid.uuid4()).replace("-", "")[:32]
    timestamp = str(int(time.time() * 1000))

    sorted_query = ""
    if query_params:
        sorted_items = sorted(query_params.items(), key=lambda x: x[0])
        sorted_query = "".join(f"{k}{v}" for k, v in sorted_items)

    body_str = ""
    if body:
        # Do NOT sort keys or add spaces; body must exactly match what is sent.
        body_str = json.dumps(body, ensure_ascii=False, separators=(",", ":"))

    digest_input = nonce + timestamp + api_key + sorted_query + body_str
    digest_hex = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()  # lowercase

    sign_input = digest_hex + secret_key
    sign_hex = hashlib.sha256(sign_input.encode("utf-8")).hexdigest()  # lowercase

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

# --------------------- Place Limit Order (Modified) ---------------------
def place_limit_order(symbol, side, quantity, guaranteed_sl=False):
    # sl and tp parameters are removed, as the bot will now calculate them
    
    if ":" in symbol:
        symbol = symbol.split(":")[-1]

    precision = ASSET_PRECISION.get(symbol, 3)
    min_qty = MIN_ORDER_QTY.get(symbol, 0.1)
    quantity = max(round(float(quantity), precision), min_qty)
    qty_str = f"{quantity:.{precision}f}"

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
        last_price = (float(bids[0][0]) + float(asks[0][0])) / 2
        last_price_str = f"{last_price:.4f}" # Use 8 for higher precision
    except Exception as e:
        print("Error parsing book prices:", e)
        return None

    side_up = side.upper() if isinstance(side, str) else str(side).upper()
    
    # --- NEW SL/TP CALCULATION LOGIC ---
    price_float = float(last_price_str)
    if side_up == "BUY":
        sl_price = price_float * (1 - STOP_LOSS_PERCENTAGE)
        tp_price = price_float * (1 + TAKE_PROFIT_PERCENTAGE)
    elif side_up == "SELL":
        sl_price = price_float * (1 + STOP_LOSS_PERCENTAGE)
        tp_price = price_float * (1 - TAKE_PROFIT_PERCENTAGE)
    else:
        print(f"⚠️ Warning: Invalid side '{side_up}' received. Cannot calculate SL/TP.")
        sl_price = None
        tp_price = None
    # --- END NEW SL/TP CALCULATION LOGIC ---

    order_body = {
        "symbol": symbol,
        "side": side_up,
        "orderType": "LIMIT",
        "price": last_price_str,
        "qty": qty_str,
        "effect": "GTC",
        "leverage": LEVERAGE,
        "tradeSide": "OPEN"
    }

    # Attach TP/SL to the order body, formatting to a consistent decimal precision
    if sl_price is not None:
        order_body["slPrice"] = f"{sl_price:.4f}" # Format for API call
    if tp_price is not None:
        order_body["tpPrice"] = f"{tp_price:.4f}" # Format for API call

    if guaranteed_sl:
        order_body["guaranteedStopLoss"] = True

    print("Placing order:", order_body)
    return send_request("POST", "/api/v1/futures/trade/place_order", body=order_body)

# --------------------- Webhook (Modified) ---------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    if not request.is_json:
        return jsonify({"status": "error", "message": "Content-Type must be application/json"}), 415
    try:
        data = request.get_json(force=True)
        symbol = data.get("symbol")
        side = data.get("side")
        quantity = data.get("quantity")
        guaranteed_sl = data.get("guaranteed_stop_loss", False)

        # The webhook no longer needs to provide SL and TP values,
        # as they are now calculated automatically.
        place_limit_order(symbol, side, quantity, guaranteed_sl)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# --------------------- Run ---------------------
if __name__ == "__main__":
    if not BITUNIX_API_KEY or not BITUNIX_API_SECRET:
        print("⚠️ BITUNIX_API_KEY or BITUNIX_API_SECRET not set")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
