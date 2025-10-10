from flask import Flask, request, jsonify
import requests
import os
import time

app = Flask(__name__)

# API keys from environment
BITUNIX_API_KEY = os.getenv("BITUNIX_API_KEY")
BITUNIX_API_SECRET = os.getenv("BITUNIX_API_SECRET")

# Bitunix endpoints
BITUNIX_ORDER_URL = "https://fapi.bitunix.com/api/v1/futures/trade/place_order"
BITUNIX_TICKER_URL = "https://fapi.bitunix.com/api/v1/futures/market/ticker"
BITUNIX_CLOSE_POS_URL = "https://fapi.bitunix.com/api/v1/futures/trade/close_position"

LEVERAGE = 50
RETRY_DELAY = 0.5  # seconds
MAX_RETRIES = 5

# Track retrying orders: key = symbol, value = dict with 'attempts' and 'active' status
retry_orders = {}

# Track open positions to close them if needed
open_positions = {}

@app.route("/", methods=["POST"])
def webhook():
    data = request.json
    symbol = data["symbol"]
    side = data["side"]
    quantity = data["quantity"]
    stop_loss = data.get("sl")
    take_profit = data.get("tp")
    guaranteed_sl = data.get("guaranteed_stop_loss", True)

    headers = {
        "Content-Type": "application/json",
        "X-API-KEY": BITUNIX_API_KEY
    }

    # 1️⃣ Cancel old retry if new alert comes
    if retry_orders.get(symbol, {}).get("active", False):
        print(f"Skipping old retry for {symbol} due to new alert.")
        retry_orders[symbol]["active"] = False

    # 2️⃣ Close all open positions if there is a position already open for this symbol
    if open_positions.get(symbol, False):
        print(f"Closing existing positions for {symbol} before new order.")
        try:
            close_payload = {
                "symbol": symbol,
                "side": "close"
            }
            response = requests.post(BITUNIX_CLOSE_POS_URL, json=close_payload, headers=headers, timeout=10)
            print(f"Closed positions response: {response.json()}")
            open_positions[symbol] = False
        except Exception as e:
            print(f"Error closing positions for {symbol}: {str(e)}")

    # 3️⃣ Initialize retry info
    retry_orders[symbol] = {"attempts": 0, "active": True}

    filled = False
    result = None  # 🔹 Added: initialize result so it's always defined

    while retry_orders[symbol]["active"] and retry_orders[symbol]["attempts"] < MAX_RETRIES:
        try:
            # Get last price
            ticker_resp = requests.get(f"{BITUNIX_TICKER_URL}?symbol={symbol}", headers=headers, timeout=10)
            ticker_data = ticker_resp.json()
            
            # 🔹 Added: check if 'lastPrice' exists
            if "lastPrice" not in ticker_data:
                raise ValueError(f"'lastPrice' not found in ticker response: {ticker_data}")
            
            last_price = float(ticker_data["lastPrice"])
            print(f"Attempt {retry_orders[symbol]['attempts'] + 1}: Last price for {symbol} is {last_price}")

            # Build order
            order_payload = {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT",
                "price": last_price,
                "quantity": quantity,
                "leverage": LEVERAGE,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "guaranteed_stop_loss": guaranteed_sl
            }

            # Send order
            response = requests.post(BITUNIX_ORDER_URL, json=order_payload, headers=headers, timeout=10)
            result = response.json()
            print(f"Order attempt {retry_orders[symbol]['attempts'] + 1}: {result}")

            # Check if filled
            if result.get("status") == "filled" or result.get("orderStatus") == "FILLED":
                filled = True
                retry_orders[symbol]["active"] = False
                open_positions[symbol] = True
                print("Order successfully filled ✅")
                return jsonify({"status": "success", "response": result})

            # Retry logic
            retry_orders[symbol]["attempts"] += 1
            time.sleep(RETRY_DELAY)

        except Exception as e:
            # 🔹 Added: always assign to result so UnboundLocalError doesn't occur
            result = str(e)
            print(f"Error on attempt {retry_orders[symbol]['attempts'] + 1} for {symbol}: {result}")
            retry_orders[symbol]["attempts"] += 1
            time.sleep(RETRY_DELAY)

    # 4️⃣ If max retries reached without fill
    retry_orders[symbol]["active"] = False
    print(f"Order for {symbol} failed after {MAX_RETRIES} retries ❌")
    return jsonify({"status": "failed", "response": result}), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
