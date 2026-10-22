export type Barcode={barcode:string;name:string;category:string};
export type Product={id:string;barcode:string;name:string;category:string;last_price:number|null;last_merchant:string|null};
export type MenuProduct={id:string;merchant_id:string;merchant:string;name:string;category:string;price:number;last_price:number|null;version:number};
export type BankPurchase={id:string;merchant:string;amount:number;occurred_at:string;version:number};
export type MerchantReport={start:string;end:string;merchants:{merchant:string;own_amount:number;total:number;unitemized:number;purchase_count:number;items:{name:string;category:string;quantity:number;own_amount:number;total:number}[]}[]};
export type Line={product_id:string|null;menu_product_id?:string|null;name:string;category:string;unit_price:number;quantity:number};
export type Debt={id:string;person:string;direction:'receivable'|'payable';kind:string;principal:number;settled:number;remaining:number;occurred_at:string;purchase_id:string|null};
export type Purchase={id:string;merchant:string;occurred_at:string;note:string;total:number;own_amount:number;version:number;items:Line[];debt:Debt|null};
export type Payment={id:string;created_at:string;bank_transaction_id:string|null;kind:string;amount:number;occurred_at:string;note:string;version:number;purchase_id:string|null;debt_id:string|null;person:string};
export type Rule={id:string;match:string;category:string};
export type Settings={month_start:number;monthly_limit:number;category_limits:Record<string,number>};
export type Point={name:string;amount:number;limit?:number};
export type Dashboard={start:string;unclassified_outflow:number;unclassified_inflow:number;end:string;income:number;expenses:number;cash_flow:number;cash_in:number;cash_out:number;previous_expenses:number;purchase_count:number;average_purchase:number;daily_average:number;categories:Point[];merchants:Point[];daily:Point[];weekly:Point[];monthly:Point[];frequent:{name:string;quantity:number;amount:number}[];limit:number};
export const categories=['Еда вне дома','Магазины у дома','Продукты','Обязательные траты','Страховка','Связь и интернет','Подписки','Здоровье и клиники','Транспорт','Жильё','PC Café','Покупки','Услуги','Готовая еда','Рестораны','Напитки','Молочные продукты','Снеки','Здоровье','Другое'];
export const won=(v:number)=>new Intl.NumberFormat('ru-RU',{maximumFractionDigits:0}).format(v)+' ₩';
export const shortDate=(s:string)=>new Date(s).toLocaleDateString('ru-RU',{day:'numeric',month:'short',timeZone:'Asia/Seoul'});
export const localInput=(s?:string)=>{const d=s?new Date(s):new Date();return new Date(d.getTime()+9*3600000).toISOString().slice(0,16)};
export const toISO=(s:string)=>new Date(s+':00+09:00').toISOString();
export async function api<T>(path:string,method='GET',data?:unknown,key?:string):Promise<T>{
 const r=await fetch('/api'+path,{method,credentials:'same-origin',headers:{'Content-Type':'application/json','X-SpendLog':'1',...(key?{'Idempotency-Key':key}:{})},body:data===undefined?undefined:JSON.stringify(data)});
 const body=await r.json();
 if(!r.ok){if(r.status===401&&!path.includes('login'))window.dispatchEvent(new Event('unauthorized'));throw new Error(typeof body.detail==='string'?body.detail:Array.isArray(body.detail)?body.detail.map((x:{msg:string})=>x.msg).join('; '):'Не удалось выполнить запрос');}
 return body as T;
}
export const errorText=(e:unknown)=>e instanceof Error?e.message:'Ошибка соединения';
