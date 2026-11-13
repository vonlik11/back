const tg = window.Telegram?.WebApp;
const $ = selector => document.querySelector(selector);
const esc = (value = '') => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
}[char]));
const icon = name => `<i class="ic" data-lucide="${name}"></i>`;
const button = (label, action, style = 'secondary') =>
  `<button type="button" class="btn btn-${style}" data-action="${action}">${label}</button>`;
const submit = label => `<button class="btn btn-primary" type="submit">${label}</button>`;
const card = content => `<div class="card glass stack mt">${content}</div>`;
const heading = label => `<header class="page-head"><h1 class="title">${label}</h1></header>`;
const labels = {pending:'На согласовании', published:'Опубликован', rejected:'Отклонён', publishing:'Публикуется', publication_unknown:'Проверьте публикацию в канале'};
let dashboard = null;
let activeTab = 'home';
let botName = '';
let currentId = null;
let busy = false;
let timer;

tg?.ready();
tg?.expand();

async function api(path, data) {
  const options = data === undefined ? {cache: 'no-store'} : {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Mark-App': '1'},
    body: JSON.stringify(data)
  };
  const response = await fetch('/api' + path, options);
  const result = await response.json();
  if (!response.ok) {
    const error = new Error(result.error || 'Сервер не ответил.');
    error.status = response.status;
    throw error;
  }
  return result;
}

function notify(message) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(timer);
  timer = setTimeout(() => toast.classList.remove('show'), 4500);
}

function botLink(label, payload = '') {
  if (!/^[A-Za-z0-9_]+$/.test(botName)) return '';
  return `<a class="btn btn-secondary" href="https://t.me/${botName}${payload}" target="_blank" rel="noopener">${label}</a>`;
}

function auth() {
  return `<div class="auth-top"><div class="brand"><span class="brand-mark">М</span>Маркетолог<sup>°</sup></div><span class="pill-muted">Mini App</span></div>
    <h1 class="display">Марк,<br><em>ваш лучший маркетолог.</em></h1>
    <p class="lead">Создавайте, согласовывайте и публикуйте посты для своего Telegram-канала.</p>
    ${card(`<form class="stack" data-form="login">
      <div class="input-group"><input name="email" type="email" placeholder="Email" aria-label="Email" autocomplete="username" required>
      <input name="password" type="password" placeholder="Пароль" aria-label="Пароль" autocomplete="current-password" required></div>
      ${submit('Войти')}</form>
      ${tg?.initData ? button('Продолжить с Telegram', 'telegram-login') : ''}
      ${botLink('Зарегистрироваться в боте', '?start=register')}`)}`;
}

function postList(posts) {
  if (!posts.length) return card('<p class="muted">Пока здесь нет постов.</p>');
  return `<div class="list glass mt">${posts.map(post => `<button class="row with-icon" data-action="post" data-id="${esc(post.id)}">
    <span class="row-icon ${post.status === 'publishing' ? 'row-progress' : ''}">${post.status === 'publishing' ? '<span class="spinner"></span>' : icon(post.photo ? 'image' : 'file-text')}</span>
    <span class="row-main"><span class="row-title wrap">${esc(post.text.split('\n')[0].slice(0, 100))}</span>
    <span class="row-sub"><span class="status-dot status-${esc(post.status)}"></span>${esc(labels[post.status] || post.status)}</span></span>${icon('chevron-right')}</button>`).join('')}</div>`;
}

