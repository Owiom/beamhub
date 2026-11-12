'use strict';

const state = {
  view: 'catalog',
  cat: 'all',
  q: '',
  source: 'all',
  sort: 'new',
  offset: 0,
  limit: 48,
  total: 0,
  items: [],
  events: 0,
  server: null,
  loading: false,
};

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};

const esc = (value) => String(value ?? '')
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

const TOKEN = (document.querySelector('meta[name="beamhub-token"]') || {}).content || '';

async function api(path, options) {
  const opts = { ...(options || {}) };
  if ((opts.method || 'GET').toUpperCase() !== 'GET') {
    opts.headers = { ...(opts.headers || {}), 'X-BeamHub-Token': TOKEN };
  }
  const response = await fetch(path, opts);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || data.message || `Ошибка ${response.status}`);
  }
  return data;
}

function toast(text, kind = '', ttl = 5200) {
  const node = el('div', `toast ${kind}`, text);
  $('#toasts').appendChild(node);
  setTimeout(() => {
    node.style.opacity = '0';
    node.style.transition = 'opacity .3s';
    setTimeout(() => node.remove(), 320);
  }, ttl);
}

function imgUrl(src) {
  return src ? `/api/img?u=${encodeURIComponent(src)}` : '';
}

/* ------------------------------------------------------------- catalog */
function skeleton(count = 8) {
  const frag = document.createDocumentFragment();
  for (let i = 0; i < count; i += 1) frag.appendChild(el('div', 'skeleton'));
  return frag;
}

function card(item) {
  const holder = el('article', 'card');
  holder.dataset.id = item.id;
  const media = el('div', 'card-img');
  if (item.image) media.style.backgroundImage = `url("${imgUrl(item.image)}")`;
  media.appendChild(el('span', 'badge-cat', item.category_label));
  if (item.size_text) media.appendChild(el('span', 'badge-size', item.size_text));
  if (item.installed) media.appendChild(el('span', 'badge-installed', 'установлен'));

  const body = el('div', 'card-body');
  const title = el('div', 'card-title', item.title);
  title.title = item.title;
  body.appendChild(title);

  const meta = el('div', 'card-meta');
  const bits = [];
  if (item.game_version) bits.push(`v${item.game_version}`);
  else if (item.source === 'playground') bits.push('playground.ru');
  else if (item.source === 'link') bits.push('по ссылке');
  if (item.version) bits.push(`мод ${item.version}`);
  if (item.ago) bits.push(item.ago);
  bits.forEach((text, index) => {
    if (index) meta.appendChild(el('span', 'dot', '•'));
    meta.appendChild(el('span', '', text));
  });
  body.appendChild(meta);

  const actions = el('div', 'card-actions');
  const installBtn = el('button', 'btn small', item.installed ? 'Установить снова' : 'Установить');
  installBtn.dataset.role = 'install';
  installBtn.addEventListener('click', () => installMod(item, installBtn));
  const detailsBtn = el('button', 'icon-btn', 'i');
  detailsBtn.title = 'Подробнее';
  detailsBtn.textContent = '≡';
  detailsBtn.addEventListener('click', () => openDetails(item.id));
  actions.append(installBtn, detailsBtn);
  body.appendChild(actions);

  holder.append(media, body);
  media.addEventListener('click', () => openDetails(item.id));
  media.style.cursor = 'pointer';
  return holder;
}

function renderGrid(append = false) {
  const grid = $('#grid');
  if (!append) grid.innerHTML = '';
  state.items.forEach((item) => grid.appendChild(card(item)));
  $('#grid-empty').hidden = state.items.length > 0;
  $('#catalog-count').textContent = state.total
    ? `${state.total.toLocaleString('ru-RU')} модов${state.cat !== 'all' ? ' в категории' : ''}`
    : '';
  $('#more').hidden = state.items.length >= state.total;
}

