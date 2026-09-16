(() => {
  for (const eventName of ['aimeton:analysis-complete', 'aimeton:analysis-partial']) {
  window.addEventListener(eventName, event => {
    if (!event.detail?.result) return;
    document.querySelector('#resultInner')?.setAttribute('hidden', '');
  });
  }
})();