const provider = () => ({api:'OpenAI API', chatgpt:'ChatGPT'}[dashboard.provider] || 'Не подключён');
const kpi = (title, value) => `<div class="kpi glass"><div class="kpi-label">${title}</div><div class="kpi-value">${value}</div></div>`;
const count = value => value == null ? 'Нет данных' : Number(value).toLocaleString('ru-RU');
const percentage = value => value == null ? 'Нет данных' : `${Number(value).toLocaleString('ru-RU')}%`;
const duration = seconds => {
  if (seconds == null) return 'Нет данных';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} ч ${minutes % 60} мин`;
  return `${Math.floor(hours / 24)} д ${hours % 24} ч`;
};
const views = {
  home() {
    const pending = dashboard.posts.filter(post => post.status === 'pending');
    return `<header class="topbar"><span class="company-pill glass">${esc(dashboard.company)}</span></header>
      <h1 class="title">Привет,<br><em>${esc(dashboard.name)}.</em></h1>
      ${card(`<div class="status-line"><span class="dot ${dashboard.provider ? '' : 'off'}"></span>${provider()}</div>
      <p class="muted">Канал ${esc(dashboard.channel)}</p>
      ${button(dashboard.provider ? 'Новый пост' : 'Подключить ИИ', dashboard.provider ? 'new' : 'connect', 'primary')}`)}
      <div class="section-head"><h2>Ждут согласования</h2></div>${postList(pending)}
      <div class="section-head"><h2>Публикации</h2></div>
      <div class="grid2">${kpi('Опубликовано', dashboard.analytics.total_posts)}${kpi('На согласовании', pending.length)}</div>`;
  },
  content: () => heading('Контент') + `<div class="content-intro"><div><strong>Очередь публикаций</strong><p class="muted small">Черновики, статусы и готовые посты</p></div>${button('Новый пост', 'new', 'primary')}</div>` + postList(dashboard.posts),
  schedule: () => heading('Расписание') + card(`<form class="stack" data-form="schedule">
    <p>Ежедневный черновик</p><p class="muted small">${esc(dashboard.timezone)}. Бот подготовит пост к этому времени и попросит одобрить публикацию.</p>
    <div class="input-group"><input name="time" aria-label="Время подготовки" type="time" value="${esc(dashboard.schedule || '')}" required></div>
    ${submit('Сохранить расписание')}${dashboard.schedule ? button('Отключить', 'schedule-off') : ''}</form>`),
  analytics() {
    const {posts, total_posts} = dashboard.analytics;
    const measured = posts.filter(post => post.reaction_total != null);
    const rated = posts.filter(post => post.reaction_rate_pct != null);
    const reactionCount = measured.length ? measured.reduce((sum, post) => sum + post.reaction_total, 0) : null;
    const commentCount = posts.reduce((sum, post) => sum + post.comment_total, 0);
    const postCard = post => {
      const mix = post.reactions.length ? post.reactions.map(reaction => {
        const type = reaction.type || {};
        const label = type.emoji || (type.type === 'custom_emoji' ? 'Пользовательский эмодзи' : type.type === 'paid' ? 'Платная реакция' : 'Реакция');
        return `${esc(label)} ${count(reaction.total_count)}`;
      }).join(' · ') : 'Реакций пока нет';
      const metric = (label, value) => `<p class="small"><strong>${label}:</strong> ${value}</p>`;
      return card(`<strong>${esc(post.text_preview)}</strong>
        <p class="muted small">${esc(post.published_at.slice(0,10))}</p>
        <div class="section-head"><h2>Вовлечение</h2></div>
        ${metric('Реакции', count(post.reaction_total))}
        ${metric('Распределение реакций', mix)}
        ${metric('Комментарии, наблюдаемые с запуска сбора', count(post.comment_total))}
        ${metric('Содержательные комментарии (эвристика)', percentage(post.substantive_comment_pct))}
        ${metric('Доля спам-подобных (эвристика)', percentage(post.spam_comment_pct))}
        ${metric('Короткие / эмодзи', count(post.short_comments))}
        ${metric('Похожие на спам', count(post.spam_comments))}
        ${metric('Реакции / аудитория', percentage(post.reaction_rate_pct))}
        ${metric('Комментарии / аудитория', percentage(post.comment_rate_pct))}
        <div class="section-head"><h2>Динамика</h2></div>
        ${metric('Реакции за первые 15 минут (по полученным событиям)', count(post.reaction_velocity_15m))}
        ${metric('Реакции за первый час (по полученным событиям)', count(post.reaction_velocity_60m))}
        ${metric('До первой замеченной реакции', duration(post.first_reaction_seconds))}
        ${metric('До первого замеченного комментария', duration(post.first_comment_seconds))}
        ${metric('Самый большой прирост реакций', duration(post.peak_after_seconds))}
        ${metric('Крупный единичный всплеск', post.possible_spike ? 'Есть, стоит проверить' : 'Не обнаружен')}
        ${metric('Время до половины итоговых реакций', duration(post.half_life_seconds))}
        ${metric('Реакции после 24 часов', count(post.long_tail_reactions))}
        ${metric('Просмотры / охват', 'Недоступны через Bot API')}
        ${metric('CTR / пересылки', 'Нет счётчиков в Bot API')}
        <div class="section-head"><h2>Аудитория</h2></div>
        ${metric('Подписчики около времени публикации', count(post.subscribers_at_publish))}
        ${metric('Изменение за 1 час', post.subscribers_delta_1h == null ? 'Нет данных' : `${post.subscribers_delta_1h > 0 ? '+' : ''}${count(post.subscribers_delta_1h)}`)}
        ${metric('Изменение за 24 часа', post.subscribers_delta_24h == null ? 'Нет данных' : `${post.subscribers_delta_24h > 0 ? '+' : ''}${count(post.subscribers_delta_24h)}`)}
        <p class="muted small">Скорость, пик, half-life и хвост считаются по полученным снимкам, поэтому при редких событиях могут быть неполными.</p>`);
    };
    return heading('Аналитика') + `<div class="grid2">${kpi('Опубликовано всего', total_posts)}${kpi('Реакции на 10 последних', count(reactionCount))}${kpi('Комментарии на 10 последних', count(commentCount))}${kpi('Средняя реакция / аудитория', rated.length ? percentage(rated.reduce((sum, post) => sum + post.reaction_rate_pct, 0) / rated.length) : 'Нет данных')}</div>
      <p class="muted small mt">Нормировка использует подписчиков, пока нет охватов. Bot API не отдаёт просмотры и пересылки поста, а клики по обычным ссылкам нельзя посчитать. Оценка содержательности — простая эвристика по тексту, не ИИ-разбор смысла. Комментарии считаются после добавления бота администратором группы обсуждений.</p>
      ${button('Обновить', 'refresh-analytics')}
      <div class="section-head"><h2>Последние публикации</h2></div>
      ${posts.length ? posts.map(postCard).join('') : card('<p class="muted">Данные появятся после первой публикации.</p>')}`;
  },
  profile: () => heading('Профиль') + card(`<strong>${esc(dashboard.name)}</strong><p>${esc(dashboard.email)}</p><p>Канал ${esc(dashboard.channel)}</p>
    <p>ИИ · ${provider()}</p>${button('Подключение ИИ', 'connect')}${botLink('Открыть бота')}${button('Выйти', 'logout')}`) +
    card(`<form class="stack" data-form="profile"><strong>Компания</strong><div class="input-group"><input name="company" aria-label="Название компании" maxlength="150" required value="${esc(dashboard.company)}">
    <textarea name="description" aria-label="Описание компании" maxlength="2000">${esc(dashboard.description)}</textarea></div>${submit('Сохранить профиль')}</form>`)
};

function render() {
  $('#screen-auth').classList.toggle('hidden', Boolean(dashboard));
  $('#screen-app').classList.toggle('hidden', !dashboard);
  if (dashboard) {
    $('#view').dataset.tab = activeTab;
    $('#view').innerHTML = views[activeTab]();
    document.querySelectorAll('.tab').forEach(tab => tab.classList.toggle('on', tab.dataset.tab === activeTab));
  } else {
    $('#screen-auth').innerHTML = auth();
  }
  window.lucide?.createIcons();
}

function sheet(title, body) {
  $('#sheet').innerHTML = `<div class="grabber"></div><div class="sheet-head"><span></span><div class="sheet-title">${title}</div>
    <button class="icon-btn close" data-action="close-sheet" aria-label="Закрыть">${icon('x')}</button></div>${body}`;
  $('#sheet').scrollTop = 0;
  $('#sheet-layer').classList.remove('hidden');
  requestAnimationFrame(() => $('#sheet-layer').classList.add('open'));
  window.lucide?.createIcons();
}

function closeSheet() {
  $('#sheet-layer').classList.remove('open');
  $('#sheet-layer').classList.add('hidden');
  currentId = null;
}

function showPost(id) {
  const post = dashboard.posts.find(item => item.id === id);
  if (!post) return;
  currentId = id;
  const cover = post.photo ? `<div class="cover"><img src="/api/posts/${esc(id)}/photo" alt="Иллюстрация поста"></div>` : '';
  const source = post.photo_source?.kind === 'web' && post.photo_source.url
    ? `<a class="image-credit" href="${esc(post.photo_source.url)}" target="_blank" rel="noopener noreferrer">Источник: ${esc(post.photo_source.title || 'Wikimedia Commons')}${post.photo_source.author ? ` · ${esc(post.photo_source.author)}` : ''}${post.photo_source.license ? ` · ${esc(post.photo_source.license)}` : ''}</a>`
    : post.photo_source?.kind === 'generated' ? '<span class="image-credit">Иллюстрация сгенерирована ИИ</span>'
      : post.photo_source?.kind === 'user' ? '<span class="image-credit">Фото пользователя</span>' : '';
  const error = post.error ? `<div class="publish-error" role="alert">${esc(post.error)}</div>` : '';
  const statusText = post.status === 'publishing'
    ? '<div class="publish-progress"><span class="spinner"></span><span>Отправляю пост в Telegram. Результат появится здесь.</span></div>'
    : post.status === 'publication_unknown'
      ? '<div class="publish-error" role="alert">Telegram не подтвердил результат. Сначала проверьте канал и только затем решайте, повторять ли публикацию.</div>'
      : '';
  const editable = post.status === 'pending' ? `<form class="stack mt" data-form="edit">
      <label class="form-label" for="post-text">Текст поста</label>
      <div class="input-group"><textarea id="post-text" name="text" maxlength="${post.photo ? 1024 : 3500}" required>${esc(post.text)}</textarea></div>
      <button class="btn btn-secondary" type="submit">Сохранить правки</button>
    </form>
    <div class="sheet-actions">
      ${button('Одобрить и опубликовать', 'publish', 'primary')}
      <div class="btn-row">${button('Найти фото', 'webimage')}${button('Создать фото', 'image')}</div>
      ${post.photo ? button('Оставить только текст', 'noimage') : ''}
      ${button('Отклонить черновик', 'reject')}
    </div>` : `<div class="textcard mt">${esc(post.text)}</div>`;
  sheet('Публикация', `<span class="status-chip"><span class="status-dot status-${esc(post.status)}"></span>${esc(labels[post.status] || post.status)}</span>${cover}${source}${error}${statusText}${editable}`);
}

async function update(path, data, message) {
  const result = await api(path, data);
  if (result.job_id) {
    const postId = currentId;
    sheet('Публикация', '<div class="publish-progress"><span class="spinner"></span><div><strong>Отправляю в канал</strong><p class="muted small">Жду подтверждения Telegram. Не нажимайте кнопку повторно.</p></div></div><p class="job-message" id="job-message">Пост в очереди…</p>');
    let job;
    while (true) {
      await new Promise(resolve => setTimeout(resolve, 900));
      job = await api('/jobs/' + result.job_id);
      dashboard = job.dashboard;
      const note = $('#job-message');
      if (note) note.textContent = job.message;
      if (job.status === 'done' || job.status === 'failed') break;
    }
    closeSheet();
    render();
    if (job.status === 'failed') {
      if (postId) showPost(postId);
      notify(job.error || 'Не удалось опубликовать пост.');
    } else {
      if (message) notify(job.message || message);
    }
    return;
  }
  dashboard = result;
  closeSheet();
  render();
  if (message) notify(message);
}

async function act(work, control, loading = 'Обрабатываю…') {
  if (busy) return;
  busy = true;
  const oldLabel = control?.innerHTML;
  if (control) {
    control.disabled = true;
    control.setAttribute('aria-busy', 'true');
    control.innerHTML = `<span class="spinner"></span><span>${esc(loading)}</span>`;
  }
  try {
    await work();
  } catch (error) {
    if (error.status === 401 && dashboard) {
      dashboard = null;
      closeSheet();
      render();
    }
    notify(error.message);
  } finally {
    if (control) {
      control.disabled = false;
      control.removeAttribute('aria-busy');
      control.innerHTML = oldLabel;
    }
    busy = false;
  }
}

let analyticsRequest = false;

setInterval(async () => {
  if (!dashboard || activeTab !== 'analytics' || busy || analyticsRequest || document.hidden) return;
  const previous = dashboard;
  analyticsRequest = true;
  try {
    const next = await api('/dashboard');
    if (dashboard !== previous || activeTab !== 'analytics' || busy) return;
    if (JSON.stringify(previous.analytics) === JSON.stringify(next.analytics)) return;
    dashboard = next;
    const view = $('#view');
    const scroll = view.scrollTop;
    render();
    view.scrollTop = scroll;
  } catch {
  } finally {
    analyticsRequest = false;
  }
}, 15000);

document.addEventListener('click', event => {
  const control = event.target.closest('[data-action]');
  if (!control) return;
  const action = control.dataset.action;
  if (action === 'close-sheet') return closeSheet();
  if (busy) return;
  if (action === 'telegram-login') return act(() => update('/login', {init_data: tg.initData}), control);
  if (!dashboard) return;
  if (action === 'refresh-analytics') return act(async () => {
    dashboard = await api('/dashboard');
    render();
  }, control);
  if (action === 'tab') {
    activeTab = control.dataset.tab;
    return act(async () => {
      render();
      $('#view').scrollTop = 0;
      dashboard = await api('/dashboard');
      render();
    }, control);
  }
  if (action === 'post') return showPost(control.dataset.id);
  if (action === 'connect') return sheet('Подключение ИИ', `<div class="stack">
    <p class="muted small">Ключ API даёт доступ к тексту и генерации изображений. Запросы оплачиваются с баланса OpenAI API отдельно от подписки ChatGPT.</p>
    ${botLink('Подключить ChatGPT в боте', '?start=chatgpt')}
    <form class="stack" data-form="connect"><div class="input-group"><input name="key" type="password" aria-label="Ключ OpenAI API" placeholder="Ключ OpenAI API" autocomplete="off" required></div>
    ${submit('Подключить OpenAI API')}</form></div>`);
  if (action === 'new') return sheet('Новый пост', `<form class="stack" data-form="create">
    <div class="input-group"><textarea name="topic" aria-label="Тема поста" placeholder="О чём написать?" maxlength="3000" required></textarea></div>
    <label class="small"><input type="checkbox" name="illustrate" checked> Подобрать иллюстрацию</label>
    <label class="small">Своё фото <input type="file" name="photo" accept="image/jpeg,image/png"></label>
    ${submit('Создать черновик')}</form>`);
  if (action === 'logout') return act(async () => {
    await api('/logout', {});
    dashboard = null;
    activeTab = 'home';
    render();
  }, control);
  if (action === 'schedule-off') return act(() => update('/schedule', {time:null}, 'Расписание отключено'), control);
  if (currentId && ['publish','reject','image','webimage','noimage'].includes(action)) {
    if (action === 'publish' && !window.confirm('Опубликовать пост в канале ' + dashboard.channel + '?')) return;
    const loading = {publish:'Отправляю в канал…', webimage:'Ищу фото…', image:'Создаю фото…', reject:'Отклоняю…', noimage:'Сохраняю…'}[action];
    return act(() => update('/posts/' + currentId + '/' + action, {}, 'Готово'), control, loading);
  }
});

document.addEventListener('submit', event => {
  const form = event.target;
  const action = form.dataset.form;
  if (!action) return;
  event.preventDefault();
  const fields = new FormData(form);
  const loading = {login:'Вхожу…', connect:'Проверяю ключ…', schedule:'Сохраняю…', profile:'Сохраняю…', edit:'Сохраняю правки…', create:'Создаю пост…'}[action];
  act(async () => {
    if (action === 'login') return update('/login', {email:fields.get('email'), password:fields.get('password')});
    if (action === 'connect') return update('/connect', {key:fields.get('key')}, 'OpenAI API подключён');
    if (action === 'schedule') return update('/schedule', {time:fields.get('time')}, 'Расписание сохранено');
    if (action === 'profile') return update('/profile', {company:fields.get('company'), description:fields.get('description')}, 'Профиль сохранён');
    if (action === 'edit') return update('/posts/' + currentId + '/edit', {text:fields.get('text')}, 'Правки сохранены');
    if (action === 'create') {
      const photo = fields.get('photo');
      let encoded = null;
      if (photo.size) {
        if (photo.size > 10 * 1024 * 1024) throw new Error('Выберите фото до 10 МБ.');
        encoded = await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => resolve(String(reader.result).split(',')[1]);
          reader.onerror = () => reject(new Error('Не удалось прочитать фото.'));
          reader.readAsDataURL(photo);
        });
      }
      notify('Готовлю черновик…');
      activeTab = 'content';
      return update('/posts', {topic:fields.get('topic'), illustrate:fields.has('illustrate'), photo:encoded}, 'Черновик готов');
    }
  }, event.submitter, loading);
});

render();
Promise.allSettled([api('/config'), api('/dashboard')]).then(([config, session]) => {
  if (config.status === 'fulfilled') botName = config.value.bot;
  if (busy || dashboard) return;
  if (session.status === 'fulfilled') dashboard = session.value;
  render();
});
