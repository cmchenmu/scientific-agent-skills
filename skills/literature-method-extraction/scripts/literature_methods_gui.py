#!/usr/bin/env python3
"""Serve a loopback-only GUI for the literature-methods CLI and review queue."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).with_name("literature_methods.py")

PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Literature Methods</title>
<style>body{margin:0;font:14px Arial,sans-serif;color:#17202a;background:#f5f7f8}header{background:#fff;border-bottom:1px solid #dbe1e5;padding:16px 24px;display:flex;justify-content:space-between;align-items:center}h1{font-size:19px;margin:0}main{max-width:1280px;margin:24px auto;padding:0 20px}.bar{display:flex;gap:10px;align-items:center;margin-bottom:16px}.stats{display:flex;gap:12px;flex-wrap:wrap}.stat{background:#fff;border:1px solid #dbe1e5;padding:12px;min-width:125px}.stat b{font-size:22px;display:block}button{border:1px solid #176b5b;background:#176b5b;color:#fff;padding:8px 12px;cursor:pointer}button.secondary{background:#fff;color:#176b5b}button:disabled{opacity:.55}#log{background:#17202a;color:#e9f0f2;min-height:48px;padding:12px;white-space:pre-wrap;font-family:monospace}table{width:100%;border-collapse:collapse;background:#fff;margin-top:16px}th,td{text-align:left;vertical-align:top;padding:10px;border-bottom:1px solid #e6eaed}th{font-size:12px;color:#56636d;background:#f9fafb}.title{font-weight:bold;max-width:360px}.snippet{max-width:440px;color:#46545e}.tag{display:inline-block;padding:3px 6px;background:#e8f4ef;color:#176b5b;font-size:12px}.unavailable{background:#fbeceb;color:#9e332b}@media(max-width:760px){main{padding:0 12px}header{padding:14px 12px}.bar{align-items:stretch;flex-direction:column}table{font-size:12px}.snippet{max-width:180px}}</style>
<header><h1>Literature Methods</h1><span id="db"></span></header><main><div class="bar"><button id="run">运行归档与提取</button><button id="extract" class="secondary">仅提取本地归档</button><button id="reload" class="secondary">刷新结果</button></div><div class="stats" id="stats"></div><div id="log">准备就绪</div><table><thead><tr><th>论文</th><th>提取状态</th><th>数据分析方法证据</th><th>人工复核</th></tr></thead><tbody id="rows"></tbody></table></main>
<script>const $=s=>document.querySelector(s);async function api(p,o){let r=await fetch(p,o);let x=await r.json();if(!r.ok)throw Error(x.error||'请求失败');return x}function esc(s){return String(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}async function load(){let d=await api('/api/results');$('#db').textContent=d.database;$('#stats').innerHTML=Object.entries(d.stats).map(([k,v])=>`<div class="stat"><b>${v}</b>${k}</div>`).join('');$('#rows').innerHTML=d.rows.map(r=>{let summary=Object.values(r.summary||{}).flat().slice(0,3).join(' ');return `<tr><td class="title">${esc(r.title)}<br><small>${esc(r.year)} ${esc(r.doi||r.pmcid)}</small></td><td><span class="tag ${r.status==='unavailable'?'unavailable':''}">${esc(r.status||'未提取')}</span></td><td class="snippet">${esc(summary||r.error||'')}</td><td><select data-id="${r.id}"><option ${r.reviewer_status==='pending'?'selected':''}>pending</option><option ${r.reviewer_status==='accepted'?'selected':''}>accepted</option><option ${r.reviewer_status==='revised'?'selected':''}>revised</option><option ${r.reviewer_status==='rejected'?'selected':''}>rejected</option></select> <button class="secondary" data-save="${r.id}">保存</button></td></tr>`}).join('')||'<tr><td colspan="4">暂无记录。运行归档与提取后刷新。</td></tr>'}async function start(skip){$('#run').disabled=$('#extract').disabled=true;$('#log').textContent='任务运行中...';try{let r=await api('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({skip_archive:skip})});$('#log').textContent=r.message;await load()}catch(e){$('#log').textContent=e.message}finally{$('#run').disabled=$('#extract').disabled=false}}$('#run').onclick=()=>start(false);$('#extract').onclick=()=>start(true);$('#reload').onclick=load;document.addEventListener('click',async e=>{let id=e.target.dataset.save;if(!id)return;let s=document.querySelector(`select[data-id="${id}"]`).value;await api('/api/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:+id,status:s})});$('#log').textContent='复核状态已保存';await load()});load().catch(e=>$('#log').textContent=e.message)</script></html>"""