async function loadMods(append = false) {
  if (state.loading) return;
  state.loading = true;
  if (!append) {
    $('#grid').innerHTML = '';
    $('#grid').appendChild(skeleton());
  }
  try {
    const params = new URLSearchParams({
      q: state.q,
      cat: state.cat,
      source: state.source,
      sort: state.sort,
      offset: String(state.offset),
      limit: String(state.limit),
    });
    const data = await api(`/api/mods?${params}`);
    state.total = data.total;
    state.items = append ? state.items.concat(data.items) : data.items;
    renderGrid(append);
  } catch (error) {
    toast(error.message, 'err');
    $('#grid').innerHTML = '';
  } finally {
    state.loading = false;
  }
}

/* ------------------------------------------------------------- actions */
async function installMod(item, button) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = 'Открываю…';
  try {
    const data = await api('/api/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: item.id }),
    });
    toast(data.message || 'Открыл браузер — жду файл', 'ok', 7000);
    if (data.mode === 'download') toast('Скачал и установил', 'ok');
    button.textContent = 'Жду файл…';
    setTimeout(() => { if (button.isConnected) { button.disabled = false; button.textContent = original; } }, 180000);
    return;
  } catch (error) {
    toast(error.message, 'err');
    button.disabled = false;
    button.textContent = original;
  }
}

function markInstalled(id) {
  document.querySelectorAll(`.card[data-id="${id}"]`).forEach((node) => {
    if (!node.querySelector('.badge-installed')) {
      const badge = el('span', 'badge-installed', 'установлен');
      node.querySelector('.card-img')?.appendChild(badge);
    }
    const button = node.querySelector('[data-role="install"]');
    if (button) { button.disabled = false; button.textContent = 'Установить снова'; }
    const details = node.querySelector('.icon-btn');
    if (details) details.disabled = false;
  });
}

async function openDetails(id) {
  const modal = $('#modal');
  const body = $('#modal-body');
  body.innerHTML = '<div style="padding:24px">Загрузка…</div>';
  modal.hidden = false;
  try {
    const data = await api(`/api/mod?id=${encodeURIComponent(id)}`);
    const item = data.item;
    body.innerHTML = `
      <div class="modal-hero" style="background-image:url('${esc(imgUrl(item.image))}')"></div>
      <div class="modal-inner">
        <h2>${esc(item.title)}</h2>
        <div class="modal-meta">
          <span class="pill">${esc(item.category_label)}</span>
          ${item.game_version ? `<span class="pill">игра ${esc(item.game_version)}</span>` : ''}
          ${item.version ? `<span class="pill">версия ${esc(item.version)}</span>` : ''}
          ${item.size_text ? `<span class="pill">${esc(item.size_text)}</span>` : ''}
          ${item.downloads ? `<span class="pill">${item.downloads.toLocaleString('ru-RU')} скачиваний</span>` : ''}
          ${item.installed ? '<span class="pill ok">уже установлен</span>' : ''}
        </div>
        <div class="modal-desc">${esc(item.description || 'Описание на странице мода.')}</div>
        <div class="modal-actions">
          <button class="btn" id="m-install">Установить</button>
          <button class="btn ghost" id="m-page">Открыть страницу</button>
        </div>
      </div>`;
    $('#m-install').addEventListener('click', (event) => installMod(item, event.target));
    $('#m-page').addEventListener('click', async () => {
      try { await api('/api/open', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: item.id }), }); } catch (error) { toast(error.message, 'err'); }
    });
  } catch (error) {
    body.innerHTML = `<div style="padding:24px">${esc(error.message)}</div>`;
  }
}

