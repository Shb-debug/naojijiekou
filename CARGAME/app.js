const homeScreen=document.getElementById('homeScreen');
const gameScreen=document.getElementById('gameScreen');
const canvas=document.getElementById('gameCanvas');
const ctx=canvas.getContext('2d');
const resultModal=document.getElementById('resultModal');
const calibrationModal=document.getElementById('calibrationModal');
const calibrationInstruction=document.getElementById('calibrationInstruction');
const calibrationPhase=document.getElementById('calibrationPhase');
const calibrationCountdown=document.getElementById('calibrationCountdown');
const calibrationProgressBar=document.getElementById('calibrationProgressBar');
const calibrationButton=document.getElementById('calibrationButton');
const calibrationStartButton=document.getElementById('calibrationStartButton');
const calibrationCloseButton=document.getElementById('calibrationCloseButton');
const scoreValue=document.getElementById('scoreValue');
const scoreBar=document.getElementById('scoreBar');
const timeValue=document.getElementById('timeValue');
const timeBar=document.getElementById('timeBar');
const laneValue=document.getElementById('laneValue');
const confidenceValue=document.getElementById('confidenceValue');
const confidenceBar=document.getElementById('confidenceBar');
const speedReadout=document.getElementById('speedReadout');
const commandLog=document.getElementById('commandLog');
const historyList=document.getElementById('historyList');
const historyCount=document.getElementById('historyCount');
const sessionTime=document.getElementById('sessionTime');
const connectionPill=document.getElementById('connectionPill');
const connectionText=document.getElementById('connectionText');
let eegSocket=null;
let eegStatus='simulation';

const TOTAL_TIME=90;
const commands={left:{label:'左道',freq:20},right:{label:'右道',freq:15}};
const targetTypes=[
  {icon:'✦',value:10,color:'#74e4c4',glow:'rgba(104,226,196,.75)'},
  {icon:'◆',value:15,color:'#f0b26d',glow:'rgba(240,178,109,.78)'}
];
const specialTwenty={icon:'✹',value:20,color:'#f7de9b',glow:'rgba(247,222,155,.95)'};
const state={running:false,score:0,targets:0,lane:1,laneVisual:1,items:[],startedAt:0,lastFrame:0,lastSpawn:0,spawnDelay:3600,twentySchedule:[],twentyDropped:0,sessionStart:performance.now(),history:[],run:1};
const CALIBRATION_URL='http://127.0.0.1:8766';
let calibrationPollTimer=null;

function resizeCanvas(){const rect=canvas.getBoundingClientRect();const ratio=Math.min(window.devicePixelRatio||1,2);canvas.width=Math.max(1,Math.floor(rect.width*ratio));canvas.height=Math.max(1,Math.floor(rect.height*ratio));ctx.setTransform(ratio,0,0,ratio,0,0);}
function lerp(a,b,t){return a+(b-a)*t;}
function clamp(n,min,max){return Math.max(min,Math.min(max,n));}
function polygon(points,fill,stroke,line=1){ctx.beginPath();ctx.moveTo(points[0][0],points[0][1]);for(let i=1;i<points.length;i++)ctx.lineTo(points[i][0],points[i][1]);ctx.closePath();if(fill){ctx.fillStyle=fill;ctx.fill();}if(stroke){ctx.lineWidth=line;ctx.strokeStyle=stroke;ctx.stroke();}}
function formatTime(seconds){const s=Math.max(0,Math.ceil(seconds));return `${String(Math.floor(s/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`;}
function laneName(lane){return ['左道','中道','右道'][lane];}
function getHistory(){try{return JSON.parse(localStorage.getItem('eegHorizonHistory')||'[]');}catch{return [];}}
function saveHistory(){try{localStorage.setItem('eegHorizonHistory',JSON.stringify(state.history.slice(0,8)));}catch{}}

