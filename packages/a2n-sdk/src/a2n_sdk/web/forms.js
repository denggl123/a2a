/* Small schema forms and readable results. No remote scripts or automatic media fetches. */
(() => {
  'use strict';
  const own=(obj,key)=>Object.prototype.hasOwnProperty.call(obj,key);
  const plain=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
  function schemaFor(card,skill){
    const item=(card.skills||[]).find(s=>s.id===skill)||{},ext=card['x-a2n']||{};
    return item.input_schema||item.inputSchema||ext.input_schemas?.[skill]||ext.input_schema||null;
  }
  function fields(schema){
    if(!plain(schema)||schema.type!=='object'||!plain(schema.properties)||schema.$ref||schema.oneOf||schema.anyOf||schema.allOf||schema.not||schema.if||schema.then||schema.else||schema.dependencies||schema.dependentRequired||schema.required!==undefined&&!Array.isArray(schema.required))return null;
    const entries=Object.entries(schema.properties);
    if(!entries.length||entries.length>32||entries.some(([k,p])=>['__proto__','constructor','prototype'].includes(k)||!plain(p)||!['string','integer','number','boolean'].includes(p.type)
      ||p.$ref||p.oneOf||p.anyOf||p.allOf||p.not||p.if||p.then||p.else||p.pattern||p.format||p.const!==undefined||p.enum&&(!Array.isArray(p.enum)||!p.enum.length||p.enum.length>32||p.enum.some(v=>p.type==='boolean'?typeof v!=='boolean':p.type==='string'?typeof v!=='string':typeof v!=='number'||!Number.isFinite(v)||p.type==='integer'&&!Number.isSafeInteger(v)))))return null;
    if((schema.required||[]).some(k=>!own(schema.properties,k)))return null;
    return entries;
  }
  function validate(schema,values){
    const entries=fields(schema);if(!entries)throw Error(I18N.t('form.schema_unsupported'));
    const result={},required=new Set(schema.required||[]);
    for(const [key,p] of entries){
      const label=p.title||key,raw=values[key];
      if(raw===undefined||raw===null||raw===''&&p.type!=='boolean'){
        if(required.has(key))throw Error(I18N.t('form.required',{label}));continue;
      }
      let value=raw;
      if(p.type==='integer'||p.type==='number'){
        value=Number(raw);if(!Number.isFinite(value)||p.type==='integer'&&!Number.isSafeInteger(value))throw Error(I18N.t(p.type==='integer'?'form.number_integer':'form.number',{label}));
        if(p.minimum!==undefined&&value<p.minimum||p.maximum!==undefined&&value>p.maximum)throw Error(I18N.t('form.out_of_range',{label}));
      }else if(p.type==='boolean'){
        if(raw!==true&&raw!==false)throw Error(I18N.t('form.boolean',{label}));
      }else{
        value=String(raw);
        const length=[...value].length;if(p.minLength!==undefined&&length<p.minLength||p.maxLength!==undefined&&length>p.maxLength)throw Error(I18N.t('form.length',{label}));
      }
      if(p.enum&&!p.enum.some(item=>item===value))throw Error(I18N.t('form.enum',{label}));
      result[key]=value;
    }
    return result;
  }
  function node(tag,text){const el=document.createElement(tag);if(text!==undefined)el.textContent=String(text);return el}
  function editor(container,onChange){
    const mode=node('select'),raw=node('textarea'),form=node('div'),hint=node('p');raw.rows=4;
    raw.id='paymentInput';hint.className='sub';form.className='formgrid';
    const label=node('label',I18N.t('form.mode_label'));label.append(mode);container.replaceChildren(label,hint,form,raw);
    let schema=null,controls=new Map(),card=null,skill='';
    function show(){const structured=mode.value==='fields';form.hidden=!structured;raw.hidden=structured;hint.textContent=structured?I18N.t('form.hint_fields'):mode.value==='json'?I18N.t('form.hint_json'):I18N.t('form.hint_text')}
    mode.onchange=()=>{show();onChange?.()};raw.oninput=()=>onChange?.();
    function setCard(value,selected){
      card=value||{};skill=selected||'';schema=schemaFor(card,skill);controls=new Map();form.replaceChildren();
      mode.replaceChildren();const entries=fields(schema);
      for(const [v,title] of [...(entries?[['fields',I18N.t('form.mode.fields')]]:[]),['text',I18N.t('form.mode.text')],['json',I18N.t('form.mode.json')]]){const option=node('option',title);option.value=v;mode.append(option)}
      raw.value='';
      if(entries){
        const required=new Set(schema.required||[]);
        for(const [key,p] of entries){
          const wrapper=node('label',I18N.t(required.has(key)?'form.field_required':'form.field_optional',{label:p.title||key}));let input;
          if(p.enum||p.type==='boolean'){
            input=node('select');const empty=node('option',I18N.t('form.select_placeholder'));empty.value='';input.append(empty);
            for(const [i,val] of (p.enum||[true,false]).entries()){const option=node('option',val===true?I18N.t('form.yes'):val===false?I18N.t('form.no'):String(val));option.value=String(i);input.append(option)}
            if(p.default!==undefined){const index=(p.enum||[true,false]).findIndex(v=>v===p.default);if(index>=0)input.value=String(index)}
          }else{
            input=node(p.type==='string'?'textarea':'input');if(p.type==='string')input.rows=3;else{input.type='number';input.step=p.type==='integer'?'1':'any'}
            if(p.default!==undefined)input.value=String(p.default);
          }
          input.dataset.inputField=key;input.oninput=()=>onChange?.();input.onchange=()=>onChange?.();wrapper.append(input);
          if(p.description)wrapper.append(node('span',p.description));form.append(wrapper);controls.set(key,{input,p});
        }
      }else if(schema){mode.value='json';raw.value=JSON.stringify(schema.examples?.[0]||schema.default||{},null,2)}
      show();
    }
    return {setCard,get(){
      if(mode.value==='fields'){
        const values={};for(const [key,{input,p}] of controls){values[key]=p.enum||p.type==='boolean'?input.value===''?undefined:(p.enum||[true,false])[Number(input.value)]:input.value}
        return validate(schema,values);
      }
      if(mode.value==='json'){let result;try{result=JSON.parse(raw.value)}catch(_){throw Error(I18N.t('form.json_invalid'))}
        const check=value=>{if(typeof value==='number'&&Number.isInteger(value)&&!Number.isSafeInteger(value))throw Error(I18N.t('form.json_big_int'));if(value&&typeof value==='object')Object.values(value).forEach(check)};check(result);return result;}
      if(!raw.value.trim())throw Error(I18N.t('form.text_required'));return raw.value;
    },get skill(){return skill}};
  }
  // 结果字段只按合法键名取表（form.field.*），查不到如实回显原字段名，不编中文。
  const fieldName=k=>I18N.text('form.field.'+k)||k;
  function renderValue(parent,value,depth=0){
    if(depth>5){parent.append(node('p',I18N.t('form.too_deep')));return}
    if(value===null||typeof value!=='object'){parent.append(node('pre',String(value??'').slice(0,20000)));return}
    if(Array.isArray(value)){
      if(value.length&&value.every(v=>plain(v))){
        const keys=[...new Set(value.slice(0,30).flatMap(v=>Object.keys(v)))].slice(0,12),table=node('table'),head=node('tr');
        keys.forEach(k=>head.append(node('th',fieldName(k))));table.append(head);
        for(const row of value.slice(0,30)){const tr=node('tr');for(const k of keys){const v=row[k];tr.append(node('td',v&&typeof v==='object'?JSON.stringify(v):v??''))}table.append(tr)}parent.append(table);
      }else{const list=node('ol');value.slice(0,50).forEach(v=>{const li=node('li');renderValue(li,v,depth+1);list.append(li)});parent.append(list)}
      if(value.length>30)parent.append(node('p',I18N.t('form.long_list')));return;
    }
    for(const [key,val] of Object.entries(value).slice(0,50)){const box=node('section');box.append(node('h4',fieldName(key)));renderValue(box,val,depth+1);parent.append(box)}
  }
  function renderOutcome(container,out){
    container.replaceChildren();const call=out.delivery||(out.result!==undefined?out:null),money=out.payment||out.settlement;
    if(money)container.append(node('p',I18N.t('form.settlement',{state:I18N.text('form.settle.'+money.state)||money.state})));
    if(call){container.append(node('h3',call.ok?I18N.t('form.delivered'):I18N.t('form.call_state',{state:call.state||I18N.t('form.state_pending')})));if(call.result!==undefined)renderValue(container,call.result);if(call.error)container.append(node('p',typeof call.error==='string'?call.error:JSON.stringify(call.error)))}
    if(call?.state?.includes('UNKNOWN')||['UNKNOWN','PENDING'].includes(money?.state))container.append(node('p',I18N.t('form.keep_original')));
    const detail=node('details');detail.append(node('summary',I18N.t('form.raw_toggle')),node('pre',JSON.stringify(out,null,2)));container.append(detail);
    const refs=call?.result?.assets||[],uid=call?.metadata?.trade_uid||out?.metadata?.trade_uid||call?.metadata?.trade_facts?.trade_uid||out?.metadata?.trade_facts?.trade_uid;
    for(const ref of (Array.isArray(refs)?refs:[]).slice(0,8)){if(ref?.v!=='a2n-asset/1')continue;const button=node('button',I18N.t('form.view_asset',{mime:ref.mime_type,size:ref.size}));button.className='ghost';button.onclick=async()=>{button.disabled=true;try{
      let local=ref;if(uid)local=await request('/v1/assets/fetch',{trade_uid:uid,asset_id:ref.asset_id});if(local.ref)local=local.ref;
      const url=`/v1/assets/${encodeURIComponent(local.asset_id)}/preview`;let view;
      if(ref.mime_type.startsWith('image/')){view=node('img');view.alt=I18N.t('form.asset_alt');view.style.maxWidth='100%';view.src=url}
      else if(ref.mime_type.startsWith('audio/')||ref.mime_type.startsWith('video/')){view=node(ref.mime_type.startsWith('audio/')?'audio':'video');view.controls=true;view.preload='metadata';view.style.maxWidth='100%';view.src=url}
      else if(ref.mime_type==='application/pdf'){view=node('iframe');view.setAttribute('sandbox','');view.title=I18N.t('form.pdf_title');view.style.cssText='width:100%;height:480px';view.src=url}
      else if(['text/plain','application/json'].includes(ref.mime_type)){const r=await fetch(url,{headers:{Range:'bytes=0-65535'}});if(!r.ok)throw Error(I18N.t('form.preview_failed'));view=node('pre',(await r.text()).slice(0,20000))}
      else{view=node('a',I18N.t('form.download_asset'));view.href=`/v1/assets/${encodeURIComponent(local.asset_id)}/content`;view.download='agent-asset'}
      container.append(view);
    }catch(e){container.append(node('p',I18N.t('form.asset_error',{error:e.message})))}finally{button.disabled=false}};container.append(button)}
    const download=node('button',I18N.t('form.download_result'));download.className='ghost';download.onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(out,null,2)],{type:'application/json'})),a=node('a');a.href=url;a.download='agent-result.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};container.append(download);
  }
  window.A2NForms={schemaFor,fields,validate,editor,renderOutcome};
})();
