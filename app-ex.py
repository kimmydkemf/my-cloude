"""
app.py — YouTube NAS 풀 기능 버전
- 비밀번호 로그인
- 썸네일 미리보기
- 폴더별 분류
- 외장하드 용량 표시
- 다중 URL 입력
- 노션 기록

설치:
    pip install flask requests yt-dlp

실행:
    python app.py

자동 시작 등록 (관리자 CMD):
    nssm install YTServer python C:\\path\\to\\app.py
    nssm start YTServer
"""

import os
import json
import mimetypes
import subprocess
import threading
import shutil
import secrets
from datetime import datetime
from pathlib import Path
from functools import wraps

import requests
from flask import (Flask, request, jsonify, render_template_string,
                   send_file, Response, session, redirect, url_for)

# ─────────────────────────────────────────────────
#  설정 — 여기만 바꾸세요
# ─────────────────────────────────────────────────

SAVE_DIR        = r"D:\Youtube"          # 외장하드 루트 경로
PASSWORD        = "sangho1018!"       # 접속 비밀번호
SECRET_KEY      = secrets.token_hex(24) # 세션 암호화 키 (그대로 두세요)

NOTION_TOKEN    = "ntn_316415009159ouGrF5TRvpw67PYdhmfZcavmupYecJFaqQn"
NOTION_PAGE_ID  = "Youtube-32675dfafcf380aab44fc0e8b676298a?source=copy_link"

# ─────────────────────────────────────────────────

app = Flask(__name__)
app.secret_key = SECRET_KEY
jobs = {}

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm"}
AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".wav"}
ALL_EXTS   = VIDEO_EXTS | AUDIO_EXTS


# ── 인증 데코레이터 ────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api") or request.is_json:
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


# ── HTML 템플릿 ───────────────────────────────────
LOGIN_HTML = """
<!DOCTYPE html><html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>내 영상 저장소</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,sans-serif;background:#f5f5f5;display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{background:#fff;border-radius:20px;padding:40px 36px;width:100%;max-width:380px;box-shadow:0 2px 12px rgba(0,0,0,.08)}
h1{font-size:22px;font-weight:700;margin-bottom:6px}
.sub{font-size:13px;color:#aaa;margin-bottom:28px}
input{width:100%;border:1.5px solid #e0e0e0;border-radius:10px;padding:12px 14px;font-size:15px;outline:none;transition:border .2s;margin-bottom:12px}
input:focus{border-color:#6c63ff}
.err{font-size:12px;color:#e53e3e;margin-bottom:10px}
button{width:100%;padding:13px;background:#6c63ff;color:#fff;border:none;border-radius:10px;font-size:15px;font-weight:600;cursor:pointer}
button:hover{background:#5a52e0}
</style></head><body>
<div class="card">
  <h1>📦 내 영상 저장소</h1>
  <p class="sub">비밀번호를 입력하세요</p>
  {% if error %}<p class="err">{{ error }}</p>{% endif %}
  <form method="post">
    <input type="password" name="password" placeholder="비밀번호" autofocus>
    <button type="submit">입장</button>
  </form>
</div>
</body></html>
"""

