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
def get_bitunix_headers(method, endpoint, params=None, body=None):
    timestamp = str(int(time.time() * 1000))
    
    # Sign public endpoints differently from private ones
    is_private = "trade" in endpoint or "position" in endpoint
    
    if is_private:
        # Prepare data for signing based on method and payload
        if method == "GET":
            sign_data = f"{timestamp}{BITUNIX_API_KEY}"
        elif method == "POST":
            json_body = json.dumps(body, separators=(',', ':')) if body else ''
            sign_data = f"{timestamp}{BITUNIX_API_KEY}{json_body}"
            
        signature = hmac.new(BITUNIX_API_SECRET.encode(), sign_data.encode(), hashlib.sha256).hexdigest()
        
        return {
            "X-BU-ACCESS-KEY": BITUNIX_API_KEY,
            "X-BU-ACCESS-SIGN": signature,
            "X-BU-ACCESS-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }
    else:
        # No signature needed for public endpoints like the ticker
        return {"Content-Type": "application/json"}

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
        position_headers = get_bitunix_headers("GET", BITUNIX_POSITION_URL)
        position_params = {"symbol": symbol}
        position_resp = requests.get(BITUNIX_POSITION_URL, params=position_params, headers=position_headers, timeout=10)
        print(f"DEBUG: Position API Status: {position_resp.status_code}, Body: {position_resp.text}")
        position_data = position_resp.json()
        
        if position_data.get("data"):
            # The 'data' might be a single dict or a list, so handle both
            current_positions = position_data["data"] if isinstance(position_data["data"], list) else [position_data["data"]]
            
            for pos in current_positions:
                if float(pos.get("size", 0)) > 0 and pos.get("symbol") == symbol:
                    print(f"Closing existing position for {symbol}...")
                    close_payload = {
                        "symbol": symbol,
                        "side": "SELL" if pos["side"].upper() == "BUY" else "BUY",
                        "type": "MARKET",
                        "quantity": pos["size"]
                    }
                    close_headers = get_bitunix_headers("POST", BITUNIX_ORDER_URL, body=close_payload)
                    close_resp = requests.post(BITUNIX_ORDER_URL, json=close_payload, headers=close_headers, timeout=10)
                    print(f"Close position response: {close_resp.json()}")
                    break # Assuming only one position per symbol for this logic

    except Exception as e:
        print(f"Error checking or closing existing position: {e}")
        # Continue with the new order even if closing fails

    # 3. Place new order with retry logic
    result = {"message": "Order failed after max retries."} # default result
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Get last price
            ticker_headers = get_bitunix_headers("GET", BITUNIX_TICKER_URL) # Public endpoint
            ticker_resp = requests.get(f"{BITUNIX_TICKER_URL}", params={"symbol": symbol}, headers=ticker_headers, timeout=10)
            print(f"DEBUG: Ticker API Status: {ticker_resp.status_code}, Body: {ticker_resp.text}")
            ticker_data = ticker_resp.json()
            
            last_price = None
            if ticker_data.get("code") == 0 and isinstance(ticker_data.get("data"), list):
                for ticker_item in ticker_data["data"]:
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
                "price": str(last_price),
                "quantity": str(quantity),
                "leverage": LEVERAGE,
                "stop_loss": str(stop_loss) if stop_loss else None,
                "take_profit": str(take_profit) if take_profit else None,
                "guaranteed_stop_loss": True
            }
            order_payload = {k: v for k, v in order_payload.items() if v is not None} # Clean up None values

            order_headers = get_bitunix_headers("POST", BITUNIX_ORDER_URL, body=order_payload)
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
    return jsonify({"status": "failed", "response": result}), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
