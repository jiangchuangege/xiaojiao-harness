# podcast_service/podcast_api.py —— Web 扩展 Blueprint：播客大脑接口
# 页面 /podcast + 接口 /api/podcast(generate) /api/podcast/status/<jid>
import os
from flask import Blueprint, request, jsonify, Response
import podcast_gen as pg

bp = Blueprint("podcast_service", __name__)

_PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>🎙️ 播客大脑 · 小焦</title>
<style>
 body{background:#0a0e1a;color:#dfe7f5;font-family:'Segoe UI',system-ui,sans-serif;display:flex;justify-content:center;min-height:100vh;margin:0}
 .wrap{max-width:640px;width:92%;padding:28px 0}
 h1{font-size:24px;margin:0 0 6px;background:linear-gradient(90deg,#5ad1ff,#8a7bff);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
 .sub{color:#7d8bb0;font-size:13px;margin-bottom:22px}
 .card{background:#111a2e;border:1px solid #2a3a5c;border-radius:14px;padding:20px}
 label{font-size:13px;color:#9fb3d8;display:block;margin:14px 0 6px}
 input,textarea,select{width:100%;box-sizing:border-box;background:#0d1524;border:1px solid #2c3f66;border-radius:8px;color:#dfe7f5;padding:10px;font-size:14px}
 input:focus,textarea:focus{border-color:#5ad1ff;outline:none}
 .row{display:flex;gap:12px}.row>div{flex:1}
 button{width:100%;margin-top:20px;padding:13px;background:linear-gradient(90deg,#1f9be0,#7a5dff);border:none;border-radius:10px;color:#fff;font-size:16px;font-weight:600;cursor:pointer}
 button:disabled{opacity:.5;cursor:not-allowed}
 #bar{height:8px;background:#1a2540;border-radius:6px;margin-top:20px;overflow:hidden}
 #bar>i{display:block;height:100%;width:0;background:linear-gradient(90deg,#5ad1ff,#8a7bff);transition:width .4s}
 #st{margin-top:10px;font-size:14px;color:#9fb3d8}
 #out{margin-top:22px;display:none}
 audio{width:100%;margin-top:10px}
 img{width:160px;border-radius:12px;margin-top:12px;display:block}
 .lbl{font-size:12px;color:#7d8bb0;margin-top:16px}
</style>
</head>
<body>
<div class="wrap">
 <h1>🎙️ 播客大脑</h1>
 <div class="sub">给小焦一个主题，它来写稿、配音、出封面，生成一段真·中文播客。</div>
 <div class="card">
  <label>播客主题</label>
  <input id="topic" placeholder="例如：AI 会取代人类工作吗？">
  <div class="row">
   <div><label>主持人 A</label><input id="ha" value="小李"></div>
   <div><label>主持人 B</label><input id="hb" value="小焦"></div>
  </div>
  <div class="row">
   <div><label>对话轮数</label><select id="rounds"><option>2</option><option selected>4</option><option>6</option><option>8</option></select></div>
   <div><label>风格</label><select id="style"><option>轻松有趣</option><option>专业严谨</option><option>幽默吐槽</option><option>深度访谈</option></select></div>
  </div>
  <label>目标时长</label>
  <select id="minutes">
   <option value="">短播客(约1-2分钟)</option>
   <option value="5">约5分钟</option>
   <option value="10">约10分钟</option>
   <option value="15" selected>约15分钟</option>
   <option value="20">约20分钟</option>
   <option value="30">约30分钟</option>
  </select>
  <div style="color:#7d8bb0;font-size:12px;margin-top:6px">时长越长 = 内容越充实、生成越久（约15分钟需排队生成几分钟）</div>
  <div class="row">
   <div style="display:flex;align-items:center;gap:8px;margin-top:18px">
     <input type="checkbox" id="cover" checked style="width:auto"><span class="lbl">用 SD1.5 生成封面图</span>
   </div>
  </div>
  <button id="go" onclick="go()">🎬 生成播客</button>
  <div id="bar"><i></i></div>
  <div id="st"></div>
  <div id="out">
   <div class="lbl">🎧 播客音频</div>
   <audio id="au" controls></audio>
   <div class="lbl" id="covlbl" style="display:none">🖼️ 封面图</div>
   <img id="cov" style="display:none">
  </div>
 </div>
</div>
<script>
var st=document.getElementById('st');
function go(){
  var topic=document.getElementById('topic').value.trim();
  if(!topic){st.textContent='请先写个播客主题';return;}
  var btn=document.getElementById('go');btn.disabled=true;btn.textContent='生成中…';
  fetch('/api/podcast',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({topic:topic,host_a:document.getElementById('ha').value,host_b:document.getElementById('hb').value,
      rounds:parseInt(document.getElementById('rounds').value),style:document.getElementById('style').value,
      minutes:document.getElementById('minutes').value||null,
      build_cover:document.getElementById('cover').checked})})
   .then(function(r){return r.json();}).then(function(d){
     if(!d.ok){st.textContent='提交失败: '+d.error;btn.disabled=false;btn.textContent='🎬 生成播客';return;}
     poll(d.jid);
   });
}
function poll(jid){
  fetch('/api/podcast/status/'+jid).then(function(r){return r.json();}).then(function(d){
    st.textContent=d.message||'';
    document.getElementById('bar').firstElementChild.style.width=(d.progress||0)+'%';
    if(d.state==='done'){done(d);}
    else if(d.state==='error'){document.getElementById('go').disabled=false;document.getElementById('go').textContent='🎬 生成播客';st.textContent='❌ '+d.message;}
    else setTimeout(function(){poll(jid);},1200);
  });
}
function done(d){
  document.getElementById('go').disabled=false;document.getElementById('go').textContent='🎬 生成播客';
  document.getElementById('out').style.display='block';
  if(d.audio){var au=document.getElementById('au');au.src=d.audio;au.play();}
  if(d.cover){var c=document.getElementById('cov');c.src=d.cover;c.style.display='block';document.getElementById('covlbl').style.display='block';}
}
</script>
</body></html>"""

@bp.route("/podcast")
def page():
    return Response(_PAGE, mimetype="text/html")

@bp.route("/api/podcast", methods=["POST", "GET"])
def api_generate():
    if request.method == "GET":
        return jsonify({"ok": True, "msg": "使用 POST 提交主题生成播客", "endpoints": ["/api/podcast(status)"]})
    d = request.get_json(silent=True) or {}
    topic = (d.get("topic") or "").strip()
    if not topic:
        return jsonify({"ok": False, "error": "缺少播客主题 topic"}), 400
    try:
        jid = pg.generate_podcast(
            topic,
            host_a=(d.get("host_a") or "小李"),
            host_b=(d.get("host_b") or "小焦"),
            rounds=int(d.get("rounds") or 4),
            style=(d.get("style") or "轻松有趣"),
            use_cover=bool(d.get("use_cover", True)),
            build_cover=bool(d.get("build_cover", True)),
            minutes=int(d.get("minutes")) if str(d.get("minutes") or "").isdigit() else None,
        )
        return jsonify({"ok": True, "jid": jid})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp.route("/api/podcast/status/<jid>")
def api_status(jid):
    return jsonify(pg.job_status(jid))
