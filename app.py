"""
app.py — 외장하드 전체 파일 NAS
- 영상/사진/문서/기타 모든 파일 브라우징
- 영상 스트리밍, 사진 미리보기, 파일 다운로드, 파일 업로드
- 유튜브 다운로드 + 노션 기록
- 비밀번호 로그인
- 폴더 탐색 (실제 폴더 구조 그대로)
- 외장하드 용량 표시

설치:
    pip install flask requests yt-dlp

실행:
    python app.py
"""

import os
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
#  설정
# ─────────────────────────────────────────────────

ROOT_DIR        = r"D:\\"               # 외장하드 루트 (전체 공유)
PASSWORD        = "os.getenv("PASSWORD")
SECRET_KEY      = secrets.token_hex(24)
NOTION_TOKEN    = os.getenv("NOTION_API_TOKEN")
NOTION_PAGE_ID  = "32675dfafcf380aab44fc0e8b676298a"
YT_DLP             = r"C:\Users\kimmy\AppData\Local\Programs\Python\Python313\Scripts\yt-dlp.exe"
FFMPEG_DIR =r"C:\ffmpeg\bin"
SERVER_URL="http://100.111.192.102:5000"
STREAM_TOKEN= "qwer1234"

# ─────────────────────────────────────────────────

import time

# 파일 인덱스 캐시
file_index = {}  # { 파일명: 전체경로 }
index_last_updated = 0

def build_index():
   global file_index, index_last_updated
   print("[INDEX] 파일 인덱스 빌드 중...")
   idx = {}
   for f in Path(ROOT_DIR).rglob("*"):
       if f.is_file() and not f.name.startswith("."):
           idx[f.name] = str(f)
   file_index = idx
   index_last_updated = time.time()
   print(f"[INDEX] 완료 — {len(idx)}개 파일")

def get_index():
   # 10분마다 자동 갱신
   if time.time() - index_last_updated > 600:
       threading.Thread(target=build_index, daemon=True).start()
   return file_index

# 서버 시작 시 최초 1회 빌드
threading.Thread(target=build_index, daemon=True).start()


app = Flask(__name__)
app.secret_key = SECRET_KEY
jobs = {}

# 파일 종류 분류
VIDEO_EXTS  = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v"}
AUDIO_EXTS  = {".mp3", ".m4a", ".flac", ".wav", ".aac", ".ogg"}
IMAGE_EXTS  = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".svg"}
DOC_EXTS    = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md"}
ZIP_EXTS    = {".zip", ".rar", ".7z", ".tar", ".gz"}

def file_type(ext):
    ext = ext.lower()
    if ext in VIDEO_EXTS:  return "video"
    if ext in AUDIO_EXTS:  return "audio"
    if ext in IMAGE_EXTS:  return "image"
    if ext in DOC_EXTS:    return "doc"
    if ext in ZIP_EXTS:    return "zip"
    return "file"

def file_icon(ftype):
    return {"video":"🎬","audio":"🎵","image":"🖼","doc":"📄","zip":"🗜","file":"📦"}.get(ftype,"📦")

def fmt_size(b):
    if b >= 1_099_511_627_776: return f"{b/1_099_511_627_776:.1f} TB"
    if b >= 1_073_741_824:     return f"{b/1_073_741_824:.1f} GB"
    if b >= 1_048_576:         return f"{b/1_048_576:.1f} MB"
    return f"{b/1024:.0f} KB"

def safe_path(rel):
    """경로 탈출 방지 — ROOT_DIR 밖으로 나가지 못하게"""
    root = Path(ROOT_DIR).resolve()
    target = (root / rel).resolve()
    if not str(target).startswith(str(root)):
        return None
    return target


# ── 인증 ──────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            if request.path.startswith("/api") or request.is_json:
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


LOGIN_HTML = """
<!DOCTYPE html><html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>📦 1Byte=8bit 저장소</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,sans-serif;background:#f5f5f5;display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{background:#fff;border-radius:20px;padding:40px 36px;width:100%;max-width:380px;box-shadow:0 2px 12px rgba(0,0,0,.08)}
h1{font-size:22px;font-weight:700;margin-bottom:6px}
.sub{font-size:13px;color:#aaa;margin-bottom:28px}
input{width:100%;border:1.5px solid #e0e0e0;border-radius:10px;padding:12px 14px;font-size:15px;outline:none;margin-bottom:12px}
input:focus{border-color:#6c63ff}
.err{font-size:12px;color:#e53e3e;margin-bottom:10px}
button{width:100%;padding:13px;background:#6c63ff;color:#fff;border:none;border-radius:10px;font-size:15px;font-weight:600;cursor:pointer}
</style></head><body>
<div class="card">
  <h1>📦 1Byte=8bit 저장소</h1>
  <p class="sub">비밀번호를 입력하세요</p>
  {% if error %}<p class="err">{{ error }}</p>{% endif %}
  <form method="post">
    <input type="password" name="password" placeholder="비밀번호" autofocus>
    <button type="submit">입장</button>
  </form>
</div></body></html>
"""

