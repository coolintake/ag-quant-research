import sqlite3
import pandas as pd
from config import DB_SUMMARY

def view_data():
    con = sqlite3.connect(DB_SUMMARY)
    # Query all data from the summary table
    df = pd.read_sql("SELECT * FROM wheat_summary", con)
    con.close()
    
    # Print the full table to terminal
    print("--- Global Wheat Summary ---")
    print(df.to_string())

if __name__ == "__main__":
    view_data()