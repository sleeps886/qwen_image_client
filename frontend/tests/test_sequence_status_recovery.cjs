const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require('node:path').join(__dirname,'../mixed-workspace.js'),'utf8');
const fn=source.slice(source.indexOf('  async function refreshSequence(){'),source.indexOf('  async function dispatchSequence('));
(async()=>{
  const timers=[],notes=[],calls=[];let failures=4;
  const context={state:{session_id:'sid',active_sequential_run:'rid'},API:'/api/incremental/mixed-sessions',sequenceTimer:null,sequenceErrors:0,
    sequenceRun:null,clearTimeout:()=>{},setTimeout:(fn,delay)=>{timers.push({fn,delay});return timers.length;},
    note:s=>notes.push(s),drawSequenceSteps:()=>{},render:()=>{},
    fetchJson:async(url,options,timeout)=>{calls.push({url,options,timeout});if(failures-->0)throw Error('timeout');return {response:{ok:true},data:{cursor:483,spec:{queue:Array(483)},status:'completed_awaiting_review'}};},
    request:async()=>{throw Error('root temporarily unavailable');}};
  vm.createContext(context);vm.runInContext(fn,context);
  await context.refreshSequence();
  for(let i=0;i<4;i++){const t=timers.shift();assert(t&&t.delay<=30000);t.fn();await new Promise(resolve=>setImmediate(resolve));}
  assert.equal(calls.length,5);assert.equal(context.sequenceRun.cursor,483);
  assert.equal(timers.length,0,'completed run keeps polling');
  assert(calls.every(c=>c.timeout===60000&&!c.options.method),'poll retries must be GET only');
  assert(notes.at(-1).includes('已完成'),'root summary failure hid completion');
  assert(!source.slice(source.indexOf('  async function refreshSequence(){'),source.indexOf('  async function dispatchSequence(')).includes('sequenceErrors<3'));
  console.log('PASS: recovers after >3 status failures; bounded GET backoff; root failure cannot hide 483-group completion; no model POST');
})().catch(e=>{console.error(e);process.exit(1);});
