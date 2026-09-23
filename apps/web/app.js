const element = (id) => document.getElementById(id);
const state = { user: null, tasks: [], capabilities: null, selected: null, detail: null, mediaId: null, polling: false };
const statuses = { DISCOVERED: '待制作', TOPIC_SELECTED: '已选题', SCRIPT_GENERATING: '文案生成中', SCRIPT_AUTO_REVIEWING: '文案自动审核', SCRIPT_PENDING_APPROVAL: '待审文案', SCRIPT_APPROVED: '文案已通过', SCRIPT_REJECTED: '文案已拒绝', MEDIA_GENERATING: '制作中', MEDIA_QC_RUNNING: '视频质检中', VIDEO_PENDING_APPROVAL: '待审视频', VIDEO_APPROVED: '视频已通过', VIDEO_REJECTED: '视频已拒绝', SCHEDULED: '已排期', PUBLISHING: '发布中', PUBLISHED: '已发布', PAUSED: '已暂停', FAILED: '失败', CANCELLED: '已取消' };
const jobStatuses = { QUEUED: '排队中', RUNNING: '处理中', SUCCEEDED: '已完成', FAILED: '失败', SUBMITTING: '提交中', SUBMITTED: '已提交 · 待平台审核', UNKNOWN: '结果不确定 · 请到抖音核对' };
const riskNames = { LOW: '低风险', HIGH: '高风险', BLOCKED: '已阻止' };
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
const dateText = (value) => new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
let toastTimer;

