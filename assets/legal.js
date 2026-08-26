(()=>{
  const buttons=[...document.querySelectorAll('.ls-btn')];
  const blocks=[...document.querySelectorAll('.lang-block')];
  const progress=document.querySelector('.progress');
  const readLanguage=()=>{try{return localStorage.getItem('ck-lang')||'ru'}catch{return 'ru'}};
  const storeLanguage=value=>{try{localStorage.setItem('ck-lang',value)}catch{}}
  const setLanguage=language=>{
    const next=language==='en'?'en':'ru';
    blocks.forEach(block=>{block.hidden=block.dataset.lang!==next});
    buttons.forEach(button=>{const active=button.dataset.l===next;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active))});
    document.documentElement.lang=next;
    storeLanguage(next);
  };
  buttons.forEach(button=>button.addEventListener('click',()=>setLanguage(button.dataset.l)));
  const updateProgress=()=>{
    if(!progress)return;
    const max=document.documentElement.scrollHeight-innerHeight;
    progress.style.transform=`scaleX(${max>0?Math.min(1,scrollY/max):0})`;
  };
  addEventListener('scroll',updateProgress,{passive:true});
  addEventListener('resize',updateProgress,{passive:true});
  setLanguage(readLanguage());
  updateProgress();
})();
