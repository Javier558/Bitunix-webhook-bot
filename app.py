from flask import Flask, request
import os
import requests

app = Flask(__name__)

# -----------------------
# Bitunix API credentials (set as Render environment variables)
# -----------------------
BITUNIX_API_KEY = os.getenv("BITUNIX_API_KEY")
BITUNIX_API_SECRET = os.getenv("BITUNIX_API_SECRET")
BITUNIX_BASE_URL = "https://api.bitunix.com"  # replace with real Bitunix API URL if different

# Default leverage and quantity (can be overridden by webhook)
DEFAULT_LEVERAGE = 50
DEFAULT_QUANTITY = 0.01  # example, adjust as needed or calculate dynamically

# -----------------------
# Webhook endpoint
# -----------------------
@app.route('/', methods=['POST'])
def webhook():
    data = request.json

    # Extract data from TradingView alert
    symbol = data.get("symbol")
    side = data.get("side")  # "buy" or "sell"
    sl = data.get("sl")
    tp = data.get("tp")
    quantity = data.get("quantity", DEFAULT_QUANTITY)
    leverage = data.get("leverage", DEFAULT_LEVERAGE)

    if not symbol or not side:
        return {"error": "Missing required fields"}, 400

    # Prepare order payload
    payload = {
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "type": "market",  # you can change to "limit" if needed
        "leverage": leverage,
        "stop_loss": sl,
        "take_profit": tp
    }

    # Send order to Bitunix
    try:
        response = requests.post(
            f"{BITUNIX_BASE_URL}/order",
            json=payload,
            headers={
                "X-API-KEY": BITUNIX_API_KEY,
                "X-API-SECRET": BITUNIX_API_SECRET,
                "Content-Type": "application/json"
            }
        )
        result = response.json()
    except Exception as e:
        print("Error sending order:", e)
        return {"error": str(e)}, 500

    # Log info for debugging
    print("Received alert:", data)
    print("Order result:", result)

    return {"status": "success", "order_result": result}

# -----------------------
# Run Flask app
# -----------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