MAIN_HTML = r"""
<!DOCTYPE html><html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>📦1byte=8bit 저장소</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,sans-serif;background:#f5f5f5;color:#1a1a1a}
.header{background:#fff;border-bottom:1px solid #f0f0f0;padding:12px 20px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:20}
.logo{font-size:16px;font-weight:700}
.header-r{display:flex;align-items:center;gap:10px}
.disk-badge{font-size:12px;color:#888;background:#f5f5f5;padding:4px 10px;border-radius:8px}
.disk-badge b{color:#6c63ff}
a.logout{font-size:12px;color:#ccc;text-decoration:none}
a.logout:hover{color:#e53e3e}

.wrap{max-width:900px;margin:0 auto;padding:16px 14px}

/* 탭 */
.tabs{display:flex;gap:4px;background:#fff;border-radius:12px;padding:5px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.tab{flex:1;padding:8px;border-radius:9px;font-size:13px;font-weight:500;cursor:pointer;border:none;background:transparent;color:#888}
.tab.on{background:#6c63ff;color:#fff}

/* 용량 바 */
.dbar{background:#fff;border-radius:12px;padding:12px 16px;margin-bottom:14px;box-shadow:0 1px 3px rgba(0,0,0,.05)}
.dbar-labels{display:flex;justify-content:space-between;font-size:12px;color:#888;margin-bottom:7px}
.dbar-track{background:#f0f0f0;border-radius:6px;height:7px;overflow:hidden}
.dbar-fill{height:100%;border-radius:6px;background:#6c63ff;transition:width .5s}
.dbar-fill.w{background:#f59e0b}.dbar-fill.d{background:#ef4444}

/* 경로 빵부스러기 */
.breadcrumb{display:flex;align-items:center;gap:4px;flex-wrap:wrap;margin-bottom:12px;font-size:13px}
.bc-item{color:#6c63ff;cursor:pointer;padding:3px 6px;border-radius:6px}
.bc-item:hover{background:#f0effe}
.bc-sep{color:#ccc}
.bc-cur{color:#888;padding:3px 6px}

/* 툴바 */
.toolbar{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap}
.search{flex:1;min-width:160px;border:1px solid #e0e0e0;border-radius:9px;padding:8px 12px;font-size:13px;outline:none}
.sort{border:1px solid #e0e0e0;border-radius:9px;padding:8px 10px;font-size:12px;background:#fff;color:#555;cursor:pointer;outline:none}
.filter{border:1px solid #e0e0e0;border-radius:9px;padding:8px 10px;font-size:12px;background:#fff;color:#555;cursor:pointer;outline:none}
.view-btn{border:1px solid #e0e0e0;border-radius:9px;padding:8px 12px;font-size:12px;background:#fff;color:#555;cursor:pointer}
.view-btn.on{background:#6c63ff;color:#fff;border-color:#6c63ff}

/* 그리드 뷰 */
.file-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px}
.fcard{background:#fff;border-radius:12px;overflow:hidden;cursor:pointer;border:1.5px solid transparent;transition:all .15s;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.fcard:hover{border-color:#6c63ff;transform:translateY(-2px)}
.fcard.folder-card{border-style:dashed;border-color:#e0e0e0}
.fcard.folder-card:hover{border-color:#6c63ff;border-style:solid}
.thumb{width:100%;aspect-ratio:16/9;background:#f5f5f5;display:flex;align-items:center;justify-content:center;font-size:28px;overflow:hidden;position:relative}
.thumb img{width:100%;height:100%;object-fit:cover}
.play-ov{position:absolute;width:32px;height:32px;background:rgba(108,99,255,.85);border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;color:#fff;opacity:0;transition:opacity .15s}
.fcard:hover .play-ov{opacity:1}
.fmeta{padding:7px 9px 4px}
.fname{font-size:11px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-bottom:2px}
.fsize{font-size:10px;color:#bbb}
.fbtns{display:flex;gap:4px;padding:0 7px 7px}
.fbtn{flex:1;padding:4px 0;font-size:10px;border-radius:6px;border:none;cursor:pointer;font-weight:500}
.fbtn-dl{background:#f0f0f0;color:#555}.fbtn-dl:hover{background:#e0e0e0}
.fbtn-del{background:#fff0f0;color:#e53e3e}.fbtn-del:hover{background:#ffe0e0}

/* 리스트 뷰 */
.file-list{display:flex;flex-direction:column;gap:4px}
.frow{background:#fff;border-radius:10px;padding:10px 14px;display:flex;align-items:center;gap:10px;cursor:pointer;border:1px solid transparent;transition:border .15s}
.frow:hover{border-color:#e0e0e0}
.frow-icon{font-size:20px;flex-shrink:0;width:28px;text-align:center}
.frow-info{flex:1;min-width:0}
.frow-name{font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.frow-sub{font-size:11px;color:#bbb;margin-top:1px}
.frow-size{font-size:11px;color:#bbb;flex-shrink:0;min-width:60px;text-align:right}
.frow-btns{display:flex;gap:5px;flex-shrink:0}

/* 미디어 플레이어 */
.player-wrap{display:none;background:#000;border-radius:14px;overflow:hidden;margin-bottom:14px}
.player-wrap.show{display:block}
video,audio{width:100%;display:block}
video{max-height:400px}
.player-info{font-size:12px;padding:8px 14px;background:#111;color:#aaa}

/* 사진 라이트박스 */
.lb{display:none;position:fixed;inset:0;background:rgba(0,0,0,.92);z-index:100;align-items:center;justify-content:center;flex-direction:column}
.lb.show{display:flex}
.lb img{max-width:90vw;max-height:85vh;border-radius:8px;object-fit:contain}
.lb-title{color:#aaa;font-size:12px;margin-top:10px}
.lb-close{position:absolute;top:16px;right:20px;color:#fff;font-size:24px;cursor:pointer;opacity:.7}
.lb-close:hover{opacity:1}

/* 업로드 존 */
.upload-zone{border:2px dashed #d0d0d0;border-radius:14px;padding:32px;text-align:center;color:#aaa;font-size:14px;cursor:pointer;transition:all .2s;background:#fff;margin-bottom:14px}
.upload-zone:hover,.upload-zone.drag{border-color:#6c63ff;color:#6c63ff;background:#f8f7ff}
.upload-zone input{display:none}
.upload-progress{margin-top:10px;font-size:12px;color:#6c63ff}

/* 유튜브 추가 */
.card{background:#fff;border-radius:14px;padding:18px;box-shadow:0 1px 3px rgba(0,0,0,.05);margin-bottom:12px}
textarea{width:100%;border:1.5px solid #e0e0e0;border-radius:10px;padding:11px 13px;font-size:13px;outline:none;resize:vertical;min-height:90px}
textarea:focus{border-color:#6c63ff}
.sel{width:100%;border:1.5px solid #e0e0e0;border-radius:10px;padding:10px 13px;font-size:13px;background:#fff;margin-top:8px;outline:none}
.btn-main{width:100%;margin-top:10px;padding:12px;background:#6c63ff;color:#fff;border:none;border-radius:10px;font-size:14px;font-weight:600;cursor:pointer}
.btn-main:hover{background:#5a52e0}
.job{border:1px solid #eee;border-radius:10px;padding:12px;margin-top:8px}
.job-title{font-size:12px;font-weight:500;margin-bottom:7px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar-wrap{background:#f0f0f0;border-radius:6px;height:6px;overflow:hidden}
.bar{height:100%;background:#6c63ff;border-radius:6px;transition:width .4s}
.job-st{font-size:11px;color:#888;margin-top:4px}
.done .bar{background:#22c55e}.error .bar{background:#ef4444}
.notion-lnk{font-size:11px;color:#6c63ff;text-decoration:none;display:inline-block;margin-top:3px}
.empty{text-align:center;color:#ccc;padding:48px 0;font-size:13px}
</style>
</head>
<body>
<div class="header">
  <span class="logo">📦 1Byte=8bit 저장소 </span>
  <div class="header-r">
    <span class="disk-badge" id="disk-badge">계산 중...</span>
    <a href="/logout" class="logout">로그아웃</a>
  </div>
</div>

<div class="wrap">
  <div class="tabs">
    <button class="tab on" onclick="showTab('files')">파일 탐색기</button>
    <button class="tab"    onclick="showTab('add')">유튜브 추가</button>
  </div>

  <!-- 파일 탭 -->
  <div id="tab-files">
    <div class="dbar">
      <div class="dbar-labels"><span id="disk-text">-</span><span id="disk-pct">-</span></div>
      <div class="dbar-track"><div class="dbar-fill" id="dbar-fill" style="width:0%"></div></div>
    </div>

    <div class="breadcrumb" id="breadcrumb"></div>

    <div class="toolbar">
      <input class="search" type="text" placeholder="이름으로 검색..." oninput="onSearch(this.value)" id="search-box">
      <select class="sort" onchange="onSort(this.value)">
        <option value="name">이름순</option>
        <option value="mtime">최신순</option>
        <option value="size">크기순</option>
      </select>
      <select class="filter" onchange="onFilter(this.value)" id="type-filter">
        <option value="">모든 파일</option>
        <option value="folder">폴더</option>
        <option value="video">영상</option>
        <option value="image">사진</option>
        <option value="audio">오디오</option>
        <option value="doc">문서</option>
        <option value="zip">압축</option>
      </select>
      <button class="view-btn on" id="btn-grid" onclick="setView('grid')">⊞ 그리드</button>
      <button class="view-btn"    id="btn-list" onclick="setView('list')">☰ 리스트</button>
    </div>

    <!-- 업로드 존 -->
    <div class="upload-zone" id="drop-zone" onclick="document.getElementById('file-input').click()"
         ondragover="event.preventDefault();this.classList.add('drag')"
         ondragleave="this.classList.remove('drag')"
         ondrop="onDrop(event)">
      <input type="file" id="file-input" multiple onchange="uploadFiles(this.files)">
      클릭하거나 파일을 여기에 드래그해서 업로드
      <div class="upload-progress" id="upload-progress"></div>
    </div>

    <div class="player-wrap" id="player-wrap">
      <video id="vid-player" controls style="display:none"></video>
      <audio id="aud-player" controls style="display:none"></audio>
      <div class="player-info" id="player-info"></div>
    </div>

    <div id="file-container"><div class="empty">불러오는 중...</div></div>
  </div>

  <!-- 유튜브 추가 탭 -->
  <div id="tab-add" style="display:none">
    <div class="card">
      <h2 style="font-size:14px;font-weight:600;margin-bottom:4px">유튜브 영상 추가</h2>
      <p style="font-size:12px;color:#aaa;margin-bottom:12px">URL을 한 줄에 하나씩 입력하세요.</p>
      <textarea id="yt-urls" placeholder="https://www.youtube.com/watch?v=..."></textarea>
      <select class="sel" id="yt-folder-sel"><option value="">📁 Youtube (기본)</option></select>
      <button class="btn-main" onclick="startYT()">저장하기</button>
    </div>
    <div id="jobs"></div>
  </div>
</div>

<!-- 라이트박스 -->
<div class="lb" id="lightbox" onclick="closeLB()">
  <span class="lb-close">✕</span>
  <img id="lb-img" src="">
  <div class="lb-title" id="lb-title"></div>
</div>

<script>
let curPath = '', curView = 'grid', curSort = 'name', curFilter = '', curSearch = '';
let allItems = [];
const jobMap = {};

// ── 탭 ──
function showTab(t) {
  document.getElementById('tab-files').style.display = t==='files'?'':'none';
  document.getElementById('tab-add').style.display   = t==='add'?'':'none';
  document.querySelectorAll('.tab').forEach((el,i)=>el.classList.toggle('on',(i===0&&t==='files')||(i===1&&t==='add')));
  if(t==='files') loadDir(curPath);
  if(t==='add')   loadFolderSel();
}

// ── 용량 ──
function loadDisk() {
  fetch('/api/disk').then(r=>r.json()).then(d=>{
    document.getElementById('disk-badge').innerHTML = '<b>'+d.free_str+'</b> 남음';
    document.getElementById('disk-text').textContent = '사용 '+d.used_str+' / 전체 '+d.total_str;
    document.getElementById('disk-pct').textContent = d.pct+'%';
    const f = document.getElementById('dbar-fill');
    f.style.width = d.pct+'%';
    f.className = 'dbar-fill'+(d.pct>90?' d':d.pct>70?' w':'');
  });
}

// ── 폴더 탐색 ──
function loadDir(path) {
  curPath = path;
  document.getElementById('search-box').value = '';
  curSearch = '';
  fetch('/api/ls?path='+encodeURIComponent(path)).then(r=>r.json()).then(data=>{
    allItems = data.items;
    renderBreadcrumb(data.path_parts);
    renderItems();
  });
}

function renderBreadcrumb(parts) {
  const bc = document.getElementById('breadcrumb');
  let html = '<span class="bc-item" onclick="loadDir(\'\')">🏠 루트</span>';
  let acc = '';
  parts.forEach((p,i)=>{
    acc += (acc?'/':'')+p;
    const isLast = i===parts.length-1;
    html += '<span class="bc-sep">/</span>';
    if(isLast) html += `<span class="bc-cur">${p}</span>`;
    else { const a=acc; html += `<span class="bc-item" onclick="loadDir('${a}')">${p}</span>`; }
  });
  bc.innerHTML = html;
}

// ── 필터/정렬/검색 ──
function onSearch(v){ curSearch=v.toLowerCase(); renderItems(); }
function onSort(v){ curSort=v; renderItems(); }
function onFilter(v){ curFilter=v; renderItems(); }
function setView(v){
  curView=v;
  document.getElementById('btn-grid').classList.toggle('on',v==='grid');
  document.getElementById('btn-list').classList.toggle('on',v==='list');
  renderItems();
}

function renderItems() {
  let items = allItems.filter(it=>{
    if(curSearch && !it.name.toLowerCase().includes(curSearch)) return false;
    if(curFilter==='folder' && !it.is_dir) return false;
    if(curFilter && curFilter!=='folder' && (it.is_dir || it.ftype!==curFilter)) return false;
    return true;
  });
  items.sort((a,b)=>{
    if(a.is_dir!==b.is_dir) return a.is_dir?-1:1;
    if(curSort==='name')  return a.name.localeCompare(b.name);
    if(curSort==='mtime') return b.mtime-a.mtime;
    if(curSort==='size')  return b.raw_size-a.raw_size;
    return 0;
  });
  const el = document.getElementById('file-container');
  if(!items.length){ el.innerHTML='<div class="empty">파일이 없어요</div>'; return; }
  if(curView==='grid') renderGrid(items, el);
  else renderList(items, el);
}

function renderGrid(items, el) {
  el.className='file-grid';
  el.innerHTML = items.map(it=>{
    const enc = encodeURIComponent(it.rel_path);
    if(it.is_dir) return `<div class="fcard folder-card" onclick="loadDir('${it.rel_path}')">
      <div class="thumb">📁</div>
      <div class="fmeta"><div class="fname">${it.name}</div><div class="fsize">폴더</div></div>
    </div>`;
    const thumb = it.ftype==='image'
      ? `<img src="/api/thumb/${enc}" loading="lazy"><div class="play-ov">🔍</div>`
      : it.ftype==='video'
      ? (it.thumb?`<img src="/api/thumb/${enc}" loading="lazy">`:'')+`<div class="play-ov">▶</div>`
      : `<span>${it.icon}</span>`;
    return `<div class="fcard" onclick="openFile('${enc}','${it.name}','${it.ftype}')">
      <div class="thumb">${thumb}</div>
      <div class="fmeta"><div class="fname" title="${it.name}">${it.name}</div><div class="fsize">${it.size}</div></div>
      <div class="fbtns">
        <a href="/api/dl/${enc}" download onclick="event.stopPropagation()"><button class="fbtn fbtn-dl">⬇ 다운</button></a>
        <button class="fbtn fbtn-del" onclick="event.stopPropagation();delFile('${enc}')">삭제</button>
      </div>
    </div>`;
  }).join('');
}

function renderList(items, el) {
  el.className='file-list';
  el.innerHTML = items.map(it=>{
    const enc = encodeURIComponent(it.rel_path);
    const click = it.is_dir ? `loadDir('${it.rel_path}')` : `openFile('${enc}','${it.name}','${it.ftype}')`;
    return `<div class="frow" onclick="${click}">
      <div class="frow-icon">${it.is_dir?'📁':it.icon}</div>
      <div class="frow-info">
        <div class="frow-name">${it.name}</div>
        <div class="frow-sub">${it.is_dir?'폴더':it.ftype} ${it.date?'· '+it.date:''}</div>
      </div>
      <div class="frow-size">${it.is_dir?'':it.size}</div>
      ${it.is_dir?'`<div class="frow-btns"></div>`':`<div class="frow-btns">
        <a href="/api/dl/${enc}" download onclick="event.stopPropagation()"><button class="fbtn fbtn-dl" style="padding:5px 8px">⬇</button></a>
        <button class="fbtn fbtn-del" style="padding:5px 8px" onclick="event.stopPropagation();delFile('${enc}')">🗑</button>
      </div>`}
    </div>`;
  }).join('');
}

// ── 파일 열기 ──
function openFile(enc, name, ftype) {
  if(ftype==='video') {
    const v=document.getElementById('vid-player'), a=document.getElementById('aud-player');
    v.style.display='block'; a.style.display='none';
    v.src='/api/stream/'+enc;
    document.getElementById('player-info').textContent=name;
    const w=document.getElementById('player-wrap'); w.classList.add('show');
    v.play(); w.scrollIntoView({behavior:'smooth'});
  } else if(ftype==='audio') {
    const v=document.getElementById('vid-player'), a=document.getElementById('aud-player');
    v.style.display='none'; a.style.display='block';
    a.src='/api/stream/'+enc;
    document.getElementById('player-info').textContent=name;
    const w=document.getElementById('player-wrap'); w.classList.add('show');
    a.play();
  } else if(ftype==='image') {
    document.getElementById('lb-img').src='/api/stream/'+enc;
    document.getElementById('lb-title').textContent=name;
    document.getElementById('lightbox').classList.add('show');
  } else if(ftype=='doc' && enc.toLowerCase().endsWith('.pdf')){
    window.open('/api/stream/'+enc);
  } else {
    window.open('/api/dl/'+enc);
  }
}

function closeLB(){ document.getElementById('lightbox').classList.remove('show'); }
document.getElementById('lightbox').addEventListener('click',function(e){if(e.target===this)closeLB();});

// ── 삭제 ──
function delFile(enc){
  if(!confirm('정말 삭제할까요?')) return;
  fetch('/api/del/'+enc,{method:'DELETE'}).then(r=>r.json()).then(d=>{
    if(d.ok) loadDir(curPath); else alert('삭제 실패: '+d.error);
  });
}

// ── 업로드 ──
function onDrop(e){
  e.preventDefault();
  document.getElementById('drop-zone').classList.remove('drag');
  uploadFiles(e.dataTransfer.files);
}

function uploadFiles(files) {
  if(!files.length) return;
  const prog = document.getElementById('upload-progress');
  const total = files.length;
  let done = 0;
  Array.from(files).forEach(file=>{
    const fd = new FormData();
    fd.append('file', file);
    fd.append('path', curPath);
    prog.textContent = `업로드 중... (${done}/${total})`;
    fetch('/api/upload', {method:'POST', body:fd})
      .then(r=>r.json()).then(d=>{
        done++;
        prog.textContent = done===total ? '업로드 완료!' : `업로드 중... (${done}/${total})`;
        if(done===total){ setTimeout(()=>{ prog.textContent=''; loadDir(curPath); loadDisk(); },1500); }
      });
  });
}

// ── 유튜브 ──
function loadFolderSel(){
  fetch('/api/ls?path=').then(r=>r.json()).then(data=>{
    const sel=document.getElementById('yt-folder-sel');
    sel.innerHTML='<option value="">📁 Youtube</option>';
    data.items.filter(it=>it.is_dir).forEach(f=>{
      const o=document.createElement('option'); o.value=f.rel_path; o.textContent='📁 '+f.name; sel.appendChild(o);
    });
  });
}

function startYT(){
  const raw=document.getElementById('yt-urls').value.trim();
  const folder=document.getElementById('yt-folder-sel').value;
  if(!raw) return;
  document.getElementById('yt-urls').value='';
  raw.split('\n').map(u=>u.trim()).filter(Boolean).forEach(url=>{
    fetch('/api/yt/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,folder})})
      .then(r=>r.json()).then(d=>{
        jobMap[d.job_id]={interval:setInterval(()=>pollJob(d.job_id),1500)};
        renderJob(d.job_id,{status:'queued',progress:0,title:url});
      });
  });
}

function pollJob(id){
  fetch('/api/yt/status/'+id).then(r=>r.json()).then(d=>{
    renderJob(id,d);
    if(d.status==='done'||d.status==='error') clearInterval(jobMap[id].interval);
  });
}

function renderJob(id,d){
  const pct=d.status==='done'?100:d.status==='error'?100:(d.progress||5);
  const cls=d.status==='done'?'done':d.status==='error'?'error':'';
  const st={queued:'대기 중...',downloading:'다운로드 중...',thumb:'썸네일 생성 중...',notion:'노션 기록 중...',done:'완료!',error:'오류'}[d.status]||d.status;
  const nl=d.notion_url?`<a class="notion-lnk" href="${d.notion_url}" target="_blank">노션에서 보기 →</a>`:'';
  const html=`<div class="job ${cls}" id="job-${id}">
    <div class="job-title">${d.title||'처리 중...'}</div>
    <div class="bar-wrap"><div class="bar" style="width:${pct}%"></div></div>
    <div class="job-st">${st}${d.error?' — '+d.error:''}</div>${nl}</div>`;
  const el=document.getElementById('job-'+id);
  if(el) el.outerHTML=html; else document.getElementById('jobs').insertAdjacentHTML('afterbegin',html);
}

loadDisk();
loadDir('');
</script>
</body></html>
"""