MAIN_HTML = r"""
<!DOCTYPE html><html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>내 영상 저장소</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,sans-serif;background:#f5f5f5;color:#1a1a1a}
.header{background:#fff;border-bottom:1px solid #f0f0f0;padding:14px 20px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:10}
.logo{font-size:17px;font-weight:700}
.header-right{display:flex;align-items:center;gap:12px}
.disk{font-size:12px;color:#888;background:#f5f5f5;padding:5px 10px;border-radius:8px}
.disk span{color:#6c63ff;font-weight:600}
.logout{font-size:12px;color:#aaa;cursor:pointer;text-decoration:none}
.logout:hover{color:#e53e3e}
.wrap{max-width:800px;margin:0 auto;padding:20px 14px}

/* 탭 */
.tabs{display:flex;gap:4px;margin-bottom:18px;background:#fff;border-radius:12px;padding:6px;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.tab{flex:1;padding:9px;border-radius:9px;font-size:14px;font-weight:500;cursor:pointer;border:none;background:transparent;color:#888;text-align:center}
.tab.active{background:#6c63ff;color:#fff}

/* 폴더 */
.folder-bar{display:flex;align-items:center;gap:8px;margin-bottom:14px;flex-wrap:wrap}
.folder-pill{padding:6px 14px;border-radius:20px;font-size:12px;font-weight:500;cursor:pointer;border:1.5px solid #e0e0e0;background:#fff;color:#666;transition:all .15s}
.folder-pill.active{background:#6c63ff;border-color:#6c63ff;color:#fff}
.new-folder{padding:6px 12px;border-radius:20px;font-size:12px;border:1.5px dashed #ccc;background:transparent;color:#aaa;cursor:pointer}
.new-folder:hover{border-color:#6c63ff;color:#6c63ff}

/* 검색 + 정렬 */
.toolbar{display:flex;gap:8px;margin-bottom:14px}
.search{flex:1;border:1px solid #e0e0e0;border-radius:9px;padding:9px 12px;font-size:13px;outline:none}
.sort{border:1px solid #e0e0e0;border-radius:9px;padding:9px 10px;font-size:12px;background:#fff;color:#666;cursor:pointer;outline:none}

/* 용량 바 */
.disk-bar{background:#fff;border-radius:12px;padding:14px 16px;margin-bottom:16px;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.disk-label{display:flex;justify-content:space-between;font-size:12px;color:#888;margin-bottom:8px}
.disk-track{background:#f0f0f0;border-radius:6px;height:8px;overflow:hidden}
.disk-fill{height:100%;border-radius:6px;background:#6c63ff;transition:width .6s}
.disk-fill.warn{background:#f59e0b}
.disk-fill.danger{background:#ef4444}

/* 파일 그리드 */
.file-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px}
.file-card{background:#fff;border-radius:12px;overflow:hidden;cursor:pointer;border:1.5px solid transparent;transition:border .15s,transform .1s;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.file-card:hover{border-color:#6c63ff;transform:translateY(-2px)}
.thumb{width:100%;aspect-ratio:16/9;background:#e8e4ff;display:flex;align-items:center;justify-content:center;font-size:32px;position:relative;overflow:hidden}
.thumb img{width:100%;height:100%;object-fit:cover}
.thumb .play-icon{position:absolute;width:36px;height:36px;background:rgba(108,99,255,.85);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;color:#fff;opacity:0;transition:opacity .15s}
.file-card:hover .play-icon{opacity:1}
.file-meta{padding:8px 10px}
.file-name{font-size:12px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:2px}
.file-size{font-size:11px;color:#bbb}
.file-actions-row{display:flex;gap:4px;padding:0 8px 8px}
.btn-xs{flex:1;padding:5px 0;font-size:11px;border-radius:7px;border:none;cursor:pointer;font-weight:500}
.btn-dl{background:#f0f0f0;color:#555}
.btn-dl:hover{background:#e0e0e0}
.btn-del{background:#fff0f0;color:#e53e3e}
.btn-del:hover{background:#ffe0e0}

/* 플레이어 */
.player-wrap{display:none;background:#000;border-radius:14px;overflow:hidden;margin-bottom:16px}
.player-wrap.show{display:block}
video{width:100%;display:block;max-height:400px}
.player-title{font-size:13px;padding:10px 14px;background:#111;color:#ccc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

/* 추가 탭 */
.card{background:#fff;border-radius:14px;padding:20px;box-shadow:0 1px 4px rgba(0,0,0,.06);margin-bottom:14px}
textarea{width:100%;border:1.5px solid #e0e0e0;border-radius:10px;padding:12px 14px;font-size:14px;outline:none;resize:vertical;min-height:100px;transition:border .2s}
textarea:focus{border-color:#6c63ff}
.folder-select{width:100%;border:1.5px solid #e0e0e0;border-radius:10px;padding:11px 14px;font-size:14px;background:#fff;color:#333;margin-top:10px;outline:none}
.btn-main{width:100%;margin-top:10px;padding:12px;background:#6c63ff;color:#fff;border:none;border-radius:10px;font-size:15px;font-weight:600;cursor:pointer}
.btn-main:hover{background:#5a52e0}
.job{border:1px solid #eee;border-radius:12px;padding:14px;margin-top:10px}
.job-title{font-size:13px;font-weight:500;margin-bottom:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar-wrap{background:#f0f0f0;border-radius:6px;height:7px;overflow:hidden}
.bar{height:100%;background:#6c63ff;border-radius:6px;transition:width .4s}
.job-status{font-size:11px;color:#888;margin-top:5px}
.done .bar{background:#22c55e}
.error .bar{background:#ef4444}
.notion-link{font-size:11px;color:#6c63ff;text-decoration:none;display:inline-block;margin-top:4px}
.empty{text-align:center;color:#ccc;font-size:13px;padding:48px 0}
</style>
</head>
<body>
<div class="header">
  <span class="logo">📦 내 영상 저장소</span>
  <div class="header-right">
    <span class="disk" id="disk-badge">불러오는 중...</span>
    <a href="/logout" class="logout">로그아웃</a>
  </div>
</div>

<div class="wrap">
  <div class="tabs">
    <button class="tab active" onclick="showTab('files')">파일 보기</button>
    <button class="tab" onclick="showTab('add')">영상 추가</button>
  </div>

  <!-- 파일 탭 -->
  <div id="tab-files">
    <div class="disk-bar">
      <div class="disk-label">
        <span id="disk-text">용량 계산 중...</span>
        <span id="disk-pct"></span>
      </div>
      <div class="disk-track"><div class="disk-fill" id="disk-fill" style="width:0%"></div></div>
    </div>

    <div class="folder-bar" id="folder-bar"></div>

    <div class="toolbar">
      <input class="search" type="text" placeholder="파일 검색..." oninput="filterFiles(this.value)" id="search-input">
      <select class="sort" onchange="sortFiles(this.value)" id="sort-sel">
        <option value="mtime">최신순</option>
        <option value="name">이름순</option>
        <option value="size">크기순</option>
      </select>
    </div>

    <div class="player-wrap" id="player-wrap">
      <video id="player" controls></video>
      <div class="player-title" id="player-title"></div>
    </div>

    <div class="file-grid" id="file-grid"><div class="empty">불러오는 중...</div></div>
  </div>

  <!-- 추가 탭 -->
  <div id="tab-add" style="display:none">
    <div class="card">
      <h2 style="font-size:15px;font-weight:600;margin-bottom:4px">영상 추가</h2>
      <p style="font-size:12px;color:#aaa;margin-bottom:14px">URL을 한 줄에 하나씩 입력하면 순서대로 처리해요.</p>
      <textarea id="urls" placeholder="https://www.youtube.com/watch?v=...&#10;https://www.youtube.com/watch?v=..."></textarea>
      <select class="folder-select" id="folder-sel">
        <option value="">📁 폴더 선택 (루트에 저장)</option>
      </select>
      <button class="btn-main" onclick="startDownload()">저장하기</button>
    </div>
    <div id="jobs"></div>
  </div>
</div>

<script>
let allFiles = [], allFolders = [], curFolder = '', curSort = 'mtime';
const jobMap = {};

// ── 탭 ──
function showTab(t) {
  document.getElementById('tab-files').style.display = t==='files'?'':'none';
  document.getElementById('tab-add').style.display   = t==='add'?'':'none';
  document.querySelectorAll('.tab').forEach((el,i)=>el.classList.toggle('active',(i===0&&t==='files')||(i===1&&t==='add')));
  if(t==='files') loadFiles();
  if(t==='add') loadFolderSelect();
}

// ── 용량 ──
function loadDisk() {
  fetch('/api/disk').then(r=>r.json()).then(d=>{
    document.getElementById('disk-badge').innerHTML = `<span>${d.free_str}</span> 남음`;
    document.getElementById('disk-text').textContent = `사용 ${d.used_str} / 전체 ${d.total_str}`;
    document.getElementById('disk-pct').textContent = d.pct + '%';
    const fill = document.getElementById('disk-fill');
    fill.style.width = d.pct + '%';
    fill.className = 'disk-fill' + (d.pct>90?' danger':d.pct>70?' warn':'');
  });
}

// ── 파일 목록 ──
function loadFiles() {
  loadDisk();
  fetch('/api/files').then(r=>r.json()).then(data=>{
    allFiles = data.files;
    allFolders = data.folders;
    renderFolderBar();
    renderFiles();
  });
}

function renderFolderBar() {
  const bar = document.getElementById('folder-bar');
  const pills = [['','전체']].concat(allFolders.map(f=>[f,f]));
  bar.innerHTML = pills.map(([val,label])=>
    `<button class="folder-pill${curFolder===val?' active':''}" onclick="setFolder('${val}')">${val?'📁 ':''} ${label}</button>`
  ).join('') + `<button class="new-folder" onclick="createFolder()">+ 폴더 만들기</button>`;
}

function setFolder(f){ curFolder=f; renderFiles(); }

function filterFiles(q){
  const filtered = allFiles.filter(f=>f.name.toLowerCase().includes(q.toLowerCase()) && (!curFolder||f.folder===curFolder));
  renderGrid(filtered);
}

function sortFiles(v){ curSort=v; renderFiles(); }

function renderFiles(){
  document.getElementById('search-input').value='';
  let files = curFolder ? allFiles.filter(f=>f.folder===curFolder) : allFiles;
  renderGrid(files);
}

function renderGrid(files){
  const s = curSort;
  files = [...files].sort((a,b)=>s==='name'?a.name.localeCompare(b.name):s==='size'?b.raw_size-a.raw_size:b.mtime-a.mtime);
  const el = document.getElementById('file-grid');
  if(!files.length){ el.innerHTML='<div class="empty">파일이 없어요</div>'; return; }
  el.innerHTML = files.map(f=>{
    const enc = encodeURIComponent(f.path);
    const thumb = f.thumb
      ? `<img src="/thumb/${enc}" loading="lazy"><div class="play-icon">▶</div>`
      : `<span style="font-size:36px">${f.is_audio?'🎵':'🎬'}</span><div class="play-icon">▶</div>`;
    return `<div class="file-card" onclick="playFile('${enc}','${f.name}')">
      <div class="thumb">${thumb}</div>
      <div class="file-meta">
        <div class="file-name" title="${f.name}">${f.name}</div>
        <div class="file-size">${f.size}</div>
      </div>
      <div class="file-actions-row">
        <a href="/download/${enc}" download onclick="event.stopPropagation()"><button class="btn-xs btn-dl">⬇ 다운</button></a>
        <button class="btn-xs btn-del" onclick="event.stopPropagation();deleteFile('${enc}',this)">삭제</button>
      </div>
    </div>`;
  }).join('');
}

function playFile(enc, name){
  const p = document.getElementById('player');
  const wrap = document.getElementById('player-wrap');
  p.src = '/stream/' + enc;
  document.getElementById('player-title').textContent = name;
  wrap.classList.add('show');
  p.play();
  wrap.scrollIntoView({behavior:'smooth'});
}

function deleteFile(enc, btn){
  if(!confirm('정말 삭제할까요?')) return;
  fetch('/api/delete/'+enc,{method:'DELETE'}).then(r=>r.json()).then(d=>{
    if(d.ok) loadFiles(); else alert('삭제 실패: '+d.error);
  });
}

function createFolder(){
  const name = prompt('폴더 이름을 입력하세요');
  if(!name) return;
  fetch('/api/folder',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})})
    .then(r=>r.json()).then(d=>{ if(d.ok) loadFiles(); });
}

// ── 추가 탭 ──
function loadFolderSelect(){
  fetch('/api/files').then(r=>r.json()).then(data=>{
    const sel = document.getElementById('folder-sel');
    sel.innerHTML = '<option value="">📁 폴더 선택 (루트에 저장)</option>';
    data.folders.forEach(f=>{ const o=document.createElement('option'); o.value=f; o.textContent='📁 '+f; sel.appendChild(o); });
  });
}

function startDownload(){
  const raw = document.getElementById('urls').value.trim();
  const folder = document.getElementById('folder-sel').value;
  if(!raw) return;
  const urls = raw.split('\n').map(u=>u.trim()).filter(Boolean);
  document.getElementById('urls').value='';
  urls.forEach(url=>{
    fetch('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,folder})})
      .then(r=>r.json()).then(d=>{
        jobMap[d.job_id]={interval:setInterval(()=>pollJob(d.job_id),1500)};
        renderJob(d.job_id,{status:'queued',progress:0,title:url});
      });
  });
}

function pollJob(id){
  fetch('/api/status/'+id).then(r=>r.json()).then(d=>{
    renderJob(id,d);
    if(d.status==='done'||d.status==='error') clearInterval(jobMap[id].interval);
  });
}

function renderJob(id,d){
  const pct=d.status==='done'?100:d.status==='error'?100:(d.progress||5);
  const cls=d.status==='done'?'done':d.status==='error'?'error':'';
  const st={queued:'대기 중...',downloading:'다운로드 중...',thumb:'썸네일 생성 중...',notion:'노션 기록 중...',done:'완료!',error:'오류'}[d.status]||d.status;
  const notion=d.notion_url?`<a class="notion-link" href="${d.notion_url}" target="_blank">노션에서 보기 →</a>`:'';
  const html=`<div class="job ${cls}" id="job-${id}">
    <div class="job-title">${d.title||'제목 가져오는 중...'}</div>
    <div class="bar-wrap"><div class="bar" style="width:${pct}%"></div></div>
    <div class="job-status">${st}${d.error?' — '+d.error:''}</div>${notion}</div>`;
  const el=document.getElementById('job-'+id);
  if(el) el.outerHTML=html; else document.getElementById('jobs').insertAdjacentHTML('afterbegin',html);
}

loadFiles();
</script>
</body></html>
"""


