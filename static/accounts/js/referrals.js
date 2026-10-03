(() => {
  const codeNode = document.getElementById('ref-code-data');
  if (!codeNode) return;
  const code = JSON.parse(codeNode.textContent);
  const shareText = JSON.parse(document.getElementById('ref-share-data').textContent);
  const feedback = document.querySelector('[data-ref-feedback]');
  const report = text => { feedback.textContent = text; };
  async function copy(text, success) {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(text);
      report(success);
    } catch (_) {
      const input = document.createElement('textarea');
      input.value = text;
      input.setAttribute('aria-label', 'Текст для копирования');
      input.style.cssText = 'position:fixed;left:0;top:0;opacity:0;pointer-events:none';
      const previous = document.activeElement;
      document.body.append(input);
      input.select();
      let copied = false;
      try { copied = document.execCommand('copy'); } catch (_) { /* Manual fallback below. */ }
      input.remove();
      previous?.focus();
      if (copied) report(success);
      else {
        const visibleCode = document.getElementById('ref-code');
        const range = document.createRange();
        range.selectNodeContents(visibleCode);
        const selection = window.getSelection();
        selection.removeAllRanges(); selection.addRange(range);
        visibleCode.focus();
        report('Не удалось скопировать автоматически. Код выделен — скопируйте его вручную.');
      }
    }
  }
  document.querySelector('[data-ref-actions]').hidden = false;
  document.querySelector('[data-ref-copy]').addEventListener('click', () => copy(code, 'Код скопирован'));
  document.querySelector('[data-ref-share]').addEventListener('click', async () => {
    if (navigator.share) {
      try { await navigator.share({text: shareText}); report('Готово'); return; }
      catch (error) { if (error.name === 'AbortError') return; }
    }
    await copy(shareText, 'Текст приглашения скопирован');
  });
})();
