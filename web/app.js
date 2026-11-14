/* Маркетолог° — Telegram Mini App (кликабельный макет, без бэкенда) */

const tg = window.Telegram && window.Telegram.WebApp;
if (tg) { try { tg.ready(); tg.expand(); } catch (e) {} }

/* ---------- helpers ---------- */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const ic = (name, cls = '') => `<i data-lucide="${name}" class="ic ${cls}"></i>`;
const esc = (s = '') => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const uid = () => Math.random().toString(36).slice(2, 9);
const hash = () => Math.random().toString(16).slice(2, 9);
function icons() { if (window.lucide) lucide.createIcons({ attrs: { 'stroke-width': 1.6 } }); }
const stored = key => { try { return localStorage.getItem(key); } catch (e) { return null; } };
const remember = (key, value) => { try { localStorage.setItem(key, value); } catch (e) {} };
const avatarFor = email => stored(`mark-avatar:${email || 'local'}`) || '';
function applyTheme() {
  const theme = S.theme;
  const dark = theme === 'dark' || (theme === 'system' && window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  document.documentElement.dataset.reduceMotion = S.reduceMotion ? 'true' : 'false';
}

const FIREBASE_CONFIG = {
  apiKey: 'AIzaSyBSD6MuSXYKTrT0FuEr-dn7wVYZkjWU4sc',
  authDomain: 'petmatch-2d396.firebaseapp.com',
  databaseURL: 'https://petmatch-2d396-default-rtdb.firebaseio.com',
  projectId: 'petmatch-2d396',
  storageBucket: 'petmatch-2d396.firebasestorage.app',
  messagingSenderId: '660491830051',
  appId: '1:660491830051:web:ca24ef3a95075fb00c0296',
  measurementId: 'G-2951H1CGH4'
};
let firebaseAuth = null;
if (window.firebase) {
  if (!firebase.apps.length) firebase.initializeApp(FIREBASE_CONFIG);
  firebaseAuth = firebase.auth();
}

let toastTimer;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2400);
  if (tg && tg.HapticFeedback) { try { tg.HapticFeedback.impactOccurred('light'); } catch (e) {} }
}

/* ---------- constants ---------- */
const DAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const DAYS_FULL = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];
const WEEK_DATES = [9, 10, 11, 12, 13, 14, 15];
const TODAY = 4;          // пятница, 13 ноября
const NOW = '11:15';
const PLATFORMS = { tg: 'Telegram', vk: 'VK', max: 'MAX' };
const RUBRICS = ['Продукт', 'Кейсы', 'Экспертиза', 'Новости', 'Закулисье', 'Итоги'];
const STATUS = {
  draft: { l: 'Черновик', c: 'orange' },
  pending: { l: 'На согласовании', c: 'blue' },
  approved: { l: 'Одобрено', c: 'green' },
  published: { l: 'Опубликовано', c: 'gray' },
  rejected: { l: 'Отклонено', c: 'red' }
};
const AUTONOMY = [
  { id: 'L0', t: 'Только идеи', s: 'Агент предлагает темы и планы' },
  { id: 'L1', t: 'Черновик + согласование', s: 'Каждый пост — только после «Одобрить»' },
  { id: 'L2', t: 'Автопубликация рубрик', s: 'Шаблонные рубрики выходят сами, в лимитах' },
  { id: 'L3', t: 'Автоответы в комментариях', s: 'Типовые вопросы — без оператора' }
];
const PRESETS = [
  { t: '3 раза в неделю', s: 'Пн, Ср, Пт · 10:00', days: [0, 2, 4], time: '10:00', title: 'Регулярный пост' },
  { t: 'Каждый будний день', s: 'Пн–Пт · 12:00', days: [0, 1, 2, 3, 4], time: '12:00', title: 'Ежедневный пост' },
  { t: 'Выходные', s: 'Сб, Вс · 11:00', days: [5, 6], time: '11:00', title: 'Пост выходного дня' }
];
const EVENTS = [
  { d: '15', m: 'ноя', t: 'День проектировщика', s: 'Отраслевой праздник' },
  { d: '01', m: 'дек', t: 'Зимние марки бетона', s: 'Сезонный инфоповод' },
  { d: '31', m: 'дек', t: 'Новый год', s: 'Итоги года и поздравление' }
];
const CRITERIA = [
  'Отвечает в Telegram', 'Помнит контекст и историю', 'Знает факты о компании', 'Создаёт текст и обложку',
  'Правки → новая версия', 'Не публикует без одобрения', 'Публикует одобренное', 'Работает 24/7 по расписанию',
  'Изоляция компаний', 'Переживает перезапуск', 'Настройка без кода', 'Движок ChatGPT / Codex'
];
const IMG = [
  'https://images.unsplash.com/photo-1541888946425-d0fbb186156f?w=800&q=80&auto=format&fit=crop',
  'https://images.unsplash.com/photo-1503387762-592deb58ef4e?w=800&q=80&auto=format&fit=crop',
  'https://images.unsplash.com/photo-1581092160607-ee22621dd758?w=800&q=80&auto=format&fit=crop',
  'https://images.unsplash.com/photo-1504917599217-d4dc5ebe6122?w=800&q=80&auto=format&fit=crop',
  'https://images.unsplash.com/photo-1495474472287-4d71bcdd2085?w=800&q=80&auto=format&fit=crop',
  'https://images.unsplash.com/photo-1509042239860-f550ce710b93?w=800&q=80&auto=format&fit=crop'
];
const ANALYTICS = {
  '7': { reach: '48 250', delta: '+18% к прошлой неделе', bars: [42, 55, 48, 70, 62, 30, 38], labels: DAYS, hi: 3,
    er: '4,8%', erd: '+0,6 п.п.', clicks: '1 842', clicksd: '+9%', leads: '64', leadsd: '+12', cost: '$6,20', costd: 'за неделю' },
  '30': { reach: '186 400', delta: '+24% к октябрю', bars: [52, 61, 70, 86], labels: ['1 нед', '2 нед', '3 нед', '4 нед'], hi: 3,
    er: '4,5%', erd: '+0,4 п.п.', clicks: '7 120', clicksd: '+15%', leads: '231', leadsd: '+38', cost: '$24,80', costd: 'за месяц' }
};

/* ---------- state ---------- */
const S = {
  user: null,
  authMode: 'login',
  theme: stored('mark-theme') || 'light',
  reduceMotion: stored('mark-reduce-motion') === 'true',
  gptFlow: 'idle',
  gpt: { connected: false, plan: 'Plus', ping: null },
  syncStatus: 'Войдите по email для синхронизации',
  tab: 'home',
  tenantId: stored('mark-active-company') || 't1',
  tenants: (() => {
    try {
      const saved = JSON.parse(localStorage.getItem('mark-companies') || 'null');
      if (Array.isArray(saved) && saved.length) return saved;
    } catch (e) {}
    return [
    { id: 't1', name: 'Салаватстекло', full: 'АО «Салаватстекло»', industry: 'Стекольное производство · Башкортостан', initials: 'С' },
    { id: 't2', name: 'Аромат', full: 'Сеть кофеен «Аромат»', industry: 'Кофейни · B2C', initials: 'АР' }
    ];
  })(),
  autonomy: 'L1',
  autopost: true,
  paused: false,
  filter: 'all',
  day: TODAY,
  period: '7',
  sheet: null,
  current: null,
  platform: 'tg',
  editSlotId: null,
  slotDraft: null,
  npTopic: '',
  wiz: null,
  crit: CRITERIA.map(() => 'idle'),
  channels: { tg: true, vk: true, max: false },
  skills: [
    { id: 'k1', name: 'Копирайтер', desc: 'Пост по брифу и фактам, 3 заголовка', on: true },
    { id: 'k2', name: 'Бренд и закон', desc: 'Тон бренда, стоп-слова, ФЗ о рекламе', on: true },
    { id: 'k3', name: 'Обложки', desc: 'Карточки с чистой кириллицей', on: true },
    { id: 'k4', name: 'Лаборатория гипотез', desc: 'A/B заголовков и выводы в память', on: true },
    { id: 'k5', name: 'Радар конкурентов', desc: 'Мониторинг 3 каналов', on: false }
  ],
  posts: [
    {
      id: 'p704', tenantId: 't1', status: 'pending', version: 'v1', hash: 'e9f2a1b', time: 'Ср, 11:30', rubric: 'Продукт',
      title: 'Арматура А500С: спеццена от 15 тонн', platforms: ['tg', 'vk', 'max'], image: IMG[0], hook: 0,
      hooks: [
        'Строите в Подмосковье? Арматура А500С на 7% дешевле от 15 тонн',
        'Почему подрядчики переплачивают за металлопрокат — и как этого избежать',
        'Арматура с завода: отгрузка за 2 часа и все сертификаты в комплекте'
      ],
      text: {
        tg: 'Открыли бронь на объёмную партию арматуры А500С по ГОСТ 34028-2016, диаметр 12–25 мм.\n\n— Скидка 7% при заказе от 15 тонн\n— Доставка манипулятором день в день\n— Паспорт качества с каждой машиной\n\nОставить заявку: stroytech.ru/go/a500',
        vk: 'Срыв поставки арматуры на один день — это простой бригады и сдвиг заливки.\n\nМы отгружаем А500С со склада в Подольске с гарантией по времени:\n1. Честный вес на поверенных весах\n2. Диаметры от 10 до 32 мм\n3. От 15 тонн — скидка 7%\n\nРассчитать партию: stroytech.ru/go/vk-a500\n\n#стройматериалы #арматура',
        max: 'Арматура А500С, ГОСТ 34028.\nОтгрузка со склада ежедневно, скидка 7% от 15 тонн.\nОтдел продаж: @stroy_sales'
      },
      facts: [{ id: 'F-102', t: 'ГОСТ 34028-2016 · Сертификаты_2026.pdf, стр. 4' }, { id: 'F-105', t: 'Скидка 7% от 15 т · Прайс_ноябрь.xlsx' }],
      score: 9.6, added: ''
    },
    {
      id: 'p703', tenantId: 't1', status: 'pending', version: 'v1', hash: 'd7a90cc', time: 'Вт, 14:00', rubric: 'Кейсы',
      title: 'Кейс: 140 плит для ЖК «Северный» за 48 часов', platforms: ['tg', 'vk'], image: IMG[1], hook: 0,
      hooks: [
        '140 плит перекрытия за 48 часов: как мы спасли график ЖК «Северный»',
        'Поставщик сорвал сроки. Что мы сделали за два дня',
        'Хроника экстренной отгрузки: ни одного дефекта'
      ],
      text: {
        tg: 'Подрядчик ЖК «Северный» остался без плит ПК 60-15 — бетонные работы встали.\n\nЗавод перестроил смену:\n— 60 плит изготовлены за сутки\n— 14 рейсов длинномеров\n— стройка продолжилась без штрафов\n\nКачество подтверждено заводской лабораторией.',
        vk: 'Как мы за 48 часов поставили 140 плит перекрытия на ЖК «Северный» — история одного форс-мажора.',
        max: 'Кейс: 140 плит для ЖК «Северный» за 48 часов.'
      },
      facts: [{ id: 'F-110', t: 'Акт приёмки ЖК «Северный» №41 от 04.11.2026' }],
      score: 9.8, added: ''
    },
    {
      id: 'p702', tenantId: 't1', status: 'approved', version: 'v2', hash: 'c4e339a', time: 'Чт, 16:00', rubric: 'Закулисье',
      title: 'Лаборатория: почему наш бетон не трескается', platforms: ['tg'], image: IMG[2], hook: 0,
      hooks: ['Как ОТК бракует смесь ещё до выезда миксера'],
      text: { tg: 'Заглянули в заводскую лабораторию: каждая партия бетона проходит испытание на прессе 250 тонн.', vk: '', max: '' },
      facts: [{ id: 'F-088', t: 'Аттестат аккредитации лаборатории' }], score: 10, added: ''
    },
    {
      id: 'p701', tenantId: 't1', status: 'published', version: 'v1', hash: 'b1187d9', time: 'Пн, 10:00', rubric: 'Новости',
      title: 'Цены на металл: дайджест недели', platforms: ['tg', 'vk'], image: IMG[3], hook: 0,
      hooks: ['Что происходит с ценой тонны проката в ноябре'],
      text: { tg: 'Короткий обзор рынка металлопроката за неделю.', vk: 'Обзор рынка металлопроката.', max: '' },
      facts: [{ id: 'F-051', t: 'Сводка биржевых котировок' }], score: 9.5, added: ''
    },
    {
      id: 'p801', tenantId: 't2', status: 'pending', version: 'v1', hash: 'a0c11f2', time: 'Сб, 08:30', rubric: 'Продукт',
      title: 'Зимний раф с пряной грушей', platforms: ['tg', 'max'], image: IMG[4], hook: 0,
      hooks: ['Новый зимний раф уже в меню', 'Груша, корица и мягкая пенка', 'Тот самый вкус первого снега'],
      text: { tg: 'В меню — зимний раф с пряной грушей. До конца недели второй напиток за полцены.', vk: '', max: 'Зимний раф уже в «Аромате».' },
      facts: [{ id: 'F-201', t: 'Сезонное меню · зима 2026' }], score: 9.4, added: ''
    }
  ],
  slots: [
    { id: 's1', tenantId: 't1', title: 'Обзор рынка', rubric: 'Новости', days: [0], time: '10:00', platforms: ['tg'], mode: 'approve', repeat: 'weekly' },
    { id: 's2', tenantId: 't1', title: 'Кейс недели', rubric: 'Кейсы', days: [1], time: '14:00', platforms: ['tg', 'vk'], mode: 'approve', repeat: 'weekly' },
    { id: 's3', tenantId: 't1', title: 'Продукт и акции', rubric: 'Продукт', days: [2], time: '11:30', platforms: ['tg', 'vk', 'max'], mode: 'approve', repeat: 'weekly' },
    { id: 's4', tenantId: 't1', title: 'Закулисье', rubric: 'Закулисье', days: [3], time: '16:00', platforms: ['tg'], mode: 'approve', repeat: 'weekly' },
    { id: 's5', tenantId: 't1', title: 'Итоги недели', rubric: 'Итоги', days: [4], time: '17:00', platforms: ['tg'], mode: 'auto', repeat: 'weekly' },
    { id: 's6', tenantId: 't2', title: 'Утренний кофе', rubric: 'Продукт', days: [0, 1, 2, 3, 4, 5, 6], time: '08:30', platforms: ['tg'], mode: 'auto', repeat: 'weekly' }
  ]
};

