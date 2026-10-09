"""Drop one actual Anvil broadcast response, then recover the same signed intent."""
import json
import sys
from installed_selection_acceptance import api,wait,ROOT
from vision_completion_acceptance import rpc

base=json.loads((ROOT/'artifacts/vision-completion-installed-2026-10-09.json').read_text(encoding='utf-8'))
run=base['run_id'];buyer='node-c';provider='node-a';token=base['payment_environment']['test_token']
direct='http://a2n-payment-testchain:8545';proxy='http://a2n-payment-testfacilitator:8546/rpc-loss'
def config(url):return {'native':[{'currency':'TETH','network':'eip155:31337','rpc_url':url,'confirmations':1,'allow_http':True}],
    'x402':{'allow_http':True,'assets':[{'currency':'TST','network':'eip155:31337','asset':token,'name':'A2N Test Token','version':'1','rpc_url':direct,'confirmations':1}]},
    'facilitator_url':'http://a2n-payment-testfacilitator:8546','allow_http':True}
out=ROOT/'artifacts/vision-completion-payment-recovery-2026-10-09.json'
report={'passed':False,'kind':'LOCAL_ANVIL_CONTROLLED_RESPONSE_LOSS'}
def main():
    status=api(buyer,'/v1/payment-coordination');assert status['test_environment']['kind']=='LOCAL_ANVIL'
    payer=status['wallet_address'];payee=api(provider,'/v1/payment-coordination')['wallet_address']
    before_nonce=int(rpc('eth_getTransactionCount',[payer,'latest']),16);before_balance=int(rpc('eth_getBalance',[payee,'latest']),16)
    try:
        api(buyer,'/v1/payment-coordination/configure',{'config':config(proxy)})
        quote=api(buyer,'/v1/payment-coordination/quote',{'projection_id':'vision-csv-profile-'+run,
            'request':{'task_id':'lost-response-'+run,'skill':'csv-profile','payload':{'csv':'a,amount\nx,2\ny,3'}}})
        index=next(i for i,r in enumerate(quote['options']) if r['currency']=='TETH')
        prepared=api(buyer,'/v1/payment-coordination/prepare',{'offer_id':quote['offer_id'],'option_index':index,'fee_cap_minor':'1000000000000000','command_id':'lost-response-'+run})
        pid=prepared['record']['plan_id'];initial=api(buyer,'/v1/payment-coordination/plans/'+pid+'/pay',{})
        assert initial['state']=='UNKNOWN',initial['state']
        recovered=wait(lambda:api(buyer,'/v1/payment-coordination/plans/'+pid+'/reconcile',{}),lambda r:r['state']=='CONFIRMED')
        first=api(buyer,'/v1/payment-coordination/plans/'+pid+'/execute',{})
        api(buyer,'/v1/payment-coordination/plans/'+pid+'/pay',{})
        second=api(buyer,'/v1/payment-coordination/plans/'+pid+'/execute',{})
        assert first['ok'] and first['metadata']['trade_uid']==second['metadata']['trade_uid']
        assert int(rpc('eth_getTransactionCount',[payer,'latest']),16)==before_nonce+1
        assert int(rpc('eth_getBalance',[payee,'latest']),16)==before_balance+12345
        report.update(passed=True,initial_state=initial['state'],recovered_state=recovered['state'],
                      plan_id=pid,transaction_count_increase=1,provider_amount_increase=12345,execution_replay_preserved=True)
        print('PASS actual broadcast accepted, response lost, same intent recovered, one payment and one delivery')
    finally:api(buyer,'/v1/payment-coordination/configure',{'config':config(direct)})
if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try:main()
    finally:out.write_text(json.dumps(report,indent=2),encoding='utf-8')
