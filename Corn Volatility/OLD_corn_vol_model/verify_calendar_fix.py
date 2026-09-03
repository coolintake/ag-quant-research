import pandas as pd
import numpy as np
from datetime import datetime

class VolatilityEngine:
    @staticmethod
    def check_calendar_arbitrage(df):
        violations = []
        df = df.copy()
        df['moneyness_bucket'] = (df['moneyness_kf'] * 20).round() / 20
        
        for bucket, group in df.groupby('moneyness_bucket'):
            group = group.sort_values('T')
            if len(group) < 2:
                continue
            
            prev_total_var = -1
            for _, row in group.iterrows():
                total_var = (row['iv']**2) * row['T']
                if total_var < prev_total_var - 1e-6:
                    violations.append(
                        f"Calendar Violation at {row['expiry']} "
                        f"(Moneyness {bucket:.2f}, Strike {row['strike']:.0f})"
                    )
                prev_total_var = total_var
        return violations

# Test 1
df_mismatch = pd.DataFrame({
    'expiry': ['Mar26', 'Jul26'],
    'strike': [455, 455],
    'und_price': [440, 465],
    'iv': [0.20, 0.15],
    'T': [0.2, 0.5],
    'moneyness_kf': [455/440, 455/465]
})

# Test 2
df_real_v = pd.DataFrame({
    'expiry': ['Mar26', 'Jul26'],
    'strike': [440, 460],
    'und_price': [440, 460],
    'iv': [0.20, 0.10],
    'T': [0.2, 0.6],
    'moneyness_kf': [440/440, 460/460]
})

print("RESULTS_START")
v1 = VolatilityEngine.check_calendar_arbitrage(df_mismatch)
print(f"Test1_Violations_{len(v1)}")
v2 = VolatilityEngine.check_calendar_arbitrage(df_real_v)
print(f"Test2_Violations_{len(v2)}")
for v in v2:
    print(f"Detail_{v}")
print("RESULTS_END")
