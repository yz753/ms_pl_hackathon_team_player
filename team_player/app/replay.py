"""Write a standalone offline HTML replay, with no third-party browser dependencies."""
from __future__ import annotations
import json
from pathlib import Path


def create_replay(metadata, frames, analyses, output, *, initial_player=None, initial_time=0.):
    data = json.dumps({"metadata": metadata, "frames": frames, "analysis": analyses, "replay_start": {"player": initial_player, "t": initial_time}}, allow_nan=False)
    data = data.replace("<", "\\u003c")
    template = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Team Player · Tracking replay</title>
<style>
body{background:#101a22;color:#e7eff5;font:16px system-ui;margin:24px auto;max-width:1250px;padding:0 16px}
h1{margin-bottom:4px}p{color:#b9cbd6}main{display:grid;grid-template-columns:2fr 1fr;gap:20px}
canvas{width:100%;background:#1f654d;border-radius:12px}aside{background:#172733;padding:18px;border-radius:12px}
button,select{font:inherit;padding:8px;border-radius:6px;margin-right:8px}input{width:65%}
details{margin-top:24px;border-top:1px solid #405261;padding-top:14px}summary{cursor:pointer;color:#b9cbd6}.story-heading{color:#f3cc80;font-size:12px;letter-spacing:1px;text-transform:uppercase}.card{border-top:1px solid #405261;padding:10px 0}.good{color:#8aefc7}.warn{color:#f3cc80}
table{width:100%;font-size:14px}td{padding:4px}small{color:#b9cbd6}.controls{margin:18px 0}
@media(max-width:850px){main{grid-template-columns:1fr}}
</style>
<h1>Team Player</h1><p>Contributions beyond goals and assists · Synthetic tracking replay</p>
<main><section><canvas id="pitch" width="1050" height="680"></canvas>
<div class="controls"><button id="play">Play</button><select id="speed"><option>0.25</option><option>0.5</option><option>1</option><option selected>2</option><option>4</option><option>8</option></select>
<input id="seek" type="range" min="0" step="1" value="0"><span id="clock"></span></div>
<p id="ballState"></p><div id="eventLog"></div>
<small><span id="teamLegend"></span> · White: ball · Gold ring: role excursion.<br>
Dashed links: observed following evidence, not proven intent. Click a player to follow their contribution.</small>
</section><aside><div class="story-heading">The contribution behind the moment</div>
<p><label for="focusPlayer">Follow a player</label> <select id="focusPlayer"></select></p>
<h3 id="selectedTitle">Player story</h3><div id="insights" aria-live="polite"></div>
<p><small>Tracking-based explanation · No AI model is called by this standalone replay.</small></p>
<details><summary>Supporting measurements</summary><div id="selected"></div><div id="formations"></div>
<h3>Team context</h3><div id="metrics"></div></details></aside></main>
<p><small>Uncalibrated geometric estimates. Concurrent space gains do not prove attribution. Replay uses only analysis available at the displayed timestamp.</small></p>
<script id="payload" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('payload').textContent), C=document.getElementById('pitch'), ctx=C.getContext('2d');
const F=D.frames,A=D.analysis, meta=D.metadata, lookup=Object.fromEntries(meta.players.map(p=>[p.id,p]));
let index=0,playing=false,selected=(lookup[D.replay_start?.player]?D.replay_start.player:null)||meta.players.find(p=>p.role.includes('ST'))?.id||meta.players[0].id,last=null,elapsed=0;
const jump=new URLSearchParams(location.hash.slice(1));if(lookup[jump.get('player')])selected=jump.get('player');const jumpTime=Number(jump.get('t')??D.replay_start?.t??0);index=Math.max(0,F.findIndex(f=>f.t>=jumpTime));
const picker=document.getElementById('focusPlayer');
for(const p of meta.players){const o=document.createElement('option');o.value=p.id;o.textContent=`${p.id} · ${p.role}`;picker.append(o)}picker.value=selected;picker.onchange=()=>{selected=picker.value;render()};
const seek=document.getElementById('seek');seek.max=F.length-1;
const text=(id,v)=>document.getElementById(id).textContent=v;
text('teamLegend',meta.teams.map((t,i)=>`${i===0?'Blue':'Coral'}: Team ${t.id} attacks ${t.attack_direction===1?'RIGHT →':'LEFT ←'}`).join(' · '));
text('formations',meta.teams.map(t=>`${t.id}: ${t.formation} (${t.attack_direction===1?'→':'←'})`).join('  |  '));
function line(x,y,u,v,color='rgba(255,255,255,.65)'){ctx.strokeStyle=color;ctx.beginPath();ctx.moveTo(x*10,y*10);ctx.lineTo(u*10,v*10);ctx.stroke()}
function circle(x,y,r,color){ctx.fillStyle=color;ctx.beginPath();ctx.arc(x*10,y*10,r*10,0,2*Math.PI);ctx.fill()}
function render(){
 const f=F[index];let a=null;for(const v of A){if(v.t>f.t+1e-6)break;a=v;}
 ctx.clearRect(0,0,C.width,C.height);ctx.lineWidth=2;ctx.strokeStyle='#bbd8c5';ctx.strokeRect(2,2,1046,676);
 line(52.5,0,52.5,68);ctx.beginPath();ctx.arc(525,340,91.5,0,Math.PI*2);ctx.stroke();
 ctx.strokeRect(0,138.5,165,403);ctx.strokeRect(885,138.5,165,403);ctx.strokeRect(0,248.4,55,183.2);ctx.strokeRect(995,248.4,55,183.2);
 const ps=Object.fromEntries(f.players.map(p=>[p.id,p]));
 if(a){ctx.setLineDash([8,6]);for(const link of a.following){const r=ps[link.runner],d=ps[link.defender];line(r.x,r.y,d.x,d.y,'#f3cc80')}ctx.setLineDash([]);}
 for(const p of f.players){const metric=a?.players.find(v=>v.player===p.id);if(metric?.excursion)circle(p.x,p.y,1.1,'#f3cc80');
 circle(p.x,p.y,.65,lookup[p.id].team===meta.teams[0].id?'#63bcff':'#ff8d80');
 if(p.id===selected){ctx.strokeStyle='white';ctx.beginPath();ctx.arc(p.x*10,p.y*10,22,0,Math.PI*2);ctx.stroke()}
 ctx.fillStyle='white';ctx.font='bold 11px system-ui';ctx.textAlign='center';ctx.lineWidth=3;ctx.strokeStyle='#16392e';
 const label=`${p.id} · ${lookup[p.id].role}`;ctx.strokeText(label,p.x*10,p.y*10-12);ctx.fillText(label,p.x*10,p.y*10-12);}
 if(f.ball.state==='shot'||f.ball.state==='pass'){const past=F[Math.max(0,index-3)];ctx.lineWidth=3;line(past.ball.x,past.ball.y,f.ball.x,f.ball.y,'rgba(255,255,255,.5)');}
 circle(f.ball.x,f.ball.y,.4,'white');seek.value=index;text('clock',`${f.t.toFixed(1)} s`);
 text('ballState',`Ball: ${f.ball.state||'unknown'}${f.ball.owner?' · '+f.ball.owner:''} ${Array.isArray(f.score)?'· Score '+f.score.join('–'):''}${f.possession?' · Team '+f.possession+' possession':' · No established possession'}`);
 const recent=F.slice(Math.max(0,index-40),index+1).flatMap(v=>v.events.map(e=>`${v.t.toFixed(1)}s ${e.type.replaceAll('_',' ')} ${e.player||''}${e.target?' → '+e.target:''}`));
 text('eventLog',recent.slice(-3).join(' | '));
 const m=document.getElementById('metrics');m.replaceChildren();
 if(a){for(const [team,v] of Object.entries(a.teams)){const card=document.createElement('div');card.className='card';
 card.textContent=`${team}: ${v.accessible_forward_receivers} forward options · ${v.attacking_control_area_m2.toFixed(0)} m² attacking control · ${v.exposed_wide_area_m2.toFixed(0)} m² exposed wide area`;m.append(card);}}
 const out=document.getElementById('insights');out.replaceChildren();
 for(const v of (a?.insights||[]).filter(v=>v.player===selected&&['striker_dropping_deep','fullback_advanced','off_ball_run'].includes(v.type)).slice(0,2)){const card=document.createElement('div');card.className='card';
 const title=document.createElement('strong');title.textContent=({'striker_dropping_deep':'Dropping deeper to offer a connection','fullback_advanced':'A fullback joins the attack','off_ball_run':'A run that defenders are following'})[v.type];
 const good=document.createElement('p');good.className='good';good.textContent=v.contribution.startsWith('Available passing option') ? (v.type==='striker_dropping_deep' ? `${selected} has come deeper and is available as a passing link. This offers the ball carrier another route through the build-up.` : `${selected} has advanced from fullback and is available as an extra passing option in the attack.`) : v.contribution;
 const risk=document.createElement('small');risk.className='warn';risk.textContent=v.type==='striker_dropping_deep' ? 'Trade-off: coming deeper can leave fewer options near goal until the player returns.' : v.type==='fullback_advanced' ? 'Trade-off: the space behind this advance may need cover if possession changes.' : v.exposure;card.append(title,good,risk);out.append(card);}
 if(!out.children.length)out.textContent=`Watching ${selected}. No sustained deep drop, advanced fullback movement or off-ball following pattern is established at this moment. Movement alone is not credited as a benefit.`;
 text('selectedTitle',`${selected} · ${lookup[selected].role}`);
 if(selected&&a){const p=a.players.find(v=>v.player===selected);text('selectedTitle',`${selected} · ${p.role}`);
 text('selected',`Opponent separation ${p.nearest_opponent_m} m. Direct attacking coverage ${p.direct_attacking_coverage_m2} m². ${p.pass_option?.accessible?'Accessible pass.':'No accessible pass detected.'}`);}
}
document.getElementById('play').onclick=()=>{if(index===F.length-1){index=0;elapsed=0;}playing=!playing;last=null;text('play',playing?'Pause':'Play')};
seek.oninput=()=>{index=+seek.value;elapsed=0;render()};
C.onclick=e=>{const r=C.getBoundingClientRect(),x=(e.clientX-r.left)/r.width*105,y=(e.clientY-r.top)/r.height*68;
 const p=F[index].players.reduce((a,b)=>Math.hypot(a.x-x,a.y-y)<Math.hypot(b.x-x,b.y-y)?a:b);selected=p.id;picker.value=selected;render()};
function tick(now){if(playing&&last!==null){elapsed+=(now-last)/1000*Number(document.getElementById('speed').value);
 while(index<F.length-1&&elapsed>=F[index+1].t-F[index].t){elapsed-=F[index+1].t-F[index].t;index++;}
 if(index===F.length-1){playing=false;text('play','Play')}render();}last=now;requestAnimationFrame(tick)}
render();requestAnimationFrame(tick);
</script></html>'''
    Path(output).write_text(template.replace("__DATA__", data), encoding="utf-8")