const tenant = () => S.tenants.find(t => t.id === S.tenantId);
if (!tenant()) S.tenantId = S.tenants[0] ? S.tenants[0].id : '';
function setCompanyList(companies, activeCompanyId) {
  S.tenants = companies;
  S.tenantId = companies.some(company => company.id === activeCompanyId) ? activeCompanyId : (companies[0]?.id || '');
  remember('mark-companies', JSON.stringify(S.tenants));
  remember('mark-active-company', S.tenantId);
}
async function saveCompaniesToServer() {
  const user = firebaseAuth && firebaseAuth.currentUser;
  if (!user || !S.user || S.user.uid !== user.uid) {
    S.syncStatus = 'Локально · для синхронизации войдите по email';
    return false;
  }
  S.syncStatus = 'Синхронизация…';
  try {
    const token = await user.getIdToken();
    const response = await fetch('/api/companies', {
      method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      body: JSON.stringify({ companies: S.tenants, activeCompanyId: S.tenantId })
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    S.syncStatus = 'Синхронизировано с сервером';
    remember('mark-companies', JSON.stringify(S.tenants));
    remember('mark-active-company', S.tenantId);
    return true;
  } catch (error) {
    S.syncStatus = 'Сервер недоступен · локальная копия сохранена';
    console.error('Company sync failed:', error);
    return false;
  }
}
async function loadCompaniesFromServer(user) {
  if (!user) return false;
  S.syncStatus = 'Загрузка с сервера…';
  try {
    const token = await user.getIdToken();
    const response = await fetch('/api/companies', { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (Array.isArray(data.companies) && data.companies.length) {
      setCompanyList(data.companies, data.activeCompanyId);
    } else {
      await saveCompaniesToServer();
      return true;
    }
    S.syncStatus = 'Синхронизировано с сервером';
    return true;
  } catch (error) {
    S.syncStatus = 'Сервер недоступен · локальная копия сохранена';
    console.error('Company sync failed:', error);
    return false;
  }
}
const tenantOf = p => S.tenants.find(t => t.id === p.tenantId) || tenant();
const myPosts = () => S.posts.filter(p => p.tenantId === S.tenantId);
const mySlots = () => S.slots.filter(s => s.tenantId === S.tenantId);
const platList = arr => arr.map(p => PLATFORMS[p]).join(', ');
const daysLabel = d => {
  const k = [...d].sort().join(',');
  if (k === '0,1,2,3,4,5,6') return 'Каждый день';
  if (k === '0,1,2,3,4') return 'Будни';
  if (k === '5,6') return 'Выходные';
  return [...d].sort().map(i => DAYS[i]).join(', ');
};
const plural = n => n === 0 ? 'нет слотов' : n === 1 ? '1 слот' : n < 5 ? `${n} слота` : `${n} слотов`;

function nextSlot() {
  const sl = mySlots();
  for (let k = 0; k < 7; k++) {
    const d = (TODAY + k) % 7;
    const list = sl.filter(s => s.days.includes(d)).sort((a, b) => a.time.localeCompare(b.time));
    for (const s of list) {
      if (k === 0 && s.time <= NOW) continue;
      const label = k === 0 ? 'Сегодня' : k === 1 ? 'Завтра' : DAYS_FULL[d];
      return { label: `${label}, ${s.time}`, slot: s };
    }
  }
  return null;
}

/* ---------- UI atoms ---------- */
const sw = (on, action, id = '') =>
  `<label class="switch"><input type="checkbox" ${on ? 'checked' : ''} data-action="${action}" ${id ? `data-id="${id}"` : ''}><span></span></label>`;

function row({ icon, avatar, title, sub, action, data = '', right = '', chev = true, cls = '' }) {
  const tag = action ? 'button' : 'div';
  return `<${tag} class="row ${icon || avatar ? 'with-icon' : ''} ${cls}" ${action ? `data-action="${action}"` : ''} ${data}>
    ${icon ? `<span class="row-icon">${ic(icon)}</span>` : ''}${avatar ? `<span class="avatar">${avatar}</span>` : ''}
    <span class="row-main"><span class="row-title">${title}</span>${sub ? `<span class="row-sub">${sub}</span>` : ''}</span>
    ${right}${chev && action ? ic('chevron-right', 'chev') : ''}
  </${tag}>`;
}
const group = (title, rows, foot = '') =>
  `<div class="group-title">${title}</div><div class="list glass">${rows.filter(Boolean).join('')}</div>${foot}`;
const kpi = (label, value, delta, neutral) =>
  `<div class="kpi glass"><div class="kpi-label">${label}</div><div class="kpi-value">${value}</div><div class="kpi-delta ${neutral ? 'neutral' : ''}">${delta}</div></div>`;
const postCard = p =>
  `<button class="post-card glass" data-action="open-post" data-id="${p.id}">
    <img src="${p.image}" alt="">
    <div class="pc-body"><div class="pc-title">${esc(p.title)}</div>
    <div class="pc-meta"><span class="dot ${STATUS[p.status].c}"></span>${esc(p.time)} · ${platList(p.platforms)}</div></div>
  </button>`;

/* ================= AUTH ================= */
function gptPane() {
  if (S.gptFlow === 'idle') return `
    <div class="gpt-head">
      <span class="row-icon dark">${ic('sparkles')}</span>
      <div><div class="t">Вход через ChatGPT</div><div class="s">Агент работает на вашей подписке Plus или Pro через Codex. API-ключи не нужны.</div></div>
    </div>
    <ol class="steps"><li>Получите одноразовый код</li><li>Откройте chatgpt.com/device</li><li>Введите код и подтвердите</li></ol>
    <button class="btn btn-primary" data-action="gpt-code">Получить код</button>`;
  if (S.gptFlow === 'code') return `
    <div class="code-box"><span class="code">K7QF-92MD</span><button class="icon-btn" data-action="toast" data-msg="Код скопирован">${ic('copy')}</button></div>
    <button class="btn btn-secondary" data-action="toast" data-msg="Открываем chatgpt.com/device">${ic('arrow-up-right')} Открыть chatgpt.com/device</button>
    <div class="waiting"><span class="spinner"></span>Ожидаем подтверждения…</div>`;
  return `
    <div class="success">${ic('circle-check')} ChatGPT подключён · ${S.gpt.plan}</div>
    <button class="btn btn-primary" data-action="gpt-finish">Готово</button>`;
}

function renderAuth() {
  const registering = S.authMode === 'register';
  const accountForm = `
    <div class="stack" style="margin-top:16px">
      <div class="input-group">
        ${registering ? '<input id="in-name" type="text" placeholder="Ваше имя" autocomplete="name" maxlength="60">' : ''}
        <input id="in-email" type="email" placeholder="Email" autocomplete="email">
        <input id="in-password" type="password" placeholder="Пароль" autocomplete="${registering ? 'new-password' : 'current-password'}">
      </div>
      <button class="btn btn-primary" data-action="${registering ? 'register' : 'login'}">${registering ? 'Создать аккаунт' : 'Войти'}</button>
      ${registering ? '<p class="hint">Пароль должен содержать не менее 6 символов.</p>' : '<button class="link center-link" data-action="forgot-password">Забыли пароль?</button>'}
    </div>`;
  $('#screen-auth').innerHTML = `
    <div class="auth-top">
      <div class="brand"><img class="auth-brand-logo" src="assets/salavat-logo.png" alt="Салаватстекло"><span>AI-МАРКЕТОЛОГ</span></div>
      <span class="pill-muted">Mini App</span>
    </div>
    <h1 class="display">Марк,<br><em> ваш лучший маркетолог.</em></h1>
    <p class="lead">Автономный AI-маркетолог для вашей компании. Пишет, согласует и публикует — по вашему расписанию.</p>
    <div class="card glass auth-card">
      <div class="segmented">
        <button class="${!registering ? 'on' : ''}" data-action="auth-mode" data-v="login">Вход</button>
        <button class="${registering ? 'on' : ''}" data-action="auth-mode" data-v="register">Регистрация</button>
      </div>
      ${accountForm}
    </div>
    <p class="fineprint">Вход и регистрация защищены Firebase Authentication.</p>`;
  icons();
}

/* ================= VIEWS ================= */
function viewHome() {
  const t = tenant();
  const pend = myPosts().filter(p => p.status === 'pending');
  const nx = nextSlot();
  const aut = AUTONOMY.find(a => a.id === S.autonomy);
  return `
    <header class="topbar">
      <button class="company-pill glass" data-action="sheet" data-sheet="company"><span class="avatar">${t.initials}</span>${esc(t.name)}${ic('chevron-down', 'chev-sm')}</button>
      <button class="avatar light" data-action="tab" data-tab="profile">${S.user.initials}</button>
    </header>
    <div>
      <div class="eyebrow">РАБОЧЕЕ ПРОСТРАНСТВО · ${esc(t.industry).toUpperCase()}</div>
      <h1 class="title">Добрый день,<br><em>${esc(S.user.name)}.</em></h1>
    </div>
    <section class="company-hero">
      <div class="company-hero-art" aria-hidden="true"><span></span><span></span><span></span><span></span><span></span></div>
      <div class="company-hero-copy">
        <div class="brand-kicker">КАРТОЧКА КОМПАНИИ</div>
        <h2>${esc(t.name.toUpperCase())}</h2>
        <p>${esc(t.full)} — ${esc(t.industry)}. Здесь собраны публикации, расписание и состояние каналов компании.</p>
        <div class="tags"><span class="tag">${esc(t.industry)}</span><span class="tag">${S.gpt.connected ? 'ИИ подключён' : 'Настройка ИИ'}</span><span class="tag">${platList(['tg','vk'])}</span></div>
      </div>
    </section>
    <div class="company-stats">
      ${[['На согласовании', pend.length, 'pending'], ['Одобрено', myPosts().filter(p => p.status === 'approved').length, 'approved'], ['Опубликовано', myPosts().filter(p => p.status === 'published').length, 'published'], ['Отклонено / ошибки', myPosts().filter(p => p.status === 'rejected').length, 'rejected']].map(([label, count, key]) => `<button class="company-stat" data-action="stat-filter" data-v="${key}"><span>${label}</span><b>${count}</b></button>`).join('')}
    </div>
    ${!S.gpt.connected ? `
    <div class="card glass connect">
      <span class="row-icon dark">${ic('sparkles')}</span>
      <div class="row-main"><div class="row-title">Подключите ChatGPT</div><div class="row-sub wrap">Без него агент не сможет писать посты</div></div>
      <button class="btn btn-primary sm" data-action="sheet" data-sheet="gpt">Подключить</button>
    </div>` : ''}
    <div class="card glass status-card">
      <div class="row-between">
        <div class="status-line"><span class="dot ${S.paused ? 'off' : ''}"></span>${S.paused ? 'Агент на паузе' : 'Агент работает'}</div>
        ${sw(!S.paused, 'toggle-pause')}
      </div>
      <div class="next">
        <div class="small muted">Следующая публикация</div>
        <div class="next-time">${nx ? nx.label : 'Нет слотов'}</div>
        <div class="small muted">${nx ? `${esc(nx.slot.title)} · ${platList(nx.slot.platforms)}` : 'Добавьте слот в расписании'}</div>
      </div>
      <div class="tags"><span class="tag">${S.autonomy} · ${aut.t}</span><span class="tag">${S.gpt.connected ? 'ChatGPT ' + S.gpt.plan : 'ИИ не подключён'}</span></div>
    </div>

    <div class="section-head"><h2>Ждут согласования</h2>${pend.length ? `<button class="link" data-action="tab" data-tab="content">Все</button>` : ''}</div>
    ${pend.length ? `<div class="hscroll">${pend.map(postCard).join('')}</div>`
      : `<div class="card glass empty">${ic('circle-check')}<div>Всё согласовано</div></div>`}

    <div class="section-head"><h2>Эта неделя</h2><button class="link" data-action="tab" data-tab="analytics">Подробнее</button></div>
    <div class="grid2">${kpi('Охват', '48 250', '+18%')}${kpi('Вовлечённость', '4,8%', '+0,6 п.п.')}${kpi('Переходы', '1 842', '+9%')}${kpi('Заявки', '64', '+12')}</div>

    <div class="section-head"><h2>Быстрые действия</h2></div>
    <div class="list glass">
      ${row({ icon: 'pen-line', title: 'Новый пост', sub: 'Тема → черновик с тремя заголовками', action: 'sheet', data: 'data-sheet="newpost"' })}
      ${row({ icon: 'calendar-plus', title: 'Добавить в расписание', sub: 'Свои дни, время и площадки', action: 'new-slot' })}
      ${row({ icon: 'layout-grid', title: 'Контент-план на неделю', sub: 'Черновики по вашим слотам', action: 'gen-plan' })}
    </div>

    <div class="section-head"><div><div class="brand-kicker">НАПРАВЛЕНИЯ И ТОН</div><h2>Примеры постов</h2></div><span class="small muted">Стиль бренда</span></div>
    <p class="style-intro">Живой корпоративный голос: факты, люди и события — в фирменной бирюзово-синей палитре. Выберите пример, чтобы начать черновик в этом направлении.</p>
    <div class="style-gallery">
      ${[
        ['team-story', 'Люди компании', 'Портрет сотрудника · уважительно и по делу', 'assets/post-examples/people.png'],
        ['company-news', 'Новости и достижения', 'Понятный повод · благодарность команде', 'assets/post-examples/vote.png'],
        ['team-event', 'События и спорт', 'Энергичный анонс · расписание и призыв', 'assets/post-examples/sport.png']
      ].map(([v, title, sub, img]) => `<button class="style-card" data-action="style-example" data-v="${v}"><img src="${img}" alt="Пример поста: ${title}" loading="lazy"><span class="style-card-copy"><b>${title}</b><small>${sub}</small><span class="style-use">Использовать стиль ${ic('arrow-up-right')}</span></span></button>`).join('')}
    </div>`;
}

function viewContent() {
  const all = myPosts();
  const cnt = s => all.filter(p => p.status === s).length;
  const filters = [['all', 'Все', 0], ['pending', 'На согласовании', cnt('pending')], ['approved', 'Одобрено', cnt('approved')], ['published', 'Опубликовано', cnt('published')], ['draft', 'Черновики', cnt('draft')]];
  const list = S.filter === 'all' ? all.filter(p => p.status !== 'rejected') : all.filter(p => p.status === S.filter);
  const showBulk = cnt('pending') > 1 && (S.filter === 'all' || S.filter === 'pending');
  return `
    <header class="page-head"><h1 class="title">Контент</h1><button class="icon-btn glass" data-action="sheet" data-sheet="newpost">${ic('plus')}</button></header>
    <div class="segmented scroll">${filters.map(([k, l, n]) => `<button class="${S.filter === k ? 'on' : ''}" data-action="filter" data-v="${k}">${l}${n ? `<span class="cnt"> ${n}</span>` : ''}</button>`).join('')}</div>
    ${showBulk ? `<button class="btn btn-secondary sm block" data-action="approve-all">${ic('check-check')} Одобрить все (${cnt('pending')})</button>` : ''}
    ${list.length ? `<div class="list glass mt">${list.map(p => `
      <button class="row post-row" data-action="open-post" data-id="${p.id}">
        <img class="thumb" src="${p.image}" alt="">
        <span class="row-main"><span class="row-title">${esc(p.title)}</span>
        <span class="row-sub"><span class="dot ${STATUS[p.status].c}"></span>${STATUS[p.status].l} · ${esc(p.time)}</span></span>
        ${ic('chevron-right', 'chev')}
      </button>`).join('')}</div>`
      : `<div class="card glass empty mt">${ic('inbox')}<div>Здесь пока пусто</div></div>`}`;
}

function viewSchedule() {
  const sl = mySlots();
  const daySlots = sl.filter(s => s.days.includes(S.day)).sort((a, b) => a.time.localeCompare(b.time));
  return `
    <header class="page-head"><h1 class="title">Расписание</h1><button class="icon-btn glass" data-action="new-slot">${ic('plus')}</button></header>
    <div class="card glass tight row-between">
      <div class="row-main"><div class="row-title">Автопостинг</div><div class="row-sub">Одобренные посты выходят по слотам 24/7</div></div>
      ${sw(S.autopost, 'toggle-autopost')}
    </div>
    <div class="days glass mt">${DAYS.map((d, i) => {
      const has = sl.some(s => s.days.includes(i));
      return `<button class="day ${i === S.day ? 'on' : ''} ${i === TODAY ? 'today' : ''}" data-action="day" data-v="${i}">
        <span class="d1">${d}</span><span class="d2">${WEEK_DATES[i]}</span><span class="pip ${has ? '' : 'none'}"></span></button>`;
    }).join('')}</div>

    <div class="section-head"><h2>${DAYS_FULL[S.day]}${S.day === TODAY ? ' · сегодня' : ''}</h2><span class="small muted">${plural(daySlots.length)}</span></div>
    ${daySlots.length ? `<div class="list glass">${daySlots.map(s => `
      <button class="row" data-action="edit-slot" data-id="${s.id}">
        <span class="slot-time">${s.time}</span>
        <span class="row-main"><span class="row-title">${esc(s.title)}</span><span class="row-sub">${s.rubric} · ${platList(s.platforms)}${s.repeat === 'once' ? ' · один раз' : ''}</span></span>
        <span class="tag">${s.mode === 'auto' ? 'Авто' : 'Согласование'}</span>
        ${ic('chevron-right', 'chev')}
      </button>`).join('')}</div>`
      : `<div class="card glass empty">${ic('calendar')}<div>В этот день публикаций нет</div><button class="btn btn-secondary sm" data-action="new-slot" data-day="${S.day}">Добавить слот</button></div>`}

    <div class="section-head"><h2>Шаблоны</h2><span class="small muted">нажмите, чтобы настроить</span></div>
    <div class="presets">${PRESETS.map((p, i) => `<button class="preset glass" data-action="apply-preset" data-v="${i}"><span class="row-title">${p.t}</span><span class="row-sub">${p.s}</span></button>`).join('')}</div>

    <div class="section-head"><h2>Инфоповоды</h2></div>
    <div class="list glass">${EVENTS.map(e => `
      <div class="row"><span class="date-badge"><b>${e.d}</b><small>${e.m}</small></span>
        <span class="row-main"><span class="row-title">${e.t}</span><span class="row-sub">${e.s}</span></span>
        <button class="link small" data-action="event-post" data-t="${esc(e.t)}">В план</button>
      </div>`).join('')}</div>`;
}

function viewAnalytics() {
  const d = ANALYTICS[S.period];
  const max = Math.max(...d.bars);
  return `
    <header class="page-head"><h1 class="title">Аналитика</h1></header>
    <div class="segmented">
      <button class="${S.period === '7' ? 'on' : ''}" data-action="period" data-v="7">7 дней</button>
      <button class="${S.period === '30' ? 'on' : ''}" data-action="period" data-v="30">30 дней</button>
    </div>
    <div class="card glass mt">
      <div class="kpi-label">Охват</div>
      <div class="big-value">${d.reach}</div>
      <div class="kpi-delta">${d.delta}</div>
      <div class="bars">${d.bars.map((v, i) => `<div class="bar ${i === d.hi ? 'on' : ''}"><b style="height:${Math.round(v / max * 100)}%"></b><span>${d.labels[i]}</span></div>`).join('')}</div>
    </div>
    <div class="grid2 mt">${kpi('Вовлечённость', d.er, d.erd)}${kpi('Переходы', d.clicks, d.clicksd)}${kpi('Заявки', d.leads, d.leadsd)}${kpi('Расход ИИ', d.cost, d.costd, true)}</div>

    <div class="section-head"><h2>Что сработало</h2></div>
    <div class="insight glass">
      <div class="insight-label"><span class="dot green"></span>Лучший пост · ER 6,8%</div>
      <div class="insight-title">«Сравнение ГОСТов ЖБИ: где обманывают»</div>
      <div class="insight-text">Провокационный заголовок и таблица характеристик без воды. 142 репоста в профессиональные чаты.</div>
    </div>
    <div class="insight glass">
      <div class="insight-label"><span class="dot orange"></span>Слабее всех · ER 1,1%</div>
      <div class="insight-title">«Поздравляем с Днём качества!»</div>
      <div class="insight-text">Шаблонная открытка без пользы для B2B. Агент исключит такие форматы из плана.</div>
    </div>

    <div class="section-head"><h2>Эксперименты</h2></div>
    <div class="card glass">
      <div class="row-title">Заголовок с ценой vs заголовок с болью</div>
      <div class="ab">
        <div class="ab-row"><div class="row-between"><span class="muted">A · с ценой</span><span>CTR 4,2%</span></div><div class="progress"><b style="width:42%;background:rgba(0,0,0,.25)"></b></div></div>
        <div class="ab-row"><div class="row-between"><span>B · с болью</span><span><b>CTR 7,1%</b></span></div><div class="progress"><b style="width:71%"></b></div></div>
      </div>
      <div class="footnote" style="margin:12px 0 0">Вывод сохранён в память агента и учитывается в новых постах.</div>
    </div>

    <div class="section-head"><h2>Конкуренты</h2></div>
    <div class="list glass">
      ${row({ title: '@beton_holding', sub: '4 поста в неделю · акции, доставка', right: '<span class="row-value">ER 3,1%</span>', chev: false })}
      ${row({ title: '@glavstroy_materialy', sub: '7 постов в неделю · видео со строек', right: '<span class="row-value">ER 4,5%</span>', chev: false })}
      ${row({ title: '@zhbi_center', sub: '2 поста в неделю · прайсы', right: '<span class="row-value">ER 2,0%</span>', chev: false })}
    </div>

    <div class="section-head"><h2>Комментарии</h2><span class="small muted">12 за сутки</span></div>
    <div class="list glass">
      <div class="row"><span class="row-main"><span class="row-title">Иван: «Сколько доставка в Подольск?»</span><span class="row-sub"><span class="dot blue"></span>Заявка · черновик ответа готов</span></span><button class="link small" data-action="toast" data-msg="Ответ отправлен Ивану">Ответить</button></div>
      <div class="row"><span class="row-main"><span class="row-title">Сергей: «Водитель опоздал на 3 часа»</span><span class="row-sub"><span class="dot red"></span>Жалоба · передано менеджеру</span></span></div>
      <div class="row"><span class="row-main"><span class="row-title">Анна: «Есть ли сертификаты на М500?»</span><span class="row-sub"><span class="dot green"></span>Вопрос · отвечено по базе знаний</span></span></div>
    </div>`;
}

function viewProfile() {
  const critDone = S.crit.filter(c => c === 'pass').length;
  const avatar = S.user.avatar
    ? `<img class="profile-avatar-image" src="${S.user.avatar}" alt="Аватар профиля">`
    : esc(S.user.initials);
  return `
    <header class="page-head"><h1 class="title">Профиль</h1></header>
    <div class="card glass profile-card">
      <button class="avatar lg profile-avatar-button" data-action="choose-avatar" aria-label="Изменить аватар">${avatar}</button>
      <input id="profile-avatar-input" class="visually-hidden" type="file" accept="image/png,image/jpeg,image/webp" data-action="avatar-upload">
      <div class="row-main"><div class="p-name">${esc(S.user.fullName)}</div><div class="row-sub">${esc(S.user.email)}</div></div>
    </div>

    ${group('Аккаунт', [
      row({ icon: 'user', title: 'Личные данные', sub: `Вход: ${S.user.via}`, action: 'sheet', data: 'data-sheet="profile-edit"' }),
      row({ icon: 'cloud', title: 'Синхронизация компаний', sub: S.syncStatus, action: 'sync-companies' }),
      row({ icon: 'sliders-horizontal', title: 'Настройки приложения', sub: 'Тема, анимация и вид интерфейса', action: 'sheet', data: 'data-sheet="settings"' }),
      row({ icon: 'bell', title: 'Уведомления о согласовании', right: sw(true, 'noop'), chev: false })
    ])}

    ${group('ИИ-движок', [
      S.gpt.connected
        ? row({ icon: 'sparkles', title: 'ChatGPT', sub: `Подключено · ${S.gpt.plan} · Codex`, right: '<span class="dot"></span>', chev: false })
        : row({ icon: 'sparkles', title: 'ChatGPT', sub: 'Не подключено', action: 'sheet', data: 'data-sheet="gpt"', right: '<span class="row-value">Подключить</span>' }),
      S.gpt.connected ? row({ icon: 'activity', title: 'Проверить соединение', sub: S.gpt.ping || 'Тестовый запрос к модели', action: 'ping' }) : '',
      S.gpt.connected ? row({ icon: 'unplug', title: 'Отключить ChatGPT', action: 'gpt-disconnect', chev: false, cls: 'danger' }) : ''
    ], `<p class="footnote">Возможности: текст, ответы по JSON-схеме, веб-поиск, изображения.</p>`)}

    ${group('Компании', [
      ...S.tenants.map(t => row({ avatar: t.initials, title: t.full, sub: t.industry, action: 'switch-tenant', data: `data-id="${t.id}"`, right: t.id === S.tenantId ? ic('check', 'check-ic') : '', chev: false })),
      row({ icon: 'plus', title: 'Добавить компанию', action: 'open-wizard' })
    ])}

    ${group('Уровень автономности', AUTONOMY.map(a => row({ title: `${a.id} · ${a.t}`, sub: a.s, action: 'set-autonomy', data: `data-v="${a.id}"`, right: S.autonomy === a.id ? ic('check', 'check-ic') : '', chev: false })))}

    ${group('Каналы', Object.keys(PLATFORMS).map(k => row({
      icon: k === 'tg' ? 'send' : k === 'vk' ? 'users' : 'message-circle',
      title: PLATFORMS[k], sub: S.channels[k] ? 'Подключено · права на публикацию' : 'Тестовый режим',
      action: 'test-channel', data: `data-v="${k}"`, right: '<span class="row-value">Проверить</span>', chev: false
    })))}

    ${group('Навыки агента', S.skills.map(s => row({ title: s.name, sub: s.desc, right: sw(s.on, 'toggle-skill', s.id), chev: false })))}

    ${group('Бюджет', [`<div class="row col"><div class="row-between"><span class="row-title">Расход за ноябрь</span><span class="row-value">$24,80 из $100</span></div><div class="progress"><b style="width:24.8%"></b></div></div>`])}

    ${group('Безопасность', [
      row({ icon: 'shield-check', title: 'Проверка изоляции данных', sub: 'Компании не видят данные друг друга', action: 'sheet', data: 'data-sheet="rls"' }),
      row({ icon: S.paused ? 'play' : 'pause', title: S.paused ? 'Возобновить работу агента' : 'Остановить все публикации', action: 'kill', chev: false, cls: S.paused ? '' : 'danger' })
    ])}

    <div class="group-title">Приёмка · ${critDone} из 12</div>
    <div class="list glass">${CRITERIA.map((c, i) => `
      <div class="row" data-crit="${i}"><span class="crit-n">${i + 1}</span><span class="row-main"><span class="row-title">${c}</span></span>
      <span class="crit-st">${S.crit[i] === 'pass' ? ic('check') : S.crit[i] === 'run' ? '<span class="spinner"></span>' : '—'}</span></div>`).join('')}
    </div>
    <button class="btn btn-secondary mt" data-action="run-criteria">Прогнать проверки</button>

    <button class="btn btn-secondary mt-l danger-text" data-action="logout">Выйти</button>
    <p class="footnote center" style="margin-top:14px">Маркетолог° · макет v0.2</p>`;
}

const VIEWS = { home: viewHome, content: viewContent, schedule: viewSchedule, analytics: viewAnalytics, profile: viewProfile };

/* ================= SHEETS ================= */
function sheetGpt() {
  return `<div class="grabber"></div>
    <div class="sheet-head"><span></span><div class="sheet-title">ChatGPT</div><button class="icon-btn close" data-action="close-sheet">${ic('x')}</button></div>
    ${gptPane()}`;
}

function sheetCompany() {
  return `<div class="grabber"></div>
    <div class="sheet-head"><span></span><div class="sheet-title">Компании</div><button class="link strong" data-action="close-sheet">Готово</button></div>
    <div class="list solid">${S.tenants.map(t => row({ avatar: t.initials, title: t.full, sub: t.industry, action: 'switch-tenant', data: `data-id="${t.id}"`, right: t.id === S.tenantId ? ic('check', 'check-ic') : '', chev: false })).join('')}</div>
    <p class="footnote">Список компаний сохраняется в браузере на этом устройстве.</p>
    <button class="btn btn-secondary mt-l" data-action="open-wizard">${ic('plus')} Добавить компанию</button>`;
}

function sheetCompanySummary() {
  return `<div class="grabber"></div>
    <div class="sheet-head"><span></span><div class="sheet-title">Сводка по компаниям</div><button class="icon-btn close" data-action="close-sheet">${ic('x')}</button></div>
    <p class="sheet-p">Состояние контента во всех рабочих пространствах.</p>
    <div class="list solid">${S.tenants.map(company => {
      const posts = S.posts.filter(post => post.tenantId === company.id);
      const pending = posts.filter(post => post.status === 'pending').length;
      const published = posts.filter(post => post.status === 'published').length;
      return `<button class="row" data-action="switch-tenant" data-id="${company.id}"><span class="avatar">${esc(company.initials)}</span><span class="row-main"><span class="row-title">${esc(company.name)}</span><span class="row-sub">${pending} на согласовании · ${published} опубликовано</span></span>${ic('chevron-right','chev')}</button>`;
    }).join('')}</div>
    <button class="btn btn-secondary mt-l" data-action="open-wizard">${ic('plus')} Добавить компанию</button>`;
}

function sheetPost() {
  const p = S.posts.find(x => x.id === S.current);
  if (!p) return '';
  const st = STATUS[p.status];
  const nextV = 'v' + (parseInt(p.version.slice(1), 10) + 1);
  const txt = esc(p.text[S.platform] || p.text.tg) + (p.added ? `<mark>${esc(p.added)}</mark>` : '');
  const editPair = `<div class="btn-row"><button class="btn btn-secondary" data-action="show-edit">${ic('pen-line')} Правки</button><button class="btn btn-secondary" data-action="show-resched">${ic('clock')} Перенести</button></div>`;
  let actions;
  if (p.status === 'pending' || p.status === 'draft') {
    actions = `<button class="btn btn-primary" data-action="approve-post">${ic('check')} Одобрить</button>${editPair}<button class="link danger center-link" data-action="reject-post">Отклонить</button>`;
  } else if (p.status === 'approved') {
    actions = `<div class="queue-note">${ic('circle-check')} Одобрено · выйдет ${esc(p.time)}</div>${editPair}<button class="link center-link" data-action="unapprove">Отменить одобрение</button>`;
  } else if (p.status === 'published') {
    actions = `<div class="queue-note muted">Опубликовано · ${esc(p.time)}</div><button class="btn btn-secondary danger-text" data-action="unpublish">Отозвать публикацию</button>`;
  } else {
    actions = `<button class="btn btn-primary" data-action="unapprove">Вернуть на согласование</button>`;
  }
  return `<div class="grabber"></div>
    <div class="sheet-head"><span class="status-chip"><span class="dot ${st.c}"></span>${st.l}</span><span class="mono muted">${p.version} · ${p.hash}</span><button class="icon-btn close" data-action="close-sheet">${ic('x')}</button></div>
    <h2 class="sheet-h">${esc(p.title)}</h2>
    <div class="small muted">${p.rubric} · ${esc(p.time)}</div>

    ${p.platforms.length > 1 ? `<div class="segmented mt">${p.platforms.map(k => `<button class="${S.platform === k ? 'on' : ''}" data-action="post-platform" data-v="${k}">${PLATFORMS[k]}</button>`).join('')}</div>` : ''}

    <div class="cover"><img src="${p.image}" alt=""><div class="cover-text"><small>${esc(tenantOf(p).name)}</small><b>${esc(p.hooks[p.hook])}</b></div></div>

    <div class="form-label">Заголовок · ${p.hooks.length > 1 ? 'выберите вариант' : 'выбран'}</div>
    <div class="list solid">${p.hooks.map((h, i) => `<button class="row ${i === p.hook ? 'on' : ''}" data-action="pick-hook" data-v="${i}"><span class="radio"></span><span class="row-main"><span class="row-title wrap">${esc(h)}</span></span></button>`).join('')}</div>

    <div class="form-label">Текст · ${PLATFORMS[S.platform]}</div>
    <div class="textcard">${txt}</div>

    <div class="form-label">Факты из базы знаний</div>
    <div class="list solid">${p.facts.map(f => `<div class="row"><span class="fact-id">${f.id}</span><span class="row-main"><span class="row-title wrap" style="font-size:14px">${esc(f.t)}</span></span></div>`).join('')}</div>

    <div class="form-label">Проверки</div>
    <div class="list solid">
      <div class="row"><span class="row-main"><span class="row-title">Тон бренда</span></span><span class="row-value">${String(p.score).replace('.', ',')} / 10</span></div>
      <div class="row"><span class="row-main"><span class="row-title">ФЗ «О рекламе»</span></span><span class="row-value ok">Без рисков</span></div>
      <div class="row"><span class="row-main"><span class="row-title">Стоп-слова</span></span><span class="row-value">Не найдены</span></div>
    </div>

    <div id="edit-box" class="hidden">
      <div class="form-label">Что изменить?</div>
      <div class="input-group"><textarea id="edit-text">Добавь телефон отдела продаж и упомяни доставку манипулятором</textarea></div>
      <button class="btn btn-primary mt" data-action="apply-edit">Создать ${nextV}</button>
    </div>
    <div id="resched-box" class="hidden">
      <div class="form-label">Новое время</div>
      <div class="chips">${['Сегодня, 17:00', 'Завтра, 10:00', 'Завтра, 14:00', 'Пн, 10:00'].map(t => `<button class="chip" data-action="set-time" data-v="${t}">${t}</button>`).join('')}</div>
    </div>

    <div class="sheet-actions">${actions}</div>`;
}

function sheetNewPost() {
  return `<div class="grabber"></div>
    <div class="sheet-head"><button class="link" data-action="close-sheet">Отмена</button><div class="sheet-title">Новый пост</div><span></span></div>
    <div class="input-group"><textarea id="np-topic" placeholder="О чём пост? Например: скидка 10% на газобетон до конца месяца">${esc(S.npTopic)}</textarea></div>
    <div class="form-label">Рубрика</div>
    <div class="chips" id="np-rubric">${RUBRICS.map((r, i) => `<button class="chip ${i === 0 ? 'on' : ''}" data-action="pick" data-v="${r}">${r}</button>`).join('')}</div>
    <div class="form-label">Площадки</div>
    <div class="chips" id="np-plat">${Object.keys(PLATFORMS).map(k => `<button class="chip ${k !== 'max' ? 'on' : ''}" data-action="toggle" data-v="${k}">${PLATFORMS[k]}</button>`).join('')}</div>
    <div class="form-label">Когда публиковать</div>
    <div class="segmented" id="np-when">
      <button class="on" data-action="pick" data-v="slot">В ближайший слот</button>
      <button data-action="pick" data-v="later">Решу позже</button>
    </div>
    <div class="form-label">Источники</div>
    <div class="list solid">${row({ icon: 'book-open', title: 'База знаний компании', sub: '42 факта · прайсы, ГОСТы, кейсы', right: ic('check', 'check-ic'), chev: false })}</div>
    <div class="sheet-actions"><button class="btn btn-primary" data-action="create-post" id="np-btn">${ic('sparkles')} Создать черновик</button></div>`;
}

function sheetSlot() {
  const editing = !!S.editSlotId;
  const s = editing ? S.slots.find(x => x.id === S.editSlotId) : S.slotDraft;
  const times = editing ? [s.time] : s.times;
  const autoHint = ['L0', 'L1'].includes(S.autonomy)
    ? `Автопубликация сработает на уровне L2 и выше. Сейчас ${S.autonomy} — посты всё равно попадут на согласование.`
    : 'Посты рубрики будут выходить без согласования, в пределах лимитов.';
  return `<div class="grabber"></div>
    <div class="sheet-head"><button class="link" data-action="close-sheet">Отмена</button><div class="sheet-title">${editing ? 'Слот' : 'Новый слот'}</div><button class="link strong" data-action="save-slot">Готово</button></div>

    <div class="input-group"><input id="slot-title" placeholder="Название, например «Кейс недели»" value="${esc(s.title)}"></div>

    <div class="form-label">Дни недели</div>
    <div class="daypick" id="slot-days">${DAYS.map((d, i) => `<button class="${s.days.includes(i) ? 'on' : ''}" data-action="toggle" data-v="${i}">${d}</button>`).join('')}</div>
    <div class="chips mt-s">${[['Будни', '0,1,2,3,4'], ['Каждый день', '0,1,2,3,4,5,6'], ['Выходные', '5,6'], ['Сбросить', '']].map(([l, v]) => `<button class="chip ghost" data-action="days-preset" data-v="${v}">${l}</button>`).join('')}</div>

    <div class="form-label">Время публикации</div>
    <div class="input-group" id="slot-times">${times.map(t => timeRow(t, !editing)).join('')}</div>
    ${editing ? '' : `<button class="link small" style="margin:10px 4px 0" data-action="add-time">+ Добавить время</button>`}

    <div class="form-label">Рубрика</div>
    <div class="chips" id="slot-rubric">${RUBRICS.map(r => `<button class="chip ${r === s.rubric ? 'on' : ''}" data-action="pick" data-v="${r}">${r}</button>`).join('')}</div>

    <div class="form-label">Площадки</div>
    <div class="list solid" id="slot-plat">${Object.keys(PLATFORMS).map(k => `<div class="row"><span class="row-main"><span class="row-title">${PLATFORMS[k]}</span></span><label class="switch"><input type="checkbox" data-plat="${k}" ${s.platforms.includes(k) ? 'checked' : ''}><span></span></label></div>`).join('')}</div>

    <div class="form-label">Публикация</div>
    <div class="segmented" id="slot-mode">
      <button class="${s.mode === 'approve' ? 'on' : ''}" data-action="pick" data-v="approve">С согласованием</button>
      <button class="${s.mode === 'auto' ? 'on' : ''}" data-action="pick" data-v="auto">Автоматически</button>
    </div>
    <p class="hint">${autoHint}</p>

    <div class="form-label">Повтор</div>
    <div class="segmented" id="slot-repeat">
      <button class="${s.repeat === 'weekly' ? 'on' : ''}" data-action="pick" data-v="weekly">Каждую неделю</button>
      <button class="${s.repeat === 'once' ? 'on' : ''}" data-action="pick" data-v="once">Один раз</button>
    </div>

    ${editing ? `<button class="btn btn-secondary danger-text mt-l" data-action="delete-slot">Удалить слот</button>` : ''}`;
}
const timeRow = (t, removable) =>
  `<div class="time-row"><span>Время</span><span style="display:flex;align-items:center;gap:4px"><input type="time" value="${t}">${removable ? `<button class="icon-btn" data-action="remove-time">${ic('x')}</button>` : ''}</span></div>`;

function sheetWizard() {
  const w = S.wiz;
  const titles = [
    ['Профиль компании', 'Как называется бизнес и чем он занимается.'],
    ['База знаний', 'Агент опирается только на подтверждённые факты.'],
    ['Каналы', 'Куда агент будет публиковать посты.'],
    ['Правила', 'Тон, запреты и уровень самостоятельности.'],
    ['Расписание', 'Можно выбрать шаблон или настроить позже.']
  ];
  const [h, p] = titles[w.step - 1];
  let body = '';
  if (w.step === 1) body = `
    <div class="input-group">
      <input id="wz-name" placeholder="Название, например «ДентаЛюкс»" value="${esc(w.name)}">
      <input id="wz-site" placeholder="Сайт (необязательно)" value="${esc(w.site)}">
    </div>
    <div class="form-label">Сфера</div>
    <div class="chips" id="wz-ind">${['Стройка', 'Медицина', 'Кафе и рестораны', 'Образование', 'Ритейл', 'Услуги'].map(x => `<button class="chip ${w.industry === x ? 'on' : ''}" data-action="wz-ind" data-v="${x}">${x}</button>`).join('')}</div>`;
  if (w.step === 2) body = `
    <button class="dropzone" data-action="wz-upload">${ic('upload')}<span class="row-title">Загрузить файлы</span><span class="row-sub wrap">Прайсы, каталоги, презентации — PDF, DOCX, XLSX</span></button>
    ${w.files.length ? `<div class="list solid mt">${w.files.map(f => row({ icon: 'file-text', title: f, sub: 'Факты извлечены', right: ic('check', 'check-ic'), chev: false })).join('')}</div>` : ''}
    <div class="list solid mt">${row({ icon: 'globe', title: 'Изучить сайт компании', sub: 'Агент соберёт факты со страниц', right: sw(true, 'noop'), chev: false })}</div>`;
  if (w.step === 3) body = `
    <div class="list solid">${Object.keys(PLATFORMS).map(k => `<div class="row with-icon"><span class="row-icon">${ic(k === 'tg' ? 'send' : k === 'vk' ? 'users' : 'message-circle')}</span><span class="row-main"><span class="row-title">${PLATFORMS[k]}</span><span class="row-sub">${w.ch[k] ? 'Права проверены' : 'Не подключено'}</span></span><button class="chip ${w.ch[k] ? 'ok' : ''}" data-action="wz-connect" data-v="${k}">${w.ch[k] ? 'Подключено' : 'Подключить'}</button></div>`).join('')}</div>
    <p class="footnote">Для Telegram добавьте бота администратором канала.</p>`;
  if (w.step === 4) body = `
    <div class="form-label" style="margin-top:0">Тон</div>
    <div class="segmented" id="wz-tone"><button class="on" data-action="pick">Деловой</button><button data-action="pick">Дружелюбный</button><button data-action="pick">Экспертный</button></div>
    <div class="form-label">Запрещённые слова</div>
    <div class="input-group"><textarea placeholder="лучший, №1, гарантированно…" style="min-height:80px"></textarea></div>
    <div class="form-label">Автономность</div>
    <div class="list solid" id="wz-aut">${AUTONOMY.map(a => `<button class="row ${a.id === 'L1' ? 'on' : ''}" data-action="pick" data-v="${a.id}"><span class="radio"></span><span class="row-main"><span class="row-title">${a.id} · ${a.t}</span><span class="row-sub">${a.s}</span></span></button>`).join('')}</div>`;
  if (w.step === 5) body = `
    <div class="list solid" id="wz-preset">
      ${PRESETS.map((pr, i) => `<button class="row ${i === 0 ? 'on' : ''}" data-action="pick" data-v="${i}"><span class="radio"></span><span class="row-main"><span class="row-title">${pr.t}</span><span class="row-sub">${pr.s}</span></span></button>`).join('')}
      <button class="row" data-action="pick" data-v="-1"><span class="radio"></span><span class="row-main"><span class="row-title">Настрою сам</span><span class="row-sub">Пустое расписание</span></span></button>
    </div>`;
  return `<div class="grabber"></div>
    <div class="sheet-head">${w.step > 1 ? `<button class="link" data-action="wiz-back">Назад</button>` : `<button class="link" data-action="close-sheet">Отмена</button>`}<div class="sheet-title">Шаг ${w.step} из 5</div><span></span></div>
    <div class="wsteps">${[1, 2, 3, 4, 5].map(i => `<span class="${i <= w.step ? 'on' : ''}"></span>`).join('')}</div>
    <h2 class="sheet-h">${h}</h2><p class="sheet-p">${p}</p>
    ${body}
    <div class="sheet-actions"><button class="btn btn-primary" data-action="wiz-next">${w.step < 5 ? 'Далее' : 'Подключить компанию'}</button></div>`;
}

function sheetRls() {
  const other = S.tenants.find(t => t.id !== S.tenantId) || tenant();
  return `<div class="grabber"></div>
    <div class="sheet-head"><span></span><div class="sheet-title">Изоляция данных</div><button class="link strong" data-action="close-sheet">Готово</button></div>
    <p class="sheet-p">Проверим, что компания «${esc(other.name)}» не видит данные «${esc(tenant().name)}». Запрос выполняется от имени другой компании.</p>
    <div class="code-block" id="rls-log"><span class="muted">Нажмите «Запустить проверку»</span></div>
    <div class="sheet-actions"><button class="btn btn-primary" data-action="run-rls">Запустить проверку</button></div>`;
}

function sheetSettings() {
  const themeLabel = { light: 'Светлая', dark: 'Тёмная', system: 'Как в системе' }[S.theme];
  return `<div class="grabber"></div>
    <div class="sheet-head"><span></span><div class="sheet-title">Настройки</div><button class="icon-btn close" data-action="close-sheet">${ic('x')}</button></div>
    <div class="settings-section">
      <div class="settings-label">Тема оформления</div>
      <div class="theme-options">
        ${[['light', 'sun', 'Светлая'], ['dark', 'moon', 'Тёмная'], ['system', 'monitor', 'Как в системе']].map(([value, icon, label]) => `<button class="theme-option ${S.theme === value ? 'on' : ''}" data-action="set-theme" data-v="${value}">${ic(icon)}<span>${label}</span>${S.theme === value ? ic('check', 'theme-check') : ''}</button>`).join('')}
      </div>
      <div class="settings-label">Доступность</div>
      <div class="list solid">
        <div class="row"><span class="row-icon">${ic('accessibility')}</span><span class="row-main"><span class="row-title">Уменьшить анимацию</span><span class="row-sub">Меньше переходов и эффектов</span></span>${sw(S.reduceMotion, 'toggle-reduced-motion')}</div>
      </div>
      <p class="footnote">Настройки сохраняются на этом устройстве.</p>
    </div>`;
}

function sheetProfileEdit() {
  return `<div class="grabber"></div>
    <div class="sheet-head"><span></span><div class="sheet-title">Личные данные</div><button class="icon-btn close" data-action="close-sheet">${ic('x')}</button></div>
    <p class="sheet-p">Измените имя, которое будет отображаться в Mark.</p>
    <div class="form-label">Имя</div>
    <div class="input-group"><input id="profile-name" type="text" value="${esc(S.user.fullName)}" autocomplete="name" maxlength="60"></div>
    <div class="form-label">Email</div>
    <div class="input-group"><input type="email" value="${esc(S.user.email)}" disabled></div>
    <p class="hint">Email используется для входа и пока не изменяется из приложения.</p>
    <div class="sheet-actions">
      <button class="btn btn-primary" data-action="save-profile">Сохранить изменения</button>
      <button class="btn btn-secondary" data-action="reset-password">Отправить письмо для смены пароля</button>
    </div>`;
}

const SHEETS = { gpt: sheetGpt, company: sheetCompany, 'company-summary': sheetCompanySummary, post: sheetPost, newpost: sheetNewPost, slot: sheetSlot, wizard: sheetWizard, rls: sheetRls, settings: sheetSettings, 'profile-edit': sheetProfileEdit };

/* ---------- render ---------- */
function render() {
  const authed = !!S.user;
  $('#screen-auth').classList.toggle('hidden', authed);
  $('#screen-app').classList.toggle('hidden', !authed);
  if (authed) renderView(); else renderAuth();
}
function renderView(keep) {
  const v = $('#view');
  const st = v.scrollTop;
  v.innerHTML = VIEWS[S.tab]();
  v.classList.remove('enter');
  if (keep) v.scrollTop = st;
  else { v.scrollTop = 0; void v.offsetWidth; v.classList.add('enter'); }
  $$('.tab').forEach(b => b.classList.toggle('on', b.dataset.tab === S.tab));
  renderSidebar();
  icons();
}
function renderSidebar() {
  const el = $('#sidebar');
  if (!el) return;
  const t = tenant();
  el.innerHTML = `<div class="sidebar-brand"><img src="assets/salavat-logo.png" alt="Салаватстекло"><span>AI-МАРКЕТОЛОГ</span></div>
    <button class="sidebar-add" data-action="open-wizard">${ic('plus')} Новая компания</button>
    <button class="sidebar-overview" data-action="sheet" data-sheet="company-summary">${ic('chart-no-axes-combined')} Сводка по компаниям</button>
    <div class="sidebar-label">ПОДКЛЮЧЁННЫЕ КОМПАНИИ</div>
    <div class="sidebar-companies">${S.tenants.map(company => `<button class="sidebar-company ${company.id === S.tenantId ? 'active' : ''}" data-action="switch-tenant" data-id="${company.id}"><span class="sidebar-company-mark">${esc(company.initials)}</span><span>${esc(company.name)}</span>${company.id === S.tenantId ? '<i class="sidebar-live"></i>' : ''}</button>`).join('')}</div>
    <div class="sidebar-label">РАЗДЕЛЫ</div>
    <nav class="sidebar-nav">${[['home','layout-dashboard','Обзор'],['content','files','Контент'],['schedule','calendar-days','Расписание'],['analytics','chart-no-axes-combined','Аналитика'],['profile','settings-2','Настройки']].map(([tab, icon, title]) => `<button class="sidebar-link ${S.tab === tab ? 'active' : ''}" data-action="tab" data-tab="${tab}">${ic(icon)} ${title}</button>`).join('')}</nav>
    <div class="sidebar-foot"><span class="sidebar-user">${esc(S.user ? S.user.initials : '')}</span><span><b>${esc(S.user ? S.user.fullName : '')}</b><small>Личный кабинет</small></span><button class="sidebar-settings" data-action="tab" data-tab="profile" aria-label="Настройки">${ic('ellipsis')}</button></div>`;
}
function openSheet(name) {
  S.sheet = name;
  const layer = $('#sheet-layer');
  const sh = $('#sheet');
  sh.innerHTML = SHEETS[name]();
  sh.scrollTop = 0;
  layer.classList.remove('hidden');
  requestAnimationFrame(() => requestAnimationFrame(() => layer.classList.add('open')));
  icons();
}
function refreshSheet() {
  if (!S.sheet) return;
  const sh = $('#sheet');
  const st = sh.scrollTop;
  sh.innerHTML = SHEETS[S.sheet]();
  sh.scrollTop = st;
  icons();
}
function closeSheet() {
  const layer = $('#sheet-layer');
  layer.classList.remove('open');
  S.sheet = null;
  setTimeout(() => { if (!S.sheet) layer.classList.add('hidden'); }, 450);
}
function rerenderGpt() { if (S.user) refreshSheet(); else renderAuth(); }

/* ---------- domain actions ---------- */
function openPost(id) {
  const p = S.posts.find(x => x.id === id);
  if (!p) return;
  S.current = id;
  if (!p.platforms.includes(S.platform)) S.platform = p.platforms[0];
  openSheet('post');
}
function openSlot({ editId = null, preset = null, day = null } = {}) {
  S.editSlotId = editId;
  if (!editId) {
    S.slotDraft = preset
      ? { title: preset.title, days: [...preset.days], times: [preset.time], rubric: 'Продукт', platforms: ['tg'], mode: 'approve', repeat: 'weekly' }
      : { title: '', days: [day ?? S.day], times: ['10:00'], rubric: 'Продукт', platforms: ['tg'], mode: 'approve', repeat: 'weekly' };
  }
  openSheet('slot');
}
function needGpt() {
  if (S.gpt.connected) return false;
  toast('Сначала подключите ChatGPT');
  S.gptFlow = 'idle';
  openSheet('gpt');
  return true;
}
function makePost({ topic, rubric, platforms, time }) {
  const title = topic.length > 56 ? topic.slice(0, 56).trim() + '…' : topic;
  const t = tenant();
  return {
    id: 'p' + uid(), tenantId: S.tenantId, status: 'pending', version: 'v1', hash: hash(), time, rubric,
    title: title.charAt(0).toUpperCase() + title.slice(1), platforms, image: IMG[Math.floor(Math.random() * IMG.length)], hook: 0,
    hooks: [title.charAt(0).toUpperCase() + title.slice(1), `Что важно знать: ${title.toLowerCase()}`, `${t.name}: коротко о главном`],
    text: {
      tg: `${title.charAt(0).toUpperCase() + title.slice(1)}.\n\n— Подтверждено документами компании\n— Сроки и условия — без мелкого шрифта\n— Ответим на вопросы в комментариях\n\nПодробнее: ${t.name.toLowerCase()}.ru/go/${uid().slice(0, 4)}`,
      vk: `${title.charAt(0).toUpperCase() + title.slice(1)}.\n\nРассказываем подробно, с цифрами и фактами из нашей базы знаний.\n\n#${t.name.toLowerCase()}`,
      max: `${title.charAt(0).toUpperCase() + title.slice(1)}. Подробности — в профиле компании.`
    },
    facts: [{ id: 'F-102', t: 'База знаний компании · проверенный факт' }], score: 9.7, added: ''
  };
}

function firebaseErrorMessage(error) {
  const messages = {
    'auth/email-already-in-use': 'Этот email уже зарегистрирован',
    'auth/invalid-email': 'Проверьте корректность email',
    'auth/weak-password': 'Пароль должен содержать минимум 6 символов',
    'auth/invalid-credential': 'Неверный email или пароль',
    'auth/user-not-found': 'Пользователь не найден',
    'auth/wrong-password': 'Неверный email или пароль',
    'auth/operation-not-allowed': 'Регистрация по email отключена в настройках Firebase',
    'auth/network-request-failed': 'Нет соединения с Firebase. Проверьте интернет и повторите.'
  };
  return messages[error && error.code] || 'Не удалось выполнить операцию. Попробуйте ещё раз';
}

/* ---------- action map ---------- */
const A = {
  noop() {},
  toast: el => toast(el.dataset.msg),

  // auth
  'auth-mode': el => { S.authMode = el.dataset.v; renderAuth(); },
  async login() {
    if (!firebaseAuth) { toast('Firebase SDK не загрузился'); return; }
    const email = (($('#in-email') || {}).value || '').trim();
    const password = ($('#in-password') || {}).value || '';
    if (!email || !password) { toast('Введите email и пароль'); return; }
    try {
      const credential = await firebaseAuth.signInWithEmailAndPassword(email, password);
      const user = credential.user;
      const name = user.displayName || user.email.split('@')[0];
      S.user = { uid: user.uid, name, fullName: name, initials: name.slice(0, 1).toUpperCase(), email: user.email, via: 'Email', avatar: avatarFor(user.email) };
      await loadCompaniesFromServer(user);
      S.tab = 'home'; render(); toast('Вы вошли в аккаунт');
    } catch (error) { toast(firebaseErrorMessage(error)); }
  },
  async register() {
    if (!firebaseAuth) { toast('Firebase SDK не загрузился'); return; }
    const name = (($('#in-name') || {}).value || '').trim();
    const email = (($('#in-email') || {}).value || '').trim();
    const password = ($('#in-password') || {}).value || '';
    if (!name) { toast('Введите ваше имя'); return; }
    if (!email || !password) { toast('Введите email и пароль'); return; }
    if (password.length < 6) { toast('Пароль должен содержать не менее 6 символов'); return; }
    try {
      const credential = await firebaseAuth.createUserWithEmailAndPassword(email, password);
      await credential.user.updateProfile({ displayName: name });
      S.user = { uid: credential.user.uid, name, fullName: name, initials: name.slice(0, 1).toUpperCase(), email: credential.user.email, via: 'Email', avatar: avatarFor(email) };
      await loadCompaniesFromServer(credential.user);
      S.tab = 'home'; render(); toast('Аккаунт создан');
    } catch (error) { toast(firebaseErrorMessage(error)); }
  },
  'forgot-password'() {
    const email = (($('#in-email') || {}).value || '').trim();
    if (!firebaseAuth || !email) { toast('Сначала введите email'); return; }
    firebaseAuth.sendPasswordResetEmail(email).then(() => toast('Письмо для сброса пароля отправлено')).catch(error => toast(firebaseErrorMessage(error)));
  },
  'gpt-code'() {
    S.gptFlow = 'code'; rerenderGpt();
    setTimeout(() => { if (S.gptFlow === 'code') { S.gptFlow = 'done'; rerenderGpt(); } }, 2600);
  },
  'gpt-finish'() { S.gpt.connected = true; closeSheet(); renderView(true); toast('ChatGPT подключён'); },
  'gpt-disconnect'() { S.gpt.connected = false; S.gpt.ping = null; S.gptFlow = 'idle'; renderView(true); toast('ChatGPT отключён'); },
  ping() {
    S.gpt.ping = 'Проверяем…'; renderView(true);
    setTimeout(() => { S.gpt.ping = `OK · ${380 + Math.floor(Math.random() * 120)} мс · только что`; renderView(true); }, 900);
  },
  async logout() { if (firebaseAuth && firebaseAuth.currentUser) await firebaseAuth.signOut(); S.user = null; S.gptFlow = 'idle'; S.gpt.connected = false; S.authMode = 'login'; closeSheet(); render(); },
  'choose-avatar'() { $('#profile-avatar-input').click(); },
  'avatar-upload'(el) {
    const file = el.files && el.files[0];
    if (!file || !S.user) return;
    if (!file.type.startsWith('image/')) { toast('Выберите изображение'); return; }
    if (file.size > 2 * 1024 * 1024) { toast('Аватарка должна быть меньше 2 МБ'); return; }
    const reader = new FileReader();
    reader.onload = () => { S.user.avatar = reader.result; remember(`mark-avatar:${S.user.email}`, reader.result); renderView(true); toast('Аватарка сохранена'); };
    reader.readAsDataURL(file);
  },
  'set-theme': el => { S.theme = el.dataset.v; remember('mark-theme', S.theme); applyTheme(); refreshSheet(); },
  'toggle-reduced-motion': el => { S.reduceMotion = el.checked; remember('mark-reduce-motion', String(S.reduceMotion)); applyTheme(); },
  async 'save-profile'() {
    const name = (($('#profile-name') || {}).value || '').trim();
    if (!name) { toast('Введите имя'); return; }
    try {
      if (firebaseAuth && firebaseAuth.currentUser) await firebaseAuth.currentUser.updateProfile({ displayName: name });
      S.user.name = name; S.user.fullName = name; S.user.initials = name.slice(0, 1).toUpperCase();
      closeSheet(); renderView(true); toast('Профиль обновлён');
    } catch (error) { toast('Не удалось сохранить профиль'); }
  },
  async 'reset-password'() {
    if (!firebaseAuth || !S.user.email || S.user.email.startsWith('@')) { toast('Сброс пароля доступен для email-аккаунта'); return; }
    try { await firebaseAuth.sendPasswordResetEmail(S.user.email); toast('Письмо для смены пароля отправлено'); }
    catch (error) { toast(firebaseErrorMessage(error)); }
  },

  // nav
  tab: el => { S.tab = el.dataset.tab; renderView(); },
  sheet: el => { if (el.dataset.sheet === 'gpt') S.gptFlow = 'idle'; if (el.dataset.sheet === 'newpost') S.npTopic = ''; openSheet(el.dataset.sheet); },
  'close-sheet': () => closeSheet(),

  // generic pickers
  pick: el => { $$('.on', el.parentElement).forEach(b => b.classList.remove('on')); el.classList.add('on'); },
  toggle: el => el.classList.toggle('on'),

  // home
  'toggle-pause': el => { S.paused = !el.checked; toast(S.paused ? 'Агент поставлен на паузу' : 'Агент снова работает'); renderView(true); },
  'gen-plan'() {
    if (needGpt()) return;
    const sl = mySlots().slice(0, 3);
    if (!sl.length) { toast('Сначала добавьте слоты в расписание'); return; }
    sl.forEach(s => S.posts.unshift(makePost({ topic: `${s.title}: новый выпуск`, rubric: s.rubric, platforms: s.platforms, time: `${DAYS[s.days[0]]}, ${s.time}` })));
    toast(`План готов: ${sl.length} черновика ждут согласования`);
    renderView(true);
  },

  // company
  'switch-tenant': el => {
    S.tenantId = el.dataset.id; S.filter = 'all';
    remember('mark-active-company', S.tenantId);
    if (S.sheet === 'company' || S.sheet === 'company-summary') closeSheet();
    saveCompaniesToServer(); renderView(true); toast(`Компания: ${tenant().name}`);
  },
  'open-wizard'() { S.wiz = { step: 1, name: '', site: '', industry: 'Услуги', files: [], ch: { tg: false, vk: false, max: false } }; openSheet('wizard'); },
  'stat-filter': el => { S.filter = el.dataset.v; S.tab = 'content'; renderView(); },
  'style-example': el => {
    const prompts = {
      'team-story': 'Пост-история о сотруднике компании: роль, профессиональный путь, вклад в общее дело и короткое интервью. Тон уважительный, живой, без громких обещаний.',
      'company-news': 'Новость о достижении компании или команды: что произошло, почему это важно, кого поблагодарить и что будет дальше. Добавь понятный призыв к действию.',
      'team-event': 'Анонс корпоративного события: дата, время, место, участники и как коллегам поддержать команду. Тон энергичный и дружелюбный.'
    };
    S.npTopic = prompts[el.dataset.v] || ''; openSheet('newpost');
  },
  'wiz-back'() { S.wiz.step--; refreshSheet(); },
  'wz-ind': el => { S.wiz.industry = el.dataset.v; $$('#wz-ind .chip').forEach(b => b.classList.toggle('on', b === el)); },
  'wz-upload'() {
    const pool = ['Прайс_2026.xlsx', 'Каталог_услуг.pdf', 'О_компании.docx', 'Отзывы.pdf'];
    const f = pool[S.wiz.files.length % pool.length];
    S.wiz.files.push(f); refreshSheet(); toast(`${f} загружен`);
  },
  'wz-connect': el => { const k = el.dataset.v; S.wiz.ch[k] = !S.wiz.ch[k]; refreshSheet(); },
  'wiz-next'() {
    const w = S.wiz;
    if (w.step === 1) {
      w.name = $('#wz-name').value.trim(); w.site = $('#wz-site').value.trim();
      if (!w.name) { toast('Введите название компании'); return; }
      if (S.tenants.some(company => company.name.toLocaleLowerCase() === w.name.toLocaleLowerCase())) { toast('Компания с таким названием уже добавлена'); return; }
    }
    if (w.step === 4) { const a = $('#wz-aut .on'); w.autonomy = a ? a.dataset.v : 'L1'; }
    if (w.step < 5) { w.step++; refreshSheet(); $('#sheet').scrollTop = 0; return; }
    const presetIdx = parseInt(($('#wz-preset .on') || { dataset: { v: '-1' } }).dataset.v, 10);
    const id = 't' + uid();
    const initials = w.name.replace(/[«»"']/g, '').split(/\s+/).filter(x => x && !/^(ООО|ИП|АО|ЗАО)$/i.test(x)).map(x => x[0]).join('').slice(0, 2).toUpperCase() || 'К';
    S.tenants.push({ id, name: w.name, full: w.name, industry: w.industry, site: w.site, initials });
    remember('mark-companies', JSON.stringify(S.tenants));
    if (presetIdx >= 0) {
      const pr = PRESETS[presetIdx];
      S.slots.push({ id: 's' + uid(), tenantId: id, title: pr.title, rubric: 'Продукт', days: [...pr.days], time: pr.time, platforms: Object.keys(w.ch).filter(k => w.ch[k]).length ? Object.keys(w.ch).filter(k => w.ch[k]) : ['tg'], mode: 'approve', repeat: 'weekly' });
    }
    S.tenantId = id; closeSheet(); renderView(true);
    remember('mark-active-company', id);
    saveCompaniesToServer();
    toast(`Компания «${w.name}» подключена`);
  },
  'sync-companies'() {
    const user = firebaseAuth && firebaseAuth.currentUser;
    if (!user || !S.user || S.user.uid !== user.uid) { toast('Войдите через аккаунт по email, чтобы включить синхронизацию'); return; }
    loadCompaniesFromServer(user).then(() => renderView(true));
  },

  // content
  filter: el => { S.filter = el.dataset.v; renderView(true); },
  'approve-all'() {
    let n = 0;
    myPosts().forEach(p => { if (p.status === 'pending') { p.status = 'approved'; p.hash = hash(); n++; } });
    toast(`Одобрено постов: ${n}`); renderView(true);
  },
  'open-post': el => openPost(el.dataset.id),
  'post-platform': el => { S.platform = el.dataset.v; refreshSheet(); },
  'pick-hook': el => { const p = S.posts.find(x => x.id === S.current); p.hook = +el.dataset.v; refreshSheet(); },
  'approve-post'() {
    const p = S.posts.find(x => x.id === S.current);
    p.status = 'approved'; p.hash = hash();
    closeSheet(); renderView(true); toast(`Одобрено · ${p.version} · ${p.hash}`);
  },
  'reject-post'() {
    const p = S.posts.find(x => x.id === S.current);
    p.status = 'rejected'; closeSheet(); renderView(true); toast('Пост отклонён и не будет опубликован');
  },
  unapprove() {
    const p = S.posts.find(x => x.id === S.current);
    p.status = 'pending'; refreshSheet(); renderView(true); toast('Пост вернулся на согласование');
  },
  unpublish() {
    const p = S.posts.find(x => x.id === S.current);
    p.status = 'draft'; closeSheet(); renderView(true); toast('Публикация отозвана из каналов');
  },
  'show-edit'() {
    $('#resched-box').classList.add('hidden');
    $('#edit-box').classList.toggle('hidden');
    const sh = $('#sheet'); sh.scrollTo({ top: sh.scrollHeight, behavior: 'smooth' });
  },
  'show-resched'() {
    $('#edit-box').classList.add('hidden');
    $('#resched-box').classList.toggle('hidden');
    const sh = $('#sheet'); sh.scrollTo({ top: sh.scrollHeight, behavior: 'smooth' });
  },
  'apply-edit'() {
    const p = S.posts.find(x => x.id === S.current);
    const fb = $('#edit-text').value.trim();
    if (!fb) { toast('Опишите, что изменить'); return; }
    p.version = 'v' + (parseInt(p.version.slice(1), 10) + 1);
    p.hash = hash(); p.status = 'pending';
    p.added = /телефон/i.test(fb)
      ? '\n\nОтдел продаж: +7 (495) 120-40-50\nДоставка манипулятором в день заказа'
      : '\n\n' + fb.charAt(0).toUpperCase() + fb.slice(1);
    refreshSheet(); renderView(true);
    toast(`Создана ${p.version} — изменения подсвечены`);
  },
  'set-time': el => {
    const p = S.posts.find(x => x.id === S.current);
    p.time = el.dataset.v; refreshSheet(); renderView(true); toast(`Перенесено на ${p.time}`);
  },
  'create-post'(el) {
    const topic = $('#np-topic').value.trim();
    if (!topic) { toast('Опишите тему поста'); return; }
    if (needGpt()) { S.npTopic = topic; return; }
    const rubric = ($('#np-rubric .on') || { dataset: { v: 'Продукт' } }).dataset.v;
    const platforms = $$('#np-plat .on').map(b => b.dataset.v);
    if (!platforms.length) { toast('Выберите хотя бы одну площадку'); return; }
    const when = ($('#np-when .on') || { dataset: { v: 'slot' } }).dataset.v;
    const nx = nextSlot();
    el.disabled = true;
    el.innerHTML = '<span class="spinner"></span> Пишем черновик…';
    setTimeout(() => {
      const p = makePost({ topic, rubric, platforms, time: when === 'slot' && nx ? nx.label : 'Без времени' });
      S.posts.unshift(p);
      renderView(true);
      openPost(p.id);
      toast('Черновик готов — проверьте и одобрите');
    }, 1300);
  },

  // schedule
  'toggle-autopost': el => { S.autopost = el.checked; toast(S.autopost ? 'Автопостинг включён' : 'Автопостинг выключен'); },
  day: el => { S.day = +el.dataset.v; renderView(true); },
  'new-slot': el => openSlot({ day: el.dataset.day != null ? +el.dataset.day : null }),
  'apply-preset': el => openSlot({ preset: PRESETS[+el.dataset.v] }),
  'edit-slot': el => openSlot({ editId: el.dataset.id }),
  'days-preset': el => {
    const v = el.dataset.v ? el.dataset.v.split(',') : [];
    $$('#slot-days button').forEach(b => b.classList.toggle('on', v.includes(b.dataset.v)));
  },
  'add-time'() {
    const box = $('#slot-times');
    box.insertAdjacentHTML('beforeend', timeRow('15:00', true));
    icons();
  },
  'remove-time': el => {
    const rows = $$('#slot-times .time-row');
    if (rows.length <= 1) { toast('Нужно хотя бы одно время'); return; }
    el.closest('.time-row').remove();
  },
  'save-slot'() {
    const days = $$('#slot-days .on').map(b => +b.dataset.v);
    const times = [...new Set($$('#slot-times input[type=time]').map(i => i.value).filter(Boolean))];
    const platforms = $$('#slot-plat input:checked').map(i => i.dataset.plat);
    const rubric = ($('#slot-rubric .on') || { dataset: { v: 'Продукт' } }).dataset.v;
    const mode = $('#slot-mode .on').dataset.v;
    const repeat = $('#slot-repeat .on').dataset.v;
    const title = $('#slot-title').value.trim() || rubric;
    if (!days.length) { toast('Выберите хотя бы один день'); return; }
    if (!times.length) { toast('Укажите время'); return; }
    if (!platforms.length) { toast('Выберите площадку'); return; }
    if (S.editSlotId) {
      Object.assign(S.slots.find(x => x.id === S.editSlotId), { title, days, time: times[0], platforms, rubric, mode, repeat });
      toast('Слот обновлён');
    } else {
      times.forEach(time => S.slots.push({ id: 's' + uid(), tenantId: S.tenantId, title, rubric, days, time, platforms, mode, repeat }));
      toast(`Добавлено: ${daysLabel(days)} · ${times.join(', ')}`);
    }
    if (!days.includes(S.day)) S.day = days[0];
    closeSheet(); renderView(true);
  },
  'delete-slot'() {
    S.slots = S.slots.filter(x => x.id !== S.editSlotId);
    closeSheet(); renderView(true); toast('Слот удалён');
  },
  'event-post': el => { S.npTopic = `${el.dataset.t}: пост к инфоповоду`; openSheet('newpost'); },

  // analytics
  period: el => { S.period = el.dataset.v; renderView(true); },

  // profile
  'set-autonomy': el => { S.autonomy = el.dataset.v; renderView(true); toast(`Автономность: ${S.autonomy}`); },
  'test-channel': el => toast(S.channels[el.dataset.v] ? `${PLATFORMS[el.dataset.v]}: права на публикацию есть` : `${PLATFORMS[el.dataset.v]}: работает в тестовом режиме`),
  'toggle-skill': el => { const s = S.skills.find(x => x.id === el.dataset.id); s.on = el.checked; toast(`${s.name}: ${s.on ? 'включён' : 'выключен'}`); },
  kill() { S.paused = !S.paused; renderView(true); toast(S.paused ? 'Все публикации остановлены' : 'Агент снова работает'); },
  'run-criteria'() {
    S.crit = CRITERIA.map(() => 'run'); renderView(true);
    CRITERIA.forEach((_, i) => setTimeout(() => {
      S.crit[i] = 'pass';
      const cell = $(`[data-crit="${i}"] .crit-st`);
      if (cell) { cell.innerHTML = ic('check'); icons(); }
      if (i === CRITERIA.length - 1) { renderView(true); toast('Все 12 проверок пройдены'); }
    }, 250 + i * 180));
  },
  'run-rls'(el) {
    const other = S.tenants.find(t => t.id !== S.tenantId) || tenant();
    const log = $('#rls-log');
    el.disabled = true;
    const lines = [
      `<span class="q">SET app.tenant_id = '${other.id}';</span>`,
      `<span class="q">SELECT count(*) FROM facts WHERE tenant_id = '${S.tenantId}';</span>`,
      `<span class="ok">→ 0 строк</span>`,
      `<span class="q">SELECT count(*) FROM posts WHERE tenant_id = '${S.tenantId}';</span>`,
      `<span class="ok">→ 0 строк</span>`,
      `<span class="ok">✓ Изоляция подтверждена. Утечек нет.</span>`
    ];
    log.innerHTML = '';
    lines.forEach((l, i) => setTimeout(() => {
      log.insertAdjacentHTML('beforeend', `<div>${l}</div>`);
      if (i === lines.length - 1) { el.disabled = false; el.textContent = 'Проверить ещё раз'; }
    }, 350 * (i + 1)));
  }
};

/* ---------- event delegation ---------- */
document.addEventListener('click', e => {
  const el = e.target.closest('[data-action]');
  if (!el) return;
  const fn = A[el.dataset.action];
  if (fn) fn(el, e);
});

document.addEventListener('change', e => {
  const el = e.target.closest('[data-action]');
  if (!el) return;
  const fn = A[el.dataset.action];
  if (fn) fn(el, e);
});

applyTheme();
if (firebaseAuth) firebaseAuth.onAuthStateChanged(async user => {
  if (!user) {
    if (S.user && S.user.uid) { S.user = null; render(); }
    return;
  }
  if (S.user && S.user.uid === user.uid) return;
  const name = user.displayName || user.email.split('@')[0];
  S.user = { uid: user.uid, name, fullName: name, initials: name.slice(0, 1).toUpperCase(), email: user.email, via: 'Email', avatar: avatarFor(user.email) };
  await loadCompaniesFromServer(user);
  render();
});
render();