# ── 라우트: 로그인 ─────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        if request.form.get("password") == PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("index"))
        return render_template_string(LOGIN_HTML, error="비밀번호가 틀렸어요.")
    return render_template_string(LOGIN_HTML, error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


@app.route("/")
@login_required
def index():
    return render_template_string(MAIN_HTML)


# ── API: 용량 ──────────────────────────────────
@app.route("/api/disk")
@login_required
def disk_info():
    try:
        usage = shutil.disk_usage(SAVE_DIR)
        def fmt(b):
            if b >= 1_099_511_627_776: return f"{b/1_099_511_627_776:.1f} TB"
            if b >= 1_073_741_824:     return f"{b/1_073_741_824:.1f} GB"
            return f"{b/1_048_576:.0f} MB"
        pct = round(usage.used / usage.total * 100)
        return jsonify(total_str=fmt(usage.total), used_str=fmt(usage.used),
                       free_str=fmt(usage.free), pct=pct)
    except Exception as e:
        return jsonify(total_str="알 수 없음", used_str="-", free_str="-", pct=0)


# ── API: 파일 목록 ─────────────────────────────
@app.route("/api/files")
@login_required
def list_files():
    root = Path(SAVE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    files, folders = [], []

    def scan(directory, folder_name=""):
        for f in directory.iterdir():
            if f.is_dir() and not f.name.startswith("."):
                folders.append(f.name)
                scan(f, f.name)
            elif f.suffix.lower() in ALL_EXTS:
                size = f.stat().st_size
                if size >= 1_073_741_824: sz = f"{size/1_073_741_824:.1f} GB"
                elif size >= 1_048_576:   sz = f"{size/1_048_576:.1f} MB"
                else:                      sz = f"{size/1024:.0f} KB"
                thumb_path = f.parent / ".thumbs" / (f.stem + ".jpg")
                files.append({
                    "name": f.name,
                    "path": str(f.relative_to(root)).replace("\\", "/"),
                    "folder": folder_name,
                    "size": sz,
                    "raw_size": size,
                    "mtime": f.stat().st_mtime,
                    "is_audio": f.suffix.lower() in AUDIO_EXTS,
                    "thumb": thumb_path.exists()
                })
    scan(root)
    return jsonify(files=files, folders=sorted(set(folders)))


# ── API: 폴더 생성 ─────────────────────────────
@app.route("/api/folder", methods=["POST"])
@login_required
def create_folder():
    name = request.json.get("name", "").strip()
    if not name: return jsonify(ok=False)
    (Path(SAVE_DIR) / name).mkdir(exist_ok=True)
    return jsonify(ok=True)


# ── 썸네일 서빙 ────────────────────────────────
@app.route("/thumb/<path:rel>")
@login_required
def serve_thumb(rel):
    f = Path(SAVE_DIR) / rel
    thumb = f.parent / ".thumbs" / (f.stem + ".jpg")
    if thumb.exists():
        return send_file(str(thumb), mimetype="image/jpeg")
    return "", 404


# ── 스트리밍 재생 ──────────────────────────────
@app.route("/stream/<path:rel>")
@login_required
def stream(rel):
    path = Path(SAVE_DIR) / rel
    if not path.exists(): return "Not found", 404
    file_size = path.stat().st_size
    rng = request.headers.get("Range")
    mime = mimetypes.guess_type(str(path))[0] or "video/mp4"
    if rng:
        start, end = rng.replace("bytes=", "").split("-")
        start = int(start); end = int(end) if end else file_size - 1
        length = end - start + 1
        def gen():
            with open(path, "rb") as f:
                f.seek(start); remaining = length
                while remaining:
                    chunk = f.read(min(65536, remaining))
                    if not chunk: break
                    remaining -= len(chunk); yield chunk
        return Response(gen(), 206, headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes", "Content-Length": str(length), "Content-Type": mime
        })
    return send_file(str(path), mimetype=mime)


# ── 다운로드 ───────────────────────────────────
@app.route("/download/<path:rel>")
@login_required
def download(rel):
    path = Path(SAVE_DIR) / rel
    if not path.exists(): return "Not found", 404
    return send_file(str(path), as_attachment=True)


# ── 삭제 ──────────────────────────────────────
@app.route("/api/delete/<path:rel>", methods=["DELETE"])
@login_required
def delete_file(rel):
    path = Path(SAVE_DIR) / rel
    try:
        path.unlink()
        # 썸네일도 같이 삭제
        thumb = path.parent / ".thumbs" / (path.stem + ".jpg")
        if thumb.exists(): thumb.unlink()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False, error=str(e))


