import os
import pandas as pd
import numpy as np

# Determine paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KLINES_DIR = os.path.join(BASE_DIR, "data_ingestion", "output", "klines")
WHALE_DIR = os.path.join(BASE_DIR, "data_ingestion", "output", "whale")

os.makedirs(WHALE_DIR, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]

def process_whale_metrics(symbol):
    print(f"Processing whale metrics for {symbol}...")
    kline_file = os.path.join(KLINES_DIR, f"{symbol}_daily.csv")
    
    if not os.path.exists(kline_file):
        print(f"Cannot process, {kline_file} does not exist. Please run fetch_binance_klines.py first.")
        return
        
    df = pd.read_csv(kline_file)
    df['number_of_trades'] = pd.to_numeric(df['number_of_trades'], errors='coerce')
    
    # 1. Average Trade Size (Proxy for Whale activity)
    # quote_asset_volume is total volume in USDT
    df['avg_trade_size_usdt'] = df['quote_asset_volume'] / df['number_of_trades']
    
    # Calculate 30-day rolling mean and standard deviation of avg trade size
    df['avg_trade_size_mean_30d'] = df['avg_trade_size_usdt'].rolling(window=30, min_periods=1).mean()
    df['avg_trade_size_std_30d'] = df['avg_trade_size_usdt'].rolling(window=30, min_periods=1).std()
    
    # Determine if today is a "Whale Day" (avg trade size is > 2 sigma above 30d mean)
    df['is_whale_activity'] = df['avg_trade_size_usdt'] > (df['avg_trade_size_mean_30d'] + 2 * df['avg_trade_size_std_30d'])
    
    # 2. Taker Buy Ratio (Whale Buying vs Selling pressure)
    df['taker_buy_ratio'] = df['taker_buy_base_asset_volume'] / df['volume']
    
    # Formulate a simplified whale metric dataset
    whale_df = df[['date', 'avg_trade_size_usdt', 'is_whale_activity', 'taker_buy_ratio']].copy()
    
    # Map the metric into an easily understandable english description for the LLM
    def build_whale_summary(row):
        summary = []
        if pd.notna(row['is_whale_activity']) and row['is_whale_activity']:
            summary.append("Exceptionally high average trade sizes indicating massive whale activity.")
        else:
            summary.append("Normal average trade sizes, standard wholesale and retail mix.")
            
        if pd.notna(row['taker_buy_ratio']):
            if row['taker_buy_ratio'] > 0.55:
                summary.append("Whales heavily accumulating (Taker buys dominate).")
            elif row['taker_buy_ratio'] < 0.45:
                summary.append("Whales heavily distributing/selling (Taker sells dominate).")
            else:
                summary.append("Whale buying and selling pressure is balanced.")
                
        return " ".join(summary)

    whale_df['whale_summary'] = whale_df.apply(build_whale_summary, axis=1)
    
    output_file = os.path.join(WHALE_DIR, f"{symbol}_whale_metrics.csv")
    whale_df.to_csv(output_file, index=False)
    print(f"Saved {symbol} whale metrics to {output_file}")

if __name__ == "__main__":
    for symbol in SYMBOLS:
        process_whale_metrics(symbol)
