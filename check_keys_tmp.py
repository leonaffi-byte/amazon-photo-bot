import sqlite3
db = sqlite3.connect('/app/data/bot_data.db')
tables = [t[0] for t in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("Tables:", tables)
for t in tables:
    if any(x in t.lower() for x in ['key', 'setting', 'config']):
        cols = [d[0] for d in db.execute("PRAGMA table_info(" + t + ")").fetchall()]
        print("-- " + t + " cols: " + str(cols))
        for r in db.execute("SELECT * FROM " + t + " LIMIT 20").fetchall():
            row = list(r)
            # mask long values
            for i, v in enumerate(row):
                if isinstance(v, str) and len(v) > 14:
                    row[i] = v[:10] + "..."
            print(row)
        print()
