const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const html=fs.readFileSync(path.join(__dirname,'../Whiteout Buff Scheduler.html'),'utf8');
const code=html.slice(html.indexOf('"use strict";'),html.indexOf('/* ================================ CORE-END'));
const c=vm.createContext({console});
vm.runInContext(code+'\nglobalThis.api={solveSchedule,buildMapping,buildPlayers,parseAvailability,ambiguousAvailability,analyzeUnplaced};',c);
const api=c.api,days=['construction','research','training'];
function member(id,avail,benefits={},history=0){return{playerId:id,name:id,tag:'TEST',sourceOrder:Number(id)||0,selectedDays:new Set(Object.keys(avail)),availableSlots:Object.fromEntries(days.map(d=>[d,new Set(avail[d]||[])])),preferredSlots:Object.fromEntries(days.map(d=>[d,new Set()])),benefitMinutes:Object.fromEntries(days.map(d=>[d,benefits[d]??null])),historyCount:history,basePoints:{construction:999999,research:999999,training:999999},generalPoints:999999999};}
const opt={slotMinutes:30,maxTotalPerPlayer:3,dayWindow:[0,60],policy:'team'};
const solve=(p,o={})=>api.solveSchedule(p,{...opt,...o});
let cases=0;
function test(name,fn){try{fn();cases++;}catch(e){e.message=name+': '+e.message;throw e;}}
test('honest zero-speedup long starts remain eligible',()=>{const p=member('1',{construction:[0]});p.basePoints={construction:0,research:0,training:0};p.generalPoints=0;assert.equal(solve([p]).assignments.length,1);});
test('distinct members before repeats',()=>{const p=[member('1',{construction:[0],research:[0]}),member('2',{construction:[0]})];const r=solve(p);assert.equal(r.coveredPlayers,2);assert.equal(r.assignments.find(a=>a.dayKey==='construction').playerId,'2');});
test('global reroute protects constrained windows',()=>{const r=solve([member('1',{construction:[0,30]}),member('2',{construction:[0]})]);assert.equal(r.coveredPlayers,2);assert.equal(r.assignments.find(a=>a.playerId==='1').start,30);});
test('old gross points cannot dominate marginal benefit',()=>{const a=member('1',{construction:[0]},{construction:1}),b=member('2',{construction:[0]},{construction:20});a.basePoints.construction=1e12;assert.equal(solve([a,b],{policy:'benefit'}).assignments[0].playerId,'2');});
test('missing estimates never become fabricated benefit',()=>assert.throws(()=>solve([member('1',{training:[0]})],{policy:'benefit'}),/needs measured/));
test('structured preference wins with honest constrained request',()=>{const a=member('1',{construction:[0,30]});a.preferredSlots.construction.add(30);assert.equal(solve([a]).assignments[0].start,30);});
test('history rotates first access',()=>{const r=solve([member('1',{construction:[0]},{},6),member('2',{construction:[0]},{},0)]);assert.equal(r.assignments[0].playerId,'2');});
test('accepted external appointment is preserved',()=>{const r=solve([member('1',{construction:[0,30]})],{locks:[{playerId:'outside',dayKey:'construction',start:0}]});assert.equal(r.assignments.find(a=>a.playerId==='outside').locked,true);assert.equal(r.assignments.find(a=>a.playerId==='1').start,30);assert.equal(r.coveredPlayers,1);});
test('accepted conflicts stop instead of dropping an appointment',()=>assert.throws(()=>solve([member('1',{construction:[30]})],{locks:[{playerId:'1',dayKey:'construction',start:0}]}),/conflicts/));
test('lock consumes event cap',()=>{const r=solve([member('1',{construction:[0],research:[0]})],{maxTotalPerPlayer:1,locks:[{playerId:'1',dayKey:'construction',start:0}]});assert.equal(r.assignments.length,1);});
test('early finish does not permit 15-minute seat',()=>assert.throws(()=>solve([],{slotMinutes:15}),/30 minutes/));
test('UTC midnight window remains two windows',()=>assert.deepEqual([...api.parseAvailability('23:30–00:30',30)].sort((a,b)=>a-b),[0,1410]));
test('zero endpoints not silently all-day',()=>assert.match(api.ambiguousAvailability('08:00-08:00'),/ambiguous/));
test('unconverted local timezone is not UTC',()=>assert.match(api.ambiguousAvailability('08:00-10:00 HST'),/UTC/));
test('comments never invert a cannot-time into a wish',()=>{const headers=['Player ID','Desired buffs','General availability','Comments'];const m=api.buildMapping(headers);const {players}=api.buildPlayers([{'Player ID':'123456','Desired buffs':'construction','General availability':'08:00-10:00','Comments':'Research only; cannot 08:00-09:00'}],m,30);assert.equal(players[0].preferredSlots.construction.size,0);assert.equal(players[0].wishRanges.length,0);});
test('partial excluded window blocks any intersecting seat',()=>{const h=['Player ID','Desired buffs','General availability','Construction unavailable UTC'];const m=api.buildMapping(h);const {players}=api.buildPlayers([{'Player ID':'123456','Desired buffs':'construction','General availability':'08:00-10:00','Construction unavailable UTC':'08:15-08:45'}],m,30);assert.deepEqual([...players[0].availableSlots.construction],[540,570]);});
test('duplicate retains latest recognized timestamp',()=>{const h=['Player ID','Desired buffs','General availability','Completion time'];const m=api.buildMapping(h);const {players,duplicates}=api.buildPlayers([{'Player ID':'123456','Desired buffs':'research','General availability':'08:00-09:00','Completion time':'2026-10-26T01:00:00Z'},{'Player ID':'123456','Desired buffs':'construction','General availability':'09:00-10:00','Completion time':'2026-10-26T02:00:00Z'}],m,30);assert.equal(duplicates.length,1);assert.equal(players[0].selectedDays.has('construction'),true);});
test('attached and dotted one-sided AM PM never widens availability',()=>{assert.match(api.ambiguousAvailability('8–10pm'),/both endpoints/);assert.match(api.ambiguousAvailability('12-2a.m.'),/both endpoints/);});
test('unknown benefit disables ranking for every member',()=>{const a=member('1',{construction:[0]},{construction:9999}),b=member('2',{construction:[0]});a.historyCount=5;const r=solve([a,b],{policy:'balanced'});assert.equal(r.completeBenefits,false);assert.equal(r.policyOrder.includes('benefit'),false);assert.equal(r.assignments[0].playerId,'2');});
test('unknown history is not secretly zero',()=>{const a=member('1',{construction:[0]},{construction:1},5),b=member('2',{construction:[0]},{construction:1},0);b.historyKnown=false;const r=solve([a,b]);assert.equal(r.policyOrder.includes('rotation'),false);assert.equal(r.assignments[0].playerId,'1');});
test('invalid history is a concrete input error',()=>{const h=['Player ID','Desired buffs','General availability','Prior appointments'];const m=api.buildMapping(h);assert.throws(()=>api.buildPlayers([{'Player ID':'123456','Desired buffs':'research','General availability':'08:00-09:00','Prior appointments':'unknown'}],m,30),/malformed history/);});
test('prior draft times survive when higher objectives tie',()=>{const p=member('1',{construction:[0,30]},{construction:1});p.previousSlots={construction:30};assert.equal(solve([p]).assignments[0].start,30);});
test('balanced and benefit-first tradeoff is explicit',()=>{const a=member('1',{construction:[0],research:[0]},{construction:10,research:10}),b=member('2',{construction:[0]},{construction:1}),d=member('3',{research:[0]},{research:1});const r=solve([a,b,d],{policy:'balanced'});assert.equal(r.tradeoff.balanced.people,2);assert.equal(r.tradeoff.benefitFirst.people,1);assert.equal(r.tradeoff.balanced.minutes,11);assert.equal(r.tradeoff.benefitFirst.minutes,20);});
// Independent complete enumeration: compare objective vectors, not solver weights.
function vector(players,ass,policy,window,maxCap){
 const unique=new Set(ass.map(a=>a.playerId)),byId=new Map(players.map(p=>[p.playerId,p]));
 const maxHistory=Math.max(0,...players.map(p=>p.historyCount));const maxOrder=Math.max(0,...players.map(p=>p.sourceOrder));const sorted=players.map(p=>p.playerId).sort();
 const v={people:unique.size,requests:ass.length,preferences:0,rotation:[...unique].reduce((s,id)=>s+maxHistory-byId.get(id).historyCount,0),benefit:0,stability:0,scarcity:0,deterministic:0};
 for(const a of ass){const p=byId.get(a.playerId);v.preferences+=p.preferredSlots[a.dayKey].has(a.start)?1:0;v.benefit+=p.benefitMinutes[a.dayKey]||0;v.stability+=p.previousSlots?.[a.dayKey]===a.start?1:0;v.scarcity+=(window[1]-window[0])/30-[...p.availableSlots[a.dayKey]].filter(s=>s>=window[0]&&s+30<=window[1]).length;v.deterministic+=(maxOrder-p.sourceOrder)*(players.length+1)+players.length-sorted.indexOf(p.playerId);}
 return (policy==='benefit'?['benefit','people','requests','preferences','rotation','stability','scarcity','deterministic']:policy==='balanced'?['people','benefit','requests','preferences','rotation','stability','scarcity','deterministic']:['people','requests','preferences','rotation','benefit','stability','scarcity','deterministic']).map(k=>v[k]);
}
function cmp(a,b){for(let i=0;i<a.length;i++){if(a[i]!==b[i])return a[i]>b[i]?1:-1;}return 0;}
let seed=7717;function rand(n){seed=(1664525*seed+1013904223)>>>0;return seed%n;}
for(let trial=0;trial<300;trial++){
 const ps=Array.from({length:3},(_,i)=>{const avail={};for(const dk of days.slice(0,2)){const starts=[0,30].filter(()=>rand(3)>0);if(starts.length)avail[dk]=starts;}const p=member(String(i+1),avail,{construction:rand(8),research:rand(8)},rand(4));for(const dk of days)p.preferredSlots[dk]=new Set([...p.availableSlots[dk]].filter(()=>rand(2)));return p;});
 const cap=1+rand(2),policy=['team','benefit','balanced'][trial%3],slots=days.slice(0,2).flatMap(dayKey=>[0,30].map(start=>({dayKey,start})));let best=null;
 function search(i,ass,counts,pd){if(i===slots.length){const v=vector(ps,ass,policy,[0,60],cap);if(best===null||cmp(v,best)>0)best=v;return;}search(i+1,ass,counts,pd);const sl=slots[i];for(const p of ps){const pk=p.playerId+'|'+sl.dayKey;if(!p.availableSlots[sl.dayKey].has(sl.start)||(counts.get(p.playerId)||0)>=cap||pd.has(pk))continue;counts.set(p.playerId,(counts.get(p.playerId)||0)+1);pd.add(pk);ass.push({...sl,playerId:p.playerId});search(i+1,ass,counts,pd);ass.pop();pd.delete(pk);counts.set(p.playerId,counts.get(p.playerId)-1);}}
 search(0,[],new Map(),new Set());const actual=solve(ps,{maxTotalPerPlayer:cap,policy});assert.deepEqual(vector(ps,actual.assignments,policy,[0,60],cap),best,'exhaustive case '+trial);
}
// Meaningful state-size timing / hard-rule check, no arbitrary performance promise.
const large=Array.from({length:120},(_,i)=>member(String(100000+i),Object.fromEntries(days.map(d=>[d,Array.from({length:48},(_,j)=>j*30).filter(()=>rand(3)>0)])),Object.fromEntries(days.map(d=>[d,rand(600)])),rand(8)));
const start=Date.now(),largeResult=solve(large,{dayWindow:[0,1440]});
assert.equal(largeResult.assignments.length,144);assert.equal(largeResult.coveredPlayers,120);
console.log(JSON.stringify({functionalCases:cases,exhaustiveCases:300,scale:{players:120,appointments:largeResult.assignments.length,elapsedMs:Date.now()-start},status:'PASS'},null,2));
