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
    "SOLUSDT": 0.001,
    "BTCUSDT": 0.0001,
    # add more symbols here
}

# Decimal precision per asset (adjust as needed)
ASSET_PRECISION = {
    "SOLUSDT": 3,
    "BTCUSDT": 5,
    # add more symbols here
}

# --------------------- Signature ---------------------
def generate_signature(api_key, secret_key, query_params=None, body=None):
    nonce = str(uuid.uuid4()).replace("-", "")[:32]
    timestamp = str(int(time.time() * 1000))
    sorted_query = ''.join(f"{k}{v}" for k, v in sorted(query_params.items())) if query_params else ''
    body_str = json.dumps(body, separators=(',', ':'), sort_keys=True) if body else ''
    digest_input = (nonce + timestamp + api_key + sorted_query + body_str).encode('utf-8')
    digest = hashlib.sha256(digest_input).hexdigest()
    sign_input = (digest + secret_key).encode('utf-8')
    sign = hashlib.sha256(sign_input).hexdigest()
    return {
        "api-key": api_key,
        "nonce": nonce,
        "timestamp": timestamp,
        "sign": sign,
        "Content-Type": "application/json"
    }

# --------------------- Request Helper ---------------------
def send_request(method, endpoint, body=None, query=None):
    url = f"{BASE_URL}{endpoint}"
    headers = generate_signature(BITUNIX_API_KEY, BITUNIX_API_SECRET, query, body)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.request(method, url,
                                 headers=headers,
                                 json=body if method.upper() in ["POST", "DELETE"] else None,
                                 params=query, timeout=10)
            r.raise_for_status()
            resp_json = r.json()
            if resp_json.get("code") == 0:
                return resp_json
            else:
                print(f"Bitunix API error: {resp_json.get('msg')}")
        except requests.exceptions.RequestException as e:
            print(f"Request error attempt {attempt}: {e}")
        except json.JSONDecodeError:
            print(f"Error decoding JSON: {r.text}")
        time.sleep(RETRY_DELAY)
    return None

# --------------------- Open Positions ---------------------
def get_open_positions(symbol):
    resp = send_request("GET", "/api/v1/futures/trade/positions", query={"symbol": symbol})
    if resp and "data" in resp:
        return [p for p in resp["data"] if float(p.get("positionAmt", 0)) != 0]
    return []

def close_all_positions(symbol):
    positions = get_open_positions(symbol)
    if not positions:
        return {"code": 1, "msg": "No open positions"}
    return send_request("POST", "/api/v1/futures/trade/close_all_position", body={"symbol": symbol})

# --------------------- Place Limit Order ---------------------
def place_limit_order(symbol, side, quantity, sl=None, tp=None, guaranteed_sl=False):
    # Clean symbol prefix if any
    if ":" in symbol:
        symbol = symbol.split(":")[-1]

    # Round quantity to asset precision and enforce minimum
    precision = ASSET_PRECISION.get(symbol, 3)
    min_qty = MIN_ORDER_QTY.get(symbol, 0.001)
    quantity = max(round(quantity, precision), min_qty)

    ticker_resp = send_request("GET", "/api/v1/futures/market/depth", query={"symbol": symbol, "limit": 1})
    if not ticker_resp or "data" not in ticker_resp:
        print("❌ Failed to fetch order book")
        return None
    bids = ticker_resp["data"].get("bids", [])
    asks = ticker_resp["data"].get("asks", [])
    if not bids or not asks:
        print("❌ Empty bids or asks")
        return None
    last_price = (float(bids[0][0]) + float(asks[0][0])) / 2

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
    order_body = {k: v for k, v in order_body.items() if v is not None}
    print("Placing order:", order_body)
    return send_request("POST", "/api/v1/futures/trade/place_order", body=order_body)

# --------------------- Webhook ---------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    if not request.is_json:
        return jsonify({"status": "error", "message": "Content-Type must be application/json"}), 415
    try:
        data = request.get_json(force=True)
        symbol = data.get("symbol")
        side = data.get("side")
        quantity = data.get("quantity")
        sl = data.get("sl")
        tp = data.get("tp")
        guaranteed_sl = data.get("guaranteed_stop_loss", False)

        if not symbol or not side or quantity is None:
            return jsonify({"status": "error", "message": "Missing required fields"}), 400

        if float(quantity) == 0.0:
            resp = close_all_positions(symbol)
            return jsonify({"status": "success", "response": resp}) if resp.get("code") == 0 else jsonify({"status": "failed", "message": resp.get("msg")}), 500

        resp = place_limit_order(symbol, side, quantity, sl, tp, guaranteed_sl)
        return jsonify({"status": "success", "response": resp}) if resp else jsonify({"status": "failed", "message": "Failed to place order"}), 500

    except Exception as e:
        print(f"Unexpected error: {e}")
        return jsonify({"status": "failed", "message": str(e)}), 500

# --------------------- Run ---------------------
if __name__ == "__main__":
    if not BITUNIX_API_KEY or not BITUNIX_API_SECRET:
        print("⚠️ BITUNIX_API_KEY or BITUNIX_API_SECRET not set")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