/* ------------------------------------------------------------- library */
async function loadLibrary() {
  const list = $('#library-list');
  list.innerHTML = '';
  try {
    const data = await api('/api/library');
    $('#library-meta').textContent = `${data.items.length} файлов · ${data.mods_dir || ''}`;
    if (!data.items.length) {
      list.appendChild(el('div', 'empty-text', 'Пока пусто. Установите мод из каталога.'));
      return;
    }
    data.items.forEach((row) => {
      const node = el('div', `row-item${row.active ? '' : ' off'}`);
      const main = el('div', 'row-main');
      main.appendChild(el('div', 'row-title', row.title || row.filename));
      main.appendChild(el('div', 'row-sub', `${row.filename} · ${row.size_text} · ${row.installed_text}`));
      node.appendChild(main);
      node.appendChild(el('span', `pill ${row.active ? 'ok' : 'bad'}`, row.active ? 'включён' : 'выключен'));
      const actions = el('div', 'row-actions');
      const toggle = el('button', 'btn ghost small', row.active ? 'Выключить' : 'Включить');
      toggle.addEventListener('click', async () => {
        try { await api('/api/library/toggle', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: row.key }) }); loadLibrary(); refreshState(); }
        catch (error) { toast(error.message, 'err'); }
      });
      const remove = el('button', 'btn danger small', 'Удалить');
      remove.addEventListener('click', async () => {
        if (!confirm(`Удалить ${row.filename} из папки модов?`)) return;
        try { await api('/api/library/remove', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: row.key }) }); loadLibrary(); refreshState(); }
        catch (error) { toast(error.message, 'err'); }
      });
      actions.append(toggle, remove);
      node.appendChild(actions);
      list.appendChild(node);
    });
  } catch (error) {
    toast(error.message, 'err');
  }
}

/* ------------------------------------------------------------- inbox */
async function loadInbox() {
  const list = $('#inbox-list');
  list.innerHTML = '';
  try {
    const data = await api('/api/inbox');
    $('#inbox-meta').textContent = `${data.items.length} архивов · модов: ${data.items.filter(i => i.valid).length} · ${data.downloads_dir || ''}`;
    if (!data.items.length) {
      list.appendChild(el('div', 'empty-text', 'Новых архивов нет.'));
      return;
    }
    data.items.forEach((row) => {
      const node = el('div', 'row-item');
      const main = el('div', 'row-main');
      main.appendChild(el('div', 'row-title', row.name));
      const date = new Date(row.modified * 1000).toLocaleString('ru-RU');
      main.appendChild(el('div', 'row-sub', `${row.size_text} · ${date}`));
      node.appendChild(main);
      const kindPill = el('span', `pill ${row.valid ? 'ok' : 'warn'}`,
        row.valid ? (row.kind === 'maps' ? 'карта' : row.kind === 'cars' ? 'машина' : 'мод') : 'не мод');
      node.appendChild(kindPill);
      const actions = el('div', 'row-actions');
      const install = el('button', 'btn small', 'Установить');
      install.disabled = !row.valid;
      install.addEventListener('click', async () => {
        install.disabled = true;
        try {
          const result = await api('/api/install-file', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: row.path }) });
          toast(result.status === 'duplicate' ? 'Уже установлен' : 'Установлено', 'ok');
          loadInbox(); loadLibrary(); refreshState();
        } catch (error) { toast(error.message, 'err'); install.disabled = false; }
      });
      actions.appendChild(install);
      node.appendChild(actions);
      list.appendChild(node);
    });
  } catch (error) {
    toast(error.message, 'err');
  }
}

/* ------------------------------------------------------------- settings */
function fillSettings(settings, extra = {}) {
  $('#set-mods').value = settings.mods_dir || '';
  $('#set-downloads').value = settings.downloads_dir || '';
  $('#set-auto').checked = !!settings.auto_install;
  $('#about').innerHTML = `
    BeamHub ${esc(extra.version || '')} — локальный каталог модов для BeamNG.drive.<br>
    Игра: ${esc(settings.game_path || 'не найдена')}<br>
    Каталог обновлён: ${esc(extra.catalog_synced || 'ещё ни разу')}<br>
    Каталог собран из публичных страниц сайтов-модохранилищ. Файлы скачиваются вашим браузером
    с сайта-источника; BeamHub только раскладывает их по папке модов и ведёт библиотеку.`;
}

