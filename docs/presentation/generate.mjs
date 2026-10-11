/** Build the documentation deck from saved evidence. No target, network or LLM access.
 * Run with the bundled Node runtime (see README.md beside this script).
 * Optional overrides: CHAOSPILOT_RUNTIME_MODULES, CHAOSPILOT_PRESENTATION_SKILL,
 * CHAOSPILOT_RUNTIME_PYTHON. All generated files stay beside this script.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';

const OUT = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(OUT, '../..');
const WORKSPACE = path.dirname(ROOT);
const runtime = path.join(os.homedir(), '.cache/codex-runtimes/codex-primary-runtime/dependencies');
const modules = process.env.CHAOSPILOT_RUNTIME_MODULES ?? path.join(runtime, 'node/node_modules');
const skill = process.env.CHAOSPILOT_PRESENTATION_SKILL ?? path.join(os.homedir(), '.codex/plugins/cache/openai-primary-runtime/presentations/26.1009.11318/skills/presentations');
const python = process.env.CHAOSPILOT_RUNTIME_PYTHON ?? path.join(runtime, 'python/bin/python3');
const requireRuntime = createRequire(path.join(modules, 'package.json'));
process.env.RUNTIME_NODE_MODULES = modules;
const { FileBlob, Presentation, PresentationFile } = await import(pathToFileURL(requireRuntime.resolve('@oai/artifact-tool')).href);
const { resolvePresentationFont, applyPresentationChartFont, finalizePresentation } = await import(pathToFileURL(path.join(skill, 'container_tools/artifact_tool_utils.mjs')).href);
const FONT = resolvePresentationFont();
const BUILD = path.join(OUT, '.build');
const revision = new Date().toISOString().replace(/[:.]/g, '-');
const STAGE = path.join(BUILD, revision);
await fs.mkdir(STAGE, { recursive: true });
await fs.mkdir(path.join(STAGE,'output'), { recursive: true });
await fs.mkdir(path.join(STAGE,'private'), { recursive: true });
process.env.TMPDIR = STAGE;
await fs.mkdir(path.join(OUT, 'assets'), { recursive: true });
await fs.mkdir(path.join(OUT, 'renders'), { recursive: true });

const inputHashes = {};
async function read(relative, base = ROOT) {
  const absolute = path.join(base, relative);
  const data = await fs.readFile(absolute);
  inputHashes[path.relative(WORKSPACE, absolute)] = createHash('sha256').update(data).digest('hex');
  return data.toString('utf8');
}
async function json(relative) { return JSON.parse(await read(relative)); }
const buildPlan = await read('BUILD_PLAN.md', WORKSPACE);
const targetNotes = await read('docs/target-notes.md');
const architecture = await read('docs/architecture.md');
const mermaid = await read('docs/graph.mmd');
const readme = await read('README.md');
const progress = await read('docs/progress.md');
const baseline = await json('.chaosai/baseline.json');
const sampleReadme = await read('examples/sample-run/README.md');
const provenance = await json('examples/sample-run/provenance.json');
await read('examples/sample-run/report.md');
const sampleHtml = await read('examples/sample-run/report.html');
await json('examples/sample-run/baseline.json');
await json('examples/sample-run/graph.json');
const decisions = await json('examples/sample-run/decisions.json');
const runIds = (await fs.readdir(path.join(ROOT, 'runs'))).filter(x => /^\d{8}-\d{6}$/.test(x)).sort();
const latestId = runIds.at(-1);
if (!latestId) throw new Error('No run directory found.');
const latest = {};
for (const f of ['run.json', 'decisions.json', 'scores.json', 'findings.json', 'summary.json']) latest[f] = await json(`runs/${latestId}/${f}`);
await read(`runs/${latestId}/report.md`);
await read(`runs/${latestId}/report.html`);
const campaignId = provenance.run_id;
const campaign = {};
for (const f of ['run.json', 'plan.json', 'scores.json', 'findings.json', 'summary.json']) campaign[f] = await json(`runs/${campaignId}/${f}`);
if (latest['run.json'].data_source !== 'measured' || provenance.data_source !== 'measured') throw new Error('Presentation requires measured evidence.');
const expectedCampaign=[['kill','auth-service'],['traffic_spike','browse'],['pause','postgres'],['stop','postgres']];
if(JSON.stringify(campaign['plan.json'].map(x=>[x.fault,x.target]))!==JSON.stringify(expectedCampaign)) throw new Error('Selected campaign changed: review captions and findings before rebuilding.');
if(latest['decisions.json'][0]?.final.target!=='redis' || !latest['findings.json'][0]?.evidence.some(x=>x.includes('Interrupted by signal'))) throw new Error('Latest run context changed: review the interrupted Redis captions.');
if(!Object.values(baseline.journeys).every(x=>x.error_rate===0)) throw new Error('Baseline is no longer error-free: review the baseline slide.');
if(!decisions.every(x=>x.allowed)) throw new Error('Campaign decision trace contains denied entries.');
const counts = [...progress.matchAll(/(\d+) unit tests passed, (\d+) integration deselected/g)].at(-1);
const unitCount = counts ? Number(counts[1]) : null;
const integrationCount = counts ? Number(counts[2]) : null;
const testLabel = unitCount === null ? '[[FILL: current verified test counts]]' : `${unitCount} unit tests   ${integrationCount} integration tests`;
// progress.md is an additional source required by AGENTS.md; counts are recorded,
// not inferred from test filenames. Tests executed during QA are recorded separately.
for (const [text, token] of [[buildPlan, '## 3. Architecture'], [targetNotes, 'ten services'], [architecture, 'LLM'], [readme, 'ChaosPilot'], [sampleReadme, campaignId]]) {
  if (!text.includes(token)) throw new Error(`Source contract changed: ${token}`);
}

// Capture actual saved-report elements. This never launches a target or fetches URLs.
// The approval screenshot remains intentional FILL unless a human saves approval.png.
const fills = [];
let reportImage = null;
let watchdogImage = null;
try {
  const { chromium } = requireRuntime('playwright');
  const chromePath = process.env.CHAOSPILOT_CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const chromeExists = await fs.stat(chromePath).then(() => true).catch(() => false);
  const browser = await chromium.launch({ headless: true, ...(chromeExists ? {executablePath:chromePath} : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 680, height: 900 }, deviceScaleFactor: 2 });
    await page.route('**/*', route => route.abort());
    await page.setContent(sampleHtml, { waitUntil: 'load' });
    await page.addStyleTag({ content: 'body{margin:12px;padding:0;font-size:22px}section{margin:0;padding:20px}h2{font-size:30px;margin:0 0 10px}.score{font-size:50px;margin:0 0 12px}' });
    reportImage = path.join(OUT, 'assets/report.png');
    const summarySection=page.locator('section').filter({ has: page.locator('h2', { hasText: /^Summary$/ }) });
    const summaryBounds=await summarySection.boundingBox();
    const scoreBounds=await summarySection.locator('.score').boundingBox();
    await page.screenshot({path:reportImage,clip:{x:summaryBounds.x,y:summaryBounds.y,width:summaryBounds.width,height:scoreBounds.y+scoreBounds.height-summaryBounds.y+20}});
    // The UNDO row is the recorded abort event, not a recreated terminal session.
    const article = page.locator('article').first();
    const rowHtml = await article.locator('li').filter({ hasText: '· UNDO ·' }).first().evaluate(el => el.outerHTML);
    if (!rowHtml.includes('login: error rate exceeds 0.5')) throw new Error('Expected recorded Watchdog event absent.');
    const style = sampleHtml.match(/<style>([\s\S]*?)<\/style>/)[1];
    await page.setContent(`<style>${style}</style><body><section><h2>Recorded Watchdog abort</h2><ul>${rowHtml}</ul></section></body>`);
    await page.addStyleTag({ content: 'body{margin:12px;padding:0;font-size:24px}section{margin:0;padding:20px}h2{font-size:30px}th,td{padding:12px}' });
    watchdogImage = path.join(OUT, 'assets/watchdog.png');
    await page.locator('section').screenshot({ path: watchdogImage });
  } finally { await browser.close(); }
} catch (error) {
  console.error(`Saved-report screenshot unavailable: ${error.message}`);
  reportImage = null; watchdogImage = null;
}
const approvalImage = path.join(OUT, 'assets/approval.png');
const approvalExists = await fs.stat(approvalImage).then(() => true).catch(() => false);

