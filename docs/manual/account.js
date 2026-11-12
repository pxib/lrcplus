/* Fictional account forms. Validation is local; nothing is saved or sent. */
'use strict';
(() => {
  const japanese = document.documentElement.lang === 'ja';
  const korean = document.documentElement.lang === 'ko';
  const cantonese = document.documentElement.lang.startsWith('yue');
  const manual = cantonese ? 'yue.html' : korean ? 'ko.html' : japanese ? 'ja.html' : 'index.html';
  const requestedReturn = new URLSearchParams(location.search).get('return');
  const returnHash = requestedReturn && requestedReturn.startsWith('#') ? requestedReturn : '#main';
  const forms = [...document.querySelectorAll('.account-form')];
  const errors = {
    credentials: cantonese ? '無法驗證輸入嘅登入資料。' : korean ? '입력한 인증 정보를 확인할 수 없습니다.' : japanese
      ? '入力された認証情報を認証できませんでした。'
      : 'The supplied credentials could not be authenticated.',
    login: cantonese ? '登入失敗。成個帳戶部門得 Ayami 一個，而佢仲未整好帳戶功能。' : korean ? '로그인에 실패했습니다. 계정 담당자는 Ayami 한 명뿐인데 아직 계정 기능을 구현하지 않았습니다.' : japanese
      ? 'ログインできませんでした。アカウント窓口は Ayami 一人です。しかも、まだアカウント機能を実装していません。'
      : 'Log in failed. Ayami is the entire account department. She has not implemented accounts yet.',
    'create-account': cantonese ? '建立帳戶失敗。Ayami 將帳戶資料庫歸類為「將來嘅我先處理」。' : korean ? '계정을 만들지 못했습니다. Ayami가 계정 데이터베이스를 “미래의 내가 할 일”로 분류했습니다.' : japanese
      ? 'アカウントを作成できませんでした。Ayami がアカウントデータベースを「未来の私の仕事」に分類しました。'
      : 'Account creation failed. Ayami filed the account database under “a future me problem”.'
  };
  const loadingMessages = {
    login: cantonese ? '登入緊…' : korean ? '로그인 중…' : japanese ? 'ログイン中…' : 'Logging in…',
    'create-account': cantonese ? '建立緊帳戶…' : korean ? '계정 만드는 중…' : japanese ? 'アカウントを作成中…' : 'Creating account…'
  };
  const states = new Map();

  function setLoading(form, loading) {
    const state = states.get(form);
    const mode = form.id === 'login-form' ? 'login' : 'create-account';
    form.setAttribute('aria-busy', String(loading));
    form.querySelectorAll('input, button').forEach(control => { control.disabled = loading; });
    state.button.textContent = loading ? loadingMessages[mode] : state.label;
    state.status.textContent = loading ? loadingMessages[mode] : '';
  }

  function cancelLoading(form) {
    const state = states.get(form);
    if (state.timer !== null) window.clearTimeout(state.timer);
    state.timer = null;
    setLoading(form, false);
  }

  // Bind cancellation before enabling any controls. method="dialog" and the
  // absence of field names also prevent a native form submission from sending data.
  for (const form of forms) {
    const button = form.querySelector('.account-submit');
    // Keep the live announcement outside the busy form so it is read immediately.
    const status = document.createElement('p');
    status.className = 'visually-hidden';
    status.setAttribute('role', 'status');
    form.before(status);
    const state = {button, label: button.textContent, status, timer: null};
    states.set(form, state);
    form.addEventListener('submit', event => {
      event.preventDefault();
      if (state.timer !== null) return;
      const mode = form.id === 'login-form' ? 'login' : 'create-account';
      const incompleteForm = [...form.querySelectorAll('input[required]')]
        .some(input => input.validity.valueMissing);
      const mismatchedPasswords = mode === 'create-account' &&
        form.querySelector('#create-account-password').value !== form.querySelector('#create-account-confirm').value;
      const error = document.getElementById(`${mode}-error`);
      error.hidden = true;
      setLoading(form, true);
      state.timer = window.setTimeout(() => {
        state.timer = null;
        setLoading(form, false);
        error.textContent = incompleteForm || mismatchedPasswords ? errors.credentials : errors[mode];
        error.hidden = false;
        error.focus();
      }, 1000);
    });
    setLoading(form, false);
  }

  function render() {
    const modes = ['login', 'create-account', 'password-reset'];
    const requestedMode = location.hash.slice(1);
    const mode = modes.includes(requestedMode) ? requestedMode : 'login';
    forms.forEach(form => { cancelLoading(form); form.reset(); });
    for (const name of modes) {
      document.getElementById(`${name}-panel`).hidden = mode !== name;
      if (name !== 'password-reset') document.getElementById(`${name}-error`).hidden = true;
    }
    const title = document.getElementById(`${mode}-heading`).textContent;
    document.title = `${title} — ${cantonese ? 'LRCPlus 使用手冊' : korean ? 'LRCPlus 설명서' : japanese ? 'LRCPlus マニュアル' : 'LRCPlus manual'}`;
    document.getElementById('special-tab').href = `#${mode}`;
    document.getElementById('manual-return').href = manual + returnHash;
    document.querySelectorAll('.account-links a, .language-switch a').forEach(link => {
      const url = new URL(link.href);
      url.searchParams.set('return', returnHash);
      if (link.closest('.language-switch')) url.hash = mode;
      else if (url.hash === `#${mode}`) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
      link.href = url.href;
    });
  }

  window.addEventListener('hashchange', () => {
    if (['#login', '#create-account', '#password-reset'].includes(location.hash)) render();
  });
  window.addEventListener('pageshow', render);
  window.addEventListener('pagehide', () => {
    forms.forEach(form => { cancelLoading(form); form.reset(); });
  });
  render();
})();
