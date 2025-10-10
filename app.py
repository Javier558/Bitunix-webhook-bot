from flask import Flask, request, jsonify
import requests
import os
import time
import hmac
import hashlib
import json

app = Flask(__name__)

# API keys from environment
BITUNIX_API_KEY = os.getenv("BITUNIX_API_KEY")
BITUNIX_API_SECRET = os.getenv("BITUNIX_API_SECRET")

# Bitunix endpoints
BITUNIX_ORDER_URL = "https://fapi.bitunix.com/api/v1/futures/trade/place_order"
BITUNIX_TICKER_URL = "https://fapi.bitunix.com/api/v1/futures/market/ticker"
BITUNIX_POSITION_URL = "https://fapi.bitunix.com/api/v1/futures/trade/position_info"

LEVERAGE = 50
RETRY_DELAY = 0.5  # seconds
MAX_RETRIES = 5

# --- Bitunix API Signing Function ---
def get_bitunix_headers(payload=None):
    timestamp = str(int(time.time() * 1000))
    if payload:
        data_string = json.dumps(payload, separators=(',', ':'))
    else:
        data_string = ""
    
    sign_payload = f"{timestamp}{BITUNIX_API_KEY}{data_string}"
    signature = hmac.new(BITUNIX_API_SECRET.encode(), sign_payload.encode(), hashlib.sha256).hexdigest()
    
    return {
        "X-BU-ACCESS-KEY": BITUNIX_API_KEY,
        "X-BU-ACCESS-SIGN": signature,
        "X-BU-ACCESS-TIMESTAMP": timestamp,
        "Content-Type": "application/json"
    }

@app.route("/", methods=["POST"])
def webhook():
    # 1. Handle incoming JSON payload and errors
    try:
        data = request.json
        if not data:
            raise ValueError("No JSON payload received.")
        
        symbol = data["symbol"].upper()  # Ensure symbol is uppercase
        side = data["side"].upper()      # Ensure side is uppercase
        quantity = data["quantity"]
        stop_loss = data.get("sl")
        take_profit = data.get("tp")
        
    except Exception as e:
        print(f"Error parsing incoming JSON: {e}")
        return jsonify({"status": "error", "message": f"Invalid JSON payload: {e}"}), 400

    print(f"Received webhook for {symbol} ({side})")

    # 2. Check for open positions and close them if necessary
    try:
        position_headers = get_bitunix_headers()
        position_params = {"symbol": symbol}
        position_resp = requests.get(BITUNIX_POSITION_URL, params=position_params, headers=position_headers, timeout=10)
        position_data = position_resp.json()
        
        if position_data.get("data"):
            current_position = position_data["data"][0] if isinstance(position_data["data"], list) and position_data["data"] else None
            if current_position and float(current_position.get("size", 0)) > 0:
                print(f"Closing existing position for {symbol}...")
                close_payload = {
                    "symbol": symbol,
                    "side": "SELL" if current_position["side"].upper() == "BUY" else "BUY",
                    "type": "MARKET",
                    "quantity": current_position["size"]
                }
                close_headers = get_bitunix_headers(payload=close_payload)
                close_resp = requests.post(BITUNIX_ORDER_URL, json=close_payload, headers=close_headers, timeout=10)
                print(f"Close position response: {close_resp.json()}")

    except Exception as e:
        print(f"Error checking or closing existing position: {e}")
        # Continue with the new order even if closing fails

    # 3. Place new order with retry logic
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Get last price
            ticker_headers = get_bitunix_headers()
            ticker_resp = requests.get(f"{BITUNIX_TICKER_URL}?symbol={symbol}", headers=ticker_headers, timeout=10)
            ticker_data = ticker_resp.json()
            
            # Parse Bitunix ticker response correctly
            ticker_list = ticker_data.get("data", [])
            last_price = None
            for ticker_item in ticker_list:
                if ticker_item.get("symbol") == symbol:
                    last_price = float(ticker_item.get("lastPrice"))
                    break
            
            if last_price is None:
                raise ValueError(f"'lastPrice' not found for {symbol} in ticker response: {ticker_data}")

            print(f"Attempt {attempt}: Last price for {symbol} is {last_price}")

            # Build and send order
            order_payload = {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT",
                "price": last_price,
                "quantity": quantity,
                "leverage": LEVERAGE,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "guaranteed_stop_loss": True # Bitunix expects boolean
            }

            order_headers = get_bitunix_headers(payload=order_payload)
            response = requests.post(BITUNIX_ORDER_URL, json=order_payload, headers=order_headers, timeout=10)
            result = response.json()
            print(f"Order attempt {attempt}: {result}")

            if result.get("code") == 0:
                print("Order successfully placed ✅")
                return jsonify({"status": "success", "response": result})

            time.sleep(RETRY_DELAY)

        except Exception as e:
            print(f"Error on attempt {attempt} for {symbol}: {e}")
            time.sleep(RETRY_DELAY)

    print(f"Order for {symbol} failed after {MAX_RETRIES} retries ❌")
    return jsonify({"status": "failed", "message": "Max retries reached."}), 400

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

