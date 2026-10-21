/* Pure candidate facts for local display. No I/O, discovery or settlement calls. */
(() => {
  'use strict';
  const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
  const strings=v=>Array.isArray(v)?[...new Set(v.filter(x=>typeof x==='string'&&x.length>0&&x.length<=512))]:[];
  const choices=v=>Array.isArray(v)?strings(v):typeof v==='string'&&v?[v]:[];
  const UNKNOWN='__undeclared__';
  const aliases={'a2n-points/1':'points','points':'points','evm-native/1':'native','native':'native','x402/2':'x402','x402':'x402'};
  const lookup=(map,key)=>Object.hasOwn(map,key)?map[key]:key;
  // 只登记合法 mode 名；文案一律走 I18N（discovery.mode.*），本文件不持有任何展示文字。
  const POINT_MODES=['EARN','DEBT','PAY'];
  function points(card){
    const policy=card?.['x-a2n']?.points;
    return object(policy)&&policy.enabled===true?strings(policy.modes).filter(m=>POINT_MODES.includes(m)):[];
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
    if(value===UNKNOWN)return I18N.t(key==='verification'?'discovery.verification_unknown':'discovery.undeclared');
    if(key==='pay'){
      if(value.startsWith('direct_pay:'))return I18N.t('discovery.pay.direct_pay',{detail:value.slice(11)});
      const mapped=I18N.text('discovery.pay.'+value);
      return mapped===null?value:mapped;
    }
    if(key==='pointMode'){
      const mapped=I18N.text('discovery.mode.'+value);
      return mapped===null?value:mapped;
    }
    if(key==='sample'){
      const mapped=I18N.text('discovery.sample.'+value);
      return mapped===null?value:mapped;
    }
    if(key==='verification')return value==='CARD_VERIFIED'?I18N.t('discovery.verification.verified'):value;
    if(key==='currency'&&value==='points')return I18N.t('discovery.currency.points');
    if(key==='chain'&&value==='a2n')return I18N.t('discovery.chain.a2n');
    return value;
  }
  function toggle(value,next){const selected=choices(value);return !next?'':selected.includes(next)?selected.filter(v=>v!==next):[...selected,next]}
  window.A2NDiscovery={facts,payments,points,matches,label,choices,toggle,UNKNOWN};
})();
