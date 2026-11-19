"""Explicit owner preference lifecycle and deterministic structured parameter precedence."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
import json
import os
from pathlib import Path
from secrets import token_hex
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from dataquery_agent.metric_query_service import MetricQueryService, RequestError
from dataquery_agent.preference_memory import channel_request, channel_scope, mark_structured, preference_command
from test_api import AUTH, TOKEN, client

REQUEST = {'metric_id':'order_count','start_date':'2025-04-01','end_date':'2025-05-01',
           'group_by':[],'filters':{}}
PREFERENCE = {'channel':'web','version':1,'source':'explicit_chat'}


class PreferenceTests(unittest.TestCase):
    def test_explicit_commands_only_and_channel_whitelist(self):
        self.assertEqual(preference_command('保存默认看 Web 渠道。'),{'action':'set','channel':'web'})
        self.assertEqual(preference_command('更新默认渠道为mobile'),{'action':'set','channel':'mobile'})
        self.assertEqual(preference_command('保存默认看unknown渠道'),{'action':'invalid'})
        for text in ('默认看web渠道','订单量是多少','web; DELETE FROM preferences'):
            self.assertIsNone(preference_command(text))

    def test_precedence_refresh_removal_and_no_mutation(self):
        service = MetricQueryService(None)
        original = deepcopy(REQUEST)
        def apply(request=REQUEST,active=None,message='查询订单量',operation=None,preference=PREFERENCE):
            trace={}
            with channel_scope(preference,active,message,1,explicit_channel='mobile' in message):
                if operation: mark_structured(operation)
                result=channel_request(request,service,trace)
            return result,trace['routing']['channel_conditions'][-1]
        self.assertEqual(apply()[0]['filters'],{'channel':'web'})
        self.assertEqual(apply(REQUEST | {'filters':{'channel':'mobile'}})[1]['source'],'request')
        active={'request':REQUEST | {'filters':{'channel':'phone'}},'channel_origin':'request'}
        self.assertEqual(apply(active=active)[0]['filters'],{'channel':'phone'})
        self.assertEqual(apply(active=active)[1]['source'],'conversation')
        memory_active={'request':REQUEST | {'filters':{'channel':'web'}},'channel_origin':'preference'}
        refreshed,_=apply(memory_active['request'],memory_active,'那5月呢','time',dict(PREFERENCE,channel='phone',version=2))
        self.assertEqual(refreshed['filters'],{'channel':'phone'})
        removed,_=apply(memory_active['request'],memory_active,'那5月呢','time',None)
        self.assertEqual(removed['filters'],{})
        removed,_=apply(REQUEST,active,'所有渠道','remove_channel')
        self.assertEqual(removed['filters'],{})
        self.assertEqual(REQUEST,original)

    def test_unsupported_filter_and_scoped_isolation(self):
        service=MetricQueryService(None)
        trace={}
        # All current 26 metrics allow channel; simulate a future unsupported contract.
        with channel_scope(PREFERENCE,None,'查询订单量',1), patch.object(service,'validate',side_effect=RequestError('unsupported_filter','test')):
            self.assertEqual(channel_request(REQUEST,service,trace),REQUEST)
        self.assertFalse(trace['routing']['channel_conditions'][0]['applied'])
        def selected(channel):
            with channel_scope(dict(PREFERENCE,channel=channel),None,'查询订单量',1):
                return channel_request(REQUEST,service,{})['filters']['channel']
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(list(pool.map(selected,['web','mobile'])),['web','mobile'])
        self.assertEqual(channel_request(REQUEST,service,{}),REQUEST)


@unittest.skipUnless(os.getenv('CONVERSATION_POSTGRES_TESTS')=='1','Real PostgreSQL opt-in')
class PreferencePostgresTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_lifecycle_isolation_atomic_failure_and_ods(self):
        from dataquery_agent.api import create_app
        from dataquery_agent.conversation_manager import local_manager
        from dataquery_agent.conversation_store import ConversationStore, owner_hash
        from evaluate_business_planning import reference_connection
        manager,_=local_manager()
        manager.agent.supervisor=False
        manager.generate=Mock(side_effect=AssertionError('Fast Path must not call Gemini'))
        manager.agent.service.execute=Mock(wraps=manager.agent.service.execute)
        owners=[token_hex(32),token_hex(32)]
        current=[owners[0]]
        app=create_app(token=TOKEN,manager_factory=lambda:(manager,current[0]))
        records=[]
        try:
            async with client(app) as http:
                async def conversation():
                    return (await http.post('/api/conversations',headers=AUTH)).json()['conversation_id']
                async def ask(cid,question,mid=None):
                    calls=(manager.agent.service.execute.call_count,manager.generate.call_count)
                    answer=(await http.post('/api/chat',headers=AUTH,json={'conversation_id':cid,
                        'message_id':mid or str(uuid4()),'question':question})).json()
                    self.assertEqual(answer['status'],'completed',answer)
                    if preference_command(question):
                        self.assertEqual(calls,(manager.agent.service.execute.call_count,manager.generate.call_count))
                    return answer
                async def count(cid,question,channel,source):
                    answer=await ask(cid,question)
                    history=manager.get_query(current[0],answer['query_id'])
                    request=history['resolved_request']
                    self.assertEqual(request.get('filters',{}),{'channel':channel} if channel else {})
                    event=answer['trace']['routing']['channel_conditions'][0]
                    self.assertEqual(event['source'],source)
                    self.assertEqual((await http.get('/api/queries/'+answer['query_id'],headers=AUTH)).json()['trace'],answer['trace'])
                    with reference_connection() as pg:
                        pg.execute('SET TRANSACTION READ ONLY')
                        expected=pg.execute('SELECT count(*) FROM ods.orders WHERE ordered_at >= %s AND ordered_at < %s '
                            'AND (%s::text IS NULL OR channel=%s)',(request['start_date'],request['end_date'],channel,channel)).fetchone()['count']
                    self.assertEqual(answer['rows'][0]['value'],str(expected))
                    records.append({'question':question,'request':request,'channel_trace':event,
                                    'actual':answer['rows'][0]['value'],'expected_ods':expected})
                    return answer
                a=await conversation()
                command_id=str(uuid4())
                saved=await ask(a,'保存默认看Web渠道',command_id)
                self.assertEqual(manager.store.preference(owners[0])['version'],1)
                self.assertEqual(saved['trace']['routing']['preference_operation']['saved']['channel'],'web')
                stored=(await http.get('/api/queries/'+saved['query_id'],headers=AUTH)).json()
                self.assertEqual(stored['trace'],saved['trace'])
                read=await ask(a,'查看默认渠道')
                self.assertEqual(read['trace']['routing']['preference_operation']['saved']['channel'],'web')
                invalid=await http.post('/api/chat',headers=AUTH,json={'conversation_id':a,'message_id':str(uuid4()),
                    'question':'保存默认看unknown渠道'})
                self.assertEqual(invalid.json()['status'],'clarification_required')
                self.assertEqual(manager.store.preference(owners[0])['channel'],'web')
                await ask(a,'保存默认看Web渠道',command_id)  # Replay must not write twice.
                self.assertEqual(manager.store.preference(owners[0])['version'],1)
                manager.store=ConversationStore(manager.store.dsn)  # Prove persistence without process-local cache.
                b=await conversation()
                await count(b,'2025年4月订单量是多少？','web','preference')
                await count(b,'只看mobile渠道','mobile','request')
                await count(b,'那5月呢','mobile','conversation')
                await count(b,'2025年6月订单量是多少？','mobile','conversation')
                memory_cid=await conversation()
                await count(memory_cid,'2025年4月订单量是多少？','web','preference')
                await ask(a,'更新默认渠道为phone')
                self.assertEqual(manager.store.preference(owners[0])['version'],2)
                await count(await conversation(),'2025年4月订单量是多少？','phone','preference')
                await count(memory_cid,'那5月呢','phone','preference')
                # Preference changes and History commit atomically: stale state rolls both back.
                finish=manager.store.finish
                def stale(claim,*args):
                    return finish(claim | {'state':claim['state'] | {'state_version':-1}},*args)
                with patch.object(manager.store,'finish',side_effect=stale):
                    failed=(await http.post('/api/chat',headers=AUTH,json={'conversation_id':a,'message_id':str(uuid4()),
                        'question':'更新默认渠道为partner'})).json()
                self.assertEqual(failed['status'],'failed')
                self.assertEqual(manager.store.preference(owners[0])['channel'],'phone')
                current[0]=owners[1]
                self.assertIsNone(manager.store.preference(owners[1]))
                for command in ('查看默认渠道','保存默认看mobile渠道','删除默认渠道'):
                    denied=await http.post('/api/chat',headers=AUTH,json={'conversation_id':a,'message_id':str(uuid4()),'question':command})
                    self.assertEqual(denied.status_code,404)
                other=await conversation()
                await count(other,'2025年4月订单量是多少？',None,'none')
                await ask(other,'保存默认看mobile渠道')
                await ask(other,'删除默认渠道')
                self.assertEqual(manager.store.preference(owners[0])['channel'],'phone')
                self.assertIsNone(manager.store.preference(owners[1]))
                current[0]=owners[0]
                await ask(a,'删除默认渠道')
                self.assertIsNone(manager.store.preference(owners[0]))
                await count(await conversation(),'2025年4月订单量是多少？',None,'none')
                await count(memory_cid,'那6月呢',None,'none')
                self.assertEqual((await http.post('/api/chat',json={'conversation_id':a,'message_id':str(uuid4()),'question':'查看默认渠道'})).status_code,401)
                invalid=await http.post('/api/chat',headers=AUTH,json={'conversation_id':a,'message_id':str(uuid4()),'question':'查看默认渠道','owner_id':owners[1]})
                self.assertEqual(invalid.status_code,422)
                manager.generate.assert_not_called()
                await ask(a,'保存默认看Web渠道')
                # An existing approved multi-metric plan receives the same trusted condition.
                from test_business_planner_v2 import PROPOSE
                manager.generate.side_effect=None
                manager.generate.return_value=json.dumps(PROPOSE | {'metric_ids':['order_count','payment_attempt_count'],
                                                                     'time_windows':['2025年4月']})
                joint=await ask(await conversation(),'2025年4月订单量和支付尝试次数分别是多少？')
                plan=manager.get_query(owners[0],joint['query_id'])['resolved_request']
                self.assertEqual(plan['filters'],{'channel':'web'})
                self.assertTrue(all(call['request']['filters']=={'channel':'web'} for call in plan['required_tool_calls']))
                with reference_connection() as pg:
                    pg.execute('SET TRANSACTION READ ONLY')
                    expected_order=pg.execute("SELECT count(*) FROM ods.orders WHERE ordered_at >= '2025-04-01' "
                        "AND ordered_at < '2025-05-01' AND channel='web'").fetchone()['count']
                    expected=pg.execute("SELECT count(*) FROM ods.payments p JOIN ods.orders o USING(order_id) "
                        "WHERE p.paid_at >= '2025-04-01' AND p.paid_at < '2025-05-01' AND o.channel='web'").fetchone()['count']
                self.assertEqual(joint['rows'][0]['metrics']['order_count']['value'],str(expected_order))
                self.assertEqual(joint['rows'][0]['metrics']['payment_attempt_count']['value'],str(expected))
                self.assertEqual(manager.generate.call_count,1)
                records.append({'question':'2025年4月订单量和支付尝试次数分别是多少？','plan':plan,
                                'channel_trace':joint['trace']['routing']['channel_conditions'],
                                'actual':joint['rows'],'expected_ods_payment_attempts':expected,
                                'expected_ods_order_count':expected_order})
                if destination:=os.getenv('PREFERENCE_REPORT'):
                    Path(destination).write_text(json.dumps({'mode':'fixed Fast Path and real PostgreSQL via HTTP ASGI',
                        'cases':records,'real_model_calls':0,'fixed_proposal_calls':1,'metric_sql':manager.agent.service.execute.call_count,
                        'save_replay_version':1,'update_version':2,'atomic_failure_rolled_back':True,
                        'owner_isolation':True,'history_trace_equal':True},ensure_ascii=False,indent=2)+'\n')
        finally:
            with manager.store.transaction() as pg:
                for owner in owners:
                    pg.execute('DELETE FROM agent_meta.preferences WHERE owner_hash=%s',(owner_hash(owner),))
                    pg.execute('DELETE FROM agent_meta.conversations WHERE owner_hash=%s',(owner_hash(owner),))