async function saveSettings() {
  try {
    const payload = {
      mods_dir: $('#set-mods').value.trim(),
      downloads_dir: $('#set-downloads').value.trim(),
      auto_install: $('#set-auto').checked,
    };
    await api('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    toast('Сохранено', 'ok');
    refreshState();
  } catch (error) { toast(error.message, 'err'); }
}

/* ------------------------------------------------------------- state */
async function refreshState() {
  try {
    const data = await api('/api/state');
    state.server = data;
    const progress = data.progress || {};
    const pending = data.pending;
    const bar = $('#progress');
    if (pending) {
      bar.classList.add('on', 'waiting');
      $('#progress-fill').style.width = '100%';
      $('#progress-text').textContent = `Жду скачивание: ${pending.title} — как только файл появится в «Загрузках», поставим его сам`;
    } else if (progress.running && progress.total) {
      bar.classList.add('on');
      bar.classList.remove('waiting');
      const percent = Math.round((progress.done / progress.total) * 100);
      $('#progress-fill').style.width = percent + '%';
      $('#progress-text').textContent = `Обновляю метаданные: ${progress.done} из ${progress.total}`;
    } else {
      bar.classList.remove('on', 'waiting');
    }
    $('#count-mods').textContent = (data.counts.mods || 0).toLocaleString('ru-RU');
    $('#count-lib').textContent = data.counts.installed || 0;
    $('#count-inbox').textContent = data.inbox_count || 0;
    const cats = $('#cats');
    const active = state.cat;
    cats.innerHTML = '';
    const all = el('button', `cat${active === 'all' ? ' active' : ''}`, '');
    all.innerHTML = '<span class="cat-dot"></span> Все моды <span class="n">' +
      (data.counts.mods || 0).toLocaleString('ru-RU') + '</span>';
    all.addEventListener('click', () => selectCategory('all'));
    cats.appendChild(all);
    (data.categories || []).forEach((cat) => {
      if (!cat.count) return;
      const node = el('button', `cat${active === cat.key ? ' active' : ''}`, '');
      node.innerHTML = `<span class="cat-dot"></span> ${esc(cat.label)} <span class="n">${cat.count}</span>`;
      node.addEventListener('click', () => selectCategory(cat.key));
      cats.appendChild(node);
    });
    const shortPath = (value) => {
      const parts = String(value || '').split(/[\\/]/).filter(Boolean);
      return parts.length > 2 ? `…\\${parts.slice(-2).join('\\')}` : (value || '');
    };
    $('#foot').innerHTML = `<b>Папка модов</b><span class="path" title="${esc(data.settings.mods_dir || '')}">${esc(shortPath(data.settings.mods_dir) || 'не найдена')}</span>`;
    if (state.view === 'settings') fillSettings(data.settings, data);
  } catch (error) {
    /* сервер перезапускается — тихо ждём */
  }
}

async function pollEvents() {
  try {
    const data = await api(`/api/events?since=${state.events}`);
    state.events = data.seq;
    (data.events || []).forEach((event) => {
      if (event.kind === 'installed') {
        toast(event.text, 'ok', 7000);
        if (event.mod_id) markInstalled(event.mod_id);
        if (state.view === 'library') loadLibrary();
      } else if (event.kind === 'error') toast(event.text, 'err');
      else if (event.kind === 'warn') toast(event.text, 'warn');
      else if (event.kind === 'duplicate') toast(event.text, 'warn');
      else if (event.kind === 'catalog') toast(event.text, '', 4000);
    });
  } catch (error) { /* ignore */ }
}

/* ------------------------------------------------------------- nav */
function selectCategory(cat) {
  state.cat = cat;
  state.offset = 0;
  state.view = 'catalog';
  switchView('catalog');
  loadMods(false);
  refreshState();
}

function switchView(view) {
  state.view = view;
  if (location.hash !== `#${view}`) history.replaceState(null, '', `#${view}`);
  document.querySelectorAll('.nav-item').forEach((node) => {
    node.classList.toggle('active', node.dataset.view === view);
  });
  ['catalog', 'library', 'inbox', 'settings'].forEach((name) => {
    $(`#view-${name}`).hidden = name !== view;
  });
  if (view === 'library') loadLibrary();
  if (view === 'inbox') loadInbox();
  if (view === 'settings' && state.server) fillSettings(state.server.settings, state.server);
  if (view === 'catalog') refreshState();
}

function debounce(fn, delay) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delay);
  };
}

