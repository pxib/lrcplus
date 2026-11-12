/* Classic scripts and hash routes deliberately support opening via file://. */
'use strict';
(() => {
  const articles = [...document.querySelectorAll('#articles > article')];
  const byId = new Map(articles.map(article => [article.id, article]));
  const searchPanel = document.getElementById('search-results');
  const searchForm = document.getElementById('search');
  const queryInput = document.getElementById('query');
  const articleTab = document.getElementById('article-tab');
  const contentsTab = document.getElementById('contents-tab');
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
    summary.textContent = 'Contents';
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
    article.querySelector('.subtitle').after(toc);
  }

  for (const article of articles) {
    for (const heading of article.querySelectorAll('h2[id]')) {
      const link = document.createElement('a');
      link.className = 'section-link';
      link.href = `#${article.id}/${heading.id}`;
      link.textContent = '§';
      link.setAttribute('aria-label', `Permanent link to ${heading.textContent}`);
      link.title = 'Permanent link to this section';
      heading.append(' ', link);
    }
    const related = (article.dataset.related || relatedPages[article.id] || '')
      .split(' ').filter(id => byId.has(id));
    if (!related.length) continue;
    const nav = document.createElement('nav');
    nav.className = 'related-articles';
    nav.setAttribute('aria-label', 'Related articles');
    const label = document.createElement('strong');
    label.textContent = 'Related articles';
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

  function showBreadcrumbs(article, searching) {
    const nav = document.getElementById('breadcrumbs');
    nav.replaceChildren();
    nav.hidden = !searching && article.id === 'main';
    if (nav.hidden) return;
    const home = document.createElement('a');
    home.href = '#main';
    home.textContent = 'Main page';
    nav.append(home, ' › ');
    if (!searching && article.id !== 'article-index') {
      const indexLink = document.createElement('a');
      indexLink.href = '#article-index';
      indexLink.textContent = 'All articles';
      nav.append(indexLink, ' › ');
    }
    const current = document.createElement('span');
    current.setAttribute('aria-current', 'page');
    current.textContent = searching ? 'Search' : article.dataset.title;
    nav.append(current);
  }

  function showSearch(query) {
    searchPanel.hidden = false;
    queryInput.value = query;
    document.title = 'Search — LRCPlus manual';
    const words = query.toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const matches = words.length ? index.filter(page =>
      words.every(word => page.text.toLocaleLowerCase().includes(word))
    ).sort((a, b) => Number(b.title.toLowerCase().includes(query.toLowerCase())) -
      Number(a.title.toLowerCase().includes(query.toLowerCase()))) : [];
    document.getElementById('search-summary').textContent = words.length
      ? `${matches.length} article${matches.length === 1 ? '' : 's'} found for “${query}”.${matches.length ? '' : ' Try fewer words, or browse the navigation.'}`
      : 'Enter a word or phrase to search all articles.';
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
    const [pageId, anchor] = route.split('/');
    const searching = pageId === 'search';
    const article = byId.get(pageId) || byId.get('main');
    articles.forEach(page => { page.hidden = searching || page !== article; });
    searchPanel.hidden = !searching;
    document.querySelectorAll('.sidebar nav a').forEach(link => {
      if (!searching && link.hash === `#${article.id}`) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    articleTab.textContent = searching ? 'Search' : article.id === 'main' ? 'Main page' : 'Article';
    articleTab.href = searching ? location.hash : `#${article.id}`;
    document.getElementById('read-tab').href = articleTab.href;
    contentsTab.hidden = searching;
    showBreadcrumbs(article, searching);
    contentsTab.href = `#${article.id}/${article.id === 'main' ? 'main-contents' : article.id + '-contents'}`;
    if (searching) showSearch(route.slice('search/'.length));
    else document.title = `${article.dataset.title} — LRCPlus manual`;
    content.focus({preventScroll: true});
    if (anchor && !searching) {
      const target = document.getElementById(anchor);
      if (target && article.contains(target)) target.scrollIntoView();
      else window.scrollTo(0, 0);
    } else window.scrollTo(0, 0);
  }

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
