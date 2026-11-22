import sqlite3
import json
import sys
sys.path.insert(0, 'backend')

from app.parsing.parsers import parse_document
from app.parsing.sniff import sniff_mime

conn = sqlite3.connect('backend/.local-data/cvea.db')
cur = conn.cursor()

# 1. Update quarantined documents in cases
cur.execute("SELECT object_path, data, generation FROM objects WHERE object_path LIKE '%state.json' AND deleted = 0")
case_rows = cur.fetchall()
print("Found cases to check:", len(case_rows))

for path, data_bytes, gen in case_rows:
    case = json.loads(data_bytes.decode())
    updated = False
    
    # Check evidence
    for doc in case.get('evidence', []):
        if doc.get('parser_status') == 'quarantined':
            doc_id = doc.get('document_id')
            tenant_id = doc.get('tenant_id')
            case_id = doc.get('case_id')
            upload_path = f"uploads/{tenant_id}/{case_id}/{doc_id}"
            
            cur.execute("SELECT data FROM objects WHERE object_path = ?", (upload_path,))
            upload_row = cur.fetchone()
            if upload_row:
                raw_bytes = upload_row[0]
                mime = sniff_mime(raw_bytes) or doc.get('mime_type')
                try:
                    parsed = parse_document(doc_id, mime, raw_bytes)
                    doc['parser_status'] = 'parsed'
                    doc['page_count'] = len(parsed.pages)
                    doc['mime_type'] = mime
                    updated = True
                    print(f"Fixed quarantined doc: {doc.get('filename')} in case {case_id} -> parsed ({len(parsed.pages)} pages)")
                except Exception as e:
                    print(f"Could not parse doc: {doc.get('filename')}: {e}")

    # Check stuck jobs
    active_job = case.get('active_job_id')
    if active_job:
        job_path = f"jobs/{case.get('tenant_id')}/{active_job}"
        cur.execute("SELECT data, generation FROM objects WHERE object_path = ?", (job_path,))
        jrow = cur.fetchone()
        if jrow:
            jdata = json.loads(jrow[0].decode())
            if jdata.get('status') in ['queued', 'working']:
                print(f"Clearing stuck job {active_job} (status={jdata.get('status')}) for case {case.get('case_id')}")
                jdata['status'] = 'failed'
                jdata['stage'] = 'failed'
                jdata['failure_code'] = 'SERVER_RESTARTED'
                jdata['failure_message'] = 'Previous job was interrupted. You can now run Prepare Draft again.'
                cur.execute("UPDATE objects SET data = ?, generation = generation + 1 WHERE object_path = ?", (json.dumps(jdata).encode(), job_path))
                
                case['active_job_id'] = None
                if len(case.get('events', [])) > 0:
                    case['stage'] = 'needs_review'
                else:
                    case['stage'] = 'evidence_collection'
                updated = True

    if updated:
        cur.execute("UPDATE objects SET data = ?, generation = generation + 1 WHERE object_path = ?", (json.dumps(case).encode(), path))
        print(f"Updated case state: {path}")

conn.commit()
conn.close()
print("Done fixing database state!")
