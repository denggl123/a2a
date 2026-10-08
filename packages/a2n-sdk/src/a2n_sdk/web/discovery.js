/* Pure candidate facts for local display. No I/O, discovery or settlement calls. */
(() => {
  'use strict';
  const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
  const strings=v=>Array.isArray(v)?[...new Set(v.filter(x=>typeof x==='string'&&x.length>0&&x.length<=512))]:[];
  const choices=v=>Array.isArray(v)?strings(v):typeof v==='string'&&v?[v]:[];
  const UNKNOWN='__undeclared__';
  const aliases={'a2n-points/1':'points','points':'points','evm-native/1':'native','native':'native','x402/2':'x402','x402':'x402'};
  const lookup=(map,key)=>Object.hasOwn(map,key)?map[key]:key;
  const modeLabels={EARN:'免费服务，供给方获积分',DEBT:'允许使用方欠账',PAY:'允许用已有积分兑换'};
  function points(card){
    const policy=card?.['x-a2n']?.points;
    return object(policy)&&policy.enabled===true?strings(policy.modes).filter(m=>Object.hasOwn(modeLabels,m)):[];
  }
  function payments(card){
    const ext=object(card?.['x-a2n'])?card['x-a2n']:{},policy=ext.points;
    const old=strings(card?.accepts).concat(strings(ext.accepts));
    const methods=Array.isArray(ext.payments?.methods)?ext.payments.methods.filter(object):[];
    const pay=new Set(old.map(m=>lookup(aliases,m)).filter(m=>m!=='points'));
    for(const m of methods){if(typeof m.method==='string'&&m.method&&lookup(aliases,m.method)!=='points')pay.add(lookup(aliases,m.method))}
    if(points(card).length||!object(policy)&&old.some(m=>lookup(aliases,m)==='points'))pay.add('points');
    // points_method is a node module declaration, not this Agent's service policy.
    return [...pay];
  }
  function facts(item){
    const c=object(item?.card)?item.card:{},ext=object(c['x-a2n'])?c['x-a2n']:{},skills=Array.isArray(c.skills)?c.skills.filter(object):[];
    const methods=Array.isArray(ext.payments?.methods)?ext.payments.methods.filter(object):[];
    const currencies=new Set(),book=object(ext.price_book)?ext.price_book:{};
    for(const skill of Object.values(book))if(object(skill))for(const currency of Object.keys(skill))currencies.add(currency);
    methods.forEach(m=>{if(typeof m.currency==='string'&&m.method!=='a2n-points/1')currencies.add(m.currency)});
    if(payments(c).includes('points'))currencies.add('points');
    const declaredModes=name=>strings(c[name]).concat(skills.flatMap(s=>strings(s[name==='defaultInputModes'?'inputModes':'outputModes'])));
    const fallback=v=>v.length?[...new Set(v)]:[UNKNOWN];
    const trial=ext.trial,sample=object(trial)&&trial.cap===10&&typeof trial.ended==='boolean'?[trial.ended?'GRADUATED':'INITIAL_FREE']:[UNKNOWN];
    const provider=item?.key?.provider_did||ext.projection?.node_did||ext.origin?.node_did;
    return {pay:fallback(payments(c)),pointMode:fallback(points(c)),currency:fallback([...currencies]),
      chain:fallback(strings(methods.map(m=>m.network)).concat(payments(c).includes('points')?['a2n']:[])),provider:fallback(typeof provider==='string'&&provider?[provider]:[]),
      version:fallback(typeof c.version==='string'&&c.version?[c.version]:[]),
      input:fallback(declaredModes('defaultInputModes')),output:fallback(declaredModes('defaultOutputModes')),
      tags:fallback(strings(skills.flatMap(s=>strings(s.tags)))),sample,
      verification:[item?.verification==='CARD_VERIFIED'?'CARD_VERIFIED':UNKNOWN]};
  }
  function matches(item,state,skip=''){
    const f=facts(item);
    return Object.keys(f).every(k=>k===skip||!choices(state[k]).length||choices(state[k]).some(v=>f[k].includes(v)));
  }
  function label(key,value){
    if(value===UNKNOWN)return key==='verification'?'未提供验签标记':'未声明';
    if(key==='pay')return lookup({points:'支持积分',native:'原生币直付',x402:'x402 授权付款',peer_account:'对等账户'},
      value.startsWith('direct_pay:')?'直付 · '+value.slice(11):value);
    if(key==='pointMode')return modeLabels[value]||value;
    if(key==='sample')return ({INITIAL_FREE:'前十次免费样品阶段',GRADUATED:'首批十次样品已完成'}[value]||value);
    if(key==='verification')return value==='CARD_VERIFIED'?'发现记录已验签':value;
    if(key==='currency'&&value==='points')return '积分（各发行方独立）';
    if(key==='chain'&&value==='a2n')return '节点积分网络';
    return value;
  }
  function toggle(value,next){const selected=choices(value);return !next?'':selected.includes(next)?selected.filter(v=>v!==next):[...selected,next]}
  window.A2NDiscovery={facts,payments,points,matches,label,choices,toggle,UNKNOWN};
})();
