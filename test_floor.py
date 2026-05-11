import sys, urllib.request, json
sys.stdout.reconfigure(encoding='utf-8')

companies = ["한일건설", "미래인테리어"]
for company in companies:
    import urllib.parse
    url = f"http://127.0.0.1:8123/api/verify/stream?company={urllib.parse.quote(company)}"
    collected = []
    with urllib.request.urlopen(url, timeout=20) as r:
        for raw in r:
            line = raw.decode('utf-8').strip()
            if line.startswith('data:'):
                try:
                    d = json.loads(line[5:])
                    collected.append(d)
                except Exception:
                    pass
    # complete event is last data line with 'company' key
    complete = next((d for d in reversed(collected) if 'company' in d), None)
    if complete:
        ai = complete.get('ai', {})
        print(f"\n=== {company} ===")
        print(f"  score: {ai.get('score')}")
        print(f"  decision: {ai.get('decision')}")
        print(f"  base_rate_applied: {ai.get('base_rate_applied')}")
        print(f"  inferred_industry: {ai.get('inferred_industry')}")
        print(f"  reasons: {ai.get('reasons')}")
