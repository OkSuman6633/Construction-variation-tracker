import json
from pathlib import Path
import sys
sys.path.insert(0, 'backend')

from app.parsing.parsers import parse_document
from app.parsing.sniff import sniff_mime

dirs = [Path(".local-data/objects"), Path("backend/.local-data/objects")]
for base in dirs:
    if not base.exists():
        continue
    for state_file in base.glob("cases/*/*/state.json"):
        case = json.loads(state_file.read_text(encoding="utf-8"))
        updated = False
        tenant_id = case.get('tenant_id')
        case_id = case.get('case_id')
        
        for doc in case.get('evidence', []):
            if doc.get('parser_status') == 'quarantined':
                doc_id = doc.get('document_id')
                upload_file = base / f"uploads/{tenant_id}/{case_id}/{doc_id}"
                if upload_file.exists():
                    raw_bytes = upload_file.read_bytes()
                    mime = sniff_mime(raw_bytes) or doc.get('mime_type')
                    try:
                        parsed = parse_document(doc_id, mime, raw_bytes)
                        doc['parser_status'] = 'parsed'
                        doc['page_count'] = len(parsed.pages)
                        doc['mime_type'] = mime
                        updated = True
                        print(f"File fixed: {state_file} - {doc.get('filename')}")
                    except Exception as e:
                        print(f"Err parsing {upload_file}: {e}")
        
        active_job = case.get('active_job_id')
        if active_job:
            job_file = base / f"jobs/{tenant_id}/{active_job}.json"
            if job_file.exists():
                jdata = json.loads(job_file.read_text(encoding="utf-8"))
                if jdata.get('status') in ['queued', 'working']:
                    jdata['status'] = 'failed'
                    jdata['stage'] = 'failed'
                    jdata['failure_code'] = 'SERVER_RESTARTED'
                    jdata['failure_message'] = 'Previous job was interrupted. You can now run Prepare Draft again.'
                    job_file.write_text(json.dumps(jdata), encoding="utf-8")
                    case['active_job_id'] = None
                    if len(case.get('events', [])) > 0:
                        case['stage'] = 'needs_review'
                    else:
                        case['stage'] = 'evidence_collection'
                    updated = True
                    print(f"File cleared stuck job: {job_file}")
        
        if updated:
            state_file.write_text(json.dumps(case), encoding="utf-8")
            print(f"File updated state: {state_file}")

print("Done filesystem sync!")