# ── 라우트 ────────────────────────────────────
@app.route("/login", methods=["GET","POST"])
def login_page():
    if request.method=="POST":
        if request.form.get("password")==PASSWORD:
            session["logged_in"]=True; return redirect(url_for("index"))
        return render_template_string(LOGIN_HTML, error="비밀번호가 틀렸어요.")
    return render_template_string(LOGIN_HTML, error=None)

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login_page"))

@app.route("/")
@login_required
def index():
    return render_template_string(MAIN_HTML)


# ── API: 용량 ──────────────────────────────────
@app.route("/api/disk")
@login_required
def api_disk():
    try:
        u = shutil.disk_usage(ROOT_DIR)
        pct = round(u.used/u.total*100)
        return jsonify(total_str=fmt_size(u.total), used_str=fmt_size(u.used),
                       free_str=fmt_size(u.free), pct=pct)
    except:
        return jsonify(total_str="-", used_str="-", free_str="-", pct=0)


# ── API: 폴더 목록 ─────────────────────────────
@app.route("/api/ls")
@login_required
def api_ls():
    rel = request.args.get("path","").strip("/\\")
    base = safe_path(rel)
    if not base or not base.is_dir():
        return jsonify(items=[], path_parts=[])
    items = []
    try:
        for f in sorted(base.iterdir(), key=lambda x:(not x.is_dir(), x.name.lower())):
            if f.name.startswith("."): continue
            try:
                is_dir = f.is_dir()
                rel_path = str(f.relative_to(Path(ROOT_DIR))).replace("\\","/")
                if is_dir:
                    items.append({"name":f.name,"rel_path":rel_path,"is_dir":True,
                                  "ftype":"folder","icon":"📁","size":"","raw_size":0,"mtime":0,"date":"","thumb":False})
                else:
                    ext = f.suffix.lower()
                    ftype = file_type(ext)
                    stat = f.stat()
                    size = stat.st_size
                    thumb_p = f.parent/".thumbs"/(f.stem+".jpg")
                    items.append({
                        "name":f.name,"rel_path":rel_path,"is_dir":False,
                        "ftype":ftype,"icon":file_icon(ftype),
                        "size":fmt_size(size),"raw_size":size,
                        "mtime":stat.st_mtime,
                        "date":datetime.fromtimestamp(stat.st_mtime).strftime("%Y.%m.%d"),
                        "thumb":thumb_p.exists()
                    })
            except PermissionError:
                continue
    except PermissionError:
        pass
    parts = [p for p in rel.replace("\\","/").split("/") if p]
    return jsonify(items=items, path_parts=parts)


