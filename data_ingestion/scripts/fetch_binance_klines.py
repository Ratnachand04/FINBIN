import os
import time
import pandas as pd
from binance.client import Client
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_SECRET")

# Initialize Binance Client (works without keys for public data, but good to have if provided)
client = Client(API_KEY, API_SECRET) if API_KEY and API_SECRET else Client()

SYMBOLS = ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]
INTERVAL = Client.KLINE_INTERVAL_1DAY
START_DATE = "1 Jan, 2017"

# Determine path relative to project root
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT_DIR = os.path.join(BASE_DIR, "data_ingestion", "output", "klines")

os.makedirs(OUTPUT_DIR, exist_ok=True)

def fetch_klines(symbol):
    print(f"Fetching {symbol} daily klines from {START_DATE}...")
    try:
        klines = client.get_historical_klines(symbol, INTERVAL, START_DATE)
        
        if not klines:
            print(f"No data returned for {symbol}.")
            return
        
        # Format the data
        df = pd.DataFrame(klines, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        
        # Convert timestamp to human-readable date
        df['date'] = pd.to_datetime(df['open_time'], unit='ms').dt.strftime('%Y-%m-%d')
        
        # Convert string columns to float
        numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'quote_asset_volume', 'taker_buy_base_asset_volume']
        for col in numeric_cols:
            df[col] = df[col].astype(float)
            
        # Keep essential columns that will also help with whale proxy
        df = df[['date', 'open', 'high', 'low', 'close', 'volume', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume']]
        
        # Save to CSV
        output_file = os.path.join(OUTPUT_DIR, f"{symbol}_daily.csv")
        df.to_csv(output_file, index=False)
        print(f"Saved {symbol} data to {output_file} ({len(df)} rows)")
    except Exception as e:
        print(f"Error fetching data for {symbol}: {e}")

if __name__ == "__main__":
    for symbol in SYMBOLS:
        fetch_klines(symbol)
        time.sleep(1) # Be polite to the API
