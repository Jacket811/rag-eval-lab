#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
麒麟知识库 · 检索层评估脚本

用法：
  python run_eval.py --validate           # 校验题库里的要点是否真的在库里存在
  python run_eval.py --run                # 跑一遍评估，输出 CSV 与分类汇总
  python run_eval.py --run --tag kr4      # 给这次结果打标签（用于前后对比）
  python run_eval.py --diff a.csv b.csv   # 对比两次结果

指标：
  top1     第一名重排分（0~1）
  w3       前三加权 = 0.5*第1 + 0.3*第2 + 0.2*第3
  cover    标准答案要点在"送入模型的 4 段"里的覆盖率
  hit      cover >= 0.5（无答案类则看 top1 < 0.5）

只调用 /api/v1/retrieval/query/collection，不触发模型生成，所以一次 50 题约 2-4 分钟。
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')

HERE = os.path.dirname(os.path.abspath(__file__))
QFILE = os.path.join(HERE, 'questions.json')
OUTDIR = os.path.join(HERE, 'results')
COVER_TOPN = 2   # 要点只在靠前 N 段里找（收紧假阳性）

# ===== 环境配置 =====
# 默认值适用于 docker compose 默认服务名；换环境时用环境变量覆盖，不必改代码
BASE = os.environ.get('OWUI_BASE', 'http://127.0.0.1:3000')
OWUI_CONTAINER = os.environ.get('OWUI_CONTAINER', 'open-webui')
PG_CONTAINER = os.environ.get('PG_CONTAINER', 'pgvector')
PG_USER = os.environ.get('PG_USER', 'owui')
PG_DB = os.environ.get('PG_DB', 'openwebui')


def load_questions():
    with open(QFILE, encoding='utf-8') as f:
        return json.load(f)


def get_token():
    cache = os.path.join(HERE, '.token')
    if os.path.exists(cache) and time.time() - os.path.getmtime(cache) < 3600:
        return open(cache, encoding='utf-8').read().strip()
    code = (
        "import sqlite3\n"
        "from open_webui.utils.auth import create_token\n"
        "c=sqlite3.connect('/app/backend/data/webui.db')\n"
        "uid=c.execute('select id from user limit 1').fetchone()[0]\n"
        "st=c.execute('select session_stamp from auth where id=?',(uid,)).fetchone()[0]\n"
        "print(create_token({'id':uid,'session_stamp':st}))\n"
    )
    out = subprocess.run(['docker', 'exec', OWUI_CONTAINER, 'python', '-c', code],
                         capture_output=True, text=True, encoding='utf-8')
    tok = (out.stdout or '').strip().splitlines()[-1] if out.stdout else ''
    if not tok:
        sys.exit('拿不到 token：' + (out.stderr or '')[-300:])
    open(cache, 'w', encoding='utf-8').write(tok)
    return tok