const C = { dark:'#102A35', ink:'#17313D', muted:'#536A73', teal:'#087D82', pale:'#E8F4F3', white:'#FAFCFC', red:'#AF3347', line:'#B8CCD1', grey:'#EFF3F4' };
const presentation = Presentation.create({ slideSize: { width:1280, height:720 } });
const manifest = [];
let currentTexts = [];
function text(s, content, x, y, w, h, size=28, color=C.ink, bold=false, align='left', fill='none', name='') {
  const sh = s.shapes.add({ geometry:'textbox', name:name || content.slice(0,48), position:{ left:x, top:y, width:w, height:h }, fill, line:{ fill:'none', width:0 } });
  sh.text = content;
  sh.text.style = { typeface:FONT, fontSize:size, color, bold, alignment:align, verticalAlignment:'middle', autoFit:'none', wrap:'square', insets:0 };
  currentTexts.push(content);
  return sh;
}
function slide(title, notes, sources, dark=false) {
  const s = presentation.slides.add();
  s.background.fill = dark ? C.dark : C.white;
  currentTexts = [];
  if (title) text(s,title,60,48,1150,90,44,dark?'#FFFFFF':C.ink,true,'left','none','slide-title');
  text(s,String(presentation.slides.items.length).padStart(2,'0'),1160,644,60,28,18,dark?'#C2D8DD':C.muted,false,'right','none','page-number');
  s.speakerNotes.text = `${notes}\n\nSources: ${sources.join('; ')}`;
  manifest.push({ number:presentation.slides.items.length, title, sources, texts:currentTexts, notes });
  return s;
}
function node(s,label,x,y,w=230,h=82,kind='code',size=26) {
  const sh = s.shapes.add({ geometry:'rect', name:`node-${label}`, position:{left:x,top:y,width:w,height:h}, fill:kind==='ai'?C.pale:kind==='external'?C.grey:'#FFFFFF', line:{fill:kind==='ai'?C.teal:C.line,width:1.5} });
  sh.text=label; sh.text.style={typeface:FONT,fontSize:size,bold:kind==='ai',color:kind==='ai'?C.teal:C.ink,alignment:'center',verticalAlignment:'middle',wrap:'square',autoFit:'none',insets:12};
  currentTexts.push(label); return sh;
}
function connect(s,a,b,from='right',to='left',dashed=false) {
  return s.shapes.connect(a,b,{kind:'straight',fromSide:from,toSide:to,line:{fill:C.muted,width:2,style:dashed?'dashed':'solid'},tail:{type:'triangle',width:'sm',length:'sm'}});
}
function table(s,values,x,y,w,h,widths,size=26) {
  const t=s.tables.add({rows:values.length,columns:values[0].length,left:x,top:y,width:w,height:h,columnWidths:widths,values});
  t.borders.assign({fill:C.line,width:0.6,style:'solid'});
  t.cells.block({row:0,column:0,rowCount:values.length,columnCount:values[0].length}).assign({textStyle:{typeface:FONT,fontSize:size,color:C.ink},margins:{top:10,bottom:10,left:14,right:10},anchor:'center'});
  for(let r=0;r<values.length;r++) {
    t.rows[r].height=h/values.length;
    for(let c=0;c<values[0].length;c++) {
      const cell=t.getCell(r,c);
      cell.fill=r===0?C.dark:r%2?C.white:C.grey;
      cell.text.style={typeface:FONT,fontSize:size,color:r===0?'#FFFFFF':C.ink,bold:r===0};
    }
  }
  currentTexts.push(...values.flat().map(String)); return t;
}
async function image(s,file,x,y,w,h,alt) {
  const bytes=await fs.readFile(file);
  s.images.add({blob:bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength),contentType:'image/png',alt,fit:'contain',position:{left:x,top:y,width:w,height:h}});
}
function fill(s,label,x,y,w,h) {
  const marker=`[[FILL: ${label}]]`; fills.push(marker);
  text(s,marker,x,y,w,h,28,C.muted,false,'center',C.grey);
}
const common=['BUILD_PLAN.md §3','chaos-ai/docs/architecture.md','chaos-ai/README.md'];
let s;

