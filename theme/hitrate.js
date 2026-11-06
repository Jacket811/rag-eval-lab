/*
 * owui-hit —— 在助手回答下方显示本次检索的「命中率」
 *
 * 数据来源：/api/v1/chats/{id} 里每条助手消息的 sources[].distances
 * 命中率定义：本次召回的 N 段资料的平均相关度（0~1 → 百分比）
 *
 * 以「页面」为准：遍历 DOM 里真实渲染的消息节点（id="message-{uuid}"），
 * 反查接口数据。这样分支（重新生成）、流式输出、刷新历史都不会错位。
 *
 * 三道校验，避免把上一条的分数挂到正在生成的新消息上：
 *   1. 该 id 在接口里必须是 done 的助手消息，且有 sources
 *   2. 正文去掉标点后，首 24 字与末 30 字都要能在节点文本里找到
 *   3. 节点文本长度不能明显短于正文（流式未完时会被挡下）
 */
(function () {
	if (window.owuiHit && window.owuiHit.ready) return;

	const PILL_CLASS = 'ow-hit';
	const TICK_MS = 1500;
	const MAX_TICKS = 160;
	const SENT_K = 6;
	const MSG_RE = /^message-[0-9a-fA-F-]{36}$/;

	const state = { timer: null, ticks: 0 };

	function getToken() {
		try {
			let t = localStorage.getItem('token');
			if (!t) return null;
			t = t.trim();
			if (t.charAt(0) === '"') t = JSON.parse(t);
			if (t.split('.').length !== 3) return null;
			return t;
		} catch (e) {
			return null;
		}
	}

	function getChatId() {
		const m = location.pathname.match(/\/c\/([0-9a-fA-F-]{36})/);
		return m ? m[1] : null;
	}

	function clean(s) {
		return (s || '').replace(/[^\u4e00-\u9fa5a-zA-Z0-9]/g, '');
	}

	function gramsOf(m) {
		const bc = clean(m && m.content);
		const grams = new Set();
		for (let i = 0; i + 3 <= bc.length; i++) grams.add(bc.slice(i, i + 3));
		return grams;
	}

	function visibleText(node) {
		const clone = node.cloneNode(true);
		clone.querySelectorAll('.' + PILL_CLASS).forEach((n) => n.remove());
		return clone.textContent || '';
	}

	function colorFor(pct) {
		if (pct >= 80) return 'ow-hit-good';
		if (pct >= 60) return 'ow-hit-mid';
		return 'ow-hit-bad';
	}

	function countCitations(text) {
		const set = new Set();
		const re = /\[(\d{1,2})\]/g;
		let m;
		while ((m = re.exec(text || ''))) set.add(m[1]);
		return set.size;
	}

	function buildPill(scores, cited, recalled) {
		const total = scores.length;
		const avg = scores.reduce((a, b) => a + b, 0) / total;
		const pct = Math.round(avg * 100);
		const max = Math.max.apply(null, scores);
		const min = Math.min.apply(null, scores);

		/* 数据异常：峰值过低说明重排没生效，不做误导性百分比。
		   注意 total === 1 时 max 恒等于 min，不能据"离散度"判为异常——
		   关闭查询改写后只召回 1 段是正常情况。 */
		const reliable = max >= 0.1 && (total < 2 || max - min >= 0.02);

		const pill = document.createElement('span');
		pill.className = PILL_CLASS + ' ' + (reliable ? colorFor(pct) : 'ow-hit-na');
		pill.title = reliable
			? '召回 ' + recalled + ' 段候选\n' +
			  '送入模型 ' + total + ' 段\n' +
			  '平均相关度 ' + pct + '%（最高 ' + Math.round(max * 100) + '% · 最低 ' + Math.round(min * 100) + '%）\n' +
			  '正文引用 ' + cited + ' 段'
			: '召回 ' + recalled + ' 段候选\n' +
			  '相关度数据异常（最高 ' + Math.round(max * 100) + '%），\n' +
			  '通常是生成这条回答时重排服务未生效，无法计算命中率';

		const dot = document.createElement('span');
		dot.className = 'ow-hit-dot';
		const label = document.createElement('span');
		label.textContent = reliable ? '命中率 ' + pct + '%' : '命中率 —';
		pill.dataset.pct = String(reliable ? pct : -1);
		pill.appendChild(dot);
		pill.appendChild(label);

		if (reliable && cited > 0) {
			const c = document.createElement('span');
			c.className = 'ow-hit-cite';
			c.textContent = '引用 ' + cited + '/' + total;
			pill.appendChild(c);
		}
		return pill;
	}

	function scoresOf(msg) {
		const out = [];
		((msg && msg.sources) || []).forEach((s) => {
			((s && s.distances) || []).forEach((d) => {
				if (typeof d === 'number' && isFinite(d)) out.push(d);
			});
		});
		return out;
	}

	async function fetchChat(id, token) {
		try {
			const res = await fetch('/api/v1/chats/' + id, {
				headers: { Authorization: 'Bearer ' + token }
			});
			if (!res.ok) {
				console.warn('[owui-hit] 拉取会话失败，HTTP ' + res.status);
				return null;
			}
			const data = await res.json();
			return (data && data.chat) || null;
		} catch (e) {
			return null;
		}
	}

	function messageNodes() {
		return Array.prototype.slice
			.call(document.querySelectorAll('[id^="message-"]'))
			.filter((n) => MSG_RE.test(n.id));
	}

	/* 页面上的消息 id 属于 history.messages（含所有分支），
	   而 messages 只是当前主链，两者不一致。合并查表。 */
	function indexMessages(chat) {
		const byId = {};
		const add = (m) => {
			if (m && m.id) byId[m.id] = m;
		};
		((chat.history || {}).messages || {}) &&
			Object.keys(chat.history.messages).forEach((k) => add(chat.history.messages[k]));
		(chat.messages || []).forEach(add);
		return byId;
	}

	function candidates(byId) {
		const out = [];
		Object.keys(byId).forEach((k) => {
			const m = byId[k];
			if (!m || m.role !== 'assistant' || m.done === false) return;
			const scores = scoresOf(m);
			if (!scores.length) return;
			const bc = clean(m.content);
			if (bc.length < 12) return;
			const grams = new Set();
			for (let i = 0; i + 3 <= bc.length; i++) grams.add(bc.slice(i, i + 3));
			out.push({ msg: m, scores: scores, bc: bc, grams: grams });
		});
		return out;
	}

	function hostFor(node) {
		const pros = node.querySelectorAll('.markdown-prose');
		return pros.length ? pros[pros.length - 1] : node;
	}

	/* 配对：正文与页面文本做 3-gram 覆盖率比较。
	   不能用连续子串——正文里的引用标签（文件名）会被插到句子中间，把连续匹配打断 */
	function coverage(grams, domClean) {
		if (!grams.size || !domClean) return 0;
		let hit = 0;
		grams.forEach((g) => {
			if (domClean.indexOf(g) !== -1) hit += 1;
		});
		return hit / grams.size;
	}

	function matches(domClean, cand) {
		if (domClean.length < 12) return 0;
		const ratio = domClean.length / cand.bc.length;
		if (ratio < 0.85 || ratio > 1.6) return 0;
		const cov = coverage(cand.grams, domClean);
		return cov >= 0.85 ? cov : 0;
	}

	function attachTo(block, msg, scores, recalled) {
		const pill = buildPill(scores, countCitations(msg.content), recalled);
		const sig = (pill.dataset.pct || '') + '|' + msg.id;

		/* 徽标是插在正文块「后面」的兄弟节点，不是子节点 */
		const next = block.nextElementSibling;
		const existing =
			next && next.classList.contains(PILL_CLASS) ? next : block.querySelector('.' + PILL_CLASS);

		if (existing) {
			const oldSig = (existing.dataset.pct || '') + '|' + (existing.dataset.msgid || '');
			if (oldSig === sig) return true;
			existing.remove();
		}
		pill.dataset.msgid = msg.id;
		block.insertAdjacentElement('afterend', pill);
		return true;
	}

	async function sweep() {
		const id = getChatId();
		const token = getToken();
		if (!id || !token) return false;

		/* 每个正文块都试配对，块在页面上才算数 */
		const blocks = Array.prototype.slice.call(document.querySelectorAll('.markdown-prose'));
		if (!blocks.length) return false;

		const chat = await fetchChat(id, token);
		if (!chat) return false;

		const byId = indexMessages(chat);
		const cands = candidates(byId);
		if (!cands.length) return false;

		let reachedLeaf = false;
		blocks.forEach((block, idx) => {
			if (block.nextElementSibling && block.nextElementSibling.classList.contains(PILL_CLASS)) {
				if (idx === blocks.length - 1) reachedLeaf = true;
			}

			/* 优先用 DOM 结构拿真实消息 id，精确配对 */
			let best = null;
			const owner = block.closest('[id^="message-"]');
			if (owner && MSG_RE.test(owner.id)) {
				const m = byId[owner.id.slice('message-'.length)];
				if (m && m.role === 'assistant' && m.done !== false) {
					const sc = scoresOf(m);
					if (sc.length) {
						const cov = matches(clean(block.textContent || ''), { bc: clean(m.content), grams: gramsOf(m) });
						if (cov > 0) best = { msg: m, scores: sc };
					}
				}
			}

			/* 兜底：按正文相似度找 */
			if (!best) {
				const domClean = clean(block.textContent || '');
				let bestScore = 0;
				for (let i = 0; i < cands.length; i++) {
					const cov = matches(domClean, cands[i]);
					if (cov > bestScore) {
						bestScore = cov;
						best = cands[i];
					}
				}
			}

			if (best) {
				/* 分数数组不保证有序，先降序再取真正送进模型的前 N 段 */
				const sorted = best.scores.slice().sort(function (a, b) {
					return b - a;
				});
				const sent = sorted.slice(0, SENT_K);
				attachTo(block, best.msg, sent, sorted.length);
				if (idx === blocks.length - 1) reachedLeaf = true;
			}
		});

		return reachedLeaf;
	}

	function schedule() {
		if (state.timer) return;
		state.ticks = 0;
		const tick = async () => {
			state.ticks += 1;
			if (state.ticks > MAX_TICKS) {
				state.timer = null;
				return;
			}
			const done = await sweep();
			if (done) {
				state.timer = null;
				return;
			}
			state.timer = setTimeout(tick, TICK_MS);
		};
		state.timer = setTimeout(tick, TICK_MS);
	}

	function watch() {
		const container =
			document.getElementById('messages-container') ||
			document.getElementById('chat-pane') ||
			document.body;

		const obs = new MutationObserver(() => {
			if (state.timer) return;
			schedule();
		});
		obs.observe(container, { childList: true, subtree: true, characterData: true });
		window.addEventListener('popstate', schedule);
		schedule();
	}

	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', watch);
	} else {
		watch();
	}

	window.owuiHit = { ready: true, refresh: schedule, sweep };
})();