def retrieve(token, collection, query, k=12, kr=4):
    body = json.dumps({'collection_names': [collection], 'query': query,
                       'k': k, 'k_reranker': kr}).encode()
    req = urllib.request.Request(BASE + '/api/v1/retrieval/query/collection', data=body,
                                 headers={'Content-Type': 'application/json',
                                          'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read().decode())
    return d['documents'][0], d['distances'][0]


def norm(s):
    return ' '.join((s or '').lower().split())


def coverage(docs, points):
    """要点覆盖率：只看靠前的 N 段（默认 2）。
    收紧到前 2 段是因为模型真正依赖的就是 top1/top2，
    放宽到 4 段会让"关键词碰巧出现在陪跑段"的错答案蒙混通过。"""
    if not points:
        return None
    blob = norm('\n'.join(docs[:COVER_TOPN]))
    hit = sum(1 for p in points if norm(p) in blob)
    return hit / len(points)


def w3(ds):
    s = sorted(ds, reverse=True)
    return 0.5 * s[0] + 0.3 * (s[1] if len(s) > 1 else 0) + 0.2 * (s[2] if len(s) > 2 else 0)


def dump_corpus():
    out = subprocess.run(['docker', 'exec', PG_CONTAINER, 'psql', '-U', PG_USER, '-d', PG_DB,
                          '-t', '-A', '-c', 'select text from document_chunk'],
                         capture_output=True, encoding='utf-8')
    return norm(out.stdout or '')


def do_validate(data):
    print('正在导出知识库全文用于校验…')
    corpus = dump_corpus()
    print('语料长度 %d 字符\n' % len(corpus))
    bad = 0
    for it in data['items']:
        if it['cat'] == 'noans':
            continue
        missing = [p for p in it['points'] if norm(p) not in corpus]
        flag = 'OK  ' if not missing else 'WARN'
        if missing:
            bad += 1
        print('%s #%-2d %-28s 缺失要点: %s' % (flag, it['id'], it['q'][:26], missing or '-'))
    print('\n要点全在库里的题：%d/%d' % (sum(1 for i in data['items'] if i['cat'] != 'noans') - bad,
                                    sum(1 for i in data['items'] if i['cat'] != 'noans')))
    print('（WARN 的要点可能是写法差异，若整题都缺说明题库描述与文档不一致，建议换词）')


def do_run(data, token, tag):
    os.makedirs(OUTDIR, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    path = os.path.join(OUTDIR, '%s-%s.csv' % (stamp, tag or 'run'))
    rows = []
    cat_stat = defaultdict(list)
    t0 = time.time()

    for it in data['items']:
        try:
            docs, ds = retrieve(token, data['meta']['collection'], it['q'])
        except Exception as e:
            print('  ! #%d 失败: %s' % (it['id'], str(e)[:60]))
            continue
        cov = coverage(docs, it['points'])
        top1 = ds[0] if ds else 0
        w = w3(ds) if ds else 0
        if it['cat'] == 'noans':
            ok = top1 < 0.5
        else:
            ok = (cov or 0) >= 0.5
        row = {'id': it['id'], 'cat': it['cat'], 'q': it['q'], 'top1': round(top1, 3),
               'w3': round(w, 3), 'cover': '' if cov is None else round(cov, 2),
               'hit': 1 if ok else 0, 'segments': len(ds),
               'difficulty': it.get('difficulty', 'mid'), 'flaky': 1 if it.get('flaky') else 0}
        rows.append(row)
        cat_stat[it['cat']].append(row)
        print('  %s #%-2d top1=%.2f w3=%.2f cover=%s | %s'
              % ('✅' if ok else '❌', it['id'], top1, w, row['cover'], it['q'][:24]))

    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        wr = csv.DictWriter(f, fieldnames=['id', 'cat', 'q', 'top1', 'w3', 'cover', 'hit',
                                           'segments', 'difficulty', 'flaky'])
        wr.writeheader()
        wr.writerows(rows)

    print('\n=== 汇总（用时 %.0fs）===' % (time.time() - t0))
    names = {'oral': '口语类', 'term': '术语类', 'multi': '多跳类', 'noans': '无答案类',
             'exact': '精确型', 'syn': '同义变体', 'cond': '带条件'}
    order = ('oral', 'term', 'multi', 'exact', 'syn', 'cond', 'noans')
    total = []
    for cat in order:
        rs = cat_stat.get(cat, [])
        if not rs:
            continue
        total += rs
        print('%-8s %2d 题  命中率 %5.1f%%  top1 %.3f  w3 %.3f'
              % (names.get(cat, cat), len(rs), 100 * sum(r['hit'] for r in rs) / len(rs),
                 sum(r['top1'] for r in rs) / len(rs), sum(r['w3'] for r in rs) / len(rs)))

    def line(label, rs):
        if not rs:
            return
        print('%-8s %2d 题  命中率 %5.1f%%  top1 %.3f  w3 %.3f'
              % (label, len(rs), 100 * sum(r['hit'] for r in rs) / len(rs),
                 sum(r['top1'] for r in rs) / len(rs), sum(r['w3'] for r in rs) / len(rs)))

    line('合计', total)
    # 剔除边界题后的口径：flaky 题的分数在阈值附近抖动，会污染对比
    stable = [r for r in total if not r['flaky']]
    if len(stable) != len(total):
        line('合计(仅稳定)', stable)
        print('        ↑ 剔除 %d 道 flaky 题后的口径，做 A/B 对比时以此为准' % (len(total) - len(stable)))

    failed = [r for r in total if not r['hit']]
    if failed:
        print('\n未通过（%d 题）：' % len(failed))
        for r in failed:
            print('  #%-2d [%s] %s' % (r['id'], r['cat'], r['q']))

    print('\n结果已写入 %s' % path)
    return path


def do_diff(a, b):
    def load(p):
        with open(p, encoding='utf-8-sig') as f:
            return {int(r['id']): r for r in csv.DictReader(f)}
    A, B = load(a), load(b)
    better = worse = 0
    print('%-3s %-26s %6s %6s %7s' % ('#', '问题', '改动前', '改动后', '变化'))
    for i in sorted(set(A) & set(B)):
        x, y = A[i], B[i]
        d = float(y['w3']) - float(x['w3'])
        if d > 0.02:
            better += 1
        elif d < -0.02:
            worse += 1
        print('%-3d %-26s %6.2f %6.2f %+7.2f' % (i, (x['q'] or '')[:24], float(x['w3']), float(y['w3']), d))
    print('\n变好 %d 题，变差 %d 题' % (better, worse))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--validate', action='store_true')
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--tag', default='')
    ap.add_argument('--diff', nargs=2)
    ap.add_argument('--topn', type=int, default=2, help='cover 只看前 N 段，默认 2')
    a = ap.parse_args()
    global COVER_TOPN
    COVER_TOPN = a.topn
    data = load_questions()
    if a.validate:
        do_validate(data)
    elif a.run:
        do_run(data, get_token(), a.tag)
    elif a.diff:
        do_diff(*a.diff)
    else:
        ap.print_help()


if __name__ == '__main__':
    main()
