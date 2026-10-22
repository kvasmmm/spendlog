import { useEffect, useRef, type ReactNode } from 'react';
import {X, PackageOpen} from 'lucide-react';
export function Modal({title,children,close}:{title:string;children:ReactNode;close:()=>void}){
 const ref=useRef<HTMLDialogElement>(null);
 useEffect(()=>{ref.current?.showModal();const previous=document.body.style.overflow;document.body.style.overflow='hidden';return()=>{document.body.style.overflow=previous}},[]);
 return <dialog ref={ref} onCancel={e=>{e.preventDefault();close()}} className="modal"><header><h2>{title}</h2><button type="button" className="icon" aria-label="Закрыть" onClick={close}><X/></button></header>{children}</dialog>
}
export function Empty({title,text,action}:{title:string;text:string;action?:ReactNode}){return <div className="empty"><PackageOpen size={34}/><h3>{title}</h3><p>{text}</p>{action}</div>}
export function Field({label,children}:{label:string;children:ReactNode}){return <label className="field"><span>{label}</span>{children}</label>}
