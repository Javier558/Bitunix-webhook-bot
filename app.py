from flask import Flask, request, jsonify
import requests
import os

app = Flask(__name__)

# Bitunix API keys as environment variables
BITUNIX_API_KEY = os.getenv('BITUNIX_API_KEY')
BITUNIX_API_SECRET = os.getenv('BITUNIX_API_SECRET')
BITUNIX_BASE_URL = "https://api.bitunix.com"  # Replace with actual API URL

# Example function to place an order
def place_order(symbol, side, quantity):
    url = f"{BITUNIX_BASE_URL}/order"
    headers = {
        "X-API-KEY": BITUNIX_API_KEY,
        "X-API-SECRET": BITUNIX_API_SECRET,
        "Content-Type": "application/json"
    }
    payload = {
        "symbol": symbol,
        "side": side,  # "buy" or "sell"
        "quantity": quantity,
        "type": "market"
    }
    response = requests.post(url, json=payload, headers=headers)
    return response.json()

@app.route('/', methods=['POST'])
def webhook():
    data = request.json
    print("Received alert:", data)
    
    try:
        symbol = data['symbol']
        action = data['action']  # "buy" or "sell"
        quantity = data['quantity']
        
        result = place_order(symbol, action, quantity)
        print("Order result:", result)
        return jsonify({"status": "success", "result": result}), 200
    except Exception as e:
        print("Error:", e)
        return jsonify({"status": "error", "message": str(e)}), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
