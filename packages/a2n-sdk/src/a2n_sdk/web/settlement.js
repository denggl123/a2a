/* Owner policy editor. Decimal amounts stay strings; list order is authoritative. */
(() => {
  'use strict';
  const methods=[['a2n-points/1',I18N.t('settlement.method.points')],['x402/2',I18N.t('settlement.method.x402')],['evm-native/1',I18N.t('settlement.method.native')]];
  window.A2NSettlementUI={mount(host){
    const groups={};
    function group(key,title,buyer){
      const box=document.createElement('section'),heading=document.createElement('h4');heading.textContent=title;box.append(heading);
      const label=document.createElement('label'),legacy=document.createElement('input');legacy.type='checkbox';label.append(legacy,document.createTextNode(I18N.t(buyer?'settlement.legacy_buyer':'settlement.legacy_seller')));box.append(label);
      const fields=document.createElement('fieldset'),list=document.createElement('div'),add=document.createElement('button');add.type='button';add.textContent=I18N.t('settlement.add');fields.append(list,add);box.append(fields);host.append(box);
      let rows=[];
      function input(parent,title,value){const label=document.createElement('label'),field=document.createElement('input');label.append(document.createTextNode(title),field);field.value=value??'';parent.append(label);return field}
      function select(parent,title,options,value){const label=document.createElement('label'),field=document.createElement('select');for(const [v,name] of options)field.add(new Option(name,v));field.value=value;label.append(document.createTextNode(title),field);parent.append(label);return field}
      function readRow(row){const {base,fields:f}=row,out={...base,method:f.method.value};for(const key of ['currency','network','asset','mode','max_amount_minor','fee_cap_minor','funding_issuer','max_cost'])if(f[key]){const value=f[key].value.trim();if(value)out[key]=value;else delete out[key]}if(out.method!=='a2n-points/1'){delete out.mode;delete out.funding_issuer;delete out.max_cost}if(out.method!=='evm-native/1')delete out.fee_cap_minor;return out}
      function draw(values){list.replaceChildren();rows=[];for(const [index,base] of values.entries()){
        const row=document.createElement('article');row.className='agent';const number=document.createElement('b');number.textContent=I18N.t(buyer?'settlement.order_prefix':'settlement.method_prefix')+(index+1);row.append(number);const f={};
        f.method=select(row,I18N.t('settlement.method_label'),methods,base.method);f.mode=select(row,I18N.t('settlement.points_mode'),buyer?[['EARN',I18N.t('settlement.points_mode.EARN')],['DEBT',I18N.t('settlement.points_mode.DEBT')],['PAY',I18N.t('settlement.points_mode.PAY')]]:[['',I18N.t('settlement.points_mode.all')],['EARN',I18N.t('settlement.points_mode.EARN')],['DEBT',I18N.t('settlement.points_mode.DEBT')],['PAY',I18N.t('settlement.points_mode.PAY')]],base.mode??(buyer?'EARN':''));
        f.currency=input(row,I18N.t('settlement.currency'),base.currency);f.network=input(row,I18N.t('settlement.network'),base.network);f.asset=input(row,I18N.t('settlement.asset'),base.asset);
        if(buyer){f.max_amount_minor=input(row,I18N.t('settlement.max_amount'),base.max_amount_minor);f.fee_cap_minor=input(row,I18N.t('settlement.fee_cap'),base.fee_cap_minor??'0');f.funding_issuer=input(row,I18N.t('settlement.funding_issuer'),base.funding_issuer);f.max_cost=input(row,I18N.t('settlement.max_cost'),base.max_cost)}
        function visibility(){f.mode.parentElement.hidden=f.method.value!=='a2n-points/1';if(buyer){f.fee_cap_minor.parentElement.hidden=f.method.value!=='evm-native/1';for(const k of ['funding_issuer','max_cost'])f[k].parentElement.hidden=f.method.value!=='a2n-points/1'||f.mode.value!=='PAY'}}f.method.onchange=visibility;f.mode.onchange=visibility;visibility();
        for(const [text,delta] of [[I18N.t('settlement.move_up'),-1],[I18N.t('settlement.move_down'),1],[I18N.t('settlement.remove'),0]]){const b=document.createElement('button');b.type='button';b.className='ghost';b.textContent=text;b.disabled=delta<0&&index===0||delta>0&&index===values.length-1;b.onclick=()=>{const current=rows.map(readRow);if(delta)[current[index],current[index+delta]]=[current[index+delta],current[index]];else current.splice(index,1);draw(current)};row.append(b)}
        rows.push({base,fields:f});list.append(row);
      }}
      legacy.onchange=()=>{fields.disabled=legacy.checked};add.onclick=()=>draw([...rows.map(readRow),{method:'a2n-points/1',...(buyer?{mode:'EARN',max_amount_minor:'0'}:{})}]);
      groups[key]={set(value){legacy.checked=value===null;fields.disabled=legacy.checked;draw(value||[])},get(){return legacy.checked?null:rows.map(readRow)}};
    }
    group('provider_methods',I18N.t('settlement.provider_group'),false);group('buyer_preferences',I18N.t('settlement.buyer_group'),true);
    return {set(policy){for(const [key,g] of Object.entries(groups))g.set(policy[key])},get(){return Object.fromEntries(Object.entries(groups).map(([key,g])=>[key,g.get()]))}};
  }};
})();
