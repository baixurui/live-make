const aiView = { taskId: null, textJobId: null, imageId: null };
const aiPanel = document.createElement('section');
aiPanel.className = 'panel action-panel';
aiPanel.id = 'ai-panel';
aiPanel.innerHTML = `
  <div class="section-heading"><h2><span class="step">AI</span> 百炼创作流水线</h2><span class="pill purple-pill">逐步确认</span></div>
  <p id="ai-config-status" class="hint"></p>
  <label>① 创作主题<textarea id="ai-topic" maxlength="1000" rows="2" placeholder="例如：雨后城市中的咖啡店，温暖治愈的产品短片"></textarea></label>
  <button id="ai-text" class="primary wide">生成文案与画面描述</button>
  <div class="divider"></div>
  <label>生成的文案（可编辑）<textarea id="ai-script" maxlength="100" rows="3" placeholder="生成文案后在这里修改；不会自动作为配音"></textarea></label>
  <label>② 首帧画面描述<textarea id="ai-image-prompt" maxlength="800" rows="4" placeholder="确认画面描述后，再生成图片"></textarea></label>
  <button id="ai-image" class="secondary wide">确认描述，生成 1 张竖屏图片</button>
  <div id="ai-image-preview" hidden><img id="ai-frame" alt="百炼生成的最新首帧图片"><p class="hint">图生视频将使用这张最新图片；不满意可修改描述重新生成。</p><a id="ai-image-download" class="text-link">下载首帧图片 ↓</a></div>
  <div class="divider"></div>
  <label>③ 视频动作与运镜<textarea id="ai-video-prompt" maxlength="1000" rows="3" placeholder="例如：镜头缓慢推进，主体保持一致，光影轻柔变化"></textarea></label>
  <label>视频时长<select id="ai-duration"><option value="8">8 秒 · 720P（默认）</option><option value="10">10 秒 · 720P</option><option value="15">15 秒 · 720P</option></select></label>
  <button id="ai-video" class="primary wide">确认首帧，生成视频</button>
  <p class="hint">各步骤独立调用并可能产生费用，不会自动连续生成或发布。8 秒默认单镜头；文案不保证自动配音。生成的视频会保存到左侧成片列表。</p>
  <button id="ai-use-caption" class="text-link">将当前文案复制到发布文案 →</button>
  <div id="ai-jobs" class="record-list"></div>`;
const templatePanel = element('generate-form').closest('section');
templatePanel.before(aiPanel);

const aiSettings = document.createElement('section');
aiSettings.className = 'panel settings-card';
aiSettings.innerHTML = `<div class="platform-logo template-logo">✦</div><h2>阿里云百炼</h2><p id="ai-settings-status"></p><dl id="ai-models"></dl><p class="hint">在项目根目录 <code>.env</code> 填写 <code>DASHSCOPE_API_KEY</code>，重启服务后即可开始。使用北京地域 Key；“已配置”不代表余额和模型权限已验证。</p><p class="hint">Key 不会发送到浏览器。图片和视频异步生成；查询中断时使用“恢复查询”，不会重复创建任务。每次生成前都会提示确认费用。</p>`;
document.querySelector('.settings-grid').append(aiSettings);

function aiCapabilities() {
  const configuration = state.capabilities?.bailian;
  if (!configuration) return;
  const message = configuration.configured ? 'Key 已配置 · 北京地域 · 权限与余额以实际调用结果为准' : '尚未配置 Key：请填写 .env 中的 DASHSCOPE_API_KEY 并重启服务。';
  element('ai-config-status').textContent = message;
  element('ai-settings-status').textContent = message;
  element('ai-models').innerHTML = [['文本', configuration.text_model], ['文生图', configuration.image_model], ['图生视频', configuration.video_model]].map(([label, model]) => `<dt>${label}</dt><dd><code>${escapeHtml(model)}</code></dd>`).join('');
}

