import sqlite3
import json

conn = sqlite3.connect('backend/.local-data/cvea.db')
cur = conn.cursor()

# 1. Clear any stuck jobs in jobs table
cur.execute("SELECT object_path, data FROM objects WHERE object_path LIKE '%jobs%' AND deleted = 0")
for path, data_bytes in cur.fetchall():
    j = json.loads(data_bytes.decode())
    if j.get('status') in ['queued', 'working']:
        print(f"Failing stuck job {j.get('job_id')} (status={j.get('status')}, stage={j.get('stage')})")
        j['status'] = 'failed'
        j['stage'] = 'failed'
        j['failure_code'] = 'RESET_FOR_RETRY'
        j['failure_message'] = 'Previous run was interrupted or encountered invalid model name. You can now run Prepare Draft again.'
        cur.execute("UPDATE objects SET data = ?, generation = generation + 1 WHERE object_path = ?", (json.dumps(j).encode(), path))

# 2. Clear active_job_id on any case still in analysing
cur.execute("SELECT object_path, data FROM objects WHERE object_path LIKE '%state.json' AND deleted = 0")
for path, data_bytes in cur.fetchall():
    c = json.loads(data_bytes.decode())
    if c.get('stage') == 'analysing' or c.get('active_job_id'):
        print(f"Resetting case {c.get('case_id')} ({c.get('title')}) from stage {c.get('stage')} (active_job={c.get('active_job_id')})")
        c['active_job_id'] = None
        if len(c.get('events', [])) > 0:
            c['stage'] = 'needs_review'
        else:
            c['stage'] = 'evidence_collection'
        cur.execute("UPDATE objects SET data = ?, generation = generation + 1 WHERE object_path = ?", (json.dumps(c).encode(), path))

conn.commit()
conn.close()
print("All stuck jobs and cases have been cleanly reset!")