# ── 다운로드 작업 ──────────────────────────────
def update_job(job_id, **kw): jobs[job_id].update(kw)

def generate_thumbnail(video_path: Path):
    """ffmpeg로 썸네일 생성 (ffmpeg 설치 필요)"""
    thumb_dir = video_path.parent / ".thumbs"
    thumb_dir.mkdir(exist_ok=True)
    thumb_path = thumb_dir / (video_path.stem + ".jpg")
    if thumb_path.exists(): return
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", str(video_path),
            "-ss", "00:00:05", "-vframes", "1",
            "-vf", "scale=320:-1", str(thumb_path)
        ], capture_output=True, timeout=30)
    except Exception:
        pass

def run_download(job_id, url, folder):
    update_job(job_id, status="downloading", progress=10)
    save_path = Path(SAVE_DIR) / folder if folder else Path(SAVE_DIR)
    save_path.mkdir(parents=True, exist_ok=True)
    try:
        title = subprocess.run(
            ["yt-dlp", "--print", "title", url],
            capture_output=True, text=True, check=True
        ).stdout.strip()
        update_job(job_id, title=title, progress=20)
    except Exception as e:
        update_job(job_id, status="error", error=str(e)); return
    try:
        subprocess.run([
            "yt-dlp",
            "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "-o", str(save_path / "%(title)s.%(ext)s"), url
        ], check=True, capture_output=True)
        update_job(job_id, progress=70)
    except Exception:
        update_job(job_id, status="error", error="다운로드 실패"); return

    # 최근 저장된 mp4 찾기
    files = sorted(save_path.glob("*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True)
    file_path = files[0] if files else save_path / f"{title}.mp4"

    # 썸네일 생성
    update_job(job_id, status="thumb", progress=80)
    generate_thumbnail(file_path)

    # 노션 기록
    update_job(job_id, status="notion", progress=90)
    notion_url = add_to_notion(title, url, str(file_path))
    update_job(job_id, status="done", progress=100, notion_url=notion_url)

def add_to_notion(title, youtube_url, file_path):
    headers = {"Authorization": f"Bearer {NOTION_TOKEN}",
               "Content-Type": "application/json", "Notion-Version": "2022-06-28"}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    blocks = [
        {"object":"block","type":"divider","divider":{}},
        {"object":"block","type":"heading_3","heading_3":{"rich_text":[{"type":"text","text":{"content":title}}]}},
        {"object":"block","type":"bookmark","bookmark":{"url":youtube_url}},
        {"object":"block","type":"paragraph","paragraph":{"rich_text":[{"type":"text","text":{"content":f"📁 {file_path}"}}]}},
        {"object":"block","type":"paragraph","paragraph":{"rich_text":[{"type":"text","text":{"content":f"📅 {now}"}}]}}
    ]
    res = requests.patch(f"https://api.notion.com/v1/blocks/{NOTION_PAGE_ID}/children",
                         headers=headers, json={"children": blocks})
    return f"https://notion.so/{NOTION_PAGE_ID.replace('-','')}" if res.ok else None


@app.route("/api/start", methods=["POST"])
@login_required
def start():
    url = request.json.get("url",""); folder = request.json.get("folder","")
    job_id = str(len(jobs)+1)
    jobs[job_id] = {"status":"queued","progress":0,"title":"","error":None}
    threading.Thread(target=run_download, args=(job_id,url,folder), daemon=True).start()
    return jsonify(job_id=job_id)

@app.route("/api/status/<job_id>")
@login_required
def status(job_id):
    return jsonify(jobs.get(job_id,{"status":"unknown"}))


if __name__ == "__main__":
    print("서버 시작! http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)