# ── API: 썸네일 ────────────────────────────────
@app.route("/api/thumb/<path:rel>")
@login_required
def api_thumb(rel):
    f = safe_path(rel)
    if not f: return "",404
    ext = f.suffix.lower()
    if ext in IMAGE_EXTS:
        return send_file(str(f), mimetype=mimetypes.guess_type(str(f))[0] or "image/jpeg")
    thumb = f.parent/".thumbs"/(f.stem+".jpg")
    if not thumb.exists():
        try:
            thumb.parent.mkdir(exist_ok=True)
            subprocess.run(["ffmpeg","-y","-i",str(f),"-ss","00:00:05",
                            "-vframes","1","-vf","scale=320:-1",str(thumb)],
                           capture_output=True, timeout=30)
        except: pass
    if thumb.exists():
        return send_file(str(thumb), mimetype="image/jpeg")
    return "",404

@app.route("/api/find/<path:filename>")
def api_find(filename):
   token = request.args.get("token")
   if not session.get("logged_in") and token != STREAM_TOKEN:
       return "Unauthorized", 401
   
   idx = get_index()
   fpath_str = idx.get(filename)
   
   # 인덱스에 없으면 직접 검색 후 인덱스 갱신
   if not fpath_str:
       matches = list(Path(ROOT_DIR).rglob(filename))
       if not matches:
           return "Not found", 404
       fpath_str = str(matches[0])
       file_index[filename] = fpath_str

   path = Path(fpath_str)
   if not path.exists():
       # 파일이 옮겨진 경우 인덱스 갱신
       threading.Thread(target=build_index, daemon=True).start()
       return "Not found — 인덱스 갱신 중, 잠시 후 다시 시도해주세요", 404

   file_size = path.stat().st_size
   mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
   rng = request.headers.get("Range")
   if rng:
       start, end = rng.replace("bytes=", "").split("-")
       start = int(start)
       end = int(end) if end else file_size - 1
       length = end - start + 1
       def gen():
           with open(path, "rb") as f:
               f.seek(start); rem = length
               while rem:
                   chunk = f.read(min(65536, rem))
                   if not chunk: break
                   rem -= len(chunk); yield chunk
       return Response(gen(), 206, headers={
           "Content-Range": f"bytes {start}-{end}/{file_size}",
           "Accept-Ranges": "bytes",
           "Content-Length": str(length),
           "Content-Type": mime
       })
   return send_file(str(path), mimetype=mime)# ── API: 스트리밍 ──────────────────────────────
