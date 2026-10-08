/* Validate owner inputs before any offer or authorization is sent. */
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const ctx={window:{}};vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(__dirname,'../packages/a2n-sdk/src/a2n_sdk/web/forms.js'),'utf8'),ctx);
const f=ctx.window.A2NForms;
const schema={type:'object',properties:{text:{type:'string',minLength:1,maxLength:2},count:{type:'integer',minimum:0,maximum:10},choice:{type:'string',enum:['A','B']},enabled:{type:'boolean'}},required:['text','count','enabled']};
assert.strictEqual(JSON.stringify(f.validate(schema,{text:'😀字',count:'0',enabled:false,choice:'A'})),JSON.stringify({text:'😀字',count:0,choice:'A',enabled:false}));
for(const input of [{count:0,enabled:false},{text:'abc',count:0,enabled:false},{text:'a',count:'1.5',enabled:true},{text:'a',count:'9007199254740993',enabled:true},{text:'a',count:11,enabled:true},{text:'a',count:0,enabled:'false'},{text:'a',count:0,enabled:true,choice:'C'}])assert.throws(()=>f.validate(schema,input));
for(const unsupported of [{...schema,required:'text'},{...schema,if:{properties:{text:{const:'a'}}}},{type:'object',properties:{nested:{type:'object'}}},{type:'object',properties:{word:{type:'string',pattern:'^x'}}},{type:'object',properties:{enum:{type:'string',enum:[{}]}}}])assert.strictEqual(f.fields(unsupported),null);
const card={skills:[{id:'a',input_schema:schema},{id:'b',inputSchema:{type:'string'}}]};
assert.strictEqual(f.schemaFor(card,'a'),schema);assert.strictEqual(f.schemaFor(card,'b').type,'string');
console.log('16 input checks passed: required fields, Unicode, precise integers, ranges, enums, false/zero, and unsupported-schema fallback.');
