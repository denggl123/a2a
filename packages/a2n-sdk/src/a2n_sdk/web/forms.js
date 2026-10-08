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
    const entries=fields(schema);if(!entries)throw Error('此输入规则请使用 JSON 编辑器。');
    const result={},required=new Set(schema.required||[]);
    for(const [key,p] of entries){
      const label=p.title||key,raw=values[key];
      if(raw===undefined||raw===null||raw===''&&p.type!=='boolean'){
        if(required.has(key))throw Error('请填写：'+label);continue;
      }
      let value=raw;
      if(p.type==='integer'||p.type==='number'){
        value=Number(raw);if(!Number.isFinite(value)||p.type==='integer'&&!Number.isSafeInteger(value))throw Error(label+'需要填写有效'+(p.type==='integer'?'整数':'数字')+'；大整数需要服务提供字符串输入字段。');
        if(p.minimum!==undefined&&value<p.minimum||p.maximum!==undefined&&value>p.maximum)throw Error(label+'超出允许范围。');
      }else if(p.type==='boolean'){
        if(raw!==true&&raw!==false)throw Error(label+'请选择是或否。');
      }else{
        value=String(raw);
        const length=[...value].length;if(p.minLength!==undefined&&length<p.minLength||p.maxLength!==undefined&&length>p.maxLength)throw Error(label+'的长度不符合要求。');
      }
      if(p.enum&&!p.enum.some(item=>item===value))throw Error(label+'请选择允许的值。');
      result[key]=value;
    }
    return result;
  }
  function node(tag,text){const el=document.createElement(tag);if(text!==undefined)el.textContent=String(text);return el}
  function editor(container,onChange){
    const mode=node('select'),raw=node('textarea'),form=node('div'),hint=node('p');raw.rows=4;
    raw.id='paymentInput';hint.className='sub';form.className='formgrid';
    const label=node('label','输入方式');label.append(mode);container.replaceChildren(label,hint,form,raw);
    let schema=null,controls=new Map(),card=null,skill='';
    function show(){const structured=mode.value==='fields';form.hidden=!structured;raw.hidden=structured;hint.textContent=structured?'按服务提供者声明的字段填写。':mode.value==='json'?'填写完整 JSON；系统会在询价前检查格式。':'直接输入需要处理的文字。'}
    mode.onchange=()=>{show();onChange?.()};raw.oninput=()=>onChange?.();
    function setCard(value,selected){
      card=value||{};skill=selected||'';schema=schemaFor(card,skill);controls=new Map();form.replaceChildren();
      mode.replaceChildren();const entries=fields(schema);
      for(const [v,title] of [...(entries?[['fields','按字段填写']]:[]),['text','文字输入'],['json','JSON 编辑器']]){const option=node('option',title);option.value=v;mode.append(option)}
      raw.value='';
      if(entries){
        const required=new Set(schema.required||[]);
        for(const [key,p] of entries){
          const wrapper=node('label',(p.title||key)+(required.has(key)?'（必填）':'（可选）'));let input;
          if(p.enum||p.type==='boolean'){
            input=node('select');const empty=node('option','请选择');empty.value='';input.append(empty);
            for(const [i,val] of (p.enum||[true,false]).entries()){const option=node('option',val===true?'是':val===false?'否':String(val));option.value=String(i);input.append(option)}
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
      if(mode.value==='json'){let result;try{result=JSON.parse(raw.value)}catch(_){throw Error('JSON 格式有误，请检查引号、逗号和括号。')}
        const check=value=>{if(typeof value==='number'&&Number.isInteger(value)&&!Number.isSafeInteger(value))throw Error('JSON 中的大整数会丢失精度，请与提供方约定用字符串传递。');if(value&&typeof value==='object')Object.values(value).forEach(check)};check(result);return result;}
      if(!raw.value.trim())throw Error('请填写本次输入。');return raw.value;
    },get skill(){return skill}};
  }
  const names={characters:'字符数',non_whitespace_characters:'非空白字符数',lines:'行数',paragraphs:'段落数',tokens:'词项数',summary:'摘要',keywords:'关键词',length:'长度',sum:'计算结果',token:'词项',count:'次数',text:'文字',text_sha256:'内容摘要标识'};
  function renderValue(parent,value,depth=0){
    if(depth>5){parent.append(node('p','内容较复杂，请展开原始结果或下载查看。'));return}
    if(value===null||typeof value!=='object'){parent.append(node('pre',String(value??'').slice(0,20000)));return}
    if(Array.isArray(value)){
      if(value.length&&value.every(v=>plain(v))){
        const keys=[...new Set(value.slice(0,30).flatMap(v=>Object.keys(v)))].slice(0,12),table=node('table'),head=node('tr');
        keys.forEach(k=>head.append(node('th',names[k]||k)));table.append(head);
        for(const row of value.slice(0,30)){const tr=node('tr');for(const k of keys){const v=row[k];tr.append(node('td',v&&typeof v==='object'?JSON.stringify(v):v??''))}table.append(tr)}parent.append(table);
      }else{const list=node('ol');value.slice(0,50).forEach(v=>{const li=node('li');renderValue(li,v,depth+1);list.append(li)});parent.append(list)}
      if(value.length>30)parent.append(node('p','较长列表可下载完整结果查看。'));return;
    }
    for(const [key,val] of Object.entries(value).slice(0,50)){const box=node('section');box.append(node('h4',names[key]||key));renderValue(box,val,depth+1);parent.append(box)}
  }
  function renderOutcome(container,out){
    container.replaceChildren();const call=out.delivery||(out.result!==undefined?out:null),money=out.payment||out.settlement;
    if(money)container.append(node('p','结算：'+({CONFIRMED:'已确认',NOT_REQUIRED:'本次无需结算',UNKNOWN:'等待原订单核对',PENDING:'等待各方确认',FAILED:'已确定失败'}[money.state]||money.state)));
    if(call){container.append(node('h3',call.ok?'服务已交付':'服务状态：'+(call.state||'待核对')));if(call.result!==undefined)renderValue(container,call.result);if(call.error)container.append(node('p',typeof call.error==='string'?call.error:JSON.stringify(call.error)))}
    if(call?.state?.includes('UNKNOWN')||['UNKNOWN','PENDING'].includes(money?.state))container.append(node('p','保留原交易，使用“核对原结算”或查看原调用记录。当前结果未知时，新建任务可能再次执行服务。'));
    const detail=node('details');detail.append(node('summary','查看原始结果和收据'),node('pre',JSON.stringify(out,null,2)));container.append(detail);
    const download=node('button','下载完整结果');download.className='ghost';download.onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(out,null,2)],{type:'application/json'})),a=node('a');a.href=url;a.download='agent-result.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};container.append(download);
  }
  window.A2NForms={schemaFor,fields,validate,editor,renderOutcome};
})();
