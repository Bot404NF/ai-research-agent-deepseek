/* 研究页面：所有模型和资料文本通过 textContent 或已有安全 Markdown 渲染器展示。 */
(() => {
  const workspace = document.querySelector('#researchWorkspace');
  const content = document.querySelector('#researchContent');
  const notice = document.querySelector('#researchNotice');
  const names = {awaiting_confirmation:'待确认',running:'运行中',waiting_continue:'等待继续',waiting_web_consent:'等待联网许可',failed:'失败',completed:'已完成',cancelled:'已取消',pending:'待执行',succeeded:'成功',skipped:'已跳过'};
  const phases = {planning:'拟定研究计划',plan:'确认研究计划',research:'分题收集与分析',review:'检查证据缺口与冲突',report:'汇总研究报告',completed:'研究完成'};
  let selected = null, shownVersion = '', requestBusy = false, polling = false;
  try { selected = localStorage.getItem('researchTaskId'); } catch {}

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function mode(research) {
    document.body.dataset.mode=research?'research':'chat';
    closeSidebar();
    workspace.hidden = !research;
    document.querySelector('.chat').hidden = research;
    document.querySelector('.top-actions').hidden = research;
    document.querySelector('#researchMode').setAttribute('aria-pressed', String(research));
    document.querySelector('#chatMode').setAttribute('aria-pressed', String(!research));
    document.querySelector('.workspace-title').textContent = research ? '研究' : '问答';
    document.querySelector('.workspace-subtitle').textContent = research ? '确认计划 · 分题取证 · 检查证据 · 汇总报告' : '基于资料的多轮问答与来源引用';
  }
  function remember(id) {
    selected = id; shownVersion = '';
    try { if (id) localStorage.setItem('researchTaskId', id); else localStorage.removeItem('researchTaskId'); } catch {}
  }
  async function api(path = '', payload) {
    const response = await fetch('/api/research/tasks' + path, {
      method: payload === undefined ? 'GET' : 'POST', cache:'no-store',
      headers: payload === undefined ? {} : {'Content-Type':'application/json'},
      body: payload === undefined ? undefined : JSON.stringify(payload),
      signal: AbortSignal.timeout(15000),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `请求失败（HTTP ${response.status}）`);
    return data;
  }
  async function perform(operation) {
    if (requestBusy) return;
    requestBusy = true; notice.textContent = '';
    content.querySelectorAll('button').forEach(button => { button.disabled = true; });
    try { await operation(); await refresh(); }
    catch (error) { notice.textContent = error.message || '请求失败，请重试。'; }
    finally {
      requestBusy = false;
      content.querySelectorAll('button').forEach(button => { button.disabled = false; });
    }
  }
  function button(text, action, primary = false) {
    const element = node('button', text, primary ? 'primary' : '');
    element.type = 'button'; element.onclick = () => perform(action);
    return element;
  }
  function field(parent, label, value = '', multiline = false, max = 1000) {
    const wrapper = node('label', label);
    const input = node(multiline ? 'textarea' : 'input');
    if (!multiline) input.type = 'text';
    input.value = value; input.maxLength = max; input.required = true;
    wrapper.append(input); parent.append(wrapper); return input;
  }
  function check(parent, label, value = false) {
    const wrapper = node('label', undefined, 'research-check');
    const input = node('input'); input.type = 'checkbox'; input.checked = value;
    wrapper.append(input, document.createTextNode(label)); parent.append(wrapper); return input;
  }
  function newResearch(options = {}) {
    remember(null); if(options.activate!==false)mode(true); notice.textContent = '';
    workspace.classList.add('research-empty');
    content.replaceChildren();
    const home = node('div', undefined, 'research-home');
    home.append(node('h1', '今天想研究什么？', 'research-welcome'));
    const form = node('form', undefined, 'research-launch composer');
    const inputWrap = node('div', undefined, 'input-wrap');
    const goal = node('textarea');goal.id='researchGoal';goal.rows=1;goal.maxLength=4000;
    goal.required=true;goal.setAttribute('aria-label','研究目标');
    goal.placeholder='输入研究主题，生成研究计划…';
    const submit = node('button', undefined, 'send');submit.type='submit';
    submit.setAttribute('aria-label','生成研究计划');submit.title='生成研究计划';submit.disabled=true;
    submit.innerHTML='<svg class="icon" aria-hidden="true"><use href="#i-send"/></svg>';
    inputWrap.append(goal, submit);form.append(inputWrap);
    let composing=false;
    function syncGoal(){
      goal.style.height='auto';goal.style.height=Math.min(goal.scrollHeight,192)+'px';
      submit.disabled=requestBusy||!goal.value.trim();
    }
    goal.addEventListener('input',syncGoal);
    goal.addEventListener('compositionstart',()=>{composing=true;});
    goal.addEventListener('compositionend',()=>{composing=false;syncGoal();});
    goal.addEventListener('keydown',event=>{
      if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing&&!composing&&event.keyCode!==229){event.preventDefault();form.requestSubmit();}
    });
    form.addEventListener('submit',async event=>{
      event.preventDefault();if(requestBusy||!goal.value.trim())return;
      submit.setAttribute('aria-busy','true');
      await perform(async()=>{
        const task=await api('',{goal:goal.value,ask_web_consent:true});remember(task.id);
      });
      submit.removeAttribute('aria-busy');syncGoal();
    });
    home.append(form);content.append(home);
    workspace.scrollTop=0;
  }

  function editPlan(task, card) {
    card.append(node('h2', '确认研究计划'));
    const scope = field(card, '研究范围', task.plan.scope, true, 2000);
    const output = field(card, '预期成果', task.plan.expected_output, true, 1000);
    const questions = node('div'); card.append(questions);
    let entries = task.plan.questions.map(item => ({...item}));
    function draw() {
      questions.replaceChildren();
      entries.forEach((item, index) => {
        const row = node('div', undefined, 'research-question');
        row.append(node('h3', `子问题 ${index + 1}`));
        const question = field(row, '研究问题', item.question, true);
        const query = field(row, '联网搜索关键词（最多 80 字符）', item.search_query, false, 80);
        const current = check(row, '需要联网核对时效性信息', item.needs_current);
        const news = check(row, '搜索最近 30 天新闻', item.news);
        question.oninput = () => { item.question = question.value; };
        query.oninput = () => { item.search_query = query.value; };
        current.onchange = () => { item.needs_current = current.checked; };
        news.onchange = () => { item.news = news.checked; };
        const remove = node('button', '移除此问题', 'research-remove'); remove.type = 'button';
        remove.onclick = () => {
          if (entries.length <= 3) { notice.textContent = '至少保留 3 个子问题。'; return; }
          entries.splice(index, 1); draw();
        };
        row.append(remove); questions.append(row);
      });
    }
    draw();
    const add = node('button', '＋ 添加子问题'); add.type = 'button';
    add.onclick = () => {
      if (entries.length >= 5) { notice.textContent = '最多支持 5 个子问题。'; return; }
      entries.push({question:'',search_query:'',needs_current:false,news:false}); draw();
    };
    card.append(add);
    function payload() { return {plan:{scope:scope.value,expected_output:output.value,questions:entries}}; }
    const actions = node('div', undefined, 'research-actions');
    actions.append(button('保存计划', async () => { render(await api(`/${task.id}/plan`, payload()), true); }),
      button('确认计划并执行', async () => { render(await api(`/${task.id}/confirm`, payload()), true); }, true));
    card.append(actions, node('p', '确认后锁定计划；需要联网补充资料时会询问你，未经同意不会联网。', 'research-muted'));
  }
  function markdown(text) {
    const view = node('div', undefined, 'research-markdown');
    renderMarkdown(view, text); return view;
  }
  function render(task, force = false) {
    workspace.classList.remove('research-empty');
    if (!force && shownVersion === task.updated_at) return;
    const resetScroll = force || !shownVersion;
    const expanded = new Set([...content.querySelectorAll('details[open]')].map(item => item.dataset.key));
    shownVersion = task.updated_at; content.replaceChildren();
    const card = node('div', undefined, 'research-card');
    card.append(node('h1', task.goal), node('span', names[task.status] || task.status, 'research-badge'));
    card.append(node('p', phases[task.phase] || task.phase));
    if (task.error) card.append(node('p', task.error, 'research-error'));
    if (task.status === 'awaiting_confirmation' && task.plan) editPlan(task, card);
    else {
      card.append(node('p', `本任务联网：${task.allow_web ? '已获许可' : task.web_consent==='unknown' ? '需要时会询问' : '仅使用资料库'}`, 'research-muted'));
      if (task.plan) {
        const plan = node('details'); plan.dataset.key = 'plan'; plan.append(node('summary', '查看已确认计划'));
        plan.append(node('p', task.plan.scope), node('p', task.plan.expected_output));
        const list = node('ol'); task.plan.questions.forEach(question => list.append(node('li', question.question))); plan.append(list); card.append(plan);
      }
      for (const [index, step] of task.steps.entries()) {
        const row = node('div', undefined, 'research-step');
        const title = node('div', undefined, 'research-step-title');
        title.append(node('span', String(index + 1).padStart(2, '0')), node('strong', step.title), node('span', names[step.status], 'research-badge'));
        row.append(title);
        if (step.error) row.append(node('p', step.error, 'research-error'));
        if (step.summary) {
          const details = node('details'); details.dataset.key = `summary-${index}`; details.append(node('summary', '查看阶段成果'), markdown(step.summary)); row.append(details);
        }
        if (step.evidence_labels?.length) {
          const details = node('details'); details.dataset.key = `evidence-${index}`; details.append(node('summary', `查看取证快照 · ${step.evidence_labels.length} 条`));
          for (const evidence of task.evidence.filter(item => step.evidence_labels.includes(item.label))) {
            details.append(node('pre', `[${evidence.label}]\n${JSON.stringify(evidence, null, 2)}`));
          }
          row.append(details);
        }
        card.append(row);
      }
      if(task.status==='waiting_web_consent'){
        const prompt=node('section',undefined,'research-consent');
        prompt.setAttribute('aria-label','联网许可');
        prompt.append(node('h2','是否允许本次研究联网补充资料？'));
        const request=task.web_consent_request||{};
        if(request.question)prompt.append(node('p',`当前问题：${request.question}`));
        prompt.append(node('p',request.reason||'资料库证据不足，需要联网补充。'));
        if(request.search_query)prompt.append(node('p',`拟搜索：${request.search_query}`,'research-muted'));
        prompt.append(node('p','许可仅适用于本次研究，搜索词会发送至外部服务；拒绝后仅用资料库继续，不再重复询问。','research-muted'));
        const choices=node('div',undefined,'research-actions');
        choices.append(button('允许本次研究联网',async()=>{render(await api(`/${task.id}/web-consent`,{allow_web:true}),true);},true),
          button('仅用资料库继续',async()=>{render(await api(`/${task.id}/web-consent`,{allow_web:false}),true);}));
        prompt.append(choices);card.append(prompt);
      }
      const actions = node('div', undefined, 'research-actions');
      if (task.status === 'failed') actions.append(button(task.confirmed ? '重试失败步骤' : '重试生成计划', async () => { render(await api(`/${task.id}/retry`, {}), true); }, true));
      if (task.status === 'waiting_continue') actions.append(button('从未完成步骤继续', async () => { render(await api(`/${task.id}/continue`, {}), true); }, true));
      card.append(actions);
    }
    if (!['completed','cancelled'].includes(task.status)) {
      if (task.cancel_requested) card.append(node('p', '已请求取消，当前外部调用结束后停止。', 'research-muted'));
      else card.append(button('取消任务', async () => { render(await api(`/${task.id}/cancel`, {}), true); }));
    }
    content.append(card);
    if (task.review) {
      const review = node('div', undefined, 'research-card'); review.append(node('h2', '证据检查记录'), markdown(task.review)); content.append(review);
    }
    if (task.report && task.status === 'completed') {
      const report = node('div', undefined, 'research-card');
      const link = node('a', '下载 Markdown 报告'); link.href = `/api/research/tasks/${task.id}/report`; link.download = `research-${task.id}.md`;
      report.append(link, markdown(task.report)); content.append(report);
    }
    content.querySelectorAll('details').forEach(item => { item.open = expanded.has(item.dataset.key); });
    if (resetScroll) workspace.scrollTop = 0;
  }
  async function refresh() {
    const id = selected;
    const {tasks} = await api();
    const list = document.querySelector('#researchTaskList'); list.replaceChildren();
    const history = document.querySelector('#researchHistory'); history.replaceChildren(new Option('打开历史研究任务', ''));
    if (!tasks.length) list.append(node('span', '尚无研究任务', 'history-empty'));
    for (const task of tasks) {
      const item = node('button'); item.type = 'button'; item.setAttribute('aria-current', String(task.id === selected));
      item.append(node('strong', task.goal), node('span', names[task.status]));
      item.onclick = () => perform(async () => { remember(task.id); mode(true); }); list.append(item);
      history.append(new Option(`${names[task.status]} · ${task.goal.slice(0, 38)}`, task.id));
    }
    history.value = selected || '';
    if (id && id === selected) {
      if (!tasks.some(task => task.id === id)) { remember(null); newResearch({activate:!workspace.hidden}); return; }
      const task = await api(`/${id}`);
      if (id === selected) render(task);
    }
    if (notice.textContent.startsWith('任务状态读取失败：')) notice.textContent = '';
  }
  document.getElementById('newResearch').onclick = newResearch;
  document.querySelector('#researchMode').onclick = () => mode(true);
  document.querySelector('#chatMode').onclick = () => mode(false);
  ['newConversation','newConversationMobile','conversationList'].forEach(id => {
    document.getElementById(id).addEventListener('click', () => mode(false));
  });
  document.querySelector('#researchHistory').onchange = event => {
    const id = event.target.value;
    if (id) perform(async () => { remember(id); mode(true); });
  };
  if (!selected) newResearch({activate:false});
  mode(false);
  refresh().catch(error => { notice.textContent = error.message; });
  setInterval(async () => {
    if (polling || requestBusy || workspace.hidden) return;
    polling = true;
    try { await refresh(); } catch (error) { notice.textContent = `任务状态读取失败：${error.message}`; }
    finally { polling = false; }
  }, 2000);
})();
