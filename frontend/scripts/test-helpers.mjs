// Exercise actual browser helpers without a browser, including denied storage.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const ts=(await import(process.env.SPENDLOG_TYPESCRIPT_PATH||'typescript')).default;
const folder=await fs.mkdtemp(path.join(os.tmpdir(),'spendlog-helper-tests-'));
try{
 for(const name of ['id','draft','bankPreview']){
  const source=await fs.readFile(new URL('../src/'+name+'.ts',import.meta.url),'utf8');
  const output=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText.replace("'./id'","'./id.mjs'");
  await fs.writeFile(path.join(folder,name+'.mjs'),output);
 }
 const {newId}=await import(pathToFileURL(path.join(folder,'id.mjs')));
 const {readDraft,writeDraft,clearDraft}=await import(pathToFileURL(path.join(folder,'draft.mjs')));
 const {merchantGroup,initialChoices,previewGroups,bulkClassify,bulkCategory,importSummary}=await import(pathToFileURL(path.join(folder,'bankPreview.mjs')));
 const row=(id,merchant,amount,status='new',purchase_candidates=[])=>({id,number:Number(id)||1,status,decision:null,transaction_id:null,data:{merchant,amount,balance:10000,occurred_at:'2026-10-22T12:00:00+09:00',raw:'source'},candidates:status==='repeat'?[{id:'existing'}]:[],purchase_candidates});
 const rows=[row('1','GS25디지스트점',-1000),row('2','gs25디지스트위성',-2000),row('3','GS25디지스트점',4000),row('4','DGIST',5000),row('5','GS25디지스트점',-3000,'repeat'),row('6','GS25디지스트점',-4000,'ambiguous'),row('7','GS25디지스트점',-5000,'new',[{id:'receipt'}])];
 const choices=initialChoices(rows);
 assert.equal(Object.keys(choices).length,rows.length);
 assert.equal(choices['6'].action,'');assert.equal(choices['7'].action,'');
 assert.equal(choices['3'].classification,'bank_unclassified');
 const groups=previewGroups(rows,choices,'all','');
 const gs25=groups.find(g=>g.name==='GS25'&&!g.incoming);
 assert.equal(gs25.rows.length,5);assert.equal(gs25.amount,15000);
 assert.equal(groups.filter(g=>g.name==='GS25').length,2); // directions stay separate
 assert.equal(previewGroups(rows,choices,'attention','').flatMap(g=>g.rows).length,2);
 assert.equal(previewGroups(rows,choices,'all','dgist').length,1);
 assert.equal(previewGroups(rows,choices,'repeat','')[0].rows[0].id,'5');
 assert.equal(merchantGroup('씨유(CU) 달서미'),'CU');assert.equal(merchantGroup('CU 디지스트'),'CU');
 assert.notEqual(merchantGroup('Cupang'),'CU');assert.equal(merchantGroup('  '),'Без описания');
 const mixed={...choices,'7':{...choices['7'],action:'link',purchase_id:'receipt'}};
 const bulk=bulkClassify(rows,mixed,'bank_transfer');
 assert.equal(bulk['1'].classification,'bank_transfer');
 assert.deepEqual(bulk['5'],mixed['5']);assert.deepEqual(bulk['6'],mixed['6']);assert.deepEqual(bulk['7'],mixed['7']);
 assert.equal(mixed['1'].classification,'bank_unclassified'); // no mutation
 const categorized=bulkCategory(rows,mixed,'Магазины у дома');
 assert.equal(categorized['1'].category,'Магазины у дома');
 assert.equal(categorized['2'].category,'Магазины у дома');
 for(const id of ['3','4','5','6','7'])assert.deepEqual(categorized[id],mixed[id]);
 assert.equal(mixed['1'].category,undefined);
 const income=bulkClassify(rows,choices,'income');
 assert.equal(income['1'].classification,'bank_unclassified');assert.equal(income['3'].classification,'income');
 assert.deepEqual(importSummary(rows,mixed),{outgoing:3000,incoming:9000,added:4,repeat:1,linked:1,unresolved:1});
 assert.equal(rows[0].data.merchant,'GS25디지스트점');assert.equal(rows[0].data.raw,'source');
 console.log('PASS: import grouping, totals, direction separation, review filters, and safe bulk decisions.');
 const store=()=>{const data=new Map();return {getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)}};
 Object.defineProperty(globalThis,'localStorage',{value:store(),configurable:true});
 Object.defineProperty(globalThis,'sessionStorage',{value:store(),configurable:true});
 globalThis.window=globalThis;
 const draft={items:[{product_id:null,name:'Food',category:'Food',unit_price:1000,quantity:1}],merchant:'GS25',note:'',time:'2026-10-22T12:00',receipt:'1000',shared:false,own:'',person:'',debtKind:'friend',key:newId()};
 assert.equal(writeDraft(draft),true);assert.deepEqual(readDraft(),draft);
 const menuDraft={...draft,items:[{...draft.items[0],menu_product_id:'menu-id'}]};
 assert.equal(writeDraft(menuDraft),true);assert.deepEqual(readDraft(),menuDraft);
 writeDraft({...draft,items:[{...draft.items[0],menu_product_id:123}]});assert.equal(readDraft(),null);
 localStorage.setItem('spendlog-draft','{"items":null}');assert.equal(readDraft(),null);
 localStorage.setItem('spendlog-draft','{broken');assert.equal(readDraft(),null);
 clearDraft();assert.equal(readDraft(),null);
 Object.defineProperty(globalThis,'localStorage',{configurable:true,get(){throw new Error('blocked')}});
 assert.equal(writeDraft(draft),false);assert.deepEqual(readDraft(),draft);
 clearDraft();assert.equal(readDraft(),null);
 Object.defineProperty(globalThis,'sessionStorage',{configurable:true,get(){throw new Error('blocked')}});
 assert.equal(writeDraft(draft),false);assert.equal(readDraft(),null);clearDraft();
 const native=globalThis.crypto;
 assert.match(newId(),/^[a-f0-9-]{36}$/);
 Object.defineProperty(globalThis,'crypto',{configurable:true,value:{getRandomValues:native.getRandomValues.bind(native)}});
 const ids=Array.from({length:10000},newId);
 assert.equal(new Set(ids).size,10000);
 ids.forEach(id=>assert.match(id,/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/));
 console.log('PASS: draft persistence/recovery, invalid drafts, denied local/session storage, logout clearing, native and HTTP UUIDs.');
}finally{await fs.rm(folder,{recursive:true,force:true})}