@app.route("/api/stream/<path:rel>")
def api_stream(rel):
   token = request.args.get("token")
   if not session.get("logged_in") and token != STREAM_TOKEN:
       return "Unauthorized", 401
   path = safe_path(rel)
   if not path or not path.exists():
       return "Not found", 404
   file_size = path.stat().st_size
   mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
   rng = request.headers.get("Range")
   if rng:
       start, end = rng.replace("bytes=", "").split("-")
       start = int(start)
       end = int(end) if end else file_size - 1
       length = end - start + 1
       def gen():
           with open(path, "rb") as f:
               f.seek(start)
               rem = length
               while rem:
                   chunk = f.read(min(65536, rem))
                   if not chunk:
                       break
                   rem -= len(chunk)
                   yield chunk
       return Response(gen(), 206, headers={
           "Content-Range": f"bytes {start}-{end}/{file_size}",
           "Accept-Ranges": "bytes",
           "Content-Length": str(length),
           "Content-Type": mime
       })
   return send_file(str(path), mimetype=mime)

# ── API: 다운로드 ──────────────────────────────
@app.route("/api/dl/<path:rel>")
@login_required
def api_dl(rel):
    path = safe_path(rel)
    if not path or not path.exists(): return "Not found",404
    return send_file(str(path), as_attachment=True)


