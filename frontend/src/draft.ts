import {newId} from './id';
import type {Line} from './api';
export const DRAFT_KEY='spendlog-draft';
export type Draft={items:Line[];merchant:string;note:string;time:string;receipt:string;shared:boolean;own:string;person:string;debtKind:string;key:string};
export function readDraft():Draft|null{
 try{
  let raw:string|null=null;
  try{raw=localStorage.getItem(DRAFT_KEY)}catch{}
  if(!raw){try{raw=sessionStorage.getItem(DRAFT_KEY)}catch{}}
  if(!raw)return null;
  const d=JSON.parse(raw);
  if(!d||!Array.isArray(d.items)||d.items.length>200)return null;
  for(const field of ['merchant','note','time','receipt','own','person','debtKind'])if(typeof d[field]!=='string')return null;
  if(typeof d.shared!=='boolean')return null;
  if(!d.items.every((i:Line)=>i&&typeof i.name==='string'&&typeof i.category==='string'&&Number.isSafeInteger(i.unit_price)&&i.unit_price>=0&&Number.isSafeInteger(i.quantity)&&i.quantity>0&&(i.product_id===null||typeof i.product_id==='string')&&(i.menu_product_id===undefined||i.menu_product_id===null||typeof i.menu_product_id==='string')))return null;
  return {...d,key:typeof d.key==='string'&&d.key?d.key:newId()};
 }catch{return null}
}
export function writeDraft(draft:Draft):boolean{
 const raw=JSON.stringify(draft);
 try{localStorage.setItem(DRAFT_KEY,raw);return true}catch{
  try{sessionStorage.setItem(DRAFT_KEY,raw)}catch{}
  return false;
 }
}
export function clearDraft(){
 for(const kind of ['localStorage','sessionStorage'] as const){try{window[kind].removeItem(DRAFT_KEY)}catch{}}
}