function renderHistory(){
  state.history=getHistory(); historyCount.textContent=`${state.history.length} 场记录`;
  if(!state.history.length){historyList.innerHTML='<div class="history-empty">还没有对局记录。<br />准备好后，进入第一局吧。</div>';return;}
  historyList.innerHTML=state.history.map((game,index)=>`<article class="history-item"><div class="history-main"><span class="history-score">${game.score}</span><span class="history-date">/ 100 · ${game.date}</span></div><div class="history-detail">收集 ${game.targets} 个目标 · ${game.phrase}</div><button class="history-play" data-history-start="${index}">进入对局 ↗</button></article>`).join('');
  historyList.querySelectorAll('.history-play').forEach(button=>button.addEventListener('click',startGame));
}

function showHome(){state.running=false;resultModal.classList.add('hidden');calibrationModal.classList.add('hidden');homeScreen.classList.remove('hidden');gameScreen.classList.add('hidden');document.body.classList.remove('game-active','calibration-active');renderHistory();}
function showCalibration(){resultModal.classList.add('hidden');calibrationModal.classList.remove('hidden');document.body.classList.add('calibration-active');calibrationInstruction.textContent='请先启动 OpenViBE 场景，再开始校准。';calibrationPhase.textContent='等待开始';calibrationCountdown.textContent='--';calibrationProgressBar.style.width='0%';pollCalibration();}
function closeCalibration(){if(calibrationPollTimer)clearTimeout(calibrationPollTimer);calibrationPollTimer=null;calibrationModal.classList.add('hidden');document.body.classList.remove('calibration-active');}
function setCalibrationState(data){
  const phaseNames={idle:'等待开始',neutral:'阶段 1 / 基线',left:'阶段 2 / 注视左道',right:'阶段 3 / 注视右道',done:'校准完成'};
  calibrationPhase.textContent=phaseNames[data.phase]||'校准状态';
  calibrationInstruction.textContent=data.message||'等待桥接器状态…';
  calibrationCountdown.textContent=data.active?formatTime(data.remaining):data.complete?'OK':'--';
  calibrationProgressBar.style.width=`${clamp((Number(data.elapsed)||0)/(Number(data.total)||55)*100,0,100)}%`;
  calibrationStartButton.disabled=Boolean(data.active);
  calibrationStartButton.querySelector('span').textContent=data.complete?'重新校准':data.active?'校准进行中':'开始校准';
  if(data.active){calibrationPollTimer=setTimeout(pollCalibration,250);}
}
async function pollCalibration(){
  try{const response=await fetch(`${CALIBRATION_URL}/calibration/state`,{cache:'no-store'});setCalibrationState(await response.json());}
  catch{calibrationInstruction.textContent='桥接器不可用，请先启动 start_eeg_bridge.bat（HTTP 8766）。';calibrationPhase.textContent='连接失败';calibrationCountdown.textContent='--';calibrationStartButton.disabled=false;}
}
async function startCalibration(){
  calibrationStartButton.disabled=true;
  try{const response=await fetch(`${CALIBRATION_URL}/calibration/start`,{cache:'no-store'});setCalibrationState(await response.json());pollCalibration();}
  catch{calibrationInstruction.textContent='无法开始校准，请确认桥接器已启动并监听 8766 端口。';calibrationStartButton.disabled=false;}
}
function startGame(){
  resultModal.classList.add('hidden');homeScreen.classList.add('hidden');gameScreen.classList.remove('hidden');document.body.classList.add('game-active');
  state.running=true;state.score=0;state.targets=0;state.lane=1;state.laneVisual=1;state.items=[];state.startedAt=performance.now();state.lastFrame=state.startedAt;state.lastSpawn=state.startedAt-2300;state.spawnDelay=3600;state.twentySchedule=[12,29,46,63,80].map(anchor=>anchor+(Math.random()*6-3)).sort((a,b)=>a-b);state.twentyDropped=0;state.run+=1;resizeCanvas();
  scoreValue.textContent='0';scoreBar.style.width='0%';timeValue.textContent='01:30';timeBar.style.width='100%';laneValue.textContent='中道';confidenceValue.textContent='86%';confidenceBar.style.width='86%';speedReadout.textContent='032';commandLog.innerHTML='<span class="log-empty">等待收集目标…</span>';activateButton('right');
  requestAnimationFrame(gameLoop);
}
function activateButton(command){document.querySelectorAll('.command-button').forEach(button=>button.classList.toggle('active',button.dataset.command===command));}
function issueCommand(command){
  if(!state.running)return; if(command==='left')state.lane=Math.max(0,state.lane-1); if(command==='right')state.lane=Math.min(2,state.lane+1);
  const pct=82+Math.floor(Math.random()*13);confidenceValue.textContent=`${pct}%`;confidenceBar.style.width=`${pct}%`;laneValue.textContent=laneName(state.lane);activateButton(command);pulseButton(command);
}
function pulseButton(command){const button=document.querySelector(`.command-button[data-command="${command}"]`);if(!button)return;button.classList.remove('command-pulse');void button.offsetWidth;button.classList.add('command-pulse');setTimeout(()=>button.classList.remove('command-pulse'),260);}
function setConnection(status,label){eegStatus=status;connectionPill.classList.remove('connected','warning','error');if(status==='eeg')connectionPill.classList.add('connected');if(status==='bridge')connectionPill.classList.add('warning');if(status==='error')connectionPill.classList.add('error');connectionText.textContent=label;}
function applyBridgeStatus(message){
  const simulated=message.simulated===true||message.stream==='SIMULATED EEG';
  if(message.openvibe_connected===true){setConnection('eeg','真实 EEG · OpenViBE 已连接');}
  else if(message.lsl_connected===true){setConnection(simulated?'simulation':'eeg',simulated?'模拟 EEG · 桥接已连接':'真实 EEG · LSL 已连接');}
  else if(message.source==='OpenViBE'&&message.openvibe_connected===false){setConnection('bridge','桥接在线 · OpenViBE 信号已停止');}
  else if(message.lsl_connected===false){setConnection('bridge','桥接在线 · 未找到 LSL EEG');}
}
function connectEEG(){
  if(eegSocket&&eegSocket.readyState===WebSocket.OPEN){eegSocket.close();return;}
  setConnection('bridge','正在连接 EEG 桥接器…');
  try{
    eegSocket=new WebSocket('ws://localhost:8765');
    eegSocket.onopen=()=>setConnection('bridge','桥接在线 · 等待 LSL EEG');
    eegSocket.onmessage=event=>{try{const message=JSON.parse(event.data);if(message.type==='status'||message.type==='hello'||message.openvibe_connected===true)applyBridgeStatus(message);if(message.type==='command'&&(message.command==='left'||message.command==='right'))issueCommand(message.command);if(typeof message.confidence==='number'){const pct=Math.round(message.confidence*100);confidenceValue.textContent=`${pct}%`;confidenceBar.style.width=`${pct}%`;}}catch{} };
    eegSocket.onerror=()=>setConnection('error','连接失败 · 点击重试');
    eegSocket.onclose=()=>{eegSocket=null;setConnection('simulation','模拟信号 · 键盘输入');};
  }catch{setConnection('error','连接失败 · 点击重试');}
}
function addCollectedLog(item){
  const chip=`<span class="log-chip"><strong>+${item.type.value}</strong> ${item.type.icon}</span>`;const existing=commandLog.querySelector('.log-empty');if(existing)commandLog.innerHTML='';commandLog.insertAdjacentHTML('afterbegin',chip);while(commandLog.children.length>5)commandLog.lastElementChild.remove();
}
function spawnTarget(width,height,elapsed){
  const isTwenty=state.twentyDropped<state.twentySchedule.length&&elapsed>=state.twentySchedule[state.twentyDropped];
  const type=isTwenty?specialTwenty:targetTypes[Math.floor(Math.random()*targetTypes.length)];
  if(isTwenty)state.twentyDropped++;
  state.items.push({lane:Math.floor(Math.random()*3),y:-35,type,speed:52+Math.random()*18,phase:Math.random()*Math.PI*2});
}
function drawMountainLayer(width,horizon,base,color,peaks,offset){const points=[[0,base]],step=width/(peaks.length-1);peaks.forEach((peak,index)=>points.push([index*step+offset,horizon+peak]));points.push([width,base]);polygon(points,color);}
function drawGame(now){
  const rect=canvas.getBoundingClientRect(),width=rect.width,height=rect.height,dt=Math.min((now-state.lastFrame)/1000,.05);state.lastFrame=now;state.laneVisual=lerp(state.laneVisual,state.lane,1-Math.pow(.0001,dt));
  const horizon=height*.30;const sky=ctx.createLinearGradient(0,0,0,horizon);sky.addColorStop(0,'#102b34');sky.addColorStop(.65,'#3c7470');sky.addColorStop(1,'#d08d65');ctx.fillStyle=sky;ctx.fillRect(0,0,width,height);
  const sun=ctx.createRadialGradient(width*.70,horizon*.68,2,width*.70,horizon*.68,width*.28);sun.addColorStop(0,'rgba(255,216,145,.8)');sun.addColorStop(.22,'rgba(240,164,99,.22)');sun.addColorStop(1,'rgba(240,164,99,0)');ctx.fillStyle=sun;ctx.fillRect(0,0,width,horizon+80);
  drawMountainLayer(width,horizon,height*.59,'#264e51',[38,-34,20,-62,29,-15,37,-40,15],(state.startedAt*.00001-width*.15)%width);drawMountainLayer(width,horizon+18,height*.66,'#173b3f',[62,-2,37,-24,70,-5,40,-33,65],(state.startedAt*.00002-width*.35)%width);
  ctx.fillStyle='#825449';ctx.fillRect(0,horizon,width,height-horizon);
  const roadLeft=width*.12,roadRight=width*.88;polygon([[width/2-roadLeft,horizon],[width/2+roadLeft,horizon],[width/2+roadRight,height],[width/2-roadRight,height]],'#263e3f','#60706a',1);
  const laneWidth=width*.22;for(let lane=1;lane<3;lane++){const topX=width/2-roadLeft+lane*(roadLeft*2/3),bottomX=width/2-roadRight+lane*(roadRight*2/3);ctx.setLineDash([10,14]);ctx.strokeStyle='rgba(238,214,162,.30)';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(topX,horizon);ctx.lineTo(bottomX,height);ctx.stroke();}ctx.setLineDash([]);
  for(let i=0;i<15;i++){const t=((i/15+now*.00012)%1),y=horizon+Math.pow(t,1.55)*(height-horizon),x=width/2+(Math.sin(t*4+now*.0007)*width*.045);ctx.fillStyle=`rgba(237,189,111,${.12+t*.32})`;ctx.fillRect(x-width*(.34+t*.24),y,2+t*4,2+t*2);ctx.fillRect(x+width*(.34+t*.24),y,2+t*4,2+t*2);}
  state.items.forEach(item=>{const x=width/2+(item.lane-1)*laneWidth;const scale=.65+(item.y/height)*.5;const wobble=Math.sin(now*.004+item.phase)*2;ctx.save();ctx.translate(x+wobble,item.y);ctx.scale(scale,scale);ctx.shadowColor=item.type.glow;ctx.shadowBlur=20;ctx.fillStyle=item.type.color;ctx.font='700 27px "Barlow Condensed",sans-serif';ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(item.type.icon,0,0);ctx.shadowBlur=0;ctx.fillStyle='rgba(8,25,26,.74)';ctx.font='600 9px Barlow,sans-serif';ctx.fillText(`+${item.type.value}`,0,25);ctx.restore();});
  const carX=width/2+(state.laneVisual-1)*laneWidth,carY=height*.79;ctx.save();ctx.translate(carX,carY);ctx.shadowColor='rgba(4,12,13,.65)';ctx.shadowBlur=25;ctx.shadowOffsetY=16;polygon([[-48,27],[-44,-8],[-26,-27],[10,-28],[39,-7],[52,27],[36,40],[-34,40]],'#c96447','#f0ae6c',2);ctx.shadowColor='transparent';polygon([[-27,-7],[-14,-25],[9,-25],[28,-7]],'#173638','#86c7b0',1);polygon([[-16,-9],[-8,-21],[5,-21],[13,-9]],'#4b7771');ctx.fillStyle='#e49d60';ctx.fillRect(-38,2,11,4);ctx.fillRect(27,2,11,4);ctx.fillStyle='#1a2328';ctx.fillRect(-45,25,16,11);ctx.fillRect(30,25,16,11);ctx.fillStyle='#ebb16c';ctx.fillRect(-41,27,8,4);ctx.fillRect(34,27,8,4);ctx.fillStyle='#f4d192';ctx.fillRect(-2,27,5,8);ctx.restore();
}
function gameLoop(now){
  if(!state.running)return;const elapsed=(now-state.startedAt)/1000,remaining=TOTAL_TIME-elapsed; if(remaining<=0){endGame();return;}
  if(now-state.lastSpawn>state.spawnDelay){spawnTarget(canvas.clientWidth,canvas.clientHeight,elapsed);state.lastSpawn=now;state.spawnDelay=3200+Math.random()*1100;}
  const dt=Math.min((now-state.lastFrame)/1000,.05);state.items.forEach(item=>item.y+=item.speed*dt);const playerY=canvas.clientHeight*.79;
  state.items=state.items.filter(item=>{if(item.y>playerY-25&&item.y<playerY+32&&item.lane===state.lane){state.score=Math.min(100,state.score+item.type.value);state.targets++;scoreValue.textContent=state.score;scoreBar.style.width=`${state.score}%`;addCollectedLog(item);return false;}return item.y<canvas.clientHeight+50;});
  timeValue.textContent=formatTime(remaining);timeBar.style.width=`${remaining/TOTAL_TIME*100}%`;drawGame(now);requestAnimationFrame(gameLoop);
}
function resultPhrase(score){if(score>=90)return'路线掌控得像风一样。';if(score>=70)return'节奏很稳，信号捕获漂亮。';if(score>=40)return'不错的路线判断，再冲一次高分。';return'第一局完成，下一次会更好。';}
function endGame(){
  if(!state.running)return;state.running=false;const score=state.score,phrase=resultPhrase(score);const record={score,targets:state.targets,phrase,date:new Date().toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})};state.history.unshift(record);saveHistory();document.getElementById('finalScore').textContent=score;document.getElementById('resultPhrase').textContent=phrase;document.getElementById('resultTargets').textContent=state.targets;document.getElementById('resultDuration').textContent=formatTime(TOTAL_TIME);resultModal.classList.remove('hidden');}