# ── API: 삭제 ──────────────────────────────────
@app.route("/api/del/<path:rel>", methods=["DELETE"])
@login_required
def api_del(rel):
    path = safe_path(rel)
    if not path: return jsonify(ok=False,error="잘못된 경로")
    try:
        if path.is_dir(): shutil.rmtree(path)
        else:
            path.unlink()
            t=path.parent/".thumbs"/(path.stem+".jpg")
            if t.exists(): t.unlink()
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(ok=False,error=str(e))


# ── API: 업로드 ────────────────────────────────
@app.route("/api/upload", methods=["POST"])
@login_required
def api_upload():
    f = request.files.get("file")
    rel = request.form.get("path","").strip("/\\")
    if not f: return jsonify(ok=False)
    dest_dir = safe_path(rel) if rel else Path(ROOT_DIR)
    if not dest_dir: return jsonify(ok=False,error="잘못된 경로")
    dest_dir.mkdir(parents=True,exist_ok=True)
    save_path = dest_dir / f.filename
    f.save(str(save_path))
    return jsonify(ok=True)


# ── 유튜브 다운로드 ────────────────────────────
def update_job(jid,**kw): jobs[jid].update(kw)

def gen_thumb(vpath):
    td=vpath.parent/".thumbs"; td.mkdir(exist_ok=True)
    tp=td/(vpath.stem+".jpg")
    if tp.exists(): return
    try:
        subprocess.run(["ffmpeg","-y","-i",str(vpath),"-ss","00:00:05",
                        "-vframes","1","-vf","scale=320:-1",str(tp)],
                       capture_output=True,timeout=30)
    except: pass

