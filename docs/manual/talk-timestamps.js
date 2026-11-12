/* Shared by the offline manual and its local authoring preview. */
'use strict';
window.ManualTalkTimestamps = (() => {
  function random(key, minimum, maximum) {
    let seed = 2166136261;
    for (const character of key) seed = Math.imul(seed ^ character.charCodeAt(0), 16777619);
    return minimum + (seed >>> 0) % (maximum - minimum + 1);
  }

  function monthsLater(timestamp, months) {
    const date = new Date(timestamp);
    const day = date.getUTCDate();
    date.setUTCDate(1);
    date.setUTCMonth(date.getUTCMonth() + months);
    const lastDay = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 0)).getUTCDate();
    date.setUTCDate(Math.min(day, lastDay));
    return date.getTime();
  }

  function dates(comments, articleId, now = Date.now()) {
    const ceiling = Math.floor(now / 60000) * 60000;
    const earliest = Date.UTC(2026, 0, 1);
    const latest = Date.UTC(2026, 10, 30, 23, 59);
    let timestamp = Math.min(ceiling,
      earliest + random(`${articleId}/start`, 0, (latest - earliest) / 60000) * 60000);
    let previousTopic = null;
    return comments.map((comment, index) => {
      const topic = comment.closest('.talk-topic').querySelector('h2').id;
      const key = `${articleId}/${topic}/${index}`;
      const months = Number(comment.dataset.talkJumpMonths || 0);
      let minutes = 0;
      if (Number.isInteger(months) && months >= 1 && months <= 120) {
        const available = Math.max(0, Math.floor((monthsLater(timestamp, months) - timestamp) / 60000));
        minutes = available ? random(`${key}/jump/${months}`, 1, available) : 0;
      } else if (index > 0) {
        minutes = topic === previousTopic ? random(key, 2, 45) : random(key, 120, 2880);
      }
      timestamp = Math.min(ceiling, timestamp + minutes * 60000);
      previousTopic = topic;
      return new Date(timestamp);
    });
  }

  function format(date, language) {
    const locale = {ja: 'ja-JP', ko: 'ko-KR', 'yue-Hant': 'zh-HK'}[language] || 'en-GB';
    return new Intl.DateTimeFormat(locale, {
      day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit',
      hourCycle: 'h23', timeZone: 'UTC'
    }).format(date) + ' (UTC)';
  }

  return { dates, format };
})();
