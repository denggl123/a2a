"""Sanitized final installed state; only close this run's controlled test disputes."""
from datetime import datetime,timezone
from pathlib import Path
import hashlib
import json
import sys
from urllib.parse import urlencode

from installed_selection_acceptance import api,wait,NODES,ROOT
from project_document_agent_usage import CONTEXT

def close_controlled_refunds(run):
    closed=[]
    for buyer,currency in [('desktop','TETH'),('node-b','TST')]:
        runtime=api(buyer,'/v1/runtime')
        rows=[r for r in runtime['disputes'] if r.get('scope')=='vision-csv-profile-'+run
              and r.get('reason')=='工程验收：验证自愿退款协商，不表示服务质量投诉']
        assert len(rows)==1
        dispute=rows[0];did=dispute['id']
        if dispute['state']!='CLOSED_BILATERAL':
            proposal=api(buyer,'/v1/disputes/'+did+'/messages',{'kind':'PROPOSAL',
                'body':{'action':'CLOSE','text':'受控付款与退款验收已完成，双方关闭测试异议','amount_minor':0,'currency':''},
                'command_id':'completion-close-'+did})
            mid=proposal['message_id']
            api(buyer,'/v1/disputes/'+did+'/agreements',{'proposal_id':mid,'command_id':'completion-close-buyer-'+did})
            def accept():
                try:return api('node-a','/v1/disputes/'+did+'/agreements',{'proposal_id':mid,'command_id':'completion-close-seller-'+did})
                except RuntimeError:return None
            wait(accept,lambda r:r is not None)
            for node in (buyer,'node-a'):
                wait(lambda:api(node,'/v1/runtime')['disputes'],lambda rows:any(r['id']==did and r['state']=='CLOSED_BILATERAL' for r in rows))
        closed.append({'currency':currency,'state':'CLOSED_BILATERAL','dispute_id':did})
    return closed

def main():
    installed=json.loads((ROOT/'artifacts/vision-completion-installed-2026-10-09.json').read_text(encoding='utf-8'))
    closures=close_controlled_refunds(installed['run_id'])
    report={'at':datetime.now(timezone.utc).isoformat(),'passed':False,'nodes':[],
            'scope':'INSTALLED_LOCAL_BUSINESS_EXCLUDES_EXTERNAL_NETWORK',
            'closed_controlled_refund_disputes':closures}
    for node in NODES:
        r=api(node,'/v1/runtime');pay=api(node,'/v1/payment-coordination')
        report['nodes'].append({'node':node,'node_did':r['node_did'],
            'policy_mode':r['experience']['policy']['values']['mode'],
            'payment_environment':pay.get('test_environment',{}).get('kind'),
            'wallet_configured':bool(pay.get('wallet_address')),
            'product_runtime':{k:r['product_runtime'].get(k) for k in ('mode','executable_sha256')},
            'installed_workflows':len(r['workflows']),
            'public_discovery':r['public_service']['services']['discovery'],
            'backup_enabled':r['maintenance']['policy']['enabled'],
            'asset_limits':r['maintenance']['asset_limits'],
            'task_quality_enabled':r['task_quality']['enabled'],
            'selection_enabled':r['selection']['enabled']})
    status=api('desktop','/v1/calibration/status?'+urlencode(CONTEXT))
    report['task_quality']={'qualified_records':status['consented_production_reviews'],
        'providers':status['qualified_providers'],'days':status['qualified_days'],
        'label_sources':status['label_sources'],'active_state':status['active']['state']}
    report['desktop_sha256']=hashlib.sha256((ROOT/'artifacts/A2N.exe').read_bytes()).hexdigest()
    assert all(n['public_discovery'] and n['wallet_configured'] and n['payment_environment']=='LOCAL_ANVIL'
               and n['selection_enabled'] and n['task_quality_enabled'] for n in report['nodes'])
    assert report['nodes'][0]['product_runtime']['executable_sha256']==report['desktop_sha256']
    assert report['task_quality']['qualified_records']==42 and report['task_quality']['days']==1
    report['passed']=True
    (ROOT/'artifacts/vision-completion-final-state-2026-10-09.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS four installed nodes, unchanged genuine calibration dates, and bilateral controlled-dispute closure')

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8');main()
