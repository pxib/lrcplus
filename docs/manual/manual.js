/* Classic scripts and hash routes deliberately support opening via file://. */
'use strict';
(() => {
  const japanese = document.documentElement.lang === 'ja';
  const korean = document.documentElement.lang === 'ko';
  const cantonese = document.documentElement.lang.startsWith('yue');
  const messages = cantonese ? {
    contents: '目錄', related: '相關文章', home: '首頁', all: '所有文章',
    search: '搜尋', article: '文章', manual: 'LRCPlus 使用手冊', source: '檢視原始碼',
    permalink: '呢一節嘅固定連結', searchPrompt: '輸入字詞或者片語，搜尋所有文章。'
  } : korean ? {
    contents: '목차', related: '관련 문서', home: '메인 페이지', all: '모든 문서',
    search: '검색', article: '문서', manual: 'LRCPlus 설명서', source: '원문 보기',
    permalink: '이 절의 고유 링크', searchPrompt: '단어나 구절을 입력하여 모든 문서를 검색하세요.'
  } : japanese ? {
    contents: '目次', related: '関連記事', home: 'メインページ', all: '記事一覧',
    search: '検索', article: '記事', manual: 'LRCPlus マニュアル', source: 'ソースを表示',
    permalink: 'この節への固定リンク', searchPrompt: '語句を入力して、すべての記事を検索できます。'
  } : {
    contents: 'Contents', related: 'Related articles', home: 'Main page', all: 'All articles',
    search: 'Search', article: 'Article', manual: 'LRCPlus manual', source: 'View source',
    permalink: 'Permanent link to this section', searchPrompt: 'Enter a word or phrase to search all articles.'
  };
  const articles = [...document.querySelectorAll('#articles > article')];
  const byId = new Map(articles.map(article => [article.id, article]));
  // Capture authored markup before adding contents, permalinks, or related links.
  const sources = new Map(articles.map(article => [article.id, article.outerHTML]));
  const searchPanel = document.getElementById('search-results');
  const searchForm = document.getElementById('search');
  const queryInput = document.getElementById('query');
  const articleTab = document.getElementById('article-tab');
  const contentsTab = document.getElementById('contents-tab');
  const talkTab = document.getElementById('talk-tab');
  const talkPanel = document.getElementById('talk-panel');
  const talkPages = new Map([...document.querySelectorAll('template[data-talk-for]')]
    .map(template => [template.dataset.talkFor, template]));
  const talkLabel = cantonese ? '討論' : korean ? '토론' : japanese ? 'ノート' : 'Talk';
  function talkLoginLink(returnHash) {
    const url = new URL(cantonese ? 'yue-account.html' : korean ? 'ko-account.html' : japanese ? 'ja-account.html' : 'account.html', location.href);
    url.searchParams.set('return', returnHash);
    url.hash = 'login';
    return url.href;
  }

  function decorateComment(comment, articleId, index, date) {
    comment.id = `talk-comment-${articleId}-${index + 1}`;
    const hash = `#${articleId}/talk/${comment.id}`;
    const permalink = new URL(location.href);
    permalink.hash = hash;
    const signature = comment.querySelector('.talk-signature');
    const time = document.createElement('time');
    time.dateTime = date.toISOString();
    time.textContent = window.ManualTalkTimestamps.format(date, document.documentElement.lang);
    time.title = cantonese ? '虛構討論時間' : korean ? '가상 토론의 날짜와 시간' : japanese ? '架空の会話の日時' : 'Fictional discussion timestamp';
    const timestamp = document.createElement('a');
    timestamp.href = permalink.href;
    timestamp.append(time);
    signature.append(' ', timestamp);

    const actions = document.createElement('div');
    actions.className = 'talk-actions';
    const reply = document.createElement('a');
    reply.href = talkLoginLink(hash);
    reply.textContent = cantonese ? '回覆' : korean ? '답글' : japanese ? '返信' : 'Reply';
    const menu = document.createElement('details');
    menu.className = 'talk-comment-menu';
    const summary = document.createElement('summary');
    summary.textContent = '•••';
    summary.setAttribute('aria-label', cantonese ? '更多留言操作' : korean ? '댓글 추가 작업' : japanese ? 'コメントのその他の操作' : 'More comment options');
    const popup = document.createElement('div');
    popup.className = 'talk-comment-popup';
    const copy = document.createElement('button');
    copy.type = 'button';
    copy.textContent = cantonese ? '複製連結' : korean ? '링크 복사' : japanese ? 'リンクをコピー' : 'Copy link';
    const status = document.createElement('span');
    status.className = 'talk-copy-status';
    status.setAttribute('role', 'status');
    copy.addEventListener('click', async () => {
      let copied = false;
      try {
        await navigator.clipboard.writeText(permalink.href);
        copied = true;
      } catch {
        // Local file pages may not expose the Clipboard API.
        const field = document.createElement('textarea');
        field.value = permalink.href;
        field.readOnly = true;
        field.className = 'visually-hidden';
        popup.append(field);
        field.select();
        try { copied = document.execCommand('copy'); } catch { /* Offer manual copy below. */ }
        field.remove();
        copy.focus({preventScroll: true});
      }
      status.textContent = copied ? (cantonese ? '已複製連結。' : korean ? '링크를 복사했습니다.' : japanese ? 'コピーしました。' : 'Link copied.')
        : (cantonese ? '請選取下面嘅連結再複製。' : korean ? '아래 링크를 선택하여 복사하세요.' : japanese ? 'リンクを選択してコピーしてください。' : 'Select the link below to copy it.');
      if (!copied && !popup.querySelector('input')) {
        const field = document.createElement('input');
        field.type = 'text';
        field.readOnly = true;
        field.value = permalink.href;
        field.setAttribute('aria-label', cantonese ? '留言固定連結' : korean ? '댓글 고유 링크' : japanese ? 'コメントの固定リンク' : 'Comment permalink');
        popup.append(field);
        field.focus();
        field.select();
      }
    });
    popup.append(copy, status);
    menu.append(summary, popup);
    menu.addEventListener('toggle', () => {
      if (menu.open) document.querySelectorAll('.talk-comment-menu[open]').forEach(other => {
        if (other !== menu) other.open = false;
      });
    });
    menu.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        menu.open = false;
        summary.focus();
        event.stopPropagation();
      }
    });
    actions.append(reply, menu);
    comment.append(actions);
  }
  document.addEventListener('click', event => {
    document.querySelectorAll('.talk-comment-menu[open]').forEach(menu => {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });
  const readTab = document.getElementById('read-tab');
  const sourceTab = document.getElementById('source-tab');
  const sourcePanel = document.getElementById('source-panel');
  const content = document.getElementById('content');
  const index = articles.map(article => ({
    id: article.id, title: article.dataset.title,
    text: article.innerText.replace(/\s+/g, ' ').trim()
  }));
  const relatedPages = {
    'getting-started': 'interface playback finding-lyrics',
    playback: 'interface settings-reference shortcuts',
    'finding-lyrics': 'translations editor troubleshooting',
    readings: 'settings-reference tutorials glossary',
    editor: 'interface tutorials sharing',
    lrcx: 'editor readings translations',
    customization: 'settings-reference sharing companion',
    plugins: 'plugin-directory troubleshooting',
    troubleshooting: 'settings-reference interface plugin-directory',
    shortcuts: 'interface editor playback'
  };

  for (const article of articles) {
    const headings = [...article.querySelectorAll('h2[id]')];
    if (!headings.length || article.id === 'main') continue;
    const toc = document.createElement('details');
    toc.className = 'toc';
    toc.id = `${article.id}-contents`;
    toc.open = true;
    const summary = document.createElement('summary');
    summary.textContent = messages.contents;
    const list = document.createElement('ol');
    for (const heading of headings) {
      const item = document.createElement('li');
      const link = document.createElement('a');
      link.href = `#${article.id}/${heading.id}`;
      link.textContent = heading.textContent;
      item.append(link);
      list.append(item);
    }
    toc.append(summary, list);
    if (article.dataset.toc === 'after-lead') headings[0].before(toc);
    else article.querySelector('.subtitle').after(toc);
  }

  for (const article of articles) {
    for (const heading of article.querySelectorAll('h2[id]')) {
      const link = document.createElement('a');
      link.className = 'section-link';
      link.href = `#${article.id}/${heading.id}`;
      link.textContent = '§';
      link.setAttribute('aria-label', `${messages.permalink}: ${heading.textContent}`);
      link.title = messages.permalink;
      heading.append(' ', link);
    }
    const related = (article.dataset.related || relatedPages[article.id] || '')
      .split(' ').filter(id => byId.has(id));
    if (!related.length) continue;
    const nav = document.createElement('nav');
    nav.className = 'related-articles';
    nav.setAttribute('aria-label', messages.related);
    const label = document.createElement('strong');
    label.textContent = messages.related;
    const list = document.createElement('ul');
    for (const id of related) {
      const item = document.createElement('li');
      const link = document.createElement('a');
      link.href = `#${id}`;
      link.textContent = byId.get(id).dataset.title;
      item.append(link);
      list.append(item);
    }
    nav.append(label, list);
    article.append(nav);
  }

  function showBreadcrumbs(article, searching, viewingTalk = false) {
    const nav = document.getElementById('breadcrumbs');
    nav.replaceChildren();
    nav.hidden = !searching && !viewingTalk && article.id === 'main';
    if (nav.hidden) return;
    const home = document.createElement('a');
    home.href = '#main';
    home.textContent = messages.home;
    nav.append(home, ' › ');
    if (!searching && article.id !== 'article-index') {
      const indexLink = document.createElement('a');
      indexLink.href = '#article-index';
      indexLink.textContent = messages.all;
      nav.append(indexLink, ' › ');
    }
    const current = document.createElement('span');
    current.setAttribute('aria-current', 'page');
    current.textContent = searching ? messages.search : article.dataset.title;
    nav.append(current);
  }

  function showSearch(query) {
    searchPanel.hidden = false;
    queryInput.value = query;
    document.title = `${messages.search} — ${messages.manual}`;
    const words = query.toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const matches = words.length ? index.filter(page =>
      words.every(word => page.text.toLocaleLowerCase().includes(word))
    ).sort((a, b) => Number(b.title.toLowerCase().includes(query.toLowerCase())) -
      Number(a.title.toLowerCase().includes(query.toLowerCase()))) : [];
    document.getElementById('search-summary').textContent = words.length
      ? cantonese
        ? `「${query}」搵到 ${matches.length} 篇文章。${matches.length ? '' : '試吓減少搜尋字詞，或者用導覽搵文章。'}`
        : korean
        ? `“${query}” 검색 결과: 문서 ${matches.length}개.${matches.length ? '' : ' 검색어를 줄이거나 탐색 메뉴에서 문서를 선택하세요.'}`
        : japanese
        ? `「${query}」に一致する記事は ${matches.length} 件です。${matches.length ? '' : '検索語を減らすか、ナビゲーションから記事を選んでください。'}`
        : `${matches.length} article${matches.length === 1 ? '' : 's'} found for “${query}”.${matches.length ? '' : ' Try fewer words, or browse the navigation.'}`
      : messages.searchPrompt;
    const results = document.getElementById('results-list');
    results.replaceChildren();
    for (const match of matches) {
      const section = document.createElement('section');
      section.className = 'result';
      const heading = document.createElement('h2');
      const link = document.createElement('a');
      link.href = `#${match.id}`;
      link.textContent = match.title;
      heading.append(link);
      const excerpt = document.createElement('p');
      const firstMatch = match.text.toLocaleLowerCase().indexOf(words[0]);
      const start = Math.max(0, firstMatch - 65);
      excerpt.textContent = (start ? '…' : '') + match.text.slice(start, start + 240) +
        (start + 240 < match.text.length ? '…' : '');
      section.append(heading, excerpt);
      results.append(section);
    }
  }

  function render() {
    let route;
    try { route = decodeURIComponent(location.hash.slice(1)); }
    catch { route = 'main'; }
    const [pageId, anchor, topic] = route.split('/');
    const searching = pageId === 'search';
    const article = byId.get(pageId) || byId.get('main');
    const viewingSource = !searching && anchor === 'source';
    const viewingTalk = !searching && anchor === 'talk';
    articles.forEach(page => { page.hidden = searching || viewingSource || viewingTalk || page !== article; });
    searchPanel.hidden = !searching;
    sourcePanel.hidden = !viewingSource;
    talkPanel.hidden = !viewingTalk;
    document.querySelectorAll('.sidebar nav:not(.language-switch) a').forEach(link => {
      if (!searching && link.hash === `#${article.id}`) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    articleTab.textContent = searching ? messages.search : article.id === 'main' ? messages.home : messages.article;
    articleTab.href = searching ? location.hash : `#${article.id}`;
    talkTab.hidden = searching;
    talkTab.href = `#${article.id}/talk`;
    for (const [tab, active] of [[articleTab, !viewingTalk], [talkTab, viewingTalk]]) {
      if (active) tab.setAttribute('aria-current', 'page');
      else tab.removeAttribute('aria-current');
    }
    readTab.href = viewingTalk ? talkTab.href : articleTab.href;
    sourceTab.href = `#${article.id}/source`;
    sourceTab.hidden = searching || viewingTalk;
    for (const [tab, active] of [[readTab, !viewingSource], [sourceTab, viewingSource]]) {
      if (active) tab.setAttribute('aria-current', 'page');
      else tab.removeAttribute('aria-current');
    }
    contentsTab.hidden = searching || viewingSource || viewingTalk;
    showBreadcrumbs(article, searching, viewingTalk);
    if (viewingTalk) {
      const current = document.querySelector('#breadcrumbs [aria-current="page"]');
      if (current) current.textContent = `${talkLabel}: ${article.dataset.title}`;
    }
    contentsTab.href = `#${article.id}/${article.id === 'main' ? 'main-contents' : article.id + '-contents'}`;
    if (searching) showSearch(route.slice('search/'.length));
    else if (viewingTalk) {
      document.title = `${talkLabel}: ${article.dataset.title} — ${messages.manual}`;
      document.getElementById('talk-heading').textContent = `${talkLabel}: ${article.dataset.title}`;
      const body = document.getElementById('talk-body');
      body.replaceChildren();
      const template = talkPages.get(article.id);
      if (template) {
        body.append(template.content.cloneNode(true));
        const toc = document.createElement('details');
        toc.className = 'toc';
        toc.id = 'talk-contents';
        toc.open = true;
        const summary = document.createElement('summary');
        summary.textContent = messages.contents;
        const list = document.createElement('ol');
        for (const heading of body.querySelectorAll('h2[id]')) {
          const item = document.createElement('li');
          const link = document.createElement('a');
          link.href = `#${article.id}/talk/${heading.id}`;
          link.textContent = heading.textContent;
          item.append(link);
          list.append(item);
          const permalink = link.cloneNode();
          permalink.className = 'section-link';
          permalink.textContent = '§';
          permalink.setAttribute('aria-label', `${messages.permalink}: ${heading.textContent}`);
          heading.append(' ', permalink);
        }
        toc.append(summary, list);
        body.prepend(toc);
      } else {
        const empty = document.createElement('p');
        empty.className = 'note';
        empty.textContent = cantonese ? '呢篇文章暫時未收錄粵語討論。' : korean ? '이 문서에는 아직 한국어 토론이 수록되어 있지 않습니다.' : japanese ? 'この記事には、まだ収録された議論がありません。' : 'No discussions have been included for this article yet.';
        body.append(empty);
      }
      const articleLink = document.getElementById('talk-article-link');
      articleLink.href = articleTab.href;
      articleLink.textContent = article.dataset.title;
      const browseLink = document.getElementById('talk-browse-link');
      browseLink.hidden = false;
      browseLink.href = talkLoginLink(`#${article.id}/talk`);
      const latest = document.getElementById('talk-latest');
      latest.replaceChildren();
      const comments = [...body.querySelectorAll('.talk-comment')];
      const dates = window.ManualTalkTimestamps.dates(comments, article.id);
      comments.forEach((comment, index) => {
        decorateComment(comment, article.id, index, dates[index]);
      });
      const comment = comments.at(-1);
      if (comment) {
        const heading = comment.closest('.talk-topic').querySelector('h2');
        const author = comment.querySelector('.talk-signature b').textContent;
        const commentLink = document.createElement('a');
        commentLink.href = `#${article.id}/talk/${comment.id}`;
        commentLink.textContent = cantonese ? '最後收錄嘅留言' : korean ? '마지막 수록 댓글' : japanese ? '最後に収録されたコメント' : 'Latest included comment';
        const topicLink = document.createElement('a');
        topicLink.href = `#${article.id}/talk/${heading.id}`;
        topicLink.textContent = heading.firstChild.textContent;
        latest.append(commentLink, cantonese ? `：${author}，話題「` : korean ? `: ${author}, 주제 “` : japanese ? `：${author}、議題「` : `: by ${author} in topic `,
          topicLink, cantonese ? '」' : korean ? '”' : japanese ? '」' : '');
      } else {
        latest.textContent = cantonese ? '呢篇文章暫時未收錄留言。' : korean ? '이 문서에는 아직 수록된 댓글이 없습니다.' : japanese ? 'この記事には、まだコメントが収録されていません。' : 'No comments included for this article yet.';
      }
    } else if (viewingSource) {
      document.title = `${messages.source}: ${article.dataset.title} — ${messages.manual}`;
      document.getElementById('source-heading').textContent = `${messages.source}: ${article.dataset.title}`;
      document.getElementById('source-code').textContent = sources.get(article.id);
    } else document.title = `${article.dataset.title} — ${messages.manual}`;
    document.querySelectorAll('.language-switch a').forEach(link => {
      link.hash = location.hash || '#main';
    });
    document.querySelectorAll('.account-links a').forEach(link => {
      const url = new URL(link.href);
      url.searchParams.set('return', location.hash || '#main');
      link.href = url.href;
    });
    content.focus({preventScroll: true});
    if (viewingTalk) {
      const target = topic && document.getElementById(topic);
      if (target && talkPanel.contains(target)) target.scrollIntoView();
      else window.scrollTo(0, 0);
    } else if (anchor && !searching && !viewingSource) {
      const target = document.getElementById(anchor);
      if (target && article.contains(target)) target.scrollIntoView();
      else window.scrollTo(0, 0);
    } else window.scrollTo(0, 0);
  }

  function setupAyamiNote() {
    const heading = document.querySelector('#ayami .infobox-above');
    if (!heading) return;
    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'ayami-name';
    trigger.textContent = 'Ayami';
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-controls', 'ayami-editorial-note');
    heading.firstChild.replaceWith(trigger);

    const row = document.createElement('tr');
    row.id = 'ayami-editorial-note';
    row.hidden = true;
    const cell = document.createElement('td');
    cell.colSpan = 2;
    cell.className = 'ayami-editorial-note';
    const message = document.createElement('p');
    message.setAttribute('role', 'status');
    const dismiss = document.createElement('button');
    dismiss.type = 'button';
    dismiss.textContent = cantonese ? '關閉' : korean ? '닫기' : japanese ? '閉じる' : 'Dismiss';
    cell.append(message, dismiss);
    row.append(cell);
    heading.parentElement.after(row);

    let clicks = 0;
    function closeNote() {
      row.hidden = true;
      message.textContent = '';
      trigger.setAttribute('aria-expanded', 'false');
      clicks = 0;
      trigger.focus({preventScroll: true});
    }
    trigger.addEventListener('click', () => {
      if (!row.hidden || ++clicks < 5) return;
      row.hidden = false;
      trigger.setAttribute('aria-expanded', 'true');
      message.textContent = cantonese
        ? '呢篇文係我自己寫嘅。開發者刪咗「才華橫溢」三次。'
        : korean
        ? '이 문서는 내가 직접 썼어. 개발자가 “뛰어난 재능”이라는 말을 세 번이나 지웠다니까.'
        : japanese
        ? 'この記事は私が書きました。「類まれな才能」って書いたら、開発者に3回消されました。'
        : 'I wrote this article myself. The developer removed “exceptionally talented” three times.';
    });
    dismiss.addEventListener('click', closeNote);
    heading.closest('table').addEventListener('keydown', event => {
      if (event.key === 'Escape' && !row.hidden) {
        event.preventDefault();
        closeNote();
      }
    });
  }

  function setupZeroGravity() {
    const sequence = ['arrowup', 'arrowup', 'arrowdown', 'arrowdown',
      'arrowleft', 'arrowright', 'arrowleft', 'arrowright', 'b', 'a'];
    let progress = [];
    let lastKey = 0;
    let stop = null;

    function launch() {
      if (stop) return;
      const previousFocus = document.activeElement;
      const layer = document.createElement('div');
      layer.className = 'zero-gravity';
      layer.setAttribute('role', 'dialog');
      layer.setAttribute('aria-modal', 'true');
      layer.setAttribute('aria-label', cantonese ? '無重力模式' : korean ? '무중력 모드' : japanese ? '無重力モード' : 'Zero gravity');
      const controls = document.createElement('div');
      controls.className = 'zero-gravity-controls';
      const hint = document.createElement('span');
      hint.textContent = cantonese ? '無重力！試吓拖住啲字再掟出去。' : korean ? '무중력! 단어를 끌어서 던져 보세요.' : japanese ? '無重力！ 単語をドラッグして投げてみよう。' : 'Zero gravity! Drag and toss the words.';
      const restore = document.createElement('button');
      restore.type = 'button';
      restore.textContent = cantonese ? '恢復重力（Esc）' : korean ? '중력 복원 (Esc)' : japanese ? '重力を戻す (Esc)' : 'Restore gravity (Esc)';
      controls.append(hint, restore);
      const particles = [];
      const originals = [];
      const bounds = {
        width: Math.max(document.documentElement.scrollWidth, innerWidth),
        height: Math.max(document.documentElement.scrollHeight, innerHeight)
      };
      const segmenter = typeof Intl.Segmenter === 'function'
        ? new Intl.Segmenter(document.documentElement.lang, {granularity: 'word'}) : null;
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
        acceptNode(node) {
          if (!node.textContent.trim() || node.parentElement.closest(
            'script, style, noscript, svg, textarea, select, [hidden], [inert], .visually-hidden'
          )) return NodeFilter.FILTER_REJECT;
          const style = getComputedStyle(node.parentElement);
          return style.visibility === 'visible' && style.display !== 'none'
            ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
        }
      });
      // Measure before changing any markup so each word lifts from its printed position.
      while (walker.nextNode()) {
        const node = walker.currentNode;
        const style = getComputedStyle(node.parentElement);
        const segments = segmenter ? segmenter.segment(node.textContent)
          : [...node.textContent.matchAll(/\S+/gu)].map(match => ({segment: match[0], index: match.index}));
        let lifted = false;
        for (const {segment, index} of segments) {
          if (!segment.trim()) continue;
          const range = document.createRange();
          range.setStart(node, index);
          range.setEnd(node, index + segment.length);
          const rect = range.getBoundingClientRect();
          if (!rect.width || !rect.height) continue;
          const word = document.createElement('span');
          word.className = 'zero-gravity-word';
          word.textContent = segment;
          word.setAttribute('aria-hidden', 'true');
          Object.assign(word.style, {
            font: style.font, color: style.color, letterSpacing: style.letterSpacing,
            textDecoration: style.textDecoration, width: `${rect.width}px`, height: `${rect.height}px`
          });
          const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
          particles.push({word, x: rect.left + scrollX, y: rect.top + scrollY,
            width: rect.width, height: rect.height,
            vx: reduced ? 0 : (Math.random() - .5) * 12,
            vy: reduced ? 0 : (Math.random() - .5) * 12, angle: 0,
            spin: reduced ? 0 : (Math.random() - .5) * 1.2});
          layer.append(word);
          lifted = true;
        }
        if (lifted) originals.push({node, wrapper: document.createElement('span')});
      }
      for (const {node, wrapper} of originals) {
        wrapper.className = 'zero-gravity-original';
        node.replaceWith(wrapper);
        wrapper.append(node);
      }
      const inertStates = [...document.body.children].map(element => [element, element.inert]);
      inertStates.forEach(([element]) => { element.inert = true; });
      layer.append(controls);
      document.body.append(layer);
      let frame;
      let previousTime = performance.now();
      let drag = null;
      const byWord = new Map(particles.map(particle => [particle.word, particle]));
      function release(event) {
        if (!drag || (event && event.pointerId !== drag.id)) return;
        const {particle, id, time} = drag;
        drag = null;
        if (!event || event.type === 'pointercancel' || performance.now() - time > 120) {
          particle.vx = particle.vy = 0;
        }
        particle.word.classList.remove('is-dragging');
        if (particle.word.hasPointerCapture(id)) particle.word.releasePointerCapture(id);
      }
      layer.addEventListener('pointerdown', event => {
        const particle = byWord.get(event.target);
        if (!particle || drag || event.button !== 0) return;
        event.preventDefault();
        drag = {particle, id: event.pointerId, dx: event.clientX + scrollX - particle.x,
          dy: event.clientY + scrollY - particle.y, time: performance.now()};
        particle.vx = particle.vy = particle.spin = 0;
        particle.damped = true;
        particle.word.classList.add('is-dragging');
        particle.word.setPointerCapture(event.pointerId);
      });
      layer.addEventListener('pointermove', event => {
        if (!drag || event.pointerId !== drag.id) return;
        const now = performance.now();
        const elapsed = Math.max(8, now - drag.time) / 1000;
        const particle = drag.particle;
        const x = Math.max(0, Math.min(bounds.width - particle.width, event.clientX + scrollX - drag.dx));
        const y = Math.max(0, Math.min(bounds.height - particle.height, event.clientY + scrollY - drag.dy));
        particle.vx = Math.max(-700, Math.min(700, (x - particle.x) / elapsed));
        particle.vy = Math.max(-700, Math.min(700, (y - particle.y) / elapsed));
        particle.x = x;
        particle.y = y;
        drag.time = now;
      });
      layer.addEventListener('pointerup', release);
      layer.addEventListener('pointercancel', release);
      layer.addEventListener('lostpointercapture', release);
      function animate(now) {
        const dt = Math.min((now - previousTime) / 1000, .04);
        previousTime = now;
        for (const particle of particles) {
          if (drag?.particle !== particle) {
            // Preserve ambient floating; handled words gradually lose their throwing momentum.
            if (particle.damped) {
              const damping = Math.exp(-.65 * dt);
              particle.vx *= damping;
              particle.vy *= damping;
              if (Math.hypot(particle.vx, particle.vy) < .5) particle.vx = particle.vy = 0;
            }
            particle.x += particle.vx * dt;
            particle.y += particle.vy * dt;
            particle.angle += particle.spin * dt;
            const maxX = Math.max(0, bounds.width - particle.width);
            const maxY = Math.max(0, bounds.height - particle.height);
            if (particle.x < 0 || particle.x > maxX) {
              particle.x = Math.max(0, Math.min(maxX, particle.x));
              particle.vx *= -1;
            }
            if (particle.y < 0 || particle.y > maxY) {
              particle.y = Math.max(0, Math.min(maxY, particle.y));
              particle.vy *= -1;
            }
          }
          particle.word.style.transform = `translate3d(${particle.x - scrollX}px, ${particle.y - scrollY}px, 0) rotate(${particle.angle}deg)`;
        }
        frame = requestAnimationFrame(animate);
      }
      stop = () => {
        cancelAnimationFrame(frame);
        release();
        layer.remove();
        originals.forEach(({node, wrapper}) => wrapper.replaceWith(node));
        inertStates.forEach(([element, inert]) => { element.inert = inert; });
        window.removeEventListener('blur', releaseDrag);
        stop = null;
        previousFocus?.focus({preventScroll: true});
      };
      const releaseDrag = () => release();
      window.addEventListener('blur', releaseDrag);
      restore.addEventListener('click', () => stop?.());
      restore.focus({preventScroll: true});
      frame = requestAnimationFrame(animate);
    }

    document.addEventListener('keydown', event => {
      if (stop) {
        if (event.key === 'Escape') { event.preventDefault(); stop(); }
        if (event.key === 'Tab') { event.preventDefault(); }
        return;
      }
      if (event.isComposing || event.repeat || event.ctrlKey || event.metaKey || event.altKey ||
          event.target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])')) {
        progress = [];
        return;
      }
      const now = performance.now();
      if (now - lastKey > 2500) progress = [];
      lastKey = now;
      progress.push(event.key.toLowerCase());
      while (progress.length && !progress.every((key, index) => sequence[index] === key)) progress.shift();
      if (progress.length >= 2 && event.key.startsWith('Arrow')) event.preventDefault();
      if (progress.length === sequence.length) { progress = []; launch(); }
    });
    for (const event of ['hashchange', 'resize', 'beforeprint', 'pagehide']) {
      window.addEventListener(event, () => { progress = []; stop?.(); });
    }
  }

  setupZeroGravity();
  setupAyamiNote();
  searchForm.hidden = false;
  document.getElementById('print').hidden = false;
  searchForm.addEventListener('submit', event => {
    event.preventDefault();
    const hash = '#search/' + encodeURIComponent(queryInput.value.trim());
    if (location.hash === hash) render();
    else location.hash = hash;
  });
  document.getElementById('print').addEventListener('click', () => window.print());
  window.addEventListener('hashchange', render);
  render();
})();
