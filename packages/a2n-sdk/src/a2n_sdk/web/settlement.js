/* Owner policy editor. Decimal amounts stay strings; list order is authoritative. */
(() => {
  'use strict';
  const methods=[['a2n-points/1','积分'],['x402/2','x402 授权付款'],['evm-native/1','直接链上转账']];
  window.A2NSettlementUI={mount(host){
    const groups={};
    function group(key,title,buyer){
      const box=document.createElement('section'),heading=document.createElement('h4');heading.textContent=title;box.append(heading);
      const label=document.createElement('label'),legacy=document.createElement('input');legacy.type='checkbox';label.append(legacy,document.createTextNode(buyer?'沿用逐笔选择（开启自动结算前请取消）':'沿用已配置的收款通道'));box.append(label);
      const fields=document.createElement('fieldset'),list=document.createElement('div'),add=document.createElement('button');add.type='button';add.textContent='增加一种方式';fields.append(list,add);box.append(fields);host.append(box);
      let rows=[];
      function input(parent,title,value){const label=document.createElement('label'),field=document.createElement('input');label.append(document.createTextNode(title),field);field.value=value??'';parent.append(label);return field}
      function select(parent,title,options,value){const label=document.createElement('label'),field=document.createElement('select');for(const [v,name] of options)field.add(new Option(name,v));field.value=value;label.append(document.createTextNode(title),field);parent.append(label);return field}
      function readRow(row){const {base,fields:f}=row,out={...base,method:f.method.value};for(const key of ['currency','network','asset','mode','max_amount_minor','fee_cap_minor','funding_issuer','max_cost'])if(f[key]){const value=f[key].value.trim();if(value)out[key]=value;else delete out[key]}if(out.method!=='a2n-points/1'){delete out.mode;delete out.funding_issuer;delete out.max_cost}if(out.method!=='evm-native/1')delete out.fee_cap_minor;return out}
      function draw(values){list.replaceChildren();rows=[];for(const [index,base] of values.entries()){
        const row=document.createElement('article');row.className='agent';const number=document.createElement('b');number.textContent=(buyer?'优先顺序 ':'方式 ')+(index+1);row.append(number);const f={};
        f.method=select(row,'结算方式',methods,base.method);f.mode=select(row,'积分约定',buyer?[['EARN','供应方积累自家积分'],['DEBT','允许欠账'],['PAY','用已有积分兑换']]:[['','支持全部已配置积分约定'],['EARN','供应方积累自家积分'],['DEBT','允许欠账'],['PAY','用已有积分兑换']],base.mode??(buyer?'EARN':''));
        f.currency=input(row,'币种（留空接受该方式可用的币种）',base.currency);f.network=input(row,'网络（可选，例如 eip155:8453）',base.network);f.asset=input(row,'资产或代币合约（可选）',base.asset);
        if(buyer){f.max_amount_minor=input(row,'单笔金额上限（最小单位整数，自动结算必填）',base.max_amount_minor);f.fee_cap_minor=input(row,'直接转账网络费上限（最小单位整数）',base.fee_cap_minor??'0');f.funding_issuer=input(row,'兑换时使用谁发行的积分（可选）',base.funding_issuer);f.max_cost=input(row,'兑换最多消耗多少积分（可选）',base.max_cost)}
        function visibility(){f.mode.parentElement.hidden=f.method.value!=='a2n-points/1';if(buyer){f.fee_cap_minor.parentElement.hidden=f.method.value!=='evm-native/1';for(const k of ['funding_issuer','max_cost'])f[k].parentElement.hidden=f.method.value!=='a2n-points/1'||f.mode.value!=='PAY'}}f.method.onchange=visibility;f.mode.onchange=visibility;visibility();
        for(const [text,delta] of [['上移',-1],['下移',1],['移除',0]]){const b=document.createElement('button');b.type='button';b.className='ghost';b.textContent=text;b.disabled=delta<0&&index===0||delta>0&&index===values.length-1;b.onclick=()=>{const current=rows.map(readRow);if(delta)[current[index],current[index+delta]]=[current[index+delta],current[index]];else current.splice(index,1);draw(current)};row.append(b)}
        rows.push({base,fields:f});list.append(row);
      }}
      legacy.onchange=()=>{fields.disabled=legacy.checked};add.onclick=()=>draw([...rows.map(readRow),{method:'a2n-points/1',...(buyer?{mode:'EARN',max_amount_minor:'0'}:{})}]);
      groups[key]={set(value){legacy.checked=value===null;fields.disabled=legacy.checked;draw(value||[])},get(){return legacy.checked?null:rows.map(readRow)}};
    }
    group('provider_methods','我作为供应方支持的收款方式',false);group('buyer_preferences','我作为使用方的付款优先顺序',true);
    return {set(policy){for(const [key,g] of Object.entries(groups))g.set(policy[key])},get(){return Object.fromEntries(Object.entries(groups).map(([key,g])=>[key,g.get()]))}};
  }};
})();
