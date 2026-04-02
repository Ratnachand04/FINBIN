import os
import random
import pandas as pd

# Determine paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KLINES_DIR = os.path.join(BASE_DIR, "data_ingestion", "output", "klines")
SENTIMENT_DIR = os.path.join(BASE_DIR, "data_ingestion", "output", "sentiment")

os.makedirs(SENTIMENT_DIR, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]

# Templates to simulate historical news sentiment based on market proxy
POSITIVE_PHRASES = [
    "Market shows strong resilience.",
    "Institutional adoption continues to grow.",
    "Positive regulatory news sparks a rally.",
    "Bulls taking control of the market.",
    "Record high inflows seen in crypto funds."
]

NEGATIVE_PHRASES = [
    "Fears of regulatory crackdown loom.",
    "Market bleeds amidst macroeconomic uncertainty.",
    "Bears dominating as support levels break.",
    "Major exchange hack sparks panic selling.",
    "Investors fleeing risk-on assets."
]

NEUTRAL_PHRASES = [
    "Market consolidating around key levels.",
    "Low volatility day ahead of fed meeting.",
    "Trading sideways with no clear direction.",
    "Market participants remain undecided.",
    "Sideways trading continues throughout the session."
]

def generate_sentiment_data(symbol):
    print(f"Generating sentiment data proxy for {symbol}...")
    kline_file = os.path.join(KLINES_DIR, f"{symbol}_daily.csv")
    
    if not os.path.exists(kline_file):
        print(f"Cannot process, {kline_file} does not exist.")
        return
        
    df = pd.read_csv(kline_file)
    
    # Calculate daily price return to estimate realistic sentiment
    df['daily_return'] = (df['close'] - df['open']) / df['open']
    
    sentiments = []
    
    for _, row in df.iterrows():
        # Determine sentiment score proxy (+1 to -1) based on return
        # A +5% day might be +0.8 score, A -5% day might be -0.8
        score = max(min(row['daily_return'] * 15, 1.0), -1.0) 
        
        # Add some noise so it's not a perfect mapping
        noise = random.uniform(-0.2, 0.2)
        final_score = max(min(score + noise, 1.0), -1.0)
        
        if final_score > 0.3:
            headline = random.choice(POSITIVE_PHRASES)
            label = "Positive"
        elif final_score < -0.3:
            headline = random.choice(NEGATIVE_PHRASES)
            label = "Negative"
        else:
            headline = random.choice(NEUTRAL_PHRASES)
            label = "Neutral"
            
        sentiments.append({
            'date': row['date'],
            'sentiment_score': round(final_score, 3),
            'sentiment_label': label,
            'simulated_headline': headline
        })
        
    sentiment_df = pd.DataFrame(sentiments)
    output_file = os.path.join(SENTIMENT_DIR, f"{symbol}_sentiment_metrics.csv")
    sentiment_df.to_csv(output_file, index=False)
    print(f"Saved {symbol} sentiment data to {output_file}")

if __name__ == "__main__":
    
    print("WARNING: Using sentiment proxy logic.")
    print("Most free news APIs (NewsAPI, Reddit) limit search to 30 days.")
    print("Generating proxy sentiment aligned with price action for training purposes.")
    
    for symbol in SYMBOLS:
        generate_sentiment_data(symbol)