class App:
    def __init__(self, config: Path, output: Path):
        self.config, self.output, self.message, self.running = config, output, "准备就绪", False

    def database(self) -> Path:
        data = json.loads(self.config.read_text(encoding="utf-8"))
        return Path(data["database"]).expanduser()

    def results(self) -> dict:
        database = self.database()
        if not database.exists():
            return {"database": str(database), "stats": {"论文": 0, "待复核": 0, "已接受": 0}, "rows": []}
        db = sqlite3.connect(database)
        db.row_factory = sqlite3.Row
        rows = db.execute("""SELECT p.id,p.title,p.year,p.doi,p.pmcid,m.status,m.error,m.summary_json,m.reviewer_status FROM papers p LEFT JOIN method_extractions m ON p.id=m.paper_id ORDER BY p.updated_at DESC LIMIT 200""").fetchall()
        stats = {"论文": len(rows), "待复核": sum(r["reviewer_status"] == "pending" for r in rows), "已接受": sum(r["reviewer_status"] in {"accepted", "revised"} for r in rows)}
        result = []
        for row in rows:
            item = dict(row)
            item["summary"] = json.loads(item.pop("summary_json") or "{}")
            result.append(item)
        db.close()
        return {"database": str(database), "stats": stats, "rows": result}

    def run(self, skip_archive: bool) -> None:
        if self.running:
            raise RuntimeError("已有任务正在运行")
        self.running, self.message = True, "任务运行中..."
        command = [sys.executable, str(SCRIPT), "run", "--archive-config", str(self.config), "--output", str(self.output)]
        if skip_archive:
            command.append("--skip-archive")
        def worker():
            completed = subprocess.run(command, capture_output=True, text=True)
            self.message = completed.stdout.strip() if completed.returncode == 0 else completed.stderr.strip()
            self.running = False
        threading.Thread(target=worker, daemon=True).start()

    def review(self, paper_id: int, status: str) -> None:
        if status not in {"pending", "accepted", "revised", "rejected"}:
            raise ValueError("无效复核状态")
        db = sqlite3.connect(self.database())
        db.execute("UPDATE method_extractions SET reviewer_status=?, updated_at=datetime('now') WHERE paper_id=?", (status, paper_id))
        db.commit(); db.close()


def handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, status: int, data, content_type="application/json; charset=utf-8"):
            body = data.encode("utf-8") if isinstance(data, str) else json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def body(self):
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length))
        def do_GET(self):
            if self.path == "/": return self.respond(200, PAGE, "text/html; charset=utf-8")
            if self.path == "/api/results": return self.respond(200, app.results())
            self.respond(404, {"error": "not found"})
        def do_POST(self):
            try:
                payload = self.body()
                if self.path == "/api/run":
                    app.run(bool(payload.get("skip_archive"))); return self.respond(202, {"message": "任务已启动；稍后刷新结果。"})
                if self.path == "/api/review":
                    app.review(int(payload["id"]), str(payload["status"])); return self.respond(200, {"message": "saved"})
                self.respond(404, {"error": "not found"})
            except (ValueError, KeyError, RuntimeError, sqlite3.Error) as exc:
                self.respond(400, {"error": str(exc)})
        def log_message(self, *_): pass
    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("methods.csv"))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if not args.archive_config.is_file(): parser.error("--archive-config does not exist")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(App(args.archive_config, args.output)))
    print(f"Literature Methods GUI: http://127.0.0.1:{args.port}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0

if __name__ == "__main__": raise SystemExit(main())