// 1. Cover
s=slide('', 'I am Harsh, presenting ChaosPilot for the Bosch hackathon. The system measures how a local cloud-native application behaves under controlled failures, then reports evidence and recommended fixes. This talk uses saved measured runs, with the completed campaign and later safety checks labelled separately.', ['AGENTS.md','chaos-ai/README.md'],true);
text(s,'ChaosPilot',60,180,1150,120,82,'#FFFFFF',true);
text(s,'AI-Driven Chaos Engineering\nfor Cloud-Native Resilience',64,330,1090,150,40,'#C8E5E5');
text(s,'Bosch hackathon   /   Harsh',64,565,1080,55,28,'#FFFFFF');
manifest.at(-1).title='ChaosPilot';

// 2. Problem and goal
s=slide('Problem and goal','A dependency failure can break customer journeys even when other containers remain running. ChaosPilot maps the app and measures actual request behavior before and during controlled faults. The goal is a safe, reproducible report of impact, recovery and recommended fixes.',common);
text(s,'Problem',60,190,510,65,34,C.red,true);
text(s,'Dependency failures break\ncustomer journeys',60,285,540,160,46,C.ink,true);
text(s,'Goal',705,190,505,65,34,C.teal,true);
text(s,'Measure faults safely.\nProve recovery.\nExplain weaknesses.',705,285,505,220,40);

