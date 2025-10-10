from flask import Flask, request, jsonify
import requests
import os
import time
import hmac
import hashlib
import json
import uuid

app = Flask(__name__)

# Environment keys
BITUNIX_API_KEY = os.getenv("BITUNIX_API_KEY")
BITUNIX_API_SECRET = os.getenv("BITUNIX_API_SECRET")

# Correct Futures endpoints
BITUNIX_ORDER_URL = "https://fapi.bitunix.com/api/v1/futures/trade/place_order"
BITUNIX_POSITION_URL = "https://fapi.bitunix.com/api/v1/futures/trade/position_info"
BITUNIX_DEPTH_URL = "https://fapi.bitunix.com/api/v1/futures/market/depth"

LEVERAGE = 50
RETRY_DELAY = 0.5
MAX_RETRIES = 5

def sha256_hex(s):
    return hashlib.sha256(s.encode()).hexdigest()

def get_bitunix_headers(method, endpoint, params=None, body=None):
    # 1️⃣ Correct timestamp format (YYYYMMDDHHMMSS)
    timestamp = time.strftime("%Y%m%d%H%M%S", time.gmtime())

    # 2️⃣ 32-char random nonce
    nonce = uuid.uuid4().hex[:32]

    # 3️⃣ Build query params string (key + value, sorted by key, no separators)
    query_str = ""
    if params:
        for k, v in sorted(params.items()):
            query_str += f"{k}{v}"

    # 4️⃣ Build body string (compact JSON, sorted keys)
    body_str = ""
    if body:
        body_str = json.dumps(body, separators=(',', ':'), sort_keys=True)

    # 5️⃣ Build digest input
    digest_input = nonce + timestamp + BITUNIX_API_KEY + query_str + body_str
    digest = sha256_hex(digest_input)

    # 6️⃣ Build final sign
    sign_input = digest + BITUNIX_API_SECRET
    sign = sha256_hex(sign_input)

    return {
        "api-key": BITUNIX_API_KEY,
        "nonce": nonce,
        "timestamp": timestamp,
        "sign": sign,
        "Content-Type": "application/json"
    }

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.json
        if not data:
            raise ValueError("No JSON payload.")
        symbol = data["symbol"].replace(".P", "").upper()
        side = data["side"].upper()
        qty = str(data["quantity"])
        sl = data.get("sl")
        tp = data.get("tp")
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    print(f"Received alert payload: {data}")
    print("Closing all open positions...")

    # --- Check and close existing positions ---
    try:
        pos_params = {"symbol": symbol, "marginCoin": "USDT"}
        headers = get_bitunix_headers("GET", BITUNIX_POSITION_URL, params=pos_params)
        pos_resp = requests.get(BITUNIX_POSITION_URL, params=pos_params, headers=headers)
        pos_data = pos_resp.json()
        if pos_data.get("data"):
            for pos in pos_data["data"]:
                if float(pos.get("size", 0)) > 0:
                    close_side = "SELL" if pos["side"].upper() == "BUY" else "BUY"
                    close_payload = {
                        "symbol": symbol,
                        "side": close_side,
                        "type": "MARKET",
                        "quantity": pos["size"]
                    }
                    close_headers = get_bitunix_headers("POST", BITUNIX_ORDER_URL, body=close_payload)
                    close_resp = requests.post(BITUNIX_ORDER_URL, json=close_payload, headers=close_headers)
                    print(f"Close response: {close_resp.text}")
    except Exception as e:
        print(f"Error closing position: {e}")

    # --- Fetch last price from order book ---
    try:
        params = {"symbol": symbol, "limit": "5"}
        resp = requests.get(BITUNIX_DEPTH_URL, params=params, timeout=10)
        resp.raise_for_status()
        depth_data = resp.json()
        if depth_data.get("code") == 0:
            bids = depth_data["data"]["bids"]
            asks = depth_data["data"]["asks"]
            last_price = (float(bids[0][0]) + float(asks[0][0])) / 2
        else:
            raise ValueError(f"Unexpected depth response: {depth_data}")
    except Exception as e:
        print(f"Error fetching last price: {e}")
        print("Could not fetch last price.")
        return jsonify({"status": "failed", "reason": str(e)}), 400

    # --- Place new order ---
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            payload = {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT",
                "price": str(last_price),
                "quantity": qty,
                "leverage": LEVERAGE,
                "stop_loss": str(sl) if sl else None,
                "take_profit": str(tp) if tp else None,
                "guaranteed_stop_loss": True
            }
            payload = {k: v for k, v in payload.items() if v is not None}
            headers = get_bitunix_headers("POST", BITUNIX_ORDER_URL, body=payload)
            r = requests.post(BITUNIX_ORDER_URL, json=payload, headers=headers)
            result = r.json()
            print(f"Attempt {attempt}: {result}")
            if result.get("code") == 0:
                return jsonify({"status": "success", "response": result})
            time.sleep(RETRY_DELAY)
        except Exception as e:
            print(f"Error on attempt {attempt}: {e}")
            time.sleep(RETRY_DELAY)

    return jsonify({"status": "failed", "message": "Max retries reached"}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
