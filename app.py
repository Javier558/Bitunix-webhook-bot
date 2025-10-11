from flask import Flask, request, jsonify
import requests
import os
import time
import json
import hashlib
import uuid

app = Flask(__name__)

# Environment Variables
BITUNIX_API_KEY = os.getenv("BITUNIX_API_KEY")
BITUNIX_API_SECRET = os.getenv("BITUNIX_API_SECRET")

BASE_URL = "https://fapi.bitunix.com"  # Bitunix base URL
LEVERAGE = 50
RETRY_DELAY = 0.5
MAX_RETRIES = 5

# ------------------------------------------------------------
# ✅ Bitunix Signature Function
# ------------------------------------------------------------
def generate_signature(api_key, secret_key, query_params=None, body=None):
    nonce = str(uuid.uuid4()).replace("-", "")[:32]
    timestamp = str(int(time.time() * 1000))

    # Sort query params by key and concatenate as per Bitunix documentation
    sorted_query = ''
    if query_params:
        sorted_params = sorted(query_params.items())
        sorted_query = ''.join(f"{k}{v}" for k, v in sorted_params)
    
    # Sort JSON body keys and format without spaces as per Bitunix documentation
    body_str = ''
    if body:
        body_str = json.dumps(body, separators=(',', ':'), sort_keys=True)

    digest_input = nonce + timestamp + api_key + sorted_query + body_str
    digest = hashlib.sha256(digest_input.encode('utf-8')).hexdigest()

    sign_input = digest + secret_key
    sign = hashlib.sha256(sign_input.encode('utf-8')).hexdigest()

    headers = {
        "api-key": api_key,
        "nonce": nonce,
        "timestamp": timestamp,
        "sign": sign,
        "Content-Type": "application/json"
    }
    return headers

# ------------------------------------------------------------
# ✅ Request Helper with Retries
# ------------------------------------------------------------
def send_request(method, endpoint, body=None, query=None):
    url = f"{BASE_URL}{endpoint}"
    headers = generate_signature(BITUNIX_API_KEY, BITUNIX_API_SECRET, query, body)
    print("Sending request:", method, url)
    print("Headers:", headers)
    print("Body:", body) # For POST/DELETE, this is the JSON body
    print("Query:", query) # For GET, this is the query parameters

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if method == "POST":
                r = requests.post(url, headers=headers, json=body, timeout=10)
            elif method == "DELETE":
                r = requests.delete(url, headers=headers, json=body, timeout=10)
            else: # GET request
                r = requests.get(url, headers=headers, params=query, timeout=10)

            resp_json = r.json()
            print(f"Attempt {attempt}: {resp_json}")

            if resp_json.get("code") == 0:
                return resp_json
            else:
                time.sleep(RETRY_DELAY)
        except Exception as e:
            print(f"Error attempt {attempt}: {e}")
            time.sleep(RETRY_DELAY)
    return None

# ------------------------------------------------------------
# ✅ Close all positions for a symbol
# ------------------------------------------------------------
def close_all_positions(symbol):
    print("Closing all open positions...")
    endpoint = "/api/v1/futures/trade/close_all_position"
    body = {"symbol": symbol}
    # For POST requests, query_params for signature are None
    return send_request("POST", endpoint, body=body)

# ------------------------------------------------------------
# ✅ Place limit order with SL/TP and guaranteed SL
# ------------------------------------------------------------
def place_limit_order(symbol, side, quantity, sl=None, tp=None, guaranteed_sl=False):
    # Get order book (bids/asks)
    # For GET requests, query is passed as params, which is used for signature
    ticker_resp = send_request("GET", "/api/v1/futures/market/depth", query={"symbol": symbol, "limit": "max"})
    if not ticker_resp or "data" not in ticker_resp:
        print("❌ Failed to fetch order book data.")
        return None

    data = ticker_resp["data"]
    bids = data.get("bids", [])
    asks = data.get("asks", [])

    if not bids or not asks:
        print("❌ Empty bids or asks")
        return None

    highest_bid = float(bids[0][0])
    lowest_ask = float(asks[0][0])
    last_price = (highest_bid + lowest_ask) / 2
    print(f"Calculated last price for {symbol}: {last_price}")

    order_body = {
        "symbol": symbol,
        "side": side,
        "type": "LIMIT",
        "price": last_price,
        "quantity": quantity,
        "timeInForce": "GTC",
        "leverage": LEVERAGE,
        "stopLossPrice": sl,
        "takeProfitPrice": tp,
        "guaranteedStopLoss": guaranteed_sl
    }

    # Remove keys with None values
    order_body = {k: v for k, v in order_body.items() if v is not None}

    # For POST requests, query_params for signature are None
    return send_request("POST", "/api/v1/futures/trade/place_order", body=order_body)

# ------------------------------------------------------------
# ✅ Partial position closing
# ------------------------------------------------------------
def partial_position_close(symbol, quantity):
    print(f"Partial closing {quantity} of {symbol}")
    endpoint = "/api/v1/futures/trade/close_partial_position"
    body = {"symbol": symbol, "quantity": quantity}
    # For POST requests, query_params for signature are None
    return send_request("POST", endpoint, body=body)

# ------------------------------------------------------------
# ✅ Webhook route
# ------------------------------------------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    if not data:
        return jsonify({"status": "error", "message": "No JSON payload received"}), 400

    print("Received alert payload:", data)

    symbol = data.get("symbol")
    side = data.get("side")
    quantity = data.get("quantity")
    sl = data.get("sl")
    tp = data.get("tp")
    guaranteed_sl = data.get("guaranteed_stop_loss", False)

    if not symbol or not side or not quantity:
        return jsonify({"status": "error", "message": "Missing required fields (symbol, side, quantity)"}), 400

    # Close all open positions first
    close_all_positions(symbol)

    # Place new limit order
    resp = place_limit_order(symbol, side, quantity, sl, tp, guaranteed_sl)

    if resp:
        return jsonify({"status": "success", "response": resp})
    else:
        return jsonify({"status": "failed"}), 500

# ------------------------------------------------------------
# ✅ Run server
# ------------------------------------------------------------
if __name__ == "__main__":
    # Ensure API keys are set for testing locally (or in production)
    if not BITUNIX_API_KEY or not BITUNIX_API_SECRET:
        print("WARNING: BITUNIX_API_KEY or BITUNIX_API_SECRET environment variables are not set.")
        print("Please set them before running the application.")
        # For demonstration purposes, you might want to exit or use dummy values if running without env vars.
        # exit(1) 

    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))