function renderAi(task, detail) {
  const jobs = detail.ai?.jobs || [];
  const image = detail.ai?.images[0];
  if (aiView.taskId !== task.id) {
    aiView.taskId = task.id;
    aiView.textJobId = null;
    aiView.imageId = null;
    for (const id of ['ai-script', 'ai-image-prompt', 'ai-video-prompt']) element(id).value = '';
    element('ai-topic').value = task.title;
  }
  const textJob = jobs.find((job) => job.stage === 'text' && job.status === 'SUCCEEDED');
  if (textJob && textJob.id !== aiView.textJobId) {
    aiView.textJobId = textJob.id;
    element('ai-script').value = textJob.result.script;
    element('ai-image-prompt').value = textJob.result.image_prompt;
    element('ai-video-prompt').value = textJob.result.video_prompt;
    if (!element('caption').value) element('caption').value = textJob.result.caption;
  }
  aiView.imageId = image?.id || null;
  element('ai-image-preview').hidden = !image;
  if (image && element('ai-frame').getAttribute('src') !== image.url) {
    element('ai-frame').src = image.url;
    element('ai-image-download').href = image.url + '?download=1';
  }
  const unsettled = jobs.some((job) => ['QUEUED', 'SUBMITTING', 'RUNNING', 'WAITING', 'UNKNOWN'].includes(job.status));
  const busy = detail.jobs.some((job) => ['QUEUED', 'RUNNING'].includes(job.status));
  const locked = unsettled || busy || task.risk_level === 'BLOCKED' || ['CANCELLED', 'PUBLISHED'].includes(task.status) || detail.publications.length > 0;
  for (const stage of ['text', 'image', 'video']) element('ai-' + stage).disabled = locked || !state.capabilities?.bailian.configured || (stage === 'video' && !image);
  if (unsettled) for (const id of ['upload', 'generate-button', 'review', 'publish']) element(id).disabled = true;
  const statusNames = { QUEUED: '排队中', SUBMITTING: '正在提交', RUNNING: '云端生成中', SUCCEEDED: '已完成', FAILED: '失败', WAITING: '待恢复查询', UNKNOWN: '提交结果未知', ACKNOWLEDGED: '已人工核对' };
  element('ai-jobs').innerHTML = jobs.map((job) => `<div class="record-item"><div><span>${{ text: '文本生成', image: '文生图', video: '图生视频' }[job.stage]}</span><span class="status ${job.status === 'SUCCEEDED' ? 'good' : ['FAILED', 'UNKNOWN'].includes(job.status) ? 'warn' : 'neutral'}">${statusNames[job.status]}</span></div><small>${dateText(job.created_at)}</small>${job.remote_id ? '<p class="remote-id">任务 ID：' + escapeHtml(job.remote_id) + '</p>' : ''}${job.error ? '<p>' + escapeHtml(job.error) + '</p>' : ''}${job.status === 'WAITING' ? '<button class="text-link ai-resume" data-id="' + job.id + '">恢复查询原任务 →</button>' : ''}${job.status === 'UNKNOWN' && state.user.is_admin ? '<button class="text-link ai-acknowledge" data-id="' + job.id + '">已核对控制台，解锁任务</button>' : ''}</div>`).join('');
  document.querySelectorAll('.ai-resume').forEach((button) => button.addEventListener('click', () => aiOperation(button, 'resume', { job_id: button.dataset.id }, false)));
  document.querySelectorAll('.ai-acknowledge').forEach((button) => button.addEventListener('click', () => {
    if (window.confirm('只有确认百炼控制台中的任务和费用后才能解锁。本操作不会取消远端任务、不会退款；重新生成可能重复计费。确认已核对？')) aiOperation(button, 'acknowledge', { job_id: button.dataset.id, confirmed: true }, false);
  }));
  aiCapabilities();
}

async function aiOperation(button, stage, body, confirmCost = true) {
  const configuration = state.capabilities?.bailian;
  if (confirmCost && !window.confirm(`确认调用 ${configuration[stage + '_model']}？\n${stage === 'video' ? body.duration + ' 秒 / 720P，使用当前首帧。\n' : ''}这会将输入发送到阿里云百炼，并可能产生费用；不会自动执行下一步。`)) return;
  button.disabled = true;
  try {
    await api('/studio/tasks/' + state.selected + '/ai/' + stage, { method: 'POST', body: { ...body, confirmed: true } });
    toast(stage === 'resume' ? '已恢复查询原任务。' : stage === 'acknowledge' ? '已记录人工核对。' : '已加入生成队列，请查看进度。');
  } catch (error) { toast(error.message, true); }
  finally { await refreshDetail().catch((error) => toast(error.message, true)); }
}

element('ai-text').addEventListener('click', () => aiOperation(element('ai-text'), 'text', { topic: element('ai-topic').value.trim() }));
element('ai-image').addEventListener('click', () => aiOperation(element('ai-image'), 'image', { prompt: element('ai-image-prompt').value.trim() }));
element('ai-video').addEventListener('click', () => aiOperation(element('ai-video'), 'video', { prompt: element('ai-video-prompt').value.trim(), image_id: aiView.imageId, duration: Number(element('ai-duration').value) }));
element('ai-use-caption').addEventListener('click', () => { element('caption').value = element('ai-script').value; element('caption').dispatchEvent(new Event('input')); toast('已复制，请审核发布文案。'); });
