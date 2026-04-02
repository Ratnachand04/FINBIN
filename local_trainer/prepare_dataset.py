import os
import json
import pandas as pd

# Determine paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KLINES_DIR = os.path.join(BASE_DIR, "data_ingestion", "output", "klines")
WHALE_DIR = os.path.join(BASE_DIR, "data_ingestion", "output", "whale")
SENTIMENT_DIR = os.path.join(BASE_DIR, "data_ingestion", "output", "sentiment")
OUTPUT_FILE = os.path.join(BASE_DIR, "local_trainer", "finance_crypto_sft.jsonl")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]

def prepare_dataset():
    print(f"Preparing dataset to {OUTPUT_FILE}...")
    
    all_data = []
    
    for symbol in SYMBOLS:
        kline_file = os.path.join(KLINES_DIR, f"{symbol}_daily.csv")
        whale_file = os.path.join(WHALE_DIR, f"{symbol}_whale_metrics.csv")
        sentiment_file = os.path.join(SENTIMENT_DIR, f"{symbol}_sentiment_metrics.csv")
        
        if not (os.path.exists(kline_file) and os.path.exists(whale_file) and os.path.exists(sentiment_file)):
            print(f"Skipping {symbol}, missing one of the data files.")
            continue
            
        kdf = pd.read_csv(kline_file)
        wdf = pd.read_csv(whale_file)
        sdf = pd.read_csv(sentiment_file)
        
        # Merge on date
        df = pd.merge(kdf, wdf, on='date')
        df = pd.merge(df, sdf, on='date')
        
        # We want to predict tomorrow's closing price or next few days' trends
        # Shift close price by -1 to get tomorrow's close
        df['next_day_close'] = df['close'].shift(-1)
        
        # Drop last row since it doesn't have a next day close
        df = df.dropna()
        
        for _, row in df.iterrows():
            coin = symbol.replace('USDT', '')
            
            # Formulate the instruction
            instruction = (
                f"You are a crypto financial analyst. The date is {row['date']}. "
                f"{coin} opened at ${row['open']:.2f} and closed at ${row['close']:.2f}. "
                f"Market volume was {row['volume']}. "
                f"Whale Activity Update: {row.get('whale_summary', 'Normal')}. "
                f"Sentiment Index: {row['sentiment_label']} (Score: {row['sentiment_score']}). "
                f"News Headline: '{row['simulated_headline']}'. "
                f"Given the above data, predict the immediate price movement trend for the next 24 hours."
            )
            
            # Determine the actual outcome to form the target response
            price_change = ((row['next_day_close'] - row['close']) / row['close']) * 100
            
            if price_change > 1.0:
                trend = "Bullish"
            elif price_change < -1.0:
                trend = "Bearish"
            else:
                trend = "Neutral"
                
            response = (
                f"{trend}. Over the subsequent 24 hours, the price moved from "
                f"${row['close']:.2f} to ${row['next_day_close']:.2f}, "
                f"representing a {price_change:.2f}% change."
            )
            
            # We wrap the text in a format expected by basic fine-tuning
            # i.e., "text" : "Instruction: [inst] Response: [resp]" or direct if customized
            text = f"Instruction: {instruction}\nResponse: {response}"
            
            all_data.append({"text": text})

    # Save to JSONL
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        for item in all_data:
            f.write(json.dumps(item) + "\n")
            
    print(f"Generated {len(all_data)} training samples in {OUTPUT_FILE}.")

if __name__ == "__main__":
    prepare_dataset()