// 3. Target environment
s=slide('Target environment','The target is the public MIT ecommerce-microservices checkout, pinned in the project documentation. It has a Next.js frontend, four Express services and five infrastructure/tool containers on local Docker Compose. Measurements use an owned auth image with a bounded demo limiter, while the original target source remains unchanged. Stripe, Cloudinary and Resend are external and never injectable.', ['chaos-ai/docs/target-notes.md §1','chaos-ai/README.md','chaos-ai/examples/sample-run/provenance.json']);
const front=node(s,'Next.js frontend',430,170,420,70);
const apps=['Auth','Product','Order','Notification'].map((v,i)=>node(s,v,60+i*296,305,270,82));
for(const n of apps.slice(0,3))connect(s,front,n,'bottom','top');
text(s,'Express services',60,255,1150,38,24,C.muted);
table(s,[['Data services','Tool'],['PostgreSQL  MongoDB  Redis  RabbitMQ','MailHog']],60,458,1160,135,[950,210],26);
text(s,'10 containers   /   MIT target   /   Local Compose sandbox',60,610,1150,45,26,C.teal,true);

// 4. Architecture based on section 3
s=slide('Architecture','The CLI delegates to a deterministic runner, which coordinates mapping, baseline measurement, experiments and reporting. Planner and analyzer calls return validated data and each has a deterministic fallback. The policy gate, injector, journal and watchdog own the Docker safety boundary. This diagram follows BUILD_PLAN section 3 and the frozen implementation contracts.',common);
const cli=node(s,'CLI',485,155,300,65);
const runner=node(s,'Runner',485,260,300,75); connect(s,cli,runner,'bottom','top');
const mapper=node(s,'Mapper',60,400,195,95);
const load=node(s,'Load + baseline',280,400,200,95);
const planner=node(s,'Planner\n+ fallback',505,400,230,95,'ai');
const gate=node(s,'Policy + journal\n+ injector',760,400,220,95);
const watchdog=node(s,'Watchdog\n+ KILL',1005,400,215,95);
for(const n of [mapper,load,planner,gate,watchdog])connect(s,runner,n,'bottom','top');
const target=node(s,'Target app',950,540,270,70);connect(s,gate,target,'bottom','top');connect(s,watchdog,target,'bottom','top');
const analyzer=node(s,'Analyzer + fallback',60,540,360,70,'ai');
const scorer=node(s,'Scorer + report',470,540,350,70);connect(s,analyzer,scorer);
// Route measured target evidence beneath the output nodes without crossing them.
for(const [x,y,w,h,arrow] of [[1085,610,0,25,false],[240,635,845,0,false],[240,610,0,25,true]]) {
 s.shapes.add({geometry:'connector',kind:'straight',position:{left:x,top:y,width:w,height:h},line:{fill:C.muted,width:2},...(arrow?{head:{type:'triangle',width:'sm',length:'sm'}}:{})});
}

// 5. Workflow
s=slide('Experiment workflow','Mapping and baseline establish the input to the plan. Each selected experiment passes approval and readiness checks before injection, and the runner measures impact before undo and HTTP recovery. The next experiment starts only after recovery and cooldown, and analysis/reporting follow safety cleanup.',common);
const workflow=['Map','Baseline','Plan','Approve','Precheck','Inject','Observe','Undo','Recover','Analyze','Report'];
const coords=[[60,190],[360,190],[660,190],[960,190],[960,355],[660,355],[360,355],[60,355],[60,520],[460,520],[860,520]];
const steps=workflow.map((v,i)=>node(s,v,coords[i][0],coords[i][1],i>=8?350:260,76,i===2||i===9?'ai':'code',30));
for(let i=1;i<steps.length;i++) { const a=coords[i-1],b=coords[i];connect(s,steps[i-1],steps[i],a[1]===b[1]?(a[0]<b[0]?'right':'left'):'bottom',a[1]===b[1]?(a[0]<b[0]?'left':'right'):'top'); }
text(s,'One fault at a time. Recovery precedes the next experiment.',60,620,1140,35,24,C.muted);

