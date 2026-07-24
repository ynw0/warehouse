"""Embedded authentication page templates."""

LOGIN_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>仓库物料系统登录</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: radial-gradient(circle at 20% 18%, rgba(64,201,255,.24), transparent 28%), linear-gradient(135deg,#07172c,#123a66 52%,#0f513a); color: #172033; font-family: Arial, "Microsoft YaHei", sans-serif; overflow:hidden; }
    body::before { content:""; position:fixed; inset:0; background:repeating-linear-gradient(90deg,rgba(255,255,255,.08) 0 1px,transparent 1px 72px),repeating-linear-gradient(0deg,rgba(255,255,255,.06) 0 1px,transparent 1px 72px); animation: drift 16s linear infinite; }
    #particles { position:fixed; inset:0; width:100%; height:100%; opacity:.68; }
    @keyframes drift { from { transform:translate(0,0); } to { transform:translate(72px,72px); } }
    .login { position:relative; width: min(440px, calc(100vw - 32px)); padding: 32px; border: 1px solid rgba(255,255,255,.42); border-radius: 14px; background: rgba(255,255,255,.92); box-shadow: 0 28px 80px rgba(0,0,0,.24); backdrop-filter: blur(16px); }
    h1 { margin: 0 0 8px; font-size: 30px; color:#0d2f63; }
    p { margin: 0 0 20px; color: #647086; }
    label { display: grid; gap: 6px; margin: 12px 0; font-weight: 700; }
    input { min-height: 40px; padding: 8px 10px; border: 1px solid #d9e0ea; border-radius: 6px; font: inherit; }
    button { width: 100%; min-height: 42px; margin-top: 12px; border: 1px solid #2675d9; border-radius: 6px; background: #2675d9; color: #fff; font: inherit; cursor: pointer; }
    .error { padding: 10px; margin: 0 0 12px; border: 1px solid #efb4b4; border-radius: 6px; background: #fff5f5; color: #b93535; }
  </style>
</head>
<body>
  <canvas id="particles"></canvas>
  <form class="login" method="post" action="/login">
    <h1>仓库物料系统</h1>
    <p>请输入账号和密码进入系统。</p>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
    <label>账号<input name="username" autocomplete="username" required autofocus></label>
    <label>密码<input name="password" type="password" autocomplete="current-password" required></label>
    <button type="submit">登录</button>
  </form>
  <script>
    const c=document.getElementById('particles'),x=c.getContext('2d'),m={x:innerWidth*.7,y:120,active:false},ps=Array.from({length:70},()=>({x:Math.random()*innerWidth,y:Math.random()*innerHeight,homeX:Math.random()*innerWidth,homeY:Math.random()*innerHeight,vx:(Math.random()-.5)*.35,vy:(Math.random()-.5)*.35,phase:Math.random()*Math.PI*2}));
    function r(){c.width=innerWidth;c.height=innerHeight} addEventListener('resize',r); addEventListener('mousemove',e=>{m.x=e.clientX;m.y=e.clientY;m.active=true}); addEventListener('mouseleave',()=>{m.active=false}); r();
    function d(){x.clearRect(0,0,c.width,c.height);ps.forEach(p=>{p.phase+=.01;let hx=p.homeX+Math.cos(p.phase)*20-p.x,hy=p.homeY+Math.sin(p.phase)*20-p.y;p.vx+=hx*.0007;p.vy+=hy*.0007;if(m.active){let dx=m.x-p.x,dy=m.y-p.y,dist=Math.max(90,Math.hypot(dx,dy)),pull=dist<260?.018*(1-dist/260):0;p.vx+=dx/dist*pull;p.vy+=dy/dist*pull}p.x+=p.vx;p.y+=p.vy;p.vx*=.94;p.vy*=.94;if(p.x<0||p.x>c.width)p.vx*=-1;if(p.y<0||p.y>c.height)p.vy*=-1;p.x=Math.max(0,Math.min(c.width,p.x));p.y=Math.max(0,Math.min(c.height,p.y));x.beginPath();x.arc(p.x,p.y,2,0,Math.PI*2);x.fillStyle='rgba(158,231,255,.45)';x.fill()});requestAnimationFrame(d)} d();
  </script>
</body>
</html>"""


CHANGE_PASSWORD_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>修改密码</title>
  <style>
    * { box-sizing: border-box; }
    body { margin:0; min-height:100vh; display:grid; place-items:center; background:linear-gradient(135deg,#07172c,#123a66 52%,#0f513a); color:#172033; font-family:Arial,"Microsoft YaHei",sans-serif; }
    .panel { width:min(460px,calc(100vw - 32px)); padding:30px; border-radius:12px; background:rgba(255,255,255,.95); box-shadow:0 28px 80px rgba(0,0,0,.24); }
    h1 { margin:0 0 8px; font-size:28px; color:#0d2f63; }
    p { margin:0 0 18px; color:#647086; line-height:1.5; }
    label { display:grid; gap:6px; margin:12px 0; font-weight:700; }
    input { min-height:40px; padding:8px 10px; border:1px solid #d9e0ea; border-radius:6px; font:inherit; }
    button { width:100%; min-height:42px; margin-top:12px; border:1px solid #2675d9; border-radius:6px; background:#2675d9; color:#fff; font:inherit; cursor:pointer; }
    .error { padding:10px; margin:0 0 12px; border:1px solid #efb4b4; border-radius:6px; background:#fff5f5; color:#b93535; }
    .success { padding:10px; margin:0 0 12px; border:1px solid #a7d8b7; border-radius:6px; background:#f0fff4; color:#237a3b; }
  </style>
</head>
<body>
  <form class="panel" method="post" action="/change-password">
    <h1>修改密码</h1>
    <p>{{ policy_text }}</p>
    {% if error %}<div class="error">{{ error }}</div>{% endif %}
    {% if success %}<div class="success">{{ success }}</div>{% endif %}
    <label>当前密码<input name="current_password" type="password" autocomplete="current-password" required autofocus></label>
    <label>新密码<input name="new_password" type="password" autocomplete="new-password" required></label>
    <label>确认新密码<input name="confirm_password" type="password" autocomplete="new-password" required></label>
    <button type="submit">保存新密码</button>
  </form>
</body>
</html>"""