function toast(message, error = false) {
  element('toast').textContent = message;
  element('toast').className = 'toast' + (error ? ' error-toast' : '');
  element('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { element('toast').hidden = true; }, error ? 7000 : 4000);
}

async function api(path, options = {}) {
  const response = await fetch('/api/v1' + path, {
    ...options,
    headers: { 'Content-Type': 'application/json', 'X-Studio-Request': '1', ...options.headers },
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const result = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    if (response.status === 403 && /authentication required|session expired|account disabled/.test(result.error)) showLogin();
    throw new Error(result.error || '请求失败，请稍后重试。');
  }
  return result;
}

async function action(button, operation) {
  const previous = button.disabled;
  button.disabled = true;
  try { await operation(); } catch (error) { toast(error.message, true); }
  finally {
    button.disabled = previous;
    if (state.selected && ['generate-button', 'review', 'publish'].includes(button.id)) await refreshDetail().catch(() => {});
  }
}

function showLogin() {
  state.user = null;
  state.selected = null;
  element('preview').pause();
  element('preview').removeAttribute('src');
  element('app-view').hidden = true;
  element('login-view').hidden = false;
}

function showView(view) {
  for (const name of ['tasks', 'detail', 'settings']) element(name + '-view').hidden = name !== view;
  element('nav-tasks').classList.toggle('active', view !== 'settings');
  element('nav-settings').classList.toggle('active', view === 'settings');
  element('breadcrumb').textContent = { tasks: '视频任务', detail: '视频工作台', settings: '平台接入' }[view];
  if (view !== 'detail') { state.selected = null; element('preview').pause(); }
}

async function initialize(user) {
  state.user = user;
  element('username').textContent = user.username;
  element('avatar').textContent = user.username.charAt(0).toUpperCase();
  element('role').textContent = user.is_admin ? '管理员' : '团队成员';
  element('login-view').hidden = true;
  element('app-view').hidden = false;
  showView('tasks');
  await refreshTasks();
}

async function refreshCapabilities() {
  state.capabilities = await api('/studio/capabilities');
  if (typeof aiCapabilities === 'function') aiCapabilities();
  const provider = state.capabilities.douyin;
  const message = !provider.configured ? '未配置应用 · 上传、预览和本地制作不受影响。' : provider.connected ? '账号已连接 · 发布前仍需完成审核并明确确认。' : '应用已配置 · 请连接账号，或更新过期授权。';
  element('douyin-state').textContent = message;
  element('publish-status').textContent = message;
  element('connect-douyin').disabled = !provider.configured || !state.user.is_admin;
  element('connect-douyin').textContent = provider.connected ? '重新授权抖音账号 →' : '连接抖音账号 →';
  updatePublishButton();
}

function updatePublishButton() {
  const provider = state.capabilities?.douyin;
  element('publish').disabled = !state.user?.is_admin || !provider?.configured || !provider?.connected || !state.detail?.media.length;
}

async function refreshTasks() {
  const [tasks, metrics] = await Promise.all([api('/tasks'), api('/metrics'), refreshCapabilities()]);
  state.tasks = tasks;
  element('stat-total').textContent = tasks.length;
  element('stat-created').textContent = metrics.tasks_created;
  element('stat-published').textContent = metrics.tasks_published;
  renderTasks();
}

function renderTasks() {
  const query = element('search').value.trim().toLowerCase();
  const status = element('status-filter').value;
  const tasks = state.tasks.filter((task) => task.title.toLowerCase().includes(query) && (!status || task.status === status));
  element('task-count').textContent = tasks.length;
  if (!tasks.length) {
    element('task-list').innerHTML = '<div class="empty-state"><span class="empty-icon">▧</span><h3>' + (state.tasks.length ? '没有符合筛选条件的任务' : '给第一个创意一个开始') + '</h3><p>' + (state.tasks.length ? '试试其他关键词或状态。' : '点击右上角「新建任务」，上传素材或用文案制作视频。') + '</p></div>';
    return;
  }
  element('task-list').innerHTML = '<table class="task-table"><thead><tr><th>任务名称</th><th>工作流状态</th><th>风险等级</th><th>创建时间</th><th>操作</th></tr></thead><tbody>' + tasks.map((task) => `<tr><td><div class="task-name"><span class="task-thumbnail">▷</span><div><strong>${escapeHtml(task.title)}</strong><small>${escapeHtml(task.topic || '独立创作')} · ${task.current_media_version ? '视频 v' + task.current_media_version : '等待添加素材'}</small></div></div></td><td><span class="status ${task.status === 'PUBLISHED' ? 'good' : 'neutral'}">${escapeHtml(statuses[task.status] || task.status)}</span></td><td><span class="status ${task.risk_level === 'LOW' ? 'good' : task.risk_level === 'HIGH' ? 'warn' : 'bad'}">${riskNames[task.risk_level]}</span></td><td>${dateText(task.created_at)}</td><td><button class="text-link open-task" data-id="${escapeHtml(task.id)}">进入任务 →</button></td></tr>`).join('') + '</tbody></table>';
  document.querySelectorAll('.open-task').forEach((button) => button.addEventListener('click', () => action(button, () => openTask(button.dataset.id))));
}

async function openTask(taskId) {
  state.selected = taskId;
  state.detail = null;
  state.mediaId = null;
  element('caption').value = '';
  element('generate-text').value = '';
  element('review-state').textContent = '审核绑定最新视频和文案；高风险需两个不同账号审核。';
  showView('detail');
  await refreshDetail();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function refreshDetail() {
  const taskId = state.selected;
  if (!taskId) return;
  const [task, detail] = await Promise.all([api('/tasks/' + taskId), api('/studio/tasks/' + taskId)]);
  if (taskId !== state.selected) return;
  const previousLatest = state.detail?.media[0]?.id;
  state.detail = detail;
  element('detail-title').textContent = task.title;
  element('detail-badges').innerHTML = `<span class="status neutral">${escapeHtml(statuses[task.status] || task.status)}</span><span class="status ${task.risk_level === 'LOW' ? 'good' : 'warn'}">${riskNames[task.risk_level]}</span><span class="hint">手动制作记录与自动工作流状态分开保存</span>`;
  const hasNewMedia = previousLatest !== detail.media[0]?.id;
  if (hasNewMedia || !state.mediaId) state.mediaId = detail.media[0]?.id || null;
  const background = element('background').value;
  element('media-select').innerHTML = detail.media.map((record) => `<option value="${record.id}">v${record.metadata.version} · ${escapeHtml(record.name)} · ${dateText(record.created_at)}</option>`).join('') || '<option>暂无视频</option>';
  element('background').innerHTML = '<option value="">渐变文字卡片</option>' + detail.media.map((record) => `<option value="${record.id}">v${record.metadata.version} · ${escapeHtml(record.name)}</option>`).join('');
  if (detail.media.some((record) => record.id === background)) element('background').value = background;
  element('media-select').value = state.mediaId || '';
  updatePreview();
  const busy = detail.jobs.some((job) => ['QUEUED', 'RUNNING'].includes(job.status));
  const blocked = task.risk_level === 'BLOCKED' || ['CANCELLED', 'PUBLISHED'].includes(task.status) || detail.publications.length > 0;
  element('upload').disabled = busy || blocked;
  element('generate-button').disabled = busy || blocked;
  element('review').disabled = busy || blocked || !detail.media.length;
  element('job-list').innerHTML = detail.jobs.length ? detail.jobs.map((job) => `<div class="record-item"><div><span>${job.kind === 'UPLOAD' ? '素材上传与转码' : '模板视频生成'}</span><span class="status ${job.status === 'FAILED' ? 'bad' : job.status === 'SUCCEEDED' ? 'good' : 'neutral'}">${jobStatuses[job.status]}</span></div><small>${dateText(job.created_at)}</small>${job.error ? '<p class="error">' + escapeHtml(job.error) + '</p>' : ''}</div>`).join('') : '<p class="hint">还没有处理记录。上传素材或生成一个视频即可开始。</p>';
  element('publication-list').innerHTML = detail.publications.map((record) => `<div class="record-item"><div><span>抖音 · ${record.visibility === 'private' ? '仅自己可见' : '公开'}</span><span class="status ${record.status === 'UNKNOWN' ? 'warn' : 'neutral'}">${jobStatuses[record.status] || record.status}</span></div><small>${dateText(record.created_at)}</small><p>${escapeHtml(record.result || '正在请求平台，请勿重复提交。')}</p></div>`).join('');
  updatePublishButton();
  if (busy || blocked) element('publish').disabled = true;
  if (typeof renderAi === 'function') renderAi(task, detail);
}

function updatePreview() {
  const record = state.detail?.media.find((entry) => entry.id === state.mediaId);
  element('preview').hidden = !record;
  element('preview-empty').hidden = Boolean(record);
  element('download').hidden = !record;
  if (!record) { element('preview').removeAttribute('src'); element('video-version').textContent = '暂无视频'; return; }
  if (element('preview').getAttribute('src') !== record.url) element('preview').src = record.url;
  element('download').href = record.url + '?download=1';
  element('video-version').textContent = 'v' + record.metadata.version + (record.id === state.detail.media[0].id ? ' · 最新' : ' · 历史版本');
  element('video-meta').textContent = `${record.metadata.width} × ${record.metadata.height} · ${record.metadata.duration.toFixed(1)} 秒 · ${(record.metadata.size / 1024 / 1024).toFixed(1)} MB`;
}

async function uploadFile(file) {
  if (!file || !state.selected) return;
  if (element('upload').disabled) { toast('当前任务正在处理或已锁定。', true); return; }
  if (!/\.(mp4|mov|webm)$/i.test(file.name) || !file.size || file.size > 100 * 1024 * 1024) { toast('请选择 100 MiB 以内的 MP4/MOV/WebM 视频。', true); return; }
  const taskId = state.selected;
  element('upload').disabled = true;
  element('upload-progress').hidden = false;
  element('upload-progress').value = 0;
  try {
    await new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open('POST', '/api/v1/studio/tasks/' + taskId + '/upload');
      request.timeout = 120000;
      request.setRequestHeader('X-Filename', encodeURIComponent(file.name));
      request.setRequestHeader('X-Studio-Request', '1');
      request.setRequestHeader('Content-Type', 'application/octet-stream');
      request.upload.onprogress = (event) => { if (event.lengthComputable) element('upload-progress').value = event.loaded / event.total * 100; };
      request.onload = () => { let result; try { result = JSON.parse(request.responseText); } catch { reject(new Error('上传响应无效。')); return; } if (request.status >= 200 && request.status < 300) resolve(result); else reject(new Error(result.error || '上传失败')); };
      request.onerror = () => reject(new Error('上传失败，请检查服务连接。'));
      request.ontimeout = () => reject(new Error('上传超时，请检查处理记录后重试。'));
      request.send(file);
    });
    toast('上传完成，正在验证和转码。');
    if (state.selected === taskId) await refreshDetail();
  } catch (error) { toast(error.message, true); }
  finally { element('upload-progress').hidden = true; element('upload').value = ''; if (state.selected === taskId) await refreshDetail().catch(() => {}); }
}

element('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  element('login-error').textContent = '';
  try {
    const form = new FormData(event.target);
    const login = await api('/auth/login', { method: 'POST', body: { username: form.get('username'), password: form.get('password') } });
    event.target.reset();
    await initialize(login.account);
  } catch (error) { element('login-error').textContent = error.message === 'invalid credentials' ? '账号或密码不正确，请重试。' : error.message; }
  finally { button.disabled = false; }
});
for (const id of ['logout', 'mobile-logout']) element(id).addEventListener('click', () => action(element(id), async () => { await api('/auth/logout', { method: 'POST' }); showLogin(); }));
element('nav-tasks').addEventListener('click', () => { showView('tasks'); refreshTasks().catch((error) => toast(error.message, true)); });
element('nav-settings').addEventListener('click', () => { showView('settings'); refreshCapabilities().catch((error) => toast(error.message, true)); });
element('back').addEventListener('click', () => { showView('tasks'); refreshTasks().catch((error) => toast(error.message, true)); });
element('refresh').addEventListener('click', () => action(element('refresh'), refreshTasks));
element('refresh-detail').addEventListener('click', () => action(element('refresh-detail'), refreshDetail));
element('search').addEventListener('input', renderTasks);
element('status-filter').innerHTML += Object.entries(statuses).map(([value, name]) => `<option value="${value}">${name}</option>`).join('');
element('status-filter').addEventListener('change', renderTasks);
element('new-task').addEventListener('click', () => { element('new-task-dialog').showModal(); element('task-title').focus(); });
element('close-dialog').addEventListener('click', () => element('new-task-dialog').close());
element('new-task-form').addEventListener('submit', (event) => {
  event.preventDefault();
  action(event.submitter, async () => {
    const task = await api('/tasks', { method: 'POST', body: { title: element('task-title').value.trim(), risk_level: element('task-risk').value } });
    element('new-task-dialog').close(); event.target.reset(); toast('任务已创建，开始制作吧。'); await openTask(task.id);
  });
});
element('upload').addEventListener('change', (event) => uploadFile(event.target.files[0]));
element('upload-zone').addEventListener('dragover', (event) => { event.preventDefault(); element('upload-zone').classList.add('dragover'); });
element('upload-zone').addEventListener('dragleave', () => element('upload-zone').classList.remove('dragover'));
element('upload-zone').addEventListener('drop', (event) => { event.preventDefault(); element('upload-zone').classList.remove('dragover'); uploadFile(event.dataTransfer.files[0]); });
element('media-select').addEventListener('change', (event) => { state.mediaId = event.target.value; updatePreview(); });
element('generate-form').addEventListener('submit', (event) => {
  event.preventDefault();
  action(event.submitter, async () => {
    await api('/studio/tasks/' + state.selected + '/generate', { method: 'POST', body: { text: element('generate-text').value.trim(), duration: Number(element('duration').value), background_id: element('background').value || null } });
    toast('制作任务已提交，完成后会自动显示成片。'); await refreshDetail();
  });
});
element('review').addEventListener('click', () => action(element('review'), async () => {
  if (state.mediaId !== state.detail.media[0]?.id) throw new Error('请先选择并查看最新版本。');
  const result = await api('/studio/tasks/' + state.selected + '/review', { method: 'POST', body: { media_id: state.mediaId, caption: element('caption').value.trim() } });
  element('review-state').textContent = `当前文案已审核：${result.reviewers} / ${result.required} 人。修改视频或文案后需要重新审核。`;
  toast('审核已保存。');
}));
element('caption').addEventListener('input', () => { element('review-state').textContent = '文案已编辑，请重新审核后提交。'; });
element('publish').addEventListener('click', () => action(element('publish'), async () => {
  if (state.mediaId !== state.detail.media[0]?.id) throw new Error('只能发布已审核的最新视频。');
  const caption = element('caption').value.trim();
  const visibility = element('visibility').value;
  if (!window.confirm(`确认向已授权的抖音账号提交此视频？\n可见性：${visibility === 'public' ? '公开发布' : '仅自己可见'}\n文案：${caption}\n这将进行真实平台操作，并占用今天的提交名额。`)) return;
  await api('/studio/tasks/' + state.selected + '/publish', { method: 'POST', body: { media_id: state.mediaId, caption, visibility, confirmed: true } });
  toast('已进入真实提交队列，请查看发布记录。'); await refreshDetail();
}));
element('connect-douyin').addEventListener('click', () => action(element('connect-douyin'), async () => { const result = await api('/studio/douyin/authorize', { method: 'POST' }); window.location.assign(result.url); }));
setInterval(async () => {
  if (!state.user || !state.selected || state.polling || document.hidden) return;
  state.polling = true;
  try { await refreshDetail(); } catch (error) { toast(error.message, true); }
  finally { state.polling = false; }
}, 3000);
api('/auth/me').then(initialize).catch(showLogin);