// 6. Native reconstruction of graph.mmd, all dependency nodes and edges preserved.
s=slide('Application mapping','The graph comes from Compose declarations and explicit overrides, with evidence attached to each dependency. This reconstruction preserves all dependency nodes and edges in docs/graph.mmd, and external services plus the isolated MailHog tool remain excluded from injection. Browse, checkout and login flows are touch-sets rather than ordered call chains. Unknown criticality is a declaration limit, not a measured health verdict.', ['chaos-ai/docs/graph.mmd','chaos-ai/docs/architecture.md','chaos-ai/docs/target-notes.md']);
const layout={frontend:[60,330,175,70], 'auth-service':[300,170,220,65], 'product-service':[300,300,220,65], 'order-service':[300,430,220,65], 'notification-service':[300,560,220,65], postgres:[655,170,210,65],redis:[655,300,210,65],mongodb:[655,430,210,65],rabbitmq:[655,560,210,65],cloudinary:[985,300,230,65],stripe:[985,430,230,65],resend:[985,560,230,65],mailhog:[985,170,230,65]};
const nodes={};
for(const [id,p] of Object.entries(layout)) nodes[id]=node(s,id,...p,['cloudinary','stripe','resend','mailhog'].includes(id)?'external':'code',23);
const names={};
for(const line of mermaid.split('\n')) {
 const m=line.match(/^\s*(n\d+)\["([^ ]+) /); if(m)names[m[1]]=m[2];
}
const graphEdges=[];
for(const line of mermaid.split('\n')) {
 const m=line.match(/^\s*(n\d+) -->\|"([^"]+)"\| (n\d+)/); if(!m)continue;
 const a=names[m[1]],b=names[m[3]]; graphEdges.push([a,b,m[2]]);
 // External edges leave the right-hand side; local dependencies use direct arrows.
 if(['cloudinary','stripe','resend'].includes(b)) {
   // Repeat the source label beside each excluded external edge to avoid routing
   // a line through a local datastore and implying a false dependency chain.
   const [x,y,w]=layout[b];
   const alias=text(s,a,x,y-48,w,28,22,C.muted,false,'center');
   connect(s,alias,nodes[b],'bottom','top',true);
 } else connect(s,nodes[a],nodes[b],'right','left');
}
if(graphEdges.length!==14) throw new Error('graph.mmd edge count changed: review graph layout.');
text(s,'Compose + overrides. External services excluded. Flows are touch-sets.',60,635,1140,35,24,C.muted);

// 7. Real baseline and absolute threshold floors.
s=slide('Monitoring baseline','The saved 120-second baseline is stable and every measured journey has zero request errors. Each displayed latency is rounded from baseline.json, and the limits derive as max(multiplier × baseline p95, baseline p95 + absolute floor). Hypothesis uses 2× and +100ms, while the default watchdog uses 10× and +750ms, with error-rate limits of 2% and 50%. Readiness/recovery instead use max(1.5× p95, p95 + 100ms), and experiment overrides can tighten abort limits.', ['chaos-ai/.chaosai/baseline.json','chaos-ai/examples/sample-run/report.md','chaos-ai/README.md']);
const h=baseline.thresholds.hypothesis,a=baseline.thresholds.abort;
const namesForBaseline=['browse','checkout','homepage','login'];
const thresholdRows=namesForBaseline.map(name=>{const p=baseline.journeys[name].p95_ms;return [name,p.toFixed(1),Math.max(h.p95_multiple*p,p+h.p95_floor_ms).toFixed(1),Math.max(a.p95_multiple*p,p+a.p95_floor_ms).toFixed(1)];});
table(s,[['Journey','p95 ms','Hypothesis ms','Abort ms'],...thresholdRows],60,185,1160,330,[250,230,340,340],28);
text(s,`${baseline.window_s}s stable baseline   /   0% errors`,60,535,1150,45,28,C.teal,true);
text(s,`Hypothesis: max(${h.p95_multiple}×p95,p95+${h.p95_floor_ms}ms)   Errors>${h.max_error_rate*100}%`,60,590,1160,36,25);
text(s,`Abort: max(${a.p95_multiple}×p95,p95+${a.p95_floor_ms}ms)   Errors>${a.max_error_rate*100}%`,60,633,1100,36,25);

// 8. Catalogue and measured coverage.
s=slide('Fault catalogue and experiments','The completed campaign applied kill, traffic spike, pause and stop, while the latest measured safety run stopped Redis. CPU limiting remains conditional on exact restoration, and unsupported quota metadata causes refusal rather than a claimed experiment. Network/netem remains deferred, and full disk is intentionally skipped because it can harm the host laptop.', ['BUILD_PLAN.md §6 and D14','chaos-ai/README.md',`chaos-ai/runs/${campaignId}/plan.json`,`chaos-ai/runs/${latestId}/report.md`]);
table(s,[['Fault','Target','Coverage'],['Kill','Auth','Measured'],['Traffic spike','Browse','Measured'],['Pause','Postgres','Measured'],['Stop','Postgres / Redis','Measured'],['CPU limit','App / DB','Conditional'],['Netem','Network','Deferred']],60,165,1160,425,[310,440,410],27);
text(s,'Full disk intentionally skipped for host safety',60,615,1140,50,30,C.red,true);

// 9. Planner inputs, ranked reasons, trace and fallback.
s=slide('AI planner','The planner receives quoted graph, baseline, catalogue and curated target facts, then proposes ranked experiments with short reasons. Policy validation retains requested/final specs and every correction in decisions.json, including grounded reason changes. The completed campaign used heuristic fallback after Gemini transport failure, so these displayed ranks are the executed campaign plan rather than a claim of live AI success. A separate successful Gemini plan is recorded in progress.md, and neither planner path can call Docker.', ['BUILD_PLAN.md §8','chaos-ai/docs/architecture.md','chaos-ai/examples/sample-run/decisions.json','chaos-ai/examples/sample-run/README.md','chaos-ai/docs/progress.md']);
text(s,'Graph, baseline, catalogue, source facts',60,157,1160,45,28,C.muted);
table(s,[['Rank','Experiment','Reason'],['1','Auth kill','Dependency handling'],['2','Browse spike','Overload capacity'],['3','Postgres pause','Shared database'],['4','Postgres stop','Shared database']],60,235,1160,300,[120,495,545],28);
text(s,'Decision trace: specs, clamps, reasons',60,552,1160,43,28,C.teal,true);
text(s,'Campaign fallback: transport failure',60,612,1160,44,27,C.muted);

// 10. Safety and recovery.
s=slide('Safety and recovery','Scope checks require both graph eligibility and the exact Compose project label, while policy limits and human approval constrain every experiment. The fsynced undo journal precedes mutation, and independent watchdog, timer and KILL paths trigger deterministic rollback. The watchdog evaluates every second, KILL is polled every half-second, and HTTP recovery needs three healthy nonempty windows across all journeys. The LLM never touches Docker, controls cleanup, or decides safety.', ['BUILD_PLAN.md §5','chaos-ai/docs/architecture.md','chaos-ai/README.md']);
text(s,'The LLM never touches Docker',60,165,1160,70,44,C.teal,true);
const safetyLabels=['Scope guard','Policy gate','Approval gate','Undo journal','Watchdog + KILL','Rollback'];
const safetyNodes=safetyLabels.map((v,i)=>node(s,v,[60,465,870,870,465,60][i],280+Math.floor(i/3)*155,350,90));
for(let i=1;i<safetyNodes.length;i++)connect(s,safetyNodes[i-1],safetyNodes[i],i===3?'bottom':i<3?'right':'left',i===3?'top':i<3?'left':'right');
text(s,'Journal before mutation. Recovery requires HTTP evidence.',60,605,1140,55,30);

// 11. Formula, penalty components and deterministic severity.
s=slide('Analysis and scoring','The analyzer explains bounded, quoted evidence and validates citations, with a templated fallback for failed calls. Deterministic score is clamp(100−E−L−R−B−A,0,100), where E=40×min(1,max(0,worst error delta)/0.5), L=20×min(1,max(0,worst p95 ratio−1)/4), R=0 for recovery within 10s or 25 if unproven or 25×min(1,recovery/60), B=10×impacted/total baseline journeys excluding all, and A=5 only for a watchdog abort. Overall averages scored experiments, skips lack scores, and critical severity means score below 30 or aborted with at least two impacted journeys, followed by high below 60, medium below 80 and low otherwise. Confirmed scores at least 80 appear under What held up, while sparse/inconclusive scores remain descriptive and AI cannot change scores or severity.', ['BUILD_PLAN.md §7','chaos-ai/README.md','chaos-ai/examples/sample-run/report.md']);
text(s,'Score = clamp(100−E−L−R−B−A, 0, 100)',60,155,1160,70,36,C.teal,true);
table(s,[['Term','Penalty','Max'],['E','Errors','40'],['L','Latency','20'],['R','Recovery','25'],['B','Blast radius','10'],['A','Watchdog','5']],60,260,575,345,[125,290,160],28);
text(s,'Analyzer\nValidated explanations\nTemplated fallback',715,270,485,150,31);
text(s,'Critical: <30 or abort\nwith ≥2 impacted journeys',715,465,485,95,28,C.red,true);
text(s,'High <60   Medium <80   Low ≥80',715,580,490,54,25,C.muted);

// 12. Native chart and two distinct measured run contexts.
s=slide('Results','The completed measured campaign applied four fault types, verified every undo, established HTTP recovery in 10, 6, 10 and 14 seconds, and passed final smoke. Its scores are 49, 99, 28 and 44, with a recorded mean of 55/100 weak, and the browse spike confirmed its hypothesis while the other three experiments watchdog-aborted. The latest run is a separate Redis stop interrupted by a signal, scored 52 with successful rollback but unproven HTTP recovery and sparse evidence. The recorded engineering checks are 657 unit tests and three live integration tests in progress.md, distinct from application measurements.', [`chaos-ai/runs/${campaignId}/scores.json`,'chaos-ai/examples/sample-run/report.md',`chaos-ai/runs/${latestId}/report.md`,'chaos-ai/docs/progress.md']);
const labels=['Auth kill\nAborted, 10s','Browse spike\nConfirmed, 6s','DB pause\nAborted, 10s','DB stop\nAborted, 14s'];
const chart=s.charts.add('bar',{position:{left:60,top:180,width:750,height:380},categories:labels,series:[{name:'Experiment score',values:campaign['scores.json'].map(x=>x.score),fill:C.teal}],hasLegend:false,barOptions:{direction:'column',grouping:'clustered',gapWidth:90},dataLabels:{showValue:true,position:'outEnd',textStyle:{fontSize:28,color:C.ink,typeface:FONT}},xAxis:{textStyle:{fontSize:23,color:C.ink,typeface:FONT}},yAxis:{min:0,max:100,majorUnit:25,textStyle:{fontSize:21,color:C.muted,typeface:FONT},majorGridlines:{fill:C.line,width:1}},chartFill:'none',chartLine:{fill:'none',width:0},plotAreaFill:'none'});
applyPresentationChartFont(chart,{fontFamily:FONT});
text(s,`${campaign['summary.json'].overall_score}/100`,880,195,335,110,76,C.teal,true);
text(s,'Weak',880,320,335,65,38,C.ink,true);
text(s,'Completed campaign',880,405,335,48,26,C.muted);
text(s,`Latest Redis: ${latest['summary.json'].overall_score}\nInterrupted, recovery unproven`,880,483,335,105,27,C.red);
text(s,`Recorded: ${testLabel}`,60,605,1130,45,27,C.muted);

// 13. Only recommendations present in actual measured findings.
s=slide('Key findings and recommended fixes','Postgres pause and stop are critical recorded findings, with deadline/circuit-breaker and bounded-backoff fixes respectively. Auth kill is high severity with healthcheck/redundancy recommendations, and the latest interrupted Redis stop has a high-severity cache deadline/database fallback recommendation. These are report recommendations rather than implemented fixes, and the reports retain unproven causality and sparse-statistic limits.', [`chaos-ai/runs/${campaignId}/findings.json`,`chaos-ai/runs/${latestId}/findings.json`,'chaos-ai/examples/sample-run/report.md']);
table(s,[['Severity / observation','Recommended fix'],['Critical: Postgres pause','Deadlines, circuit breaker'],['Critical: Postgres stop','Deadlines, backoff'],['High: Auth kill','Healthchecks, redundancy'],['High: Redis interruption','Cache deadlines, DB fallback']],60,190,1160,350,[540,620],28);
text(s,'Causality unproven. Sparse statistics remain descriptive.',60,587,1150,70,29,C.muted);

// 14. Real saved-report captures, and explicit missing approval evidence.
s=slide('Demo walkthrough','Show the real approval prompt with the target, fault, duration, hypothesis and abort limits before accepting an experiment. Then show the saved auth-kill UNDO event where login error rate exceeded 0.5, and the completed campaign report with its score and findings. The two report images are captures of saved HTML, while the approval panel explicitly marks the missing screenshot. Rehearse the live sequence separately because saved screenshots do not demonstrate a new live injection.', ['chaos-ai/examples/sample-run/report.html',`chaos-ai/runs/${campaignId}/report.md`,'chaos-ai/README.md']);
text(s,'1  Approval',60,155,530,48,30,C.ink,true);
if(approvalExists)await image(s,approvalImage,60,225,530,345,'Human-supplied actual approval prompt screenshot');
else fill(s,'approval prompt screenshot',60,225,530,345);
text(s,'2  Watchdog abort',675,155,535,48,30,C.ink,true);
if(watchdogImage)await image(s,watchdogImage,675,215,535,175,'Actual recorded Watchdog UNDO row from saved campaign report');
else fill(s,'watchdog abort screenshot',675,215,535,175);
text(s,'3  Findings report',675,400,535,48,30,C.ink,true);
if(reportImage)await image(s,reportImage,675,460,535,160,'Actual Summary section from saved measured campaign report');
else fill(s,'report screenshot',675,460,535,160);
text(s,'Saved report evidence from the completed measured campaign',60,630,1100,35,24,C.muted);

// 15. Engineering checks and reproducibility, counts have explicit provenance.
s=slide('Engineering quality','Progress records 657 unit tests passing after the final severity regression and three live integration tests passing in the authorized follow-up. Offline fixture mode makes no target or model calls, and cache-only replay regenerates analysis without transport. The clean-run script validates setup, measured experiment completion, restoration, recovery and final smoke, while model discovery/probing, validated JSON and deterministic fallbacks keep model failure separate from safety. The original BUILD_PLAN checkpoint contains older counts, so the newer progress record is cited explicitly.', ['chaos-ai/docs/progress.md final follow-up','BUILD_PLAN.md Phase 10','chaos-ai/README.md','chaos-ai/docs/architecture.md']);
text(s,'Recorded verification',60,165,1160,35,24,C.muted);
text(s,testLabel,60,200,1160,100,52,C.teal,true);
text(s,'Offline fixtures\nCache-only replay\nClean-run script',60,325,550,235,35);
text(s,'Runtime model resolution\nValidated JSON\nDeterministic fallbacks',705,325,510,235,35);

// 16. Grounded limitations, next steps explicitly prospective.
s=slide('Limitations and next steps','These runs assess one owned local Compose sandbox and cannot establish exhaustive application or production resilience. Sparse observations remain descriptive, queue telemetry can be intermittent, and some CPU configurations cannot be restored exactly. Next steps are to validate report-recommended fixes, broaden measured coverage and consider the optional netem and replanning work listed in BUILD_PLAN. Full-disk injection remains intentionally excluded for host safety.', ['chaos-ai/README.md','chaos-ai/docs/architecture.md','BUILD_PLAN.md Phase 11','chaos-ai/examples/sample-run/README.md']);
text(s,'Limitations',60,185,550,60,34,C.red,true);
text(s,'Sparse samples\nIntermittent queue metrics\nCPU restoration constraints\nLocal Compose scope',60,295,550,285,32);
text(s,'Next steps',705,185,510,60,34,C.teal,true);
text(s,'Validate proposed fixes\nExpand measured coverage\nConsider netem and replanning',705,295,510,285,32);

// 17. Closing
s=slide('', 'Thank the judges and engineers for their time. The GitHub repository contains the project, deployment steps, safety documentation and the measured sample report. Invite questions about the deterministic safety boundary and the evidence behind the findings.', ['chaos-ai git remote origin','chaos-ai/README.md'],true);
text(s,'Thank you',60,205,1150,125,82,'#FFFFFF',true);
text(s,'Harsh',64,365,1100,60,35,'#C8E5E5');
const repoUrl='https://github.com/harshgmrn04-byte/chaos-ai';
const repo=text(s,repoUrl,64,495,1140,65,32,'#FFFFFF');
repo.text.get(repoUrl).link={uri:repoUrl,isExternal:true};
manifest.at(-1).title='Thank you';

await fs.writeFile(path.join(OUT,'evidence.json'),JSON.stringify({campaignId,latestId,baselineCreated:baseline.created_at,unitCount,integrationCount,testCountSource:'chaos-ai/docs/progress.md (required by AGENTS.md)',inputHashes,graphEdges,rounding:'Latency table: one decimal ms; scores are saved integers; overall is the saved mean.',fills,slides:manifest},null,2)+'\n');
for(const [i,sl] of presentation.slides.items.entries()) {
 const stem=`slide-${String(i+1).padStart(2,'0')}`;
 const png=await presentation.export({slide:sl,format:'png',scale:1});
 await fs.writeFile(path.join(OUT,'renders',`${stem}.png`),new Uint8Array(await png.arrayBuffer()));
 const layout=await sl.export({format:'layout'});
 await fs.writeFile(path.join(STAGE,`${stem}.layout.json`),await layout.text());
 console.log(`Rendered ${stem}`);
}
const candidate=path.join(STAGE,'candidate.pptx');
await (await PresentationFile.exportPptx(presentation)).save(candidate);
const validated=path.join(STAGE,'output','validated.pptx');
const receipt=await finalizePresentation({workspaceDir:STAGE,candidatePath:candidate,finalPath:validated,pythonExecutable:python,integrityValidatorPath:path.join(skill,'container_tools/inspect_presentation_package_integrity.py'),layoutValidatorPath:path.join(skill,'container_tools/inspect_presentation_layout_geometry.py'),layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-bullet-geometry','--validate-heading-fit',...[3,7,8,9,11,13].flatMap(n=>['--require-native-table-slide',String(n)])],explicitTotalSlideCount:17,requiredNativeTableOwnerSlides:[3,7,8,9,11,13],requiredNativeChartOwnerSlides:[12],materializeLiteralChartWorkbooks:true,fontPolicy:{basis:'design',families:[FONT]},verifyArtifactToolImport:true,receiptPath:path.join(STAGE,'private','validation.json')});
await fs.copyFile(validated,path.join(OUT,'ChaosPilot.pptx'));
await fs.copyFile(path.join(STAGE,'private','validation.json'),path.join(OUT,'validation.json'));
// Finalizer embeds a literal-data chart workbook. Render the actual final package
// as well, so previews describe the delivered file rather than only the draft.
const finalPresentation=await PresentationFile.importPptx(await FileBlob.load(path.join(OUT,'ChaosPilot.pptx')));
for(const [i,sl] of finalPresentation.slides.items.entries()) {
 const png=await finalPresentation.export({slide:sl,format:'png',scale:1});
 await fs.writeFile(path.join(OUT,'renders',`slide-${String(i+1).padStart(2,'0')}.png`),new Uint8Array(await png.arrayBuffer()));
}
console.log(JSON.stringify({output:path.join(OUT,'ChaosPilot.pptx'),slides:17,font:FONT,fills,validated:receipt.finalSha256},null,2));
