import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './style.css';
import './palette.css';
class AppBoundary extends React.Component<{children:React.ReactNode},{failed:boolean}>{
 state={failed:false};
 static getDerivedStateFromError(){return {failed:true}}
 render(){return this.state.failed?<div className="login-page"><section className="login-card" role="alert"><h2>Не удалось открыть экран</h2><p>Обнови страницу и попробуй снова. Незавершённый ввод мог не сохраниться.</p><button className="primary full" onClick={()=>window.location.reload()}>Обновить страницу</button></section></div>:this.props.children}
}
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><AppBoundary><App/></AppBoundary></React.StrictMode>);
if('serviceWorker' in navigator) window.addEventListener('load',()=>{navigator.serviceWorker.register('/sw.js').catch(()=>{})});
