/*!
 * ttft.js —— 首字延迟计时 + 等待期状态提示
 *
 * 为什么这么写：
 *   /api/chat/completions 只返回任务确认（task_ids），真正的流不走这个响应，
 *   所以不能靠拦截 fetch 计时。改为观察 DOM，但用"空闲快照 + 发送时比对"
 *   绕开两个坑：
 *     ① 新气泡可能在计时开始前就被创建（数量判断失效）
 *     ② 气泡节点可能被复用（引用判断失效）
 *
 * 界面只加两类元素，不改任何模型输出：
 *   等待期：状态条（已发送 → 检索 → 组织回答，带秒数）
 *   完成后：回答下方徽标（首字 X · 全文 Y）
 */
(function () {
	'use strict';

	const STAGE_CLASS = 'ow-stage';
	const BADGE_CLASS = 'ow-ttft';
	const STORE_KEY = 'owui_ttft_samples';
	const MAX_SAMPLES = 30;
	const POLL_MS = 200;
	const IDLE_MS = 500;

	let pending = null;
	/* 空闲快照：最后一个回答气泡及其正文长度（发送前的基线） */
	let idle = { node: null, len: 0, at: 0 };

	function now() { return performance.now(); }
	function fmt(ms) { return (ms / 1000).toFixed(1) + 's'; }

	function bubbles() {
		return Array.prototype.slice.call(document.querySelectorAll('.chat-assistant'));
	}

	function lastBubble() {
		const b = bubbles();
		return b.length ? b[b.length - 1] : null;
	}

	/* 只认正文块与思考块，避免把模型名、时间戳算成"已有内容" */
	function bodyLen(node) {
		if (!node) return 0;
		let total = 0;
		node.querySelectorAll('.markdown-prose, [class*="thinking"], [class*="reasoning"]').forEach(function (el) {
			total += (el.textContent || '').replace(/\s+/g, '').length;
		});
		return total;
	}

	function saveSample(o) {
		try {
			const list = JSON.parse(localStorage.getItem(STORE_KEY) || '[]');
			list.push(o);
			while (list.length > MAX_SAMPLES) list.shift();
			localStorage.setItem(STORE_KEY, JSON.stringify(list));
		} catch (e) { /* 忽略 */ }
	}

	/* ---------- 状态条 ---------- */

	function stageText(ms) {
		if (ms < 1200) return '已发送，正在准备…';
		if (ms < 3500) return '检索知识库…';
		if (ms < 10000) return '组织回答…';
		return '仍在处理，首次提问需要加载模型，可能较慢…';
	}

	function showStage(host) {
		if (pending && pending.stage) return;
		const el = document.createElement('div');
		el.className = STAGE_CLASS + ' ow-stage-float';
		el.innerHTML =
			'<span class="ow-stage-dots"><i></i><i></i><i></i></span>' +
			'<span class="ow-stage-text">已发送，正在准备…</span>' +
			'<span class="ow-stage-time">0.0s</span>';
		const box = host || document.getElementById('messages-container') || document.body;
		box.appendChild(el);
		if (pending) {
			pending.stage = el;
			pending.stageTimer = setInterval(function () {
				const t = el.querySelector('.ow-stage-text');
				const m = el.querySelector('.ow-stage-time');
				const ms = now() - pending.t0;
				if (t) t.textContent = stageText(ms);
				if (m) m.textContent = fmt(ms);
			}, 200);
		}
	}

	function hideStage() {
		if (!pending) return;
		if (pending.stageTimer) clearInterval(pending.stageTimer);
		const el = pending.stage;
		pending.stage = null;
		pending.stageTimer = null;
		if (!el) return;
		el.classList.add('ow-stage-out');
		setTimeout(function () {
			if (el.parentNode) el.parentNode.removeChild(el);
		}, 220);
	}

	/* ---------- 徽标 ---------- */

	function attachBadge(ttft, total, node) {
		const host = node || lastBubble();
		if (!host) return;
		const old = host.querySelector('.' + BADGE_CLASS);
		if (old) old.remove();
		const el = document.createElement('div');
		el.className = BADGE_CLASS + ' ' + (ttft < 2500 ? 'ok' : ttft < 8000 ? 'warn' : 'bad');
		el.textContent = '首字 ' + fmt(ttft) + ' · 全文 ' + fmt(total);
		el.title = '首字 = 从按下发送到正文出现第一个字；全文 = 到输出结束';
		host.appendChild(el);
	}

	/* ---------- 空闲快照 ---------- */

	function snapshotIdle() {
		if (pending) return;
		const node = lastBubble();
		idle = { node: node, len: bodyLen(node), at: now() };
	}

	/* ---------- 主流程 ---------- */

	function start() {
		if (pending) return;
		snapshotIdle(); /* 确保基线是发送前的状态 */

		pending = { t0: now(), node: null, stage: null, stageTimer: null,
			baseNode: idle.node, baseLen: idle.len, started: false, timer: null };

		showStage(null);

		if (pending.baseNode) {
			/* 若新气泡已在本轮开始前创建，状态条挂到它上面 */
			pending.node = pending.baseNode;
		}

		pending.timer = setInterval(tick, POLL_MS);
		window.__owTtftStarts = (window.__owTtftStarts || 0) + 1;
	}

	function tick() {
		if (!pending) return;
		const last = lastBubble();
		if (!last) return;

		const len = bodyLen(last);
		window.__owTtftDebug = { len: len, started: pending.started, base: pending.baseLen };

		if (!pending.started) {
			/* 判定"这是本轮的新回答"的三种情形 */
			const isNew =
				last !== pending.baseNode ||           /* 换了节点 */
				len < pending.baseLen ||                /* 同节点被清空 */
				(pending.baseLen === 0 && len > 0);     /* 基线本来就是空的 */

			if (isNew) {
				pending.started = true;
				pending.node = last;
				pending.baseLen = 0;
			}
		}

		if (pending.started && len > 0) {
			const ttft = now() - pending.t0;
			hideStage();
			if (pending.timer) clearInterval(pending.timer);
			pending.timer = null;
			watchDone(pending.node || last, ttft);
			return;
		}
	}

	function watchDone(node, ttft) {
		let lastLen = bodyLen(node);
		let stable = 0;
		const t = setInterval(function () {
			const len = bodyLen(node);
			if (len === lastLen) stable += 1;
			else { stable = 0; lastLen = len; }
			if (stable >= 3) {
				clearInterval(t);
				const total = now() - pending.t0;
				attachBadge(ttft, total, node);
				saveSample({ ts: Date.now(), ttft: Math.round(ttft), total: Math.round(total), chars: lastLen });
				pending = null;
			}
		}, 400);
		/* 兜底：3 分钟后强制收尾 */
		setTimeout(function () { clearInterval(t); if (pending && pending.timer === null) pending = null; }, 180000);
	}

	/* ---------- 触发 ---------- */

	function bind() {
		document.addEventListener('keydown', function (e) {
			if (e.key !== 'Enter' || e.shiftKey) return;
			const t = e.target;
			const ok = t && (t.id === 'chat-input' || (t.closest && t.closest('#chat-input')));
			if (!ok) return;
			setTimeout(start, 0);
		}, true);

		document.addEventListener('submit', function (e) {
			const f = e.target;
			if (!f || !f.querySelector || !f.querySelector('#chat-input')) return;
			setTimeout(start, 0);
		}, true);

		document.addEventListener('click', function (e) {
			const btn = e.target && e.target.closest ? e.target.closest('button') : null;
			if (!btn) return;
			const label = (btn.getAttribute('aria-label') || btn.id || '') + '';
			if (label.indexOf('发送') !== -1 || label === 'send-message-button') {
				setTimeout(start, 0);
			}
		}, true);
	}

	/* ---------- 对外接口 ---------- */

	window.owuiTTFT = {
		samples: function () {
			try { return JSON.parse(localStorage.getItem(STORE_KEY) || '[]'); } catch (e) { return []; }
		},
		clear: function () { localStorage.removeItem(STORE_KEY); },
		stats: function () {
			const s = this.samples().map(function (x) { return x.ttft; });
			if (!s.length) return null;
			s.sort(function (a, b) { return a - b; });
			return { n: s.length, p50: s[Math.floor(s.length / 2)], p90: s[Math.floor(s.length * 0.9)] || s[s.length - 1], max: s[s.length - 1] };
		}
	};

	if (document.readyState === 'loading') {
		document.addEventListener('DOMContentLoaded', function () { bind(); setInterval(snapshotIdle, IDLE_MS); });
	} else {
		bind();
		setInterval(snapshotIdle, IDLE_MS);
	}
})();
