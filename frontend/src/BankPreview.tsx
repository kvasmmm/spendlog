import {useCategories} from './Categories';
import {useState} from 'react';
import {ChevronDown,Search,Check,AlertCircle} from 'lucide-react';
import {won} from './api';
import {bulkEligible,previewGroups,importSummary,type Batch,type BankRow,type Choices,type Decision,type Classification,type PreviewFilter} from './bankPreview';
const date=(s:string)=>new Date(s).toLocaleString('ru-RU',{timeZone:'Asia/Seoul',day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
function decisionLabel(row:BankRow,decision?:Decision){
 if(!decision?.action)return row.purchase_candidates.length?'Выбрать чек или отдельную операцию':'Нужно решение';
 if(decision.action==='repeat')return 'Повтор · пропустить';
 if(decision.action==='link')return 'Связать с чеком';
 if(decision.classification==='bank_transfer')return 'Перевод';
 if(decision.classification==='income')return 'Доход';
 return row.data.amount>0?'Поступление · не доход':decision.category||'Расход';
}
export function BankPreview({batch,choices,busy,change,bulk,setCategory,confirm}:{batch:Batch;choices:Choices;busy:boolean;change:(id:string,patch:Partial<Decision>)=>void;bulk:(rows:BankRow[],kind:Classification)=>void;setCategory:(rows:BankRow[],category:string)=>void;confirm:()=>void}){
 const categories=useCategories();
 const[filter,setFilter]=useState<PreviewFilter>(()=>batch.rows.some(r=>!choices[r.id]?.action)?'attention':'all');
 const[search,setSearch]=useState('');
 const summary=importSummary(batch.rows,choices);
 const groups=previewGroups(batch.rows,choices,filter,search);
 const tabs:{id:PreviewFilter;label:string;count:number}[]=[{id:'all',label:'Все',count:batch.rows.length},{id:'attention',label:'Нужно решить',count:summary.unresolved},{id:'outgoing',label:'Списания',count:batch.rows.filter(r=>r.data.amount<0).length},{id:'incoming',label:'Поступления',count:batch.rows.filter(r=>r.data.amount>0).length},{id:'repeat',label:'Повторы',count:summary.repeat}];
 return <div className="import-preview">
  <div className="import-overview"><div><span>Новых списаний</span><strong>{won(summary.outgoing)}</strong></div><div><span>Новых поступлений</span><strong>{won(summary.incoming)}</strong></div></div>
  <p className="hint">Добавить: {summary.added} · Пропустить повторы: {summary.repeat} · Связать с чеками: {summary.linked}. Поступления автоматически не становятся доходом.</p>
  {summary.unresolved>0?<div className="import-attention" role="status"><AlertCircle size={18}/><span>Нужно решить {summary.unresolved} операций. Остальные уже подготовлены.</span><button className="text-button" onClick={()=>{setSearch('');setFilter('attention')}}>Показать</button></div>:<div className="import-ready" role="status"><Check size={18}/><span>Все операции подготовлены. Проверь суммы по группам.</span><button className="primary small" disabled={busy} onClick={confirm}>{busy?'Сохраняем…':'Подтвердить'}</button></div>}
  <div className="import-tabs" role="group" aria-label="Фильтр предпросмотра">{tabs.map(t=><button key={t.id} type="button" aria-pressed={filter===t.id} onClick={()=>setFilter(t.id)}>{t.label}<span>{t.count}</span></button>)}</div>
  <label className="import-search"><Search size={17}/><input aria-label="Поиск по названию операции" placeholder="Найти магазин или получателя" value={search} onChange={e=>setSearch(e.target.value)}/></label>
  <div className="import-groups">{groups.length?groups.map(group=>{
   const eligible=group.rows.filter(r=>bulkEligible(r,choices));
   const labels=[...new Set(group.rows.map(r=>decisionLabel(r,choices[r.id])))];
   return <details className="import-group" key={filter+group.id} open={filter==='attention'}>
    <summary><span className="import-group-icon"><ChevronDown size={17}/></span><span className="import-group-name"><b>{group.name}</b><small>{group.rows.length} операций · {group.incoming?'Поступления':'Списания'}{group.unresolved?` · Решить: ${group.unresolved}`:''}</small></span><span className="import-group-total"><strong>{group.incoming?'+':'−'}{won(group.amount)}</strong><small>{labels.length===1?labels[0]:'Разные решения'}</small></span></summary>
    <div className="import-group-body">
     {eligible.length>0&&<div className="import-bulk"><span>Для {eligible.length} новых операций группы:</span><div><button className="secondary small" disabled={busy} onClick={()=>bulk(group.rows,'bank_unclassified')}>{group.incoming?'Поступления, не доход':'Расходы'}</button><button className="secondary small" disabled={busy} onClick={()=>bulk(group.rows,'bank_transfer')}>Переводы</button>{group.incoming&&<button className="secondary small" disabled={busy} onClick={()=>bulk(group.rows,'income')}>Доходы</button>}</div>{!group.incoming&&<label className="category-picker">Категория новых расходов<select disabled={busy||!eligible.some(r=>choices[r.id].classification==='bank_unclassified')} aria-label={'Категория группы '+group.name} value={(()=>{const labels=[...new Set(eligible.filter(r=>choices[r.id].classification==='bank_unclassified').map(r=>choices[r.id].category||''))];return labels.length===1?labels[0]:''})()} onChange={e=>setCategory(group.rows,e.target.value)}><option value="">Выбрать категорию</option>{categories.map(c=><option key={c}>{c}</option>)}</select></label>}</div>}
     {group.rows.map(row=><details className="import-line" key={row.id} open={!choices[row.id]?.action}>
      <summary><span><b>{row.data.merchant||'Без описания'}</b><small>{date(row.data.occurred_at)}</small></span><span><strong>{row.data.amount>0?'+':'−'}{won(Math.abs(row.data.amount))}</strong><small className={!choices[row.id]?.action?'import-needs-review':''}>{decisionLabel(row,choices[row.id])}</small></span></summary>
      <div className="import-line-controls"><label>Что сделать с операцией<select disabled={busy} value={choices[row.id]?.action==='link'?'link:'+choices[row.id].purchase_id:choices[row.id]?.action==='repeat'?'repeat:'+choices[row.id].transaction_id:choices[row.id]?.action||''} onChange={e=>{const[action,id]=e.target.value.split(':');change(row.id,{action:action as Decision['action'],transaction_id:action==='repeat'?id:undefined,purchase_id:action==='link'?id:undefined})}}><option value="">Выбери действие</option><option value="new">Добавить отдельную операцию</option>{row.candidates.map(t=><option key={t.id} disabled={t.amount!==row.data.amount||t.occurred_at!==row.data.occurred_at} value={'repeat:'+t.id}>Повтор: {t.merchant||'Без описания'} · {date(t.occurred_at)} · {won(t.amount)} · остаток {won(t.balance)}</option>)}{row.purchase_candidates.map(p=><option key={p.id} value={'link:'+p.id}>Связать с чеком: {p.merchant} · {date(p.occurred_at)} · {won(p.total)}{p.merchant_match?'':' · магазин отличается'}</option>)}</select></label>
       {choices[row.id]?.action==='new'&&<label>Как учитывать<select disabled={busy} value={choices[row.id].classification} onChange={e=>change(row.id,{classification:e.target.value as Classification})}><option value="bank_unclassified">{row.data.amount<0?'Расход без категории':'Поступление, не доход'}</option><option value="bank_transfer">Перевод без расхода и дохода</option>{row.data.amount>0&&<option value="income">Обычный доход</option>}</select></label>}
       {choices[row.id]?.action==='new'&&choices[row.id].classification==='bank_unclassified'&&row.data.amount<0&&<label>Категория<select value={choices[row.id].category||''} disabled={busy} onChange={e=>change(row.id,{category:e.target.value})}><option value="">Без категории</option>{categories.map(c=><option key={c}>{c}</option>)}</select></label>}
       <details className="hint"><summary>Данные из PDF · № {row.number}</summary><p>Остаток после операции: {won(row.data.balance)}</p><pre className="bank-source">{row.data.raw}</pre></details>
      </div>
     </details>)}
    </div>
   </details>
  }):<p className="import-empty">{filter==='attention'&&!summary.unresolved?'Спорных операций не осталось. Можно подтвердить импорт.':'По этому фильтру ничего не найдено.'}</p>}</div>
  <div className="import-confirm"><span>{summary.unresolved?`Осталось решить: ${summary.unresolved}`:`Готово: ${batch.rows.length} операций`}</span><button className="primary" disabled={busy||summary.unresolved>0} onClick={confirm}>{busy?'Сохраняем…':summary.added+summary.linked>0?`Импортировать ${summary.added+summary.linked} операций`:'Подтвердить повторы'}</button></div>
 </div>
}
