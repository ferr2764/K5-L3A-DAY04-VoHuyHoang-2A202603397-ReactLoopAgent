"""Kiểm tra agent với tools bonus mà không gọi LLM API."""
import json
import sys
import unittest
from unittest.mock import patch
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent import run_agent
from bonus.tools import BonusTools


def call(call_id,name,args):
    raw=json.dumps(args)
    c={'id':call_id,'name':name,'arguments':raw}
    m={'role':'assistant','content':None,'tool_calls':[{'id':call_id,'type':'function','function':{'name':name,'arguments':raw}}]}
    return {'message':m,'calls':[c],'finish':'tool_calls','usage':{}}

def final(answer='Đã kiểm tra'):
    return {'message':{'role':'assistant','content':answer},'calls':[],'finish':'stop','usage':{}}


class ScriptedProvider:
    def __init__(self,responses): self.responses=iter(responses); self.histories=[]; self.schemas=[]
    def complete(self,messages,schemas):
        self.histories.append(list(messages));self.schemas.append(schemas)
        return next(self.responses)


class BonusAgentTests(unittest.TestCase):
    def test_final_after_transient_limit_gets_retry_opportunity(self):
        t=BonusTools(scenario='rate_limit_once')
        p=ScriptedProvider([call('a','get_vercel_status',{}),final('Thiếu dữ liệu'),
                            call('b','get_vercel_status',{}),final('Đã retry: https://www.vercel-status.com')])
        result=run_agent('Kiểm tra',p,t,max_steps=5)
        self.assertEqual(result['status'],'completed')
        self.assertTrue(any(e['event']=='validation_pending_retry' for e in result['trace']))
        self.assertEqual(t.calls[('get_vercel_status','vercel')],2)

    def test_persistent_rate_limit_stops_real_tool_calls(self):
        t=BonusTools(scenario='rate_limit_always')
        p=ScriptedProvider([call(str(i),'get_vercel_status',{}) for i in range(5)] + [final('Vercel chưa xác minh được')])
        with patch('agent.time.sleep'):
            result=run_agent('Kiểm tra',p,t,max_steps=6)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(t.calls[('get_vercel_status','vercel')],3)
        self.assertEqual(json.loads(p.histories[4][-1]['content'])['error'],'RETRY_EXHAUSTED')

    def test_pagination_returns_next_page_to_model(self):
        t=BonusTools()
        args={'provider':'vercel','incident_id':'sps6n39gm4sg','offset':0,'limit':3}
        first=t.execute('get_incident_details',args)
        next_args={**args,'offset':first['next_offset']}
        p=ScriptedProvider([call('a','get_incident_details',args),call('b','get_incident_details',next_args),final('Theo timeline '+first['url'])])
        result=run_agent('Đọc thông báo upstream',p,t,max_steps=4)
        self.assertEqual(result['status'],'completed')
        second=json.loads(p.histories[2][-1]['content'])
        self.assertEqual(second['offset'],3)
        self.assertTrue(any('GitHub-connected' in u['body'] for u in second['updates']))

    def test_markdown_url_is_not_mistaken_for_new_source(self):
        from agent import extract_urls
        url='https://stspg.io/pjgptg3cfc6n'
        self.assertEqual(extract_urls('**['+url+']('+url+')**'),{url})

    def test_long_retry_after_is_deferred_without_tool_retry(self):
        from unittest.mock import Mock
        t=Mock(spec=BonusTools)
        t.schemas=BonusTools.schemas
        t.system_prompt=BonusTools.system_prompt
        t.execute.return_value={'ok':False,'error':'RATE_LIMITED','retry_after':'120'}
        p=ScriptedProvider([call('a','get_vercel_status',{}),call('b','get_vercel_status',{}),final('Chưa xác minh được')])
        with patch('agent.time.sleep') as sleep:
            result=run_agent('Kiểm tra',p,t)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(t.execute.call_count,1)
        self.assertEqual(json.loads(p.histories[2][-1]['content'])['error'],'RETRY_DEFERRED')
        sleep.assert_not_called()

    def test_bonus_schemas_and_system_prompt(self):
        t=BonusTools();p=ScriptedProvider([final()])
        self.assertEqual(run_agent('Kiểm tra',p,t)['status'],'completed')
        names={x['function']['name'] for x in p.schemas[0]}
        self.assertTrue({'get_github_status','get_cloudflare_status','get_vercel_status','get_npm_status','get_digitalocean_status'}.issubset(names))
        self.assertIn('DigitalOcean',p.histories[0][0]['content'])
    def test_429_observation_returns_to_model(self):
        t=BonusTools(scenario='rate_limit_once')
        p=ScriptedProvider([call('a','get_vercel_status',{}),call('b','get_vercel_status',{}),final()])
        result=run_agent('Kiểm tra',p,t,max_steps=5)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(json.loads(p.histories[1][-1]['content'])['error'],'RATE_LIMITED')
        self.assertEqual(json.loads(p.histories[2][-1]['content'])['provider'],'vercel')
    def test_invalid_arguments_observation_returns_to_model(self):
        t=BonusTools()
        bad={'provider':'npm','incident_id':'cc0t2r4403sy','offset':'zero'}
        good={'provider':'npm','incident_id':'cc0t2r4403sy','offset':0}
        details=t.execute('get_incident_details',good)
        p=ScriptedProvider([call('a','get_incident_details',bad),call('b','get_incident_details',good),final('Nguồn: '+details['url'])])
        result=run_agent('Kiểm tra',p,t,max_steps=5)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(json.loads(p.histories[1][-1]['content'])['error'],'INVALID_ARGUMENTS')
        self.assertTrue(json.loads(p.histories[2][-1]['content'])['ok'])

if __name__=='__main__': unittest.main()
