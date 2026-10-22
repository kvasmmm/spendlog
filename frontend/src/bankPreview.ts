export type Candidate={id:string;merchant:string;total:number;own_amount:number;occurred_at:string;created_at:string;seconds_apart:number;merchant_match:boolean};
export type Transaction={id:string;merchant:string;amount:number;balance:number;occurred_at:string;created_at:string;payment_created_at:string;precision:string;kind:string;expense_category:string;purchase_id:string|null;version:number;purchase_candidates:Candidate[]};
export type BankRow={id:string;number:number;status:'new'|'repeat'|'ambiguous';decision:string|null;transaction_id:string|null;data:{merchant:string;amount:number;balance:number;occurred_at:string;raw:string};candidates:Transaction[];purchase_candidates:Candidate[]};
export type Batch={id:string;filename:string;account_label:string;period_start:string;period_end:string;created_at:string;confirmed_at:string|null;rows:BankRow[]};
export type Classification='bank_unclassified'|'bank_transfer'|'income';
export type Decision={row_id:string;action:''|'new'|'repeat'|'link';transaction_id?:string;purchase_id?:string;classification:Classification;category?:string};
export type Choices=Record<string,Decision>;
export type PreviewFilter='all'|'attention'|'outgoing'|'incoming'|'repeat';

// Display aliases only: original merchant names and financial row identities stay intact.
export function merchantGroup(name:string){
 const normalized=name.normalize('NFKC').trim().replace(/\s+/g,' ');
 if(/^gs\s*25/i.test(normalized))return 'GS25';
 if(/^(?:씨유\s*\(?cu\)?|cu(?=\b|[^a-z]))/i.test(normalized))return 'CU';
 if(/^dgist(?=\b|[^a-z])/i.test(normalized))return 'DGIST';
 return normalized||'Без описания';
}
export function initialChoices(rows:BankRow[]):Choices{
 return Object.fromEntries(rows.map(r=>[r.id,{row_id:r.id,action:r.status==='new'&&!r.purchase_candidates.length?'new':r.status==='repeat'?'repeat':'',transaction_id:r.status==='repeat'?r.candidates[0]?.id:undefined,classification:'bank_unclassified'}]));
}
export function bulkEligible(row:BankRow,choices:Choices){
 return row.status==='new'&&!row.purchase_candidates.length&&choices[row.id]?.action==='new';
}
export function bulkClassify(rows:BankRow[],choices:Choices,classification:Classification):Choices{
 const next={...choices};
 for(const row of rows){
  if(!bulkEligible(row,choices)||classification==='income'&&row.data.amount<=0)continue;
  next[row.id]={...choices[row.id],classification};
 }
 return next;
}
export function previewGroups(rows:BankRow[],choices:Choices,filter:PreviewFilter,search:string){
 const query=search.normalize('NFKC').trim().toLocaleLowerCase();
 const groups=new Map<string,{id:string;name:string;incoming:boolean;rows:BankRow[];amount:number;unresolved:number}>();
 for(const row of rows){
  if(filter==='attention'&&choices[row.id]?.action)continue;
  if(filter==='outgoing'&&row.data.amount>=0||filter==='incoming'&&row.data.amount<=0)continue;
  if(filter==='repeat'&&choices[row.id]?.action!=='repeat')continue;
  const name=merchantGroup(row.data.merchant);
  if(query&&!`${name} ${row.data.merchant}`.normalize('NFKC').toLocaleLowerCase().includes(query))continue;
  const incoming=row.data.amount>0;
  const id=JSON.stringify([name.toLocaleLowerCase(),incoming]);
  const group=groups.get(id)||{id,name,incoming,rows:[],amount:0,unresolved:0};
  group.rows.push(row);group.amount+=Math.abs(row.data.amount);
  if(!choices[row.id]?.action)group.unresolved++;
  groups.set(id,group);
 }
 return [...groups.values()].sort((a,b)=>b.unresolved-a.unresolved||b.rows.length-a.rows.length||a.name.localeCompare(b.name));
}
export function importSummary(rows:BankRow[],choices:Choices){
 let outgoing=0,incoming=0,added=0,repeat=0,linked=0,unresolved=0;
 for(const row of rows){
  const action=choices[row.id]?.action;
  if(!action){unresolved++;continue}
  if(action==='repeat'){repeat++;continue}
  if(action==='link'){linked++;continue}
  added++;
  if(row.data.amount<0)outgoing-=row.data.amount;else incoming+=row.data.amount;
 }
 return {outgoing,incoming,added,repeat,linked,unresolved};
}

export function bulkCategory(rows:BankRow[],choices:Choices,category:string):Choices{
 const next={...choices};
 for(const row of rows)if(bulkEligible(row,choices)&&row.data.amount<0&&choices[row.id].classification==='bank_unclassified')next[row.id]={...choices[row.id],category};
 return next;
}
