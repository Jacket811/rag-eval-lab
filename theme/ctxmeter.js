/**
 * Open WebUI 上下文窗口指示器
 * 在输入框工具栏插入一个环形进度按钮，点击展开用量明细。
 *
 * 数据来源：
 *   - 上下文总量：POST /ollama/api/show 读模型 Modelfile 里的 num_ctx（真实值）
 *   - 已用 token：按文本估算（汉字 0.66 / 其他字符 0.25，实测校准误差 5~12%）
 */
(() => {
	const CJK = /[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]/;
	const MODEL_RE = /^[a-z0-9][a-z0-9._-]*:[a-z0-9._-]+$/i;

	const state = {
		model: '',
		limit: 0, // 该模型的上下文窗口
		used: 0,
		parts: { system: 0, messages: 0, draft: 0 },
		open: false,
		loadingCtx: false
	};

	const cache = {}; // model -> num_ctx

	// ---------- token 估算 ----------
	function estimate(text) {
		if (!text) return 0;
		let cjk = 0;
		let other = 0;
		for (const ch of text) {
			if (CJK.test(ch)) cjk++;
			else other++;
		}
		return Math.round(cjk * 0.66 + other * 0.25);
	}

	// ---------- 当前模型 ----------
	function currentModel() {
		const bar = document.querySelector('#message-input-container > div:last-child');
		if (!bar) return '';
		const scope = bar.lastElementChild || bar;
		const nodes = scope.querySelectorAll('div, span, button');
		for (const el of nodes) {
			if (el.children.length > 0) continue;
			const t = (el.textContent || '').trim();
			if (t && t.length < 60 && MODEL_RE.test(t)) return t;
		}
		return '';
	}

	// ---------- 读模型上下文窗口 ----------
	async function loadLimit(model) {
		if (!model) return 0;
		if (cache[model] !== undefined) return cache[model];
		if (state.loadingCtx) return 0;
		state.loadingCtx = true;
		try {
			const r = await fetch('/ollama/api/show', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ model })
			});
			if (r.ok) {
				const d = await r.json();
				const m = /num_ctx\s+(\d+)/.exec(d.parameters || '');
				// 模型没写 num_ctx 时，Ollama 按 OLLAMA_CONTEXT_LENGTH 的默认值跑（默认 4096）
				cache[model] = m ? parseInt(m[1], 10) : 4096;
			}
		} catch (e) {
			/* 网络失败忽略 */
		} finally {
			state.loadingCtx = false;
		}
		return cache[model] || 0;
	}

	// ---------- 实测用量（以 ollama 回报的 prompt_eval_count 为准） ----------
	let measured = null; // { prompt, completion, used, ts }
	let measuredFor = null;
	let historyContent = ''; // 接口返回的完整对话文本，用于无实测值时估算

	function chatId() {
		const m = location.pathname.match(/\/c\/([0-9a-fA-F-]{36})/);
		return m ? m[1] : null;
	}

	function token() {
		try {
			let t = localStorage.getItem('token');
			if (!t) return null;
			t = t.trim();
			if (t.charAt(0) === '"') t = JSON.parse(t);
			return t.split('.').length === 3 ? t : null;
		} catch (e) {
			return null;
		}
	}

	async function loadUsage() {
		const id = chatId();
		if (!id) {
			measured = null;
			historyText = 0;
			return;
		}
		if (measuredFor === id && measured && Date.now() - measured.ts < 4000) return;

		const t = token();
		if (!t) return;
		try {
			const r = await fetch('/api/v1/chats/' + id, { headers: { Authorization: 'Bearer ' + t } });
			if (!r.ok) return;
			const d = await r.json();
			const chat = (d && d.chat) || {};

			/* 0.13 里 assistant 消息主要存在 history.messages，
			   chat.messages 往往只有用户那条，所以两边都要扫 */
			const pool = [];
			((chat.history || {}).messages
				? Object.keys(chat.history.messages).map((k) => chat.history.messages[k])
				: []
			).forEach((m) => m && pool.push(m));
			(chat.messages || []).forEach((m) => m && pool.push(m));

			/* 兜底估算：用接口返回的完整消息文本，而不是页面上渲染出来的那部分 */
			let text = '';
			pool.forEach((m) => {
				if (typeof m.content === 'string') text += m.content;
			});
			historyContent = text;

			const assistants = pool.filter(
				(m) => m.role === 'assistant' && m.usage && m.usage.prompt_eval_count
			);
			if (assistants.length) {
				assistants.sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0));
				const u = assistants[assistants.length - 1].usage;
				measured = {
					prompt: u.prompt_eval_count,
					completion: u.eval_count || 0,
					used: u.prompt_eval_count + (u.eval_count || 0),
					ts: Date.now()
				};
				measuredFor = id;
			} else {
				measured = null;
			}
		} catch (e) {
			/* 忽略 */
		}
	}

	// ---------- 统计各部分 ----------
	function collect() {
		let messages = 0;
		document.querySelectorAll('#messages-container .chat-user .markdown-prose').forEach((el) => {
			messages += estimate(el.textContent);
		});
		document.querySelectorAll('#messages-container .chat-assistant .markdown-prose').forEach((el) => {
			messages += estimate(el.textContent);
		});

		const editor = document.querySelector('#chat-input-container');
		const draft = estimate(editor ? editor.textContent : '');

		// Open WebUI 每次请求都会带一段系统提示 + 对话模板标记，按固定值估
		const system = 128;

		/* 有实测值时用它（含检索资料与模板开销），否则退回文本估算 */
		if (measured) {
			state.parts = {
				system: system,
				messages: Math.max(messages, measured.prompt - system),
				draft: draft,
				measured: true,
				raw: measured
			};
			state.used = measured.used + draft;
		} else {
			/* 页面是虚拟列表，只数 DOM 会偏小且随滚动变化；
			   接口里有完整文本时以它为准 */
			const est = historyContent ? estimate(historyContent) : messages;
			state.parts = { system, messages: Math.max(messages, est), draft, measured: false };
			state.used = system + Math.max(messages, est) + draft;
		}
	}

	// ---------- UI ----------
	let btn = null;
	let pop = null;
	let ringVal = null;

	function svgRing() {
		const NS = 'http://www.w3.org/2000/svg';
		const svg = document.createElementNS(NS, 'svg');
		svg.setAttribute('viewBox', '0 0 36 36');
		svg.setAttribute('width', '26');
		svg.setAttribute('height', '26');
		svg.classList.add('ow-ctx-ring');

		const bg = document.createElementNS(NS, 'circle');
		bg.setAttribute('cx', '18');
		bg.setAttribute('cy', '18');
		bg.setAttribute('r', '15');
		bg.setAttribute('fill', 'none');
		bg.setAttribute('stroke-width', '3');
		bg.classList.add('ow-ctx-ring-bg');

		const fg = document.createElementNS(NS, 'circle');
		fg.setAttribute('cx', '18');
		fg.setAttribute('cy', '18');
		fg.setAttribute('r', '15');
		fg.setAttribute('fill', 'none');
		fg.setAttribute('stroke-width', '3');
		fg.setAttribute('stroke-linecap', 'round');
		fg.setAttribute('transform', 'rotate(-90 18 18)');
		fg.setAttribute('stroke-dasharray', '94.25');
		fg.setAttribute('stroke-dashoffset', '94.25');
		fg.classList.add('ow-ctx-ring-fg');

		svg.appendChild(bg);
		svg.appendChild(fg);
		ringVal = fg;
		return svg;
	}

	function buildButton() {
		const b = document.createElement('button');
		b.id = 'ow-ctx-button';
		b.type = 'button';
		b.title = '上下文用量';
		b.setAttribute('aria-label', '上下文用量');
		b.appendChild(svgRing());
		b.addEventListener('click', (e) => {
			e.preventDefault();
			e.stopPropagation();
			togglePopover();
		});
		return b;
	}

	function fmt(n) {
		return n.toLocaleString('en-US');
	}

	function buildPopover() {
		const p = document.createElement('div');
		p.id = 'ow-ctx-popover';
		p.innerHTML = `
			<div class="ow-ctx-head">
				<span class="ow-ctx-title">上下文用量</span>
				<button type="button" class="ow-ctx-close" aria-label="关闭">×</button>
			</div>
			<div class="ow-ctx-summary">
				<div class="ow-ctx-pct"><span id="ow-ctx-pctnum">0</span><i>%</i></div>
				<div class="ow-ctx-nums">已使用 <b id="ow-ctx-used">0</b> / <span id="ow-ctx-limit">—</span></div>
			</div>
			<div class="ow-ctx-bar"><i id="ow-ctx-barfill"></i></div>
			<ul class="ow-ctx-list">
				<li><span class="ow-dot ow-dot-a"></span>系统提示词<b id="ow-ctx-system">0</b></li>
				<li><span class="ow-dot ow-dot-b"></span><span id="ow-ctx-msg-label">对话消息</span><b id="ow-ctx-messages">0</b></li>
				<li><span class="ow-dot ow-dot-c"></span>输入草稿<b id="ow-ctx-draft">0</b></li>
				<li><span class="ow-dot ow-dot-d"></span><span id="ow-ctx-left-label">剩余</span><b id="ow-ctx-left">0</b></li>
			</ul>
			<div class="ow-ctx-foot"><span id="ow-ctx-src">估算依据：汉字 0.66 token / 其他字符 0.25 token</span><span>窗口大小取自模型真实配置</span></div>
		`;
		p.querySelector('.ow-ctx-close').addEventListener('click', (e) => {
			e.stopPropagation();
			togglePopover(false);
		});
		return p;
	}

	function togglePopover(force) {
		const want = force === undefined ? !state.open : force;
		if (want && !pop) {
			pop = buildPopover();
			document.body.appendChild(pop);
		}
		state.open = want;
		if (!pop) return;
		if (want) {
			const r = btn.getBoundingClientRect();
			const box = document.querySelector('#message-input-container');
			const rb = box ? box.getBoundingClientRect() : r;
			pop.classList.add('show');
			// 先显示再量尺寸，避免定位跳动
			const pw = pop.offsetWidth;
			const ph = pop.offsetHeight;
			// 右边缘对齐按钮，但不超出视口
			let left = r.right - pw;
			if (left + pw > window.innerWidth - 12) left = window.innerWidth - pw - 12;
			if (left < 12) left = 12;
			// 浮在整个输入面板上方，避免压住输入框
			let top = rb.top - ph - 12;
			if (top < 12) top = rb.bottom + 12;
			pop.style.left = left + 'px';
			pop.style.top = top + 'px';
			render();
		} else {
			pop.classList.remove('show');
		}
	}

	function color(pct) {
		if (pct >= 90) return 'var(--ow-ctx-crit)';
		if (pct >= 70) return 'var(--ow-ctx-warn)';
		return 'var(--ow-accent, #3d9bff)';
	}

	function render() {
		const limit = state.limit || 0;
		const used = state.used;
		const pct = limit ? Math.min(100, (used / limit) * 100) : 0;
		const c = color(pct);

		if (ringVal) {
			const C = 94.25;
			ringVal.setAttribute('stroke-dashoffset', String(C - (C * pct) / 100));
			ringVal.style.stroke = c;
		}
		if (btn) btn.title = limit ? `上下文 ${pct.toFixed(1)}%（${fmt(used)} / ${fmt(limit)} token）` : '上下文用量';

		if (!pop || !state.open) return;
		const $ = (id) => pop.querySelector('#' + id);
		$('ow-ctx-pctnum').textContent = pct.toFixed(1);
		$('ow-ctx-pctnum').style.color = c;
		$('ow-ctx-used').textContent = fmt(used);
		$('ow-ctx-limit').textContent = limit ? fmt(limit) : '未知（模型未回报 num_ctx）';
		$('ow-ctx-system').textContent = fmt(state.parts.system);
		$('ow-ctx-messages').textContent = fmt(state.parts.messages);
		$('ow-ctx-draft').textContent = fmt(state.parts.draft);
		$('ow-ctx-left').textContent = limit
			? used > limit
				? '超出 ' + fmt(used - limit)
				: fmt(limit - used)
			: '—';
		const leftLabel = $('ow-ctx-left-label');
		if (leftLabel) leftLabel.textContent = limit && used > limit ? '已超出' : '剩余';

		const label = $('ow-ctx-msg-label');
		const src = $('ow-ctx-src');
		if (state.parts.measured) {
			if (label) label.textContent = '历史 + 检索资料（实测）';
			if (src) src.textContent = '实测：上一轮 prompt ' + fmt(state.parts.raw.prompt) + ' + 回答 ' + fmt(state.parts.raw.completion);
		} else {
			if (label) label.textContent = '对话消息';
			if (src) src.textContent = '估算依据：汉字 0.66 token / 其他字符 0.25 token';
		}
		const fill = $('ow-ctx-barfill');
		fill.style.width = pct + '%';
		fill.style.background = c;
	}

	// ---------- 挂载 / 循环 ----------
	function ensureMounted() {
		const voice = document.querySelector('#voice-input-button');
		if (!voice) {
			if (btn && btn.isConnected) btn.remove();
			return;
		}
		if (!btn) btn = buildButton();
		if (!btn.isConnected) {
			const anchor = voice.closest('div.flex') || voice;
			anchor.parentElement.insertBefore(btn, anchor);
		}
	}

	async function tick() {
		ensureMounted();
		if (!btn || !btn.isConnected) return;

		const m = currentModel();
		if (m && m !== state.model) {
			state.model = m;
			const n = await loadLimit(m);
			state.limit = n || state.limit || 0;
		} else if (!state.limit && state.model) {
			state.limit = (await loadLimit(state.model)) || 0;
		}

		await loadUsage();
		collect();
		render();
	}

	// 点击外部关闭
	document.addEventListener(
		'click',
		(e) => {
			if (!state.open) return;
			if (pop && pop.contains(e.target)) return;
			if (btn && btn.contains(e.target)) return;
			togglePopover(false);
		},
		true
	);

	// 输入时即时更新
	document.addEventListener('input', () => setTimeout(tick, 60), true);

	const boot = () => {
		tick();
		setInterval(tick, 900);
	};

	if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
	else boot();

	window.owuiCtx = {
		get state() {
			return JSON.parse(JSON.stringify(state));
		},
		get cache() {
			return { ...cache };
		},
		refresh: tick,
		open: () => togglePopover(true),
		close: () => togglePopover(false)
	};
})();
