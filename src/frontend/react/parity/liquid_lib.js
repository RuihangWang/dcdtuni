// 逐控件驱动脚本：对每个控件施加一个已知改动，回读 runDefaults.liquid + fluids
(function(){
  // headless: document.hasFocus()===false => focus()/blur() 不派发任何事件
  function setNative(el, v){
    var proto = el.tagName === 'SELECT' ? window.HTMLSelectElement.prototype : window.HTMLInputElement.prototype;
    el.dispatchEvent(new FocusEvent('focusin',{bubbles:true}));
    Object.getOwnPropertyDescriptor(proto,'value').set.call(el, String(v));
    el.dispatchEvent(new Event('input',{bubbles:true}));
    el.dispatchEvent(new Event('change',{bubbles:true}));
    el.dispatchEvent(new FocusEvent('focusout',{bubbles:true}));
  }
  window.__lqSet = function(id, v){
    var el = document.getElementById(id);
    if(!el) return 'MISSING:'+id;
    setNative(el, v);
    return 'ok';
  };
  window.__lqRead = function(st){
    var liq = ((st.runDefaults||{}).liquid)||{};
    var s = liq.solver||{};
    var fl = ((((st.models||{}).materialModels)||{}).fluids)||{};
    var f = Object.values(fl)[0]||{};
    return {
      dt_s:s.dt_s, N_t:s.N_t, bdf_order:s.bdf_order, convergence_tol:s.convergence_tol,
      device:s.device, fluidKeys:Object.keys(fl),
      f_type:f.type, f_rho:f.rho, f_cp:f.cp, f_mu:f.mu, f_k:f.k
    };
  };
})();