const keyMap={ArrowLeft:'left',ArrowRight:'right','1':'left','2':'right'};document.addEventListener('keydown',event=>{if(keyMap[event.key]){event.preventDefault();issueCommand(keyMap[event.key]);}if(event.key==='Escape'&&state.running)endGame();});document.querySelectorAll('.command-button').forEach(button=>button.addEventListener('click',()=>issueCommand(button.dataset.command)));document.getElementById('startButton').addEventListener('click',startGame);document.getElementById('replayButton').addEventListener('click',startGame);document.getElementById('finishButton').addEventListener('click',showHome);document.getElementById('homeButton').addEventListener('click',()=>{if(state.running)endGame();else showHome();});connectionPill.addEventListener('click',connectEEG);window.addEventListener('resize',resizeCanvas);
setInterval(()=>{const elapsed=Math.floor((performance.now()-state.sessionStart)/1000);sessionTime.textContent=`${String(Math.floor(elapsed/60)).padStart(2,'0')}:${String(elapsed%60).padStart(2,'0')}`;},850);
function stimulusLoop(now){document.querySelectorAll('.command-button').forEach(button=>{const frequency=Number(button.style.getPropertyValue('--freq'))||12;const phase=Math.floor((now/1000)*frequency*2)%2===0;button.classList.toggle('stim-on',phase);});requestAnimationFrame(stimulusLoop);}
calibrationButton.addEventListener('click',showCalibration);calibrationStartButton.addEventListener('click',startCalibration);calibrationCloseButton.addEventListener('click',closeCalibration);
renderHistory();resizeCanvas();drawGame(performance.now());requestAnimationFrame(stimulusLoop);
