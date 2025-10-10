from flask import Flask, request, jsonify
import requests
import os
import time
from threading import Lock

app = Flask(__name__)

# API keys (stored securely in Render environment)
BITUNIX_API_KEY = os.getenv("BITUNIX_API_KEY")
BITUNIX_API_SECRET = os.getenv("BITUNIX_API_SECRET")

# Thread lock to prevent race conditions
lock = Lock()

# Keep track of current active order
active_order = {"retrying": False}

BITUNIX_API_URL = "https://fapi.bitunix.com/api/v1/futures/trade/place_order"

HEADERS = {
    "X-BX-APIKEY": BITUNIX_API_KEY,
    "Content-Type": "application/json"
}


def get_last_price(symbol):
    """Fetch last traded price from Bitunix"""
    ticker_url = f"https://openapi.bitunix.com/api/v1/market/ticker?symbol={symbol}"
    try:
        resp = requests.get(ticker_url)
        resp.raise_for_status()
        data = resp.json()
        return float(data["data"]["last"])
    except Exception as e:
        print(f"Error fetching last price: {e}")
        return None


def place_limit_order(symbol, side, quantity, price):
    """Send limit order to Bitunix"""
    payload = {
        "symbol": symbol,
        "side": side,
        "type": "limit",
        "price": price,
        "quantity": quantity,
        "timeInForce": "GTC"
    }
    response = requests.post(BITUNIX_API_URL, headers=HEADERS, json=payload)
    print("Order response:", response.text)
    return response


def close_all_positions(symbol):
    """Close any open positions before new entry"""
    print("Closing all open positions...")
    try:
        close_payload = {
            "symbol": symbol,
            "side": "close",
            "type": "market",
            "quantity": "100%"  # This closes full position
        }
        response = requests.post(BITUNIX_API_URL, headers=HEADERS, json=close_payload)
        print("Close response:", response.text)
        return response
    except Exception as e:
        print("Error closing positions:", e)
        return None


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    print("Received alert payload:", data)

    symbol = data.get("symbol")
    side = data.get("side")
    quantity = data.get("quantity", 1)  # default
    sl = data.get("sl")
    tp = data.get("tp")

    # Skip if retrying
    with lock:
        if active_order["retrying"]:
            print("An order is retrying. Skipping new one.")
            return jsonify({"status": "skipped"}), 200
        active_order["retrying"] = True

    try:
        # Close open positions before opening new one
        close_all_positions(symbol)

        # Fetch last price
        last_price = get_last_price(symbol)
        if not last_price:
            print("Could not fetch last price.")
            return jsonify({"error": "Failed to fetch price"}), 400

        # Retry placing the order up to 5 times
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            print(f"Attempt {attempt}: Placing limit order at {last_price}")
            response = place_limit_order(symbol, side, quantity, last_price)
            if response.status_code == 200:
                print("Order placed successfully.")
                break
            else:
                print("Order failed. Retrying...")
                time.sleep(2)
        else:
            print("Max retries reached. Cancelling order.")
            return jsonify({"status": "failed"}), 400

        # Partial position closing (your section preserved)
        print("Partial position closing... (if applicable)")

        return jsonify({"status": "success"}), 200

    finally:
        with lock:
            active_order["retrying"] = False


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
