"""Move cold metadata to encrypted history without erasing audit records."""
from __future__ import annotations
import hashlib
import json

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()).hexdigest()

def get_record(store, namespace, key):
    row = store.get(namespace, key)
    return row if row is not None else store.get(namespace+"_archive", key)

def page_records(store,namespace,query):
    limit=int(query.get('limit',100));offset=int(query.get('offset',0))
    if not 1<=limit<=100 or not 0<=offset<=1000000:raise ValueError('INVALID_HISTORY_PAGE')
    include=str(query.get('archived','false')).lower() in {'true','1'}
    with store.tx():
        rows=store.items(namespace+'_archive') if include else {}
        rows.update(store.items(namespace))
        ordered=sorted(rows.items(),key=lambda item:(item[1].get('created_at',0),item[0]),reverse=True)
    keys=[key for key,row in ordered[offset:offset+limit]]
    return {'keys':keys,'total':len(ordered),'include_archived':include,
            'next_offset':offset+limit if offset+limit<len(ordered) else None}

def archive(store, namespace, *, keep, eligible=lambda row:True):
    with store.tx():
        rows=store.items(namespace)
        removable=sorted(((k,r) for k,r in rows.items() if eligible(r)),
                         key=lambda item:(item[1].get("created_at",0),item[0]))
        moved=[]
        for key,row in removable[:max(0,len(rows)-keep)]:
            old=store.get(namespace+"_archive",key)
            if old is not None and digest(old)!=digest(row): raise ValueError("ARCHIVE_HISTORY_CONFLICT")
            store.put(namespace+"_archive",key,row)
            store.delete(namespace,key); moved.append(key)
        return {"moved":len(moved),"retained":len(rows)-len(moved)}
