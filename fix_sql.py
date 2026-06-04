import json
with open('backup.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

with open('restore.sql', 'w', encoding='utf-8') as f:
    f.write('-- Restore script for PostgreSQL\n')
    for table, rows in data.items():
        if rows:
            cols = list(rows[0].keys())
            for row in rows:
                vals = []
                for v in row.values():
                    if v is None:
                        vals.append('NULL')
                    elif isinstance(v, bool):
                        vals.append('TRUE' if v else 'FALSE')
                    elif isinstance(v, (str, bytes)):
                        vals.append("'" + str(v).replace("'", "''") + "'")
                    else:
                        vals.append(str(v))
                f.write(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(vals)});\n")
print('restore.sql recreated')