#!/usr/bin/env python3
"""Synthetic supplied-task arithmetic -> whole plans -> exact appointment solve."""
from pathlib import Path
import json
import sys
B=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(B/'mechanics'))
sys.path.insert(0,str(B/'advanced'))
from task_model import compile_same_work_plan
from optimizer import solve

slots=[{'id':'vp-0800','day':'build-day','office':'VP','start':'2026-11-02T08:00:00Z','end':'2026-11-02T08:30:00Z'},{'id':'vp-0830','day':'build-day','office':'VP','start':'2026-11-02T08:30:00Z','end':'2026-11-02T09:00:00Z'}]

def work(base,start):
 return {'mode':'same-work','base_work_seconds':str(base),'existing_speed_percent':'100','title_speed_percent':'10','action_state':'new','started_at':start,'office':'VP','appointments':slots,'evidence':{'source':'Synthetic supplied workload and percentages. No assertion of live state settings.'}}

def plan(plan_id,slot,task):
 fragment=compile_same_work_plan([task],general_speedup_minutes='0')
 return {'id':plan_id,'appointments':[slot],**fragment}

problem={'objective_unit':'secondsSaved','policy':'balanced','deadline_seconds':60,'slots':slots,'members':[
 {'id':'flexible-long-start','requested_days':['build-day'],'budgets':{'general_minutes':0},'history_count':0,'availability':[{'start':slots[0]['start'],'end':slots[1]['end']}],
  'plans':[{'id':'without-public-title','baseline':True,'incremental_benefit':0,'appointments':[],'baseline_work':'Same new work starts without the additional title; contribution still occurs.'},plan('start-0805','vp-0800',work(46200,'2026-11-02T08:05:00Z')),plan('start-0835','vp-0830',work(46200,'2026-11-02T08:35:00Z'))]},
 {'id':'short-window-long-start','requested_days':['build-day'],'budgets':{'general_minutes':0},'history_count':0,'availability':[{'start':slots[0]['start'],'end':slots[0]['end']}],
  'plans':[{'id':'without-public-title','baseline':True,'incremental_benefit':0,'appointments':[],'baseline_work':'Same new work starts without the additional title; contribution still occurs.'},plan('start-0805','vp-0800',work(21000,'2026-11-02T08:05:00Z'))]}
], 'assumptions':['Both new builds are ready to start. General spend is zero. All other costs and feasibility are explicitly assumed ready for this synthetic example. No game account or event is consulted.']}
result=solve(problem)
assert result['status']=='OPTIMAL',result
assert result['metrics']['people']==2
assert result['metrics']['benefit']==1600
by_member={a['member_id']:a['slot_id'] for a in result['assignments']}
assert by_member['flexible-long-start']=='vp-0830'
assert by_member['short-window-long-start']=='vp-0800'
(B/'examples/task-derived-input.json').write_text(json.dumps(problem,indent=2)+'\n')
(B/'examples/task-derived-result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'status':result['status'],'members':2,'additional_saved_seconds':1600,'general_minutes_consumed':0,'global_reroute_verified':True}))
