from config.styles import ANNUAL_METRIC_SEQUENCE, MONTHLY_METRIC_SEQUENCE, METRIC_ORDER

print('ANNUAL_METRIC_SEQUENCE:')
for i, m in enumerate(ANNUAL_METRIC_SEQUENCE):
    if m['tier1_metric']:
        print(f'  {i}: Block {m["block"]}: {m["tier1_metric"]} ({m["unit"]}) group={m.get("group", "")} calc={m.get("is_calculated", False)}')
    else:
        print(f'  {i}: SPACER')

print()
print('MONTHLY_METRIC_SEQUENCE length:', len(MONTHLY_METRIC_SEQUENCE))
print('METRIC_ORDER keys:', list(METRIC_ORDER.keys()))
for group, metrics in METRIC_ORDER.items():
    print(f'  {group}: {len(metrics)} metrics')
    for m in metrics:
        print(f'    Block {m["block"]}: {m["tier1_metric"]} ({m["unit"]})')