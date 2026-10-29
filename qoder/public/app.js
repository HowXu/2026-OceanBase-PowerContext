(() => {
  const $ = id => document.getElementById(id);
  const state = { tasks: [], handoffs: [], scopeId: null, loaded: false };
  const apiBase = document.documentElement.dataset.apiBase;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  const statusLabel = value => ({ todo: '待办', in_progress: '进行中', done: '已完成' })[value] || value;
  const priorityLabel = value => ({ high: '高', medium: '中', low: '低' })[value] || value;
  function notice(message, success = false) {
    $('notice').textContent = message; $('notice').hidden = !message;
    $('notice').className = success ? 'notice success' : 'notice';
  }
  async function request(path, method = 'GET', body) {
    const response = await fetch(apiBase + path, { method, headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(15000) });
    let data;
    try { data = await response.json(); } catch { throw new Error('服务返回格式不正确，请检查发布配置。'); }
    if (!response.ok) throw new Error(data.error || `操作失败（HTTP ${response.status}）`);
    return data;
  }
  async function run(button, action) {
    button.disabled = true; notice('');
    try { await action(); } catch (error) { notice(error.message || '无法连接服务，请稍后重试。'); }
    finally { button.disabled = false; }
  }
  function card(task, actions = false) {
    return `<article class="task-item"><div class="task-item-header"><h3 class="task-item-title">${esc(task.title)}</h3><span class="badge badge-info">${esc(statusLabel(task.status))}</span></div>
      <p>${esc(task.description)}</p><div class="task-item-meta"><span class="badge badge-gray">${esc(priorityLabel(task.priority))}优先级</span>${task.deadline ? `<span class="badge badge-gray">截止 ${esc(task.deadline)}</span>` : ''}<span class="badge badge-gray">版本 ${task.revision}</span></div>
      <progress max="100" value="${esc(task.progress)}" aria-label="任务进度">${esc(task.progress)}%</progress><span> ${esc(task.progress)}%</span>
      ${actions ? `<div class="task-item-actions"><button class="btn btn-secondary btn-sm" data-action="edit" data-id="${esc(task.id)}">编辑进度</button><button class="btn btn-danger btn-sm" data-action="delete" data-id="${esc(task.id)}">删除</button></div>` : ''}</article>`;
  }
  function render() {
    const tasks = state.tasks;
    const counts = [['总任务', tasks.length], ['待办', tasks.filter(t => t.status === 'todo').length], ['进行中', tasks.filter(t => t.status === 'in_progress').length], ['已完成', tasks.filter(t => t.status === 'done').length], ['高优先级', tasks.filter(t => t.priority === 'high').length], ['平均进度', `${tasks.length ? Math.round(tasks.reduce((s, t) => s + t.progress, 0) / tasks.length) : 0}%`]];
    $('stats').innerHTML = counts.map(([label, value]) => `<div class="stat-card"><div class="stat-value">${value}</div><div class="stat-label">${label}</div></div>`).join('');
    $('recentTasks').innerHTML = tasks.slice(-5).reverse().map(t => card(t)).join('') || '<p>尚无任务，添加第一件要完成的事。</p>';
    renderTasks();
    const selected = $('handoffTask').value;
    $('handoffTask').innerHTML = '<option value="">请选择任务</option>' + tasks.map(t => `<option value="${esc(t.id)}">${esc(t.title)} · ${t.progress}%</option>`).join('');
    $('handoffTask').value = selected;
    $('handoffList').innerHTML = state.handoffs.slice().reverse().map(h => `<article class="handoff-item"><h3>${esc(h.taskTitle)}</h3><p>${esc(h.summary)}</p><p class="handoff-item-next">下一步：${esc(h.nextAction)}</p><p>${esc(h.progress)}% · ${esc(statusLabel(h.status))} · ${esc(new Date(h.createdAt).toLocaleString('zh-CN'))}</p><small>记忆 ${esc(h.id)} / 版本 ${h.revision}</small></article>`).join('') || '<p>尚无交接记录。</p>';
    $('evidence').textContent = JSON.stringify({ scopeId: state.scopeId, tasks: tasks.map(({ id, revision }) => ({ id, revision })), handoffs: state.handoffs.map(({ id, revision }) => ({ id, revision })) }, null, 2);
  }
  function renderTasks() {
    const tasks = state.tasks.filter(t => (!$('filterStatus').value || t.status === $('filterStatus').value) && (!$('filterPriority').value || t.priority === $('filterPriority').value));
    $('taskList').innerHTML = tasks.map(t => card(t, true)).join('') || '<p>没有符合条件的任务。</p>';
  }
  async function refresh() {
    try {
      const health = await request('/health');
      const [tasks, handoffs] = await Promise.all([request('/tasks'), request('/handoff')]);
      state.tasks = tasks.tasks; state.handoffs = handoffs.handoffs; state.scopeId = health.scopeId; state.loaded = true;
      $('connection').textContent = '已连接 PowerContext'; $('connection').className = 'connected'; render();
    } catch (error) {
      $('connection').textContent = '未连接 PowerContext'; $('connection').className = 'disconnected';
      if (!state.loaded) for (const id of ['recentTasks', 'taskList', 'handoffList']) $(id).textContent = '服务连接后读取记录。';
      throw error;
    }
  }
  function showPage() {
    const requested = location.hash.slice(1) || location.pathname.slice(1) || 'dashboard';
    const page = ['dashboard', 'tasks', 'handoff', 'about'].includes(requested) ? requested : 'dashboard';
    document.querySelectorAll('[data-section]').forEach(el => { el.hidden = el.dataset.section !== page; });
    document.querySelectorAll('[data-page]').forEach(el => el.classList.toggle('active', el.dataset.page === page));
    window.scrollTo(0, 0);
  }
  let previousFocus;
  function openModal(task) {
    previousFocus = document.activeElement; $('taskForm').reset();
    $('modalTitle').textContent = task ? '编辑任务' : '新建任务';
    $('taskId').value = task?.id || ''; $('taskRevision').value = task?.revision || '';
    $('taskTitle').value = task?.title || ''; $('taskDesc').value = task?.description || '';
    $('taskPriority').value = task?.priority || 'medium'; $('taskDeadline').value = task?.deadline || '';
    $('taskProgress').value = task?.progress || 0;
    $('taskModal').hidden = false; $('taskModal').classList.add('active'); $('taskTitle').focus();
  }
  function closeModal() { $('taskModal').hidden = true; $('taskModal').classList.remove('active'); previousFocus?.focus(); }
  $('addTask').addEventListener('click', () => openModal());
  $('closeModal').addEventListener('click', closeModal); $('cancelModal').addEventListener('click', closeModal);
  document.addEventListener('keydown', event => {
    if ($('taskModal').hidden) return;
    if (event.key === 'Escape') closeModal();
    if (event.key === 'Tab') {
      const fields = [...$('taskModal').querySelectorAll('button,input:not([type="hidden"]),textarea,select')].filter(el => !el.disabled);
      if (event.shiftKey && document.activeElement === fields[0]) { event.preventDefault(); fields.at(-1).focus(); }
      else if (!event.shiftKey && document.activeElement === fields.at(-1)) { event.preventDefault(); fields[0].focus(); }
    }
  });
  $('taskForm').addEventListener('submit', event => {
    event.preventDefault();
    run(event.submitter, async () => {
      const id = $('taskId').value;
      const body = { title: $('taskTitle').value.trim(), description: $('taskDesc').value.trim(), priority: $('taskPriority').value,
        deadline: $('taskDeadline').value || null, progress: Number($('taskProgress').value), ...(id ? { revision: Number($('taskRevision').value) } : {}) };
      await request(id ? `/tasks/${encodeURIComponent(id)}` : '/tasks', id ? 'PUT' : 'POST', body);
      closeModal(); await refresh(); notice('任务已保存到 PowerContext。', true);
    });
  });
  $('quickAddForm').addEventListener('submit', event => {
    event.preventDefault(); run(event.submitter, async () => {
      await request('/tasks', 'POST', { title: $('quickTitle').value.trim(), priority: $('quickPriority').value });
      event.target.reset(); await refresh(); notice('任务已保存到 PowerContext。', true);
    });
  });
  $('taskList').addEventListener('click', event => {
    const button = event.target.closest('[data-action]'); if (!button) return;
    const task = state.tasks.find(t => t.id === button.dataset.id); if (!task) return;
    if (button.dataset.action === 'edit') openModal(task);
    else if (confirm(`删除「${task.title}」？交接历史仍会保留。`)) run(button, async () => {
      await request(`/tasks/${encodeURIComponent(task.id)}`, 'DELETE', { revision: task.revision }); await refresh(); notice('任务已删除。', true);
    });
  });
  $('handoffForm').addEventListener('submit', event => {
    event.preventDefault(); run(event.submitter, async () => {
      await request('/handoff', 'POST', { taskId: $('handoffTask').value, summary: $('handoffSummary').value.trim(), nextAction: $('handoffNext').value.trim() });
      event.target.reset(); await refresh(); notice('交接已保存，其他会话可以继续读取。', true);
    });
  });
  $('contextForm').addEventListener('submit', event => {
    event.preventDefault(); run(event.submitter, async () => {
      const data = await request('/context', 'POST', { query: $('contextQuery').value.trim() });
      $('contextResult').textContent = data.content || '没有找到相关上下文，请使用已保存任务中的关键词。';
    });
  });
  $('refresh').addEventListener('click', event => run(event.currentTarget, refresh));
  for (const id of ['filterStatus', 'filterPriority']) $(id).addEventListener('change', renderTasks);
  window.addEventListener('hashchange', showPage);
  let legacy = [];
  try { const value = JSON.parse(localStorage.getItem('tasks') || '[]'); if (Array.isArray(value)) legacy = value; } catch { /* legacy data remains untouched */ }
  $('importLegacy').hidden = !legacy.length;
  $('importLegacy').addEventListener('click', event => run(event.currentTarget, async () => {
    if (!confirm(`将逐条导入 ${legacy.length} 个旧版任务，保留原浏览器数据。继续？`)) return;
    let completed = 0;
    try {
      for (const task of legacy) {
        await request('/tasks', 'POST', { title: task.title, description: task.description || '', priority: task.priority || 'medium', deadline: task.deadline || null, progress: Number(task.progress || 0) });
        completed++;
      }
      $('importLegacy').hidden = true; legacy = []; await refresh(); notice('旧版任务已导入；原始数据仍保留。', true);
    } catch (error) { await refresh(); throw new Error(`已导入 ${completed} 条。${error.message} 请核对列表，勿重复导入。`); }
  }));
  showPage(); run($('refresh'), refresh);
})();