/* ------------------------------------------------------------- init */
function init() {
  document.querySelectorAll('.nav-item').forEach((node) => {
    node.addEventListener('click', () => switchView(node.dataset.view));
  });

  $('#search').addEventListener('input', debounce((event) => {
    state.q = event.target.value.trim();
    state.offset = 0;
    switchView('catalog');
    loadMods(false);
  }, 350));

  $('#sort').addEventListener('change', (event) => {
    state.sort = event.target.value;
    state.offset = 0;
    loadMods(false);
  });

  $('#source').addEventListener('change', (event) => {
    state.source = event.target.value;
    state.offset = 0;
    loadMods(false);
  });

  $('#more').addEventListener('click', () => {
    state.offset += state.limit;
    loadMods(true);
  });

  $('#sync').addEventListener('click', async (event) => {
    const button = event.target;
    button.disabled = true;
    try {
      const data = await api('/api/sync', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      toast(`Каталог обновлён, добавлено: ${data.added}`, 'ok');
      state.offset = 0;
      loadMods(false);
    } catch (error) { toast(error.message, 'err'); }
    button.disabled = false;
    refreshState();
  });

  $('#add-link').addEventListener('click', async () => {
    const url = prompt('Ссылка на страницу мода (playground.ru, beamng.com, modland…):');
    if (!url) return;
    try {
      const data = await api('/api/add-link', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url.trim() }) });
      toast(`Добавлено: ${data.item.title}`, 'ok');
      state.offset = 0;
      loadMods(false);
    } catch (error) { toast(error.message, 'err'); }
  });

  $('#save-settings').addEventListener('click', saveSettings);
  $('#quit-app').addEventListener('click', async () => {
    if (!confirm('Остановить BeamHub? Каталог закроется, моды в игре останутся на месте.')) return;
    try {
      await api('/api/quit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
    } catch (error) { /* сервер уже мог закрыться */ }
    document.body.innerHTML = '<div style="display:grid;place-items:center;height:100vh;' +
      'color:#8d93a1;font:15px Segoe UI;text-align:center;line-height:1.7">' +
      'BeamHub остановлен.<br>Вкладку можно закрыть.</div>';
  });
  $('#rescan').addEventListener('click', async () => {
    try { const data = await api('/api/library/rescan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      toast(`Библиотека пересобрана: ${data.count} файлов`, 'ok'); loadLibrary(); refreshState(); }
    catch (error) { toast(error.message, 'err'); }
  });
  $('#detect-mods').addEventListener('click', async () => {
    try { const data = await api('/api/detect');
      $('#set-mods').value = data.mods_dir || '';
      toast('Нашёл папку модов', 'ok'); }
    catch (error) { toast(error.message, 'err'); }
  });
  $('#detect-downloads').addEventListener('click', () => {
    $('#set-downloads').value = '';
    toast('Будет использована стандартная папка загрузок', '', 3500);
  });

  $('#modal-close').addEventListener('click', () => { $('#modal').hidden = true; });
  $('#modal').addEventListener('click', (event) => {
    if (event.target.id === 'modal') $('#modal').hidden = true;
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') $('#modal').hidden = true;
  });

  loadMods(false);
  refreshState();
  const initial = (location.hash || '').replace('#', '');
  if (['library', 'inbox', 'settings'].includes(initial)) switchView(initial);
  const deepMod = new URLSearchParams(location.search).get('mod');
  if (deepMod) openDetails(deepMod);
  window.addEventListener('hashchange', () => {
    const view = (location.hash || '').replace('#', '');
    if (['catalog', 'library', 'inbox', 'settings'].includes(view)) switchView(view);
  });
  setInterval(refreshState, 5000);
  setInterval(pollEvents, 2500);
  pollEvents();
}

init();