def run_yt(jid, url, folder):
   update_job(jid, status="downloading", progress=10)
   dest = Path(ROOT_DIR) / (folder if folder else "youtube")
   dest.mkdir(parents=True, exist_ok=True)

   # 제목 가져오기
   try:
       title = subprocess.run(
           [YT_DLP, "--print", "title", url],
           capture_output=True, text=True, check=True
       ).stdout.strip()
       update_job(jid, title=title, progress=20)
   except Exception as e:
       update_job(jid, status="error", error=str(e)); return

   # 다운로드 (ffmpeg로 자동 병합)
   out_tpl = str(dest / "%(title)s.%(ext)s")
   try:
       subprocess.run([
           YT_DLP,
           "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
           "--merge-output-format", "mp4",
           "--ffmpeg-location", FFMPEG_DIR,   # ffmpeg 경로 명시
           "-o", out_tpl,
           url
       ], check=True, capture_output=True)
       update_job(jid, progress=70)
   except Exception as e:
       update_job(jid, status="error", error="다운로드 실패: "+str(e)); return

   # 저장된 파일 찾기
   files = sorted(dest.glob("*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True)
   fpath = files[0] if files else dest / f"{title}.mp4"

   # 썸네일 생성
   update_job(jid, status="thumb", progress=80)
   gen_thumb(fpath)

   # 노션 기록
   update_job(jid, status="notion", progress=90)
   notion_result = add_notion(title, url, str(fpath))
   if notion_result:
       update_job(jid, status="done", progress=100, notion_url=notion_result)
   else:
       # 노션 실패해도 다운로드는 완료로 표시, 오류 메시지 추가
       update_job(jid, status="done", progress=100, notion_url=None,
                  error="다운로드 완료. 노션 기록 실패 (토큰/페이지ID 확인 필요)")

def add_notion(title, yt_url, fpath):
   h = {"Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"}

   # 파일 상대경로로 스트리밍 URL 생성
   try:
       rel = Path(fpath).relative_to(Path(ROOT_DIR))
       filename=Path(fpath).name
       stream_url = SERVER_URL + "/api/find/" + filename +"?token="+STREAM_TOKEN
   except:
       stream_url = yt_url

   blocks = [
       {"object": "block", "type": "divider", "divider": {}},
       {
           "object": "block", "type": "heading_3",
           "heading_3": {"rich_text": [{"type": "text", "text": {"content": title}}]}
       },
       {
           "object": "block", "type": "paragraph",
           "paragraph": {"rich_text": [
               {"type": "text", "text": {"content": "▶ 내 드라이브에서 재생", "link": {"url": stream_url}},
                "annotations": {"bold": True, "color": "purple"}}
           ]}
       },
       {
           "object": "block", "type": "paragraph",
           "paragraph": {"rich_text": [
               {"type": "text", "text": {"content": "유튜브 원본", "link": {"url": yt_url}},
                "annotations": {"color": "gray"}}
           ]}
       },
   ]
   res = requests.patch(
       f"https://api.notion.com/v1/blocks/{NOTION_PAGE_ID}/children",
       headers=h, json={"children": blocks}
   )
   return f"https://notion.so/{NOTION_PAGE_ID.replace('-', '')}" if res.ok else None
@app.route("/api/yt/start",methods=["POST"])
@login_required
def yt_start():
    url=request.json.get("url",""); folder=request.json.get("folder","")
    jid=str(len(jobs)+1)
    jobs[jid]={"status":"queued","progress":0,"title":"","error":None}
    threading.Thread(target=run_yt,args=(jid,url,folder),daemon=True).start()
    return jsonify(job_id=jid)

@app.route("/api/yt/status/<jid>")
@login_required
def yt_status(jid):
    return jsonify(jobs.get(jid,{"status":"unknown"}))


if __name__=="__main__":
    print("서버 시작! http://localhost:5000")
    app.run(host="0.0.0.0",port=5000,debug=False)

