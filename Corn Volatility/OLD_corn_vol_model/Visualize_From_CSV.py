import pandas as pd
from Async_VolSurface import visualize_surface

def main():
    csv_path = r"C:\Users\ahmed\OneDrive\Desktop\Python\Volatility_Surface_CZ\corn_options_surface_data.csv"
    
    print(f"Loading data from {csv_path}...")
    try:
        df = pd.read_csv(csv_path)
        print(f"Loaded {len(df)} rows.")
        
        # Compatibility handling:
        # The new visualize_surface expects 'underlying_price' column in the dataframe.
        # If loading an old CSV, we must add it.
        if 'underlying_price' not in df.columns:
            assumed_price = 438.75
            print(f"Warning: 'underlying_price' column missing. using assumed price: {assumed_price}")
            df['underlying_price'] = assumed_price
        else:
             print("Using underlying prices from CSV.")

        visualize_surface(df)
        
    except FileNotFoundError:
        print("Error: CSV file not found. Please run Async_VolSurface.py first to collect data.")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
