# Copyright (c) 2013 OpenStack Foundation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import collections
import contextlib
import datetime
import uuid

import mock
from neutron_lib import constants
from oslo_config import cfg
from oslo_utils import importutils
from oslo_utils import timeutils
from sqlalchemy import orm
import testscenarios

from neutron import context as n_context
from neutron.db import agents_db
from neutron.db import db_base_plugin_v2 as db_v2
from neutron.db import l3_agentschedulers_db
from neutron.db import l3_db
from neutron.db import l3_dvr_ha_scheduler_db
from neutron.db import l3_dvrscheduler_db
from neutron.db import l3_hamode_db
from neutron.db import l3_hascheduler_db
from neutron.extensions import l3
from neutron.extensions import l3_ext_ha_mode as l3_ha
from neutron.extensions import l3agentscheduler as l3agent
from neutron.extensions import portbindings
from neutron import manager
from neutron.scheduler import l3_agent_scheduler
from neutron.tests import base
from neutron.tests.common import helpers
from neutron.tests.unit.db import test_db_base_plugin_v2
from neutron.tests.unit.extensions import test_l3
from neutron.tests.unit import testlib_api

# the below code is required for the following reason
# (as documented in testscenarios)
"""Multiply tests depending on their 'scenarios' attribute.
   This can be assigned to 'load_tests' in any test module to make this
   automatically work across tests in the module.
"""
load_tests = testscenarios.load_tests_apply_scenarios

HOST_DVR = 'my_l3_host_dvr'
HOST_DVR_SNAT = 'my_l3_host_dvr_snat'
DEVICE_OWNER_COMPUTE = constants.DEVICE_OWNER_COMPUTE_PREFIX + 'fake'
DEVICE_OWNER_COMPUTE_NOVA = constants.DEVICE_OWNER_COMPUTE_PREFIX + 'nova'


class FakeL3Scheduler(l3_agent_scheduler.L3Scheduler):

    def schedule(self):
        pass

    def _choose_router_agent(self):
        pass

    def _choose_router_agents_for_ha(self):
        pass


class FakePortDB(object):
    def __init__(self, port_list):
        self._port_list = port_list

    def _get_query_answer(self, port_list, filters):
        answers = []
        for port in port_list:
            matched = True
            for key, search_values in filters.items():
                port_value = port.get(key, None)
                if not port_value:
                    matched = False
                    break

                if isinstance(port_value, list):
                    sub_answers = self._get_query_answer(port_value,
                                                         search_values)
                    matched = len(sub_answers) > 0
                else:
                    matched = port_value in search_values

                if not matched:
                    break

            if matched:
                answers.append(port)

        return answers

    def get_port(self, context, port_id):
        for port in self._port_list:
            if port['id'] == port_id:
                if port['tenant_id'] == context.tenant_id or context.is_admin:
                    return port
                break

        return None

    def get_ports(self, context, filters=None):
        query_filters = dict()
        if filters:
            query_filters.update(filters)

        if not context.is_admin:
            query_filters['tenant_id'] = [context.tenant_id]

        result = self._get_query_answer(self._port_list, query_filters)
        return result


class L3SchedulerBaseTestCase(base.BaseTestCase):

    def setUp(self):
        super(L3SchedulerBaseTestCase, self).setUp()
        self.scheduler = FakeL3Scheduler()
        self.plugin = mock.Mock()

    def test_auto_schedule_routers(self):
        self.plugin.get_enabled_agent_on_host.return_value = [mock.ANY]
        with mock.patch.object(self.scheduler,
                               '_get_routers_to_schedule') as gs,\
                mock.patch.object(self.scheduler,
                                  '_get_routers_can_schedule') as gr:
            result = self.scheduler.auto_schedule_routers(
                self.plugin, mock.ANY, mock.ANY, mock.ANY)
            self.assertTrue(self.plugin.get_enabled_agent_on_host.called)
            self.assertTrue(result)
            self.assertTrue(gs.called)
            self.assertTrue(gr.called)

    def test_auto_schedule_routers_no_agents(self):
        self.plugin.get_enabled_agent_on_host.return_value = None
        result = self.scheduler.auto_schedule_routers(
            self.plugin, mock.ANY, mock.ANY, mock.ANY)
        self.assertTrue(self.plugin.get_enabled_agent_on_host.called)
        self.assertFalse(result)

    def test_auto_schedule_routers_no_unscheduled_routers(self):
        type(self.plugin).supported_extension_aliases = (
            mock.PropertyMock(return_value=[]))
        with mock.patch.object(self.scheduler,
                               '_get_routers_to_schedule') as mock_routers:
            mock_routers.return_value = []
            result = self.scheduler.auto_schedule_routers(
                self.plugin, mock.ANY, mock.ANY, mock.ANY)
        self.assertTrue(self.plugin.get_enabled_agent_on_host.called)
        self.assertFalse(result)

    def test_auto_schedule_routers_no_target_routers(self):
        self.plugin.get_enabled_agent_on_host.return_value = [mock.ANY]
        with mock.patch.object(
            self.scheduler,
            '_get_routers_to_schedule') as mock_unscheduled_routers,\
                mock.patch.object(
                    self.scheduler,
                    '_get_routers_can_schedule') as mock_target_routers:
            mock_unscheduled_routers.return_value = mock.ANY
            mock_target_routers.return_value = None
            result = self.scheduler.auto_schedule_routers(
                self.plugin, mock.ANY, mock.ANY, mock.ANY)
        self.assertTrue(self.plugin.get_enabled_agent_on_host.called)
        self.assertFalse(result)

    def test__get_routers_to_schedule_with_router_ids(self):
        router_ids = ['foo_router_1', 'foo_router_2']
        expected_routers = [
            {'id': 'foo_router1'}, {'id': 'foo_router_2'}
        ]
        self.plugin.get_routers.return_value = expected_routers
        with mock.patch.object(self.scheduler,
                               '_filter_unscheduled_routers') as mock_filter:
            mock_filter.return_value = expected_routers
            unscheduled_routers = self.scheduler._get_routers_to_schedule(
                mock.ANY, self.plugin, router_ids)
        mock_filter.assert_called_once_with(
            mock.ANY, self.plugin, expected_routers)
        self.assertEqual(expected_routers, unscheduled_routers)

    def test__get_routers_to_schedule_without_router_ids(self):
        expected_routers = [
            {'id': 'foo_router1'}, {'id': 'foo_router_2'}
        ]
        with mock.patch.object(self.scheduler,
                               '_get_unscheduled_routers') as mock_get:
            mock_get.return_value = expected_routers
            unscheduled_routers = self.scheduler._get_routers_to_schedule(
                mock.ANY, self.plugin)
        mock_get.assert_called_once_with(mock.ANY, self.plugin)
        self.assertEqual(expected_routers, unscheduled_routers)

    def _test__get_routers_can_schedule(self, routers, agent, target_routers):
        self.plugin.get_l3_agent_candidates.return_value = agent
        result = self.scheduler._get_routers_can_schedule(
            mock.ANY, self.plugin, routers, mock.ANY)
        self.assertEqual(target_routers, result)

    def _test__filter_unscheduled_routers(self, routers, agents, expected):
        self.plugin.get_l3_agents_hosting_routers.return_value = agents
        unscheduled_routers = self.scheduler._filter_unscheduled_routers(
            mock.ANY, self.plugin, routers)
        self.assertEqual(expected, unscheduled_routers)

    def test__filter_unscheduled_routers_already_scheduled(self):
        self._test__filter_unscheduled_routers(
            [{'id': 'foo_router1'}, {'id': 'foo_router_2'}],
            [{'id': 'foo_agent_id'}], [])

    def test__filter_unscheduled_routers_non_scheduled(self):
        self._test__filter_unscheduled_routers(
            [{'id': 'foo_router1'}, {'id': 'foo_router_2'}],
            None, [{'id': 'foo_router1'}, {'id': 'foo_router_2'}])

    def test__get_routers_can_schedule_with_compat_agent(self):
        routers = [{'id': 'foo_router'}]
        self._test__get_routers_can_schedule(routers, mock.ANY, routers)

    def test__get_routers_can_schedule_with_no_compat_agent(self):
        routers = [{'id': 'foo_router'}]
        self._test__get_routers_can_schedule(routers, None, [])

    def test__bind_routers_centralized(self):
        routers = [{'id': 'foo_router'}]
        with mock.patch.object(self.scheduler, 'bind_router') as mock_bind:
            self.scheduler._bind_routers(mock.ANY, mock.ANY, routers, mock.ANY)
        mock_bind.assert_called_once_with(mock.ANY, 'foo_router', mock.ANY)

    def _test__bind_routers_ha(self, has_binding):
        routers = [{'id': 'foo_router', 'ha': True, 'tenant_id': '42'}]
        agent = agents_db.Agent(id='foo_agent')
        with mock.patch.object(self.scheduler,
                               '_router_has_binding',
                               return_value=has_binding) as mock_has_binding,\
                mock.patch.object(self.scheduler,
                                  'create_ha_port_and_bind') as mock_bind:
            self.scheduler._bind_routers(mock.ANY, mock.ANY, routers, agent)
            mock_has_binding.assert_called_once_with(mock.ANY, 'foo_router',
                                                     'foo_agent')
            self.assertEqual(not has_binding, mock_bind.called)

    def test__bind_routers_ha_has_binding(self):
        self._test__bind_routers_ha(has_binding=True)

    def test__bind_routers_ha_no_binding(self):
        self._test__bind_routers_ha(has_binding=False)

    def test__get_candidates_iterable_on_early_returns(self):
        plugin = mock.MagicMock()
        # non-distributed router already hosted
        plugin.get_l3_agents_hosting_routers.return_value = [{'id': 'a1'}]
        router = {'distributed': False, 'id': 'falafel'}
        iter(self.scheduler._get_candidates(plugin, mock.MagicMock(), router))
        # distributed router but no agents
        router['distributed'] = True
        plugin.get_l3_agents.return_value = []
        iter(self.scheduler._get_candidates(plugin, mock.MagicMock(), router))
        self.assertFalse(plugin.get_l3_agent_candidates.called)

    def test__get_candidates_skips_get_l3_agent_candidates_if_dvr_scheduled(
            self):
        plugin = mock.MagicMock()
        # distributed router already hosted
        plugin.get_l3_agents_hosting_routers.return_value = [{'id': 'a1'}]
        router = {'distributed': True, 'id': str(uuid.uuid4())}
        plugin.get_l3_agents.return_value = ['a1']
        self.scheduler._get_candidates(plugin, mock.MagicMock(), router)
        self.assertFalse(plugin.get_l3_agent_candidates.called)


class L3SchedulerBaseMixin(object):

    def _register_l3_agents(self, plugin=None):
        self.agent1 = helpers.register_l3_agent(
            'host_1', constants.L3_AGENT_MODE_LEGACY)
        self.agent_id1 = self.agent1.id
        self.agent2 = helpers.register_l3_agent(
            'host_2', constants.L3_AGENT_MODE_LEGACY)
        self.agent_id2 = self.agent2.id

    def _register_l3_dvr_agents(self):
        self.l3_dvr_agent = helpers.register_l3_agent(
            HOST_DVR, constants.L3_AGENT_MODE_DVR)
        self.l3_dvr_agent_id = self.l3_dvr_agent.id
        self.l3_dvr_snat_agent = helpers.register_l3_agent(
            HOST_DVR_SNAT, constants.L3_AGENT_MODE_DVR_SNAT)
        self.l3_dvr_snat_id = self.l3_dvr_snat_agent.id

    def _set_l3_agent_admin_state(self, context, agent_id, state=True):
        update = {'agent': {'admin_state_up': state}}
        self.plugin.update_agent(context, agent_id, update)

    def _set_l3_agent_dead(self, agent_id):
        update = {
            'agent': {
                'heartbeat_timestamp':
                timeutils.utcnow() - datetime.timedelta(hours=1)}}
        self.plugin.update_agent(self.adminContext, agent_id, update)

    @contextlib.contextmanager
    def router_with_ext_gw(self, name='router1', admin_state_up=True,
                           fmt=None, tenant_id=str(uuid.uuid4()),
                           external_gateway_info=None,
                           subnet=None, set_context=False,
                           **kwargs):
        router = self._make_router(fmt or self.fmt, tenant_id, name,
                                   admin_state_up, external_gateway_info,
                                   set_context, **kwargs)
        self._add_external_gateway_to_router(
            router['router']['id'],
            subnet['subnet']['network_id'])

        yield router

        self._remove_external_gateway_from_router(
            router['router']['id'], subnet['subnet']['network_id'])
        self._delete('routers', router['router']['id'])


class L3SchedulerTestBaseMixin(object):

    def _test_add_router_to_l3_agent(self,
                                     distributed=False,
                                     already_scheduled=False,
                                     external_gw=None):
        agent_id = self.agent_id1
        agent = self.agent1
        if distributed:
            self._register_l3_dvr_agents()
            agent_id = self.l3_dvr_snat_id
            agent = self.l3_dvr_snat_agent
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r1')
        router['router']['distributed'] = distributed
        router['router']['external_gateway_info'] = external_gw
        if already_scheduled:
            self._test_schedule_bind_router(agent, router)
        with mock.patch.object(self.plugin,
                               "validate_agent_router_combination"),\
                mock.patch.object(self.plugin,
                                  "create_router_to_agent_binding") as auto_s,\
                mock.patch('neutron.db.l3_db.L3_NAT_db_mixin.get_router',
                           return_value=router['router']):
            self.plugin.add_router_to_l3_agent(self.adminContext, agent_id,
                                        router['router']['id'])
            self.assertNotEqual(already_scheduled, auto_s.called)

    def test__unbind_router_removes_binding(self):
        agent_id = self.agent_id1
        agent = self.agent1
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r1')
        self._test_schedule_bind_router(agent, router)
        self.plugin._unbind_router(self.adminContext,
                            router['router']['id'],
                            agent_id)
        bindings = self.plugin._get_l3_bindings_hosting_routers(
            self.adminContext, [router['router']['id']])
        self.assertEqual(0, len(bindings))

    def _create_router_for_l3_agent_dvr_test(self,
                                             distributed=False,
                                             external_gw=None):
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r1')
        router['router']['distributed'] = distributed
        router['router']['external_gateway_info'] = external_gw
        return router

    def _prepare_l3_agent_dvr_move_exceptions(self,
                                              distributed=False,
                                              external_gw=None,
                                              agent_id=None,
                                              expected_exception=None):
        router = self._create_router_for_l3_agent_dvr_test(
            distributed=distributed, external_gw=external_gw)
        with mock.patch.object(self.plugin, "create_router_to_agent_binding"),\
                mock.patch('neutron.db.l3_db.L3_NAT_db_mixin.get_router',
                           return_value=router['router']):
            self.assertRaises(expected_exception,
                              self.plugin.add_router_to_l3_agent,
                              self.adminContext, agent_id,
                              router['router']['id'])

    def test_add_router_to_l3_agent_mismatch_error_dvr_to_legacy(self):
        self._register_l3_agents()
        self._prepare_l3_agent_dvr_move_exceptions(
            distributed=True,
            agent_id=self.agent_id1,
            expected_exception=l3agent.RouterL3AgentMismatch)

    def test_add_router_to_l3_agent_mismatch_error_legacy_to_dvr(self):
        self._register_l3_dvr_agents()
        self._prepare_l3_agent_dvr_move_exceptions(
            agent_id=self.l3_dvr_agent_id,
            expected_exception=l3agent.DVRL3CannotAssignToDvrAgent)

    def test_add_router_to_l3_agent_mismatch_error_dvr_to_dvr(self):
        self._register_l3_dvr_agents()
        self._prepare_l3_agent_dvr_move_exceptions(
            distributed=True,
            agent_id=self.l3_dvr_agent_id,
            expected_exception=l3agent.DVRL3CannotAssignToDvrAgent)

    def test_add_router_to_l3_agent_dvr_to_snat(self):
        external_gw_info = {
            "network_id": str(uuid.uuid4()),
            "enable_snat": True
        }
        self._register_l3_dvr_agents()
        agent_id = self.l3_dvr_snat_id
        router = self._create_router_for_l3_agent_dvr_test(
            distributed=True,
            external_gw=external_gw_info)
        with mock.patch.object(self.plugin,
                               "validate_agent_router_combination"),\
                mock.patch.object(
                    self.plugin,
                    "create_router_to_agent_binding") as rtr_agent_binding,\
                mock.patch('neutron.db.l3_db.L3_NAT_db_mixin.get_router',
                           return_value=router['router']):

            self.plugin.add_router_to_l3_agent(self.adminContext, agent_id,
                                        router['router']['id'])
            rtr_agent_binding.assert_called_once_with(
                self.adminContext, mock.ANY, router['router'])

    def test_add_router_to_l3_agent(self):
        self._test_add_router_to_l3_agent()

    def test_add_distributed_router_to_l3_agent(self):
        external_gw_info = {
            "network_id": str(uuid.uuid4()),
            "enable_snat": True
        }
        self._test_add_router_to_l3_agent(distributed=True,
                                          external_gw=external_gw_info)

    def test_add_router_to_l3_agent_already_scheduled(self):
        self._test_add_router_to_l3_agent(already_scheduled=True)

    def test_add_distributed_router_to_l3_agent_already_scheduled(self):
        external_gw_info = {
            "network_id": str(uuid.uuid4()),
            "enable_snat": True
        }
        self._test_add_router_to_l3_agent(distributed=True,
                                          already_scheduled=True,
                                          external_gw=external_gw_info)

    def test_remove_router_from_l3_agent_in_dvr_mode(self):
        self._register_l3_dvr_agents()
        self.assertRaises(l3agent.DVRL3CannotRemoveFromDvrAgent,
                          self.plugin.remove_router_from_l3_agent,
                          self.adminContext,
                          self.l3_dvr_agent_id,
                          mock.ANY)

    def test_remove_router_from_l3_agent_in_dvr_snat_mode(self):
        self._register_l3_dvr_agents()
        router = self._create_router_for_l3_agent_dvr_test(
            distributed=True)
        agent_id = self.l3_dvr_snat_id
        l3_notifier = mock.Mock()
        self.plugin.agent_notifiers = {constants.AGENT_TYPE_L3: l3_notifier}
        self.plugin.remove_router_from_l3_agent(self.adminContext, agent_id,
                                                router['router']['id'])
        l3_notifier.router_removed_from_agent.assert_called_once_with(
            self.adminContext, router['router']['id'],
            self.l3_dvr_snat_agent.host)

    def _prepare_schedule_dvr_tests(self):
        scheduler = l3_agent_scheduler.ChanceScheduler()
        agent = agents_db.Agent()
        agent.admin_state_up = True
        agent.heartbeat_timestamp = timeutils.utcnow()
        plugin = mock.Mock()
        plugin.get_l3_agents_hosting_routers.return_value = []
        plugin.get_l3_agents.return_value = [agent]
        plugin.get_l3_agent_candidates.return_value = [agent]

        return scheduler, agent, plugin

    def test_schedule_dvr_router_without_snatbinding_and_no_gw(self):
        scheduler, agent, plugin = self._prepare_schedule_dvr_tests()
        sync_router = {
            'id': 'foo_router_id',
            'distributed': True
        }
        plugin.get_router.return_value = sync_router
        with mock.patch.object(scheduler, 'bind_router'),\
                mock.patch.object(plugin,
                                  'get_snat_bindings',
                                  return_value=False):
            scheduler._schedule_router(
                plugin, self.adminContext, 'foo_router_id', None)
        expected_calls = [
            mock.call.get_router(mock.ANY, 'foo_router_id'),
            mock.call.get_l3_agents_hosting_routers(
                mock.ANY, ['foo_router_id'], admin_state_up=True),
            mock.call.get_l3_agents(mock.ANY, active=True),
            mock.call.get_l3_agent_candidates(mock.ANY, sync_router, [agent]),
        ]
        plugin.assert_has_calls(expected_calls)

    def test_schedule_router_distributed(self):
        scheduler, agent, plugin = self._prepare_schedule_dvr_tests()
        sync_router = {
            'id': 'foo_router_id',
            'distributed': True,
            'external_gateway_info': {
                'network_id': str(uuid.uuid4()),
                'enable_snat': True
            }
        }
        plugin.get_router.return_value = sync_router
        with mock.patch.object(scheduler, 'bind_router'):
            scheduler._schedule_router(
                plugin, self.adminContext, 'foo_router_id', None)
            expected_calls = [
                mock.call.get_router(mock.ANY, 'foo_router_id'),
                mock.call.get_l3_agents_hosting_routers(
                    mock.ANY, ['foo_router_id'], admin_state_up=True),
                mock.call.get_l3_agents(mock.ANY, active=True),
                mock.call.get_l3_agent_candidates(mock.ANY, sync_router,
                                                  [agent]),
            ]
            plugin.assert_has_calls(expected_calls)

    def _test_schedule_bind_router(self, agent, router):
        ctx = self.adminContext
        session = ctx.session
        db = l3_agentschedulers_db.RouterL3AgentBinding
        scheduler = l3_agent_scheduler.ChanceScheduler()

        rid = router['router']['id']
        scheduler.bind_router(ctx, rid, agent)
        results = (session.query(db).filter_by(router_id=rid).all())
        self.assertTrue(len(results) > 0)
        self.assertIn(agent.id, [bind.l3_agent_id for bind in results])

    def test_bind_new_router(self):
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r1')
        with mock.patch.object(l3_agent_scheduler.LOG, 'debug') as flog:
            self._test_schedule_bind_router(self.agent1, router)
            self.assertEqual(1, flog.call_count)
            args, kwargs = flog.call_args
            self.assertIn('is scheduled', args[0])

    def test_bind_absent_router(self):
        scheduler = l3_agent_scheduler.ChanceScheduler()
        # checking that bind_router() is not throwing
        # when supplied with router_id of non-existing router
        scheduler.bind_router(self.adminContext, "dummyID", self.agent1)

    def test_bind_existing_router(self):
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r2')
        self._test_schedule_bind_router(self.agent1, router)
        with mock.patch.object(l3_agent_scheduler.LOG, 'debug') as flog:
            self._test_schedule_bind_router(self.agent1, router)
            self.assertEqual(1, flog.call_count)
            args, kwargs = flog.call_args
            self.assertIn('has already been scheduled', args[0])

    def _check_get_l3_agent_candidates(
            self, router, agent_list, exp_host, count=1):
        candidates = self.plugin.get_l3_agent_candidates(self.adminContext,
                                                         router, agent_list)
        self.assertEqual(count, len(candidates))
        if count:
            self.assertEqual(exp_host, candidates[0]['host'])

    def test_get_l3_agent_candidates_legacy(self):
        self._register_l3_dvr_agents()
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r2')
        router['external_gateway_info'] = None
        router['id'] = str(uuid.uuid4())
        agent_list = [self.agent1, self.l3_dvr_agent]

        # test legacy agent_mode case: only legacy agent should be candidate
        router['distributed'] = False
        exp_host = 'host_1'
        self._check_get_l3_agent_candidates(router, agent_list, exp_host)

    def test_get_l3_agent_candidates_dvr(self):
        self._register_l3_dvr_agents()
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r2')
        router['external_gateway_info'] = None
        router['id'] = str(uuid.uuid4())
        agent_list = [self.agent1, self.l3_dvr_agent]
        # test dvr agent_mode case no candidates
        router['distributed'] = True
        self.get_subnet_ids_on_router = mock.Mock()
        self._check_dvr_serviceable_ports_on_host = mock.Mock(
            return_value=True)
        self._check_get_l3_agent_candidates(router, agent_list, None, count=0)

    def test_get_l3_agent_candidates_dvr_no_vms(self):
        self._register_l3_dvr_agents()
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r2')
        router['external_gateway_info'] = None
        router['id'] = str(uuid.uuid4())
        agent_list = [self.agent1, self.l3_dvr_agent]
        router['distributed'] = True
        # Test no VMs present case
        self.get_subnet_ids_on_router = mock.Mock()
        self._check_dvr_serviceable_ports_on_host = mock.Mock(
            return_value=False)
        self._check_get_l3_agent_candidates(
            router, agent_list, HOST_DVR, count=0)

    def test_get_l3_agent_candidates_dvr_snat(self):
        self._register_l3_dvr_agents()
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r2')
        router['external_gateway_info'] = None
        router['id'] = str(uuid.uuid4())
        router['distributed'] = True

        agent_list = [self.l3_dvr_snat_agent]
        self.get_subnet_ids_on_router = mock.Mock()
        self._check_dvr_serviceable_ports_on_host = mock.Mock(
            return_value=True)
        self._check_get_l3_agent_candidates(router, agent_list, HOST_DVR_SNAT)

    def test_get_l3_agent_candidates_dvr_snat_no_vms(self):
        self._register_l3_dvr_agents()
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r2')
        router['external_gateway_info'] = None
        router['id'] = str(uuid.uuid4())
        router['distributed'] = True

        agent_list = [self.l3_dvr_snat_agent]
        self._check_dvr_serviceable_ports_on_host = mock.Mock(
            return_value=False)
        # Test no VMs present case
        self.get_subnet_ids_on_router = mock.Mock()
        self._check_dvr_serviceable_ports_on_host.return_value = False
        self._check_get_l3_agent_candidates(
            router, agent_list, HOST_DVR_SNAT, count=1)

    def test_get_l3_agent_candidates_dvr_ha_snat_no_vms(self):
        self._register_l3_dvr_agents()
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r2')
        router['external_gateway_info'] = None
        router['id'] = str(uuid.uuid4())
        router['distributed'] = True
        router['ha'] = True

        agent_list = [self.l3_dvr_snat_agent]
        self.check_ports_exist_on_l3agent = mock.Mock(return_value=False)
        # Test no VMs present case
        self.check_ports_exist_on_l3agent.return_value = False
        self.get_subnet_ids_on_router = mock.Mock(return_value=set())
        self._check_get_l3_agent_candidates(
            router, agent_list, HOST_DVR_SNAT, count=1)

    def test_get_l3_agent_candidates_centralized(self):
        self._register_l3_dvr_agents()
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r2')
        router['external_gateway_info'] = None
        router['id'] = str(uuid.uuid4())
        # check centralized test case
        router['distributed'] = False
        agent_list = [self.l3_dvr_snat_agent]
        self._check_get_l3_agent_candidates(router, agent_list, HOST_DVR_SNAT)

    def test_get_l3_agents_hosting_routers(self):
        agent = helpers.register_l3_agent('host_6')
        router = self._make_router(self.fmt,
                                   tenant_id=str(uuid.uuid4()),
                                   name='r1')
        ctx = self.adminContext
        router_id = router['router']['id']
        self.plugin.router_scheduler.bind_router(ctx, router_id, agent)
        agents = self.plugin.get_l3_agents_hosting_routers(ctx,
                                                    [router_id])
        self.assertEqual([agent.id], [agt.id for agt in agents])
        agents = self.plugin.get_l3_agents_hosting_routers(ctx,
                                                    [router_id],
                                                    admin_state_up=True)
        self.assertEqual([agent.id], [agt.id for agt in agents])

        self._set_l3_agent_admin_state(ctx, agent.id, False)
        agents = self.plugin.get_l3_agents_hosting_routers(ctx,
                                                    [router_id])
        self.assertEqual([agent.id], [agt.id for agt in agents])
        agents = self.plugin.get_l3_agents_hosting_routers(ctx,
                                                    [router_id],
                                                    admin_state_up=True)
        self.assertEqual([], agents)


class L3SchedulerTestCaseMixin(test_l3.L3NatTestCaseMixin,
                               L3SchedulerBaseMixin,
                               L3SchedulerTestBaseMixin):

    def setUp(self):
        self.mock_rescheduling = False
        ext_mgr = test_l3.L3TestExtensionManager()
        plugin_str = ('neutron.tests.unit.extensions.test_l3.'
                      'TestL3NatIntAgentSchedulingPlugin')
        super(L3SchedulerTestCaseMixin, self).setUp(plugin=plugin_str,
                                                    ext_mgr=ext_mgr)

        self.adminContext = n_context.get_admin_context()
        self.plugin = manager.NeutronManager.get_plugin()
        self.plugin.router_scheduler = importutils.import_object(
            'neutron.scheduler.l3_agent_scheduler.ChanceScheduler'
        )
        self._register_l3_agents()


class L3AgentChanceSchedulerTestCase(L3SchedulerTestCaseMixin,
                                     test_db_base_plugin_v2.
                                     NeutronDbPluginV2TestCase):

    def test_random_scheduling(self):
        random_patch = mock.patch('random.choice')
        random_mock = random_patch.start()

        def side_effect(seq):
            return seq[0]
        random_mock.side_effect = side_effect

        with self.subnet() as subnet:
            self._set_net_external(subnet['subnet']['network_id'])
            with self.router_with_ext_gw(name='r1', subnet=subnet) as r1:
                agents = self.plugin.get_l3_agents_hosting_routers(
                    self.adminContext, [r1['router']['id']],
                    admin_state_up=True)

                self.assertEqual(1, len(agents))
                self.assertEqual(1, random_mock.call_count)

                with self.router_with_ext_gw(name='r2', subnet=subnet) as r2:
                    agents = self.plugin.get_l3_agents_hosting_routers(
                        self.adminContext, [r2['router']['id']],
                        admin_state_up=True)

                    self.assertEqual(len(agents), 1)
                    self.assertEqual(2, random_mock.call_count)

        random_patch.stop()

    def test_scheduler_auto_schedule_when_agent_added(self):
        self._set_l3_agent_admin_state(self.adminContext,
                                       self.agent_id1, False)
        self._set_l3_agent_admin_state(self.adminContext,
                                       self.agent_id2, False)

        with self.subnet() as subnet:
            self._set_net_external(subnet['subnet']['network_id'])
            with self.router_with_ext_gw(name='r1', subnet=subnet) as r1:
                agents = self.plugin.get_l3_agents_hosting_routers(
                    self.adminContext, [r1['router']['id']],
                    admin_state_up=True)
                self.assertEqual(0, len(agents))

                self._set_l3_agent_admin_state(self.adminContext,
                                               self.agent_id1, True)
                self.plugin.auto_schedule_routers(self.adminContext,
                                                  'host_1',
                                                  [r1['router']['id']])

                agents = self.plugin.get_l3_agents_hosting_routers(
                    self.adminContext, [r1['router']['id']],
                    admin_state_up=True)
                self.assertEqual('host_1', agents[0]['host'])


class L3AgentLeastRoutersSchedulerTestCase(L3SchedulerTestCaseMixin,
                                           test_db_base_plugin_v2.
                                           NeutronDbPluginV2TestCase):

    def setUp(self):
        super(L3AgentLeastRoutersSchedulerTestCase, self).setUp()
        self.plugin.router_scheduler = importutils.import_object(
            'neutron.scheduler.l3_agent_scheduler.LeastRoutersScheduler'
        )

    def test_scheduler(self):
        # disable one agent to force the scheduling to the only one.
        self._set_l3_agent_admin_state(self.adminContext,
                                       self.agent_id2, False)

        with self.subnet() as subnet:
            self._set_net_external(subnet['subnet']['network_id'])
            with self.router_with_ext_gw(name='r1', subnet=subnet) as r1:
                agents = self.plugin.get_l3_agents_hosting_routers(
                    self.adminContext, [r1['router']['id']],
                    admin_state_up=True)
                self.assertEqual(1, len(agents))

                agent_id1 = agents[0]['id']

                with self.router_with_ext_gw(name='r2', subnet=subnet) as r2:
                    agents = self.plugin.get_l3_agents_hosting_routers(
                        self.adminContext, [r2['router']['id']],
                        admin_state_up=True)
                    self.assertEqual(1, len(agents))

                    agent_id2 = agents[0]['id']

                    self.assertEqual(agent_id1, agent_id2)

                    # re-enable the second agent to see whether the next router
                    # spawned will be on this one.
                    self._set_l3_agent_admin_state(self.adminContext,
                                                   self.agent_id2, True)

                    with self.router_with_ext_gw(name='r3',
                                                 subnet=subnet) as r3:
                        agents = self.plugin.get_l3_agents_hosting_routers(
                            self.adminContext, [r3['router']['id']],
                            admin_state_up=True)
                        self.assertEqual(1, len(agents))

                        agent_id3 = agents[0]['id']

                        self.assertNotEqual(agent_id1, agent_id3)


class L3DvrScheduler(l3_db.L3_NAT_db_mixin,
                     l3_dvrscheduler_db.L3_DVRsch_db_mixin):
    pass


class L3DvrSchedulerTestCase(testlib_api.SqlTestCase):

    def setUp(self):
        self.setup_coreplugin('ml2')
        super(L3DvrSchedulerTestCase, self).setUp()
        self.adminContext = n_context.get_admin_context()
        self.dut = L3DvrScheduler()

    def test__notify_l3_agent_update_port_with_allowed_address_pairs_revert(
            self):
        port_id = str(uuid.uuid4())
        kwargs = {
            'context': self.adminContext,
            'port': {
                'id': port_id,
                'admin_state_up': False,
                portbindings.HOST_ID: 'vm-host',
                'device_id': 'vm-id',
                'allowed_address_pairs': [
                    {'ip_address': '10.1.0.201',
                     'mac_address': 'aa:bb:cc:dd:ee:ff'}],
                'device_owner': DEVICE_OWNER_COMPUTE,
            },
            'original_port': {
                'id': port_id,
                'admin_state_up': True,
                portbindings.HOST_ID: 'vm-host',
                'device_id': 'vm-id',
                'allowed_address_pairs': [
                    {'ip_address': '10.1.0.201',
                     'mac_address': 'aa:bb:cc:dd:ee:ff'}],
                'device_owner': DEVICE_OWNER_COMPUTE,
            },
        }
        port = kwargs.get('original_port')
        port_addr_pairs = port['allowed_address_pairs']
        l3plugin = mock.Mock()

        with mock.patch.object(manager.NeutronManager,
                               'get_service_plugins',
                               return_value={'L3_ROUTER_NAT': l3plugin}):
            l3_dvrscheduler_db._notify_l3_agent_port_update(
                'port', 'after_update', mock.ANY, **kwargs)
            l3plugin._get_allowed_address_pair_fixed_ips.return_value = (
                ['10.1.0.21'])
            self.assertTrue(
                l3plugin.remove_unbound_allowed_address_pair_port_binding.
                called)
            l3plugin.remove_unbound_allowed_address_pair_port_binding.\
                assert_called_once_with(
                    self.adminContext,
                    port,
                    port_addr_pairs[0])
            self.assertFalse(
                l3plugin.update_arp_entry_for_dvr_service_port.called)
            l3plugin.delete_arp_entry_for_dvr_service_port.\
                assert_called_once_with(
                    self.adminContext,
                    port,
                    fixed_ips_to_delete=mock.ANY)
            self.assertFalse(l3plugin.dvr_handle_new_service_port.called)

    def test__notify_l3_agent_update_port_with_allowed_address_pairs(self):
        port_id = str(uuid.uuid4())
        kwargs = {
            'context': self.adminContext,
            'port': {
                'id': port_id,
                portbindings.HOST_ID: 'vm-host',
                'allowed_address_pairs': [
                    {'ip_address': '10.1.0.201',
                     'mac_address': 'aa:bb:cc:dd:ee:ff'}],
                'device_id': 'vm-id',
                'device_owner': DEVICE_OWNER_COMPUTE,
                'admin_state_up': True,
            },
            'original_port': {
                'id': port_id,
                portbindings.HOST_ID: 'vm-host',
                'device_id': 'vm-id',
                'device_owner': DEVICE_OWNER_COMPUTE,
                'admin_state_up': True,
            },
        }
        port = kwargs.get('port')
        port_addr_pairs = port['allowed_address_pairs']
        l3plugin = mock.Mock()

        with mock.patch.object(manager.NeutronManager,
                               'get_service_plugins',
                               return_value={'L3_ROUTER_NAT': l3plugin}):
            l3_dvrscheduler_db._notify_l3_agent_port_update(
                'port', 'after_update', mock.ANY, **kwargs)
            self.assertTrue(
                l3plugin.update_unbound_allowed_address_pair_port_binding.
                called)
            l3plugin.update_unbound_allowed_address_pair_port_binding.\
                assert_called_once_with(
                    self.adminContext,
                    port,
                    port_addr_pairs[0])
            self.assertTrue(
                l3plugin.update_arp_entry_for_dvr_service_port.called)
            self.assertTrue(l3plugin.dvr_handle_new_service_port.called)

    def test__notify_l3_agent_update_port_no_removing_routers(self):
        port_id = 'fake-port'
        kwargs = {
            'context': self.adminContext,
            'port': None,
            'original_port': {
                'id': port_id,
                portbindings.HOST_ID: 'vm-host',
                'device_id': 'vm-id',
                'device_owner': DEVICE_OWNER_COMPUTE,
                'mac_address': '02:04:05:17:18:19'
            },
            'mac_address_updated': True
        }

        plugin = manager.NeutronManager.get_plugin()
        l3plugin = mock.Mock()
        l3plugin.supported_extension_aliases = [
            'router', constants.L3_AGENT_SCHEDULER_EXT_ALIAS,
            constants.L3_DISTRIBUTED_EXT_ALIAS
        ]

        with mock.patch.object(manager.NeutronManager,
                               'get_service_plugins',
                               return_value={'L3_ROUTER_NAT': l3plugin}):
            l3_dvrscheduler_db._notify_l3_agent_port_update(
                'port', 'after_update', plugin, **kwargs)
            self.assertFalse(
                l3plugin.update_arp_entry_for_dvr_service_port.called)
            self.assertFalse(
                l3plugin.dvr_handle_new_service_port.called)
            self.assertFalse(l3plugin.remove_router_from_l3_agent.called)
            self.assertFalse(l3plugin.get_dvr_routers_to_remove.called)

    def test__notify_l3_agent_new_port_action(self):
        kwargs = {
            'context': self.adminContext,
            'original_port': None,
            'port': {
                'device_owner': DEVICE_OWNER_COMPUTE,
            },
        }
        l3plugin = mock.Mock()
        with mock.patch.object(manager.NeutronManager,
                               'get_service_plugins',
                               return_value={'L3_ROUTER_NAT': l3plugin}):
            l3_dvrscheduler_db._notify_l3_agent_new_port(
                'port', 'after_create', mock.ANY, **kwargs)
            l3plugin.update_arp_entry_for_dvr_service_port.\
                assert_called_once_with(
                    self.adminContext, kwargs.get('port'))
            l3plugin.dvr_handle_new_service_port.assert_called_once_with(
                self.adminContext, kwargs.get('port'))

    def test__notify_l3_agent_new_port_no_action(self):
        kwargs = {
            'context': self.adminContext,
            'original_port': None,
            'port': {
                'device_owner': constants.DEVICE_OWNER_NETWORK_PREFIX + 'None',
            }
        }
        l3plugin = mock.Mock()
        with mock.patch.object(manager.NeutronManager,
                               'get_service_plugins',
                               return_value={'L3_ROUTER_NAT': l3plugin}):
            l3_dvrscheduler_db._notify_l3_agent_new_port(
                'port', 'after_create', mock.ANY, **kwargs)
            self.assertFalse(
                l3plugin.update_arp_entry_for_dvr_service_port.called)
            self.assertFalse(
                l3plugin.dvr_handle_new_service_port.called)

    def test__notify_l3_agent_update_port_with_migration_port_profile(self):
        kwargs = {
            'context': self.adminContext,
            'original_port': {
                portbindings.HOST_ID: 'vm-host',
                'device_owner': DEVICE_OWNER_COMPUTE,
            },
            'port': {
                portbindings.HOST_ID: 'vm-host',
                'device_owner': DEVICE_OWNER_COMPUTE,
                portbindings.PROFILE: {'migrating_to': 'vm-host2'},
            },
        }
        l3plugin = mock.Mock()
        with mock.patch.object(manager.NeutronManager,
                               'get_service_plugins',
                               return_value={'L3_ROUTER_NAT': l3plugin}):
            l3_dvrscheduler_db._notify_l3_agent_port_update(
                'port', 'after_update', mock.ANY, **kwargs)
            l3plugin.dvr_handle_new_service_port.assert_called_once_with(
                self.adminContext, kwargs.get('port'), dest_host='vm-host2')
            l3plugin.update_arp_entry_for_dvr_service_port.\
                assert_called_once_with(
                    self.adminContext, kwargs.get('port'))

    def test__notify_l3_agent_update_port_no_action(self):
        kwargs = {
            'context': self.adminContext,
            'original_port': {
                portbindings.HOST_ID: 'vm-host',
                'device_owner': DEVICE_OWNER_COMPUTE,
            },
            'port': {
                portbindings.HOST_ID: 'vm-host',
                'device_owner': DEVICE_OWNER_COMPUTE,
            },
        }
        l3plugin = mock.Mock()
        with mock.patch.object(manager.NeutronManager,
                               'get_service_plugins',
                               return_value={'L3_ROUTER_NAT': l3plugin}):
            l3_dvrscheduler_db._notify_l3_agent_port_update(
                'port', 'after_update', mock.ANY, **kwargs)

            self.assertFalse(
                l3plugin.update_arp_entry_for_dvr_service_port.called)
            self.assertFalse(
                l3plugin.dvr_handle_new_service_port.called)
            self.assertFalse(l3plugin.remove_router_from_l3_agent.called)
            self.assertFalse(l3plugin.get_dvr_routers_to_remove.called)

    def test__notify_l3_agent_update_port_with_mac_address_update(self):
        kwargs = {
            'context': self.adminContext,
            'original_port': {
                portbindings.HOST_ID: 'vm-host',
                'mac_address': '02:04:05:17:18:19'
            },
            'port': {
                portbindings.HOST_ID: 'vm-host',
                'mac_address': '02:04:05:17:18:29'
            },
            'mac_address_updated': True
        }
        l3plugin = mock.Mock()
        with mock.patch.object(manager.NeutronManager,
                               'get_service_plugins',
                               return_value={'L3_ROUTER_NAT': l3plugin}):
            l3_dvrscheduler_db._notify_l3_agent_port_update(
                'port', 'after_update', mock.ANY, **kwargs)

            l3plugin.update_arp_entry_for_dvr_service_port.\
                assert_called_once_with(
                    self.adminContext, kwargs.get('port'))
            self.assertFalse(l3plugin.dvr_handle_new_service_port.called)

    def test__notify_l3_agent_port_binding_change(self):
        self._test__notify_l3_agent_port_binding_change()

    def test__notify_l3_agent_port_binding_change_removed_routers(self):
        router_to_remove = [{'agent_id': 'foo_agent',
                             'router_id': 'foo_id',
                             'host': 'vm-host1'}]
        self._test__notify_l3_agent_port_binding_change(router_to_remove)

    def test__notify_l3_agent_port_binding_change_removed_routers_fip(self):
        fip = {'router_id': 'router_id'}
        router_to_remove = [{'agent_id': 'foo_agent',
                             'router_id': 'foo_id',
                             'host': 'vm-host1'}]
        self._test__notify_l3_agent_port_binding_change(router_to_remove, fip)

    def test__notify_l3_agent_port_binding_change_with_fip(self):
        fip = {'router_id': 'router_id'}
        self._test__notify_l3_agent_port_binding_change(None, fip)

    def _test__notify_l3_agent_port_binding_change(self,
                                                   routers_to_remove=None,
                                                   fip=None):
        source_host = 'vm-host1'
        kwargs = {
            'context': self.adminContext,
            'original_port': {
                'id': str(uuid.uuid4()),
                portbindings.HOST_ID: source_host,
                'device_owner': DEVICE_OWNER_COMPUTE,
            },
            'port': {
                portbindings.HOST_ID: 'vm-host2',
                'device_owner': DEVICE_OWNER_COMPUTE,
            },
        }
        l3plugin = mock.Mock()
        with mock.patch.object(manager.NeutronManager,
                               'get_service_plugins',
                               return_value={'L3_ROUTER_NAT': l3plugin}),\
                mock.patch.object(l3plugin, 'get_dvr_routers_to_remove',
                                  return_value=routers_to_remove),\
                mock.patch.object(l3plugin, '_get_floatingip_on_port',
                                  return_value=fip):
            l3_dvrscheduler_db._notify_l3_agent_port_update(
                'port', 'after_update', mock.ANY, **kwargs)
            if routers_to_remove:
                (l3plugin.l3_rpc_notifier.router_removed_from_agent.
                 assert_called_once_with(mock.ANY, 'foo_id', source_host))
                self.assertEqual(
                    1,
                    l3plugin.delete_arp_entry_for_dvr_service_port.call_count)
            if fip and not routers_to_remove:
                (l3plugin.l3_rpc_notifier.routers_updated_on_host.
                 assert_called_once_with(mock.ANY, ['router_id'], source_host))
            self.assertEqual(
                1, l3plugin.update_arp_entry_for_dvr_service_port.call_count)
            l3plugin.dvr_handle_new_service_port.assert_called_once_with(
                self.adminContext, kwargs.get('port'), dest_host=None)

    def test__notify_l3_agent_update_port_removing_routers(self):
        port_id = 'fake-port'
        source_host = 'vm-host'
        kwargs = {
            'context': self.adminContext,
            'port': {
                'id': port_id,
                portbindings.HOST_ID: None,
                'device_id': '',
                'device_owner': ''
            },
            'mac_address_updated': False,
            'original_port': {
                'id': port_id,
                portbindings.HOST_ID: source_host,
                'device_id': 'vm-id',
                'device_owner': DEVICE_OWNER_COMPUTE
            }
        }

        plugin = manager.NeutronManager.get_plugin()
        l3plugin = mock.Mock()
        l3plugin.supported_extension_aliases = [
            'router', constants.L3_AGENT_SCHEDULER_EXT_ALIAS,
            constants.L3_DISTRIBUTED_EXT_ALIAS
        ]
        with mock.patch.object(manager.NeutronManager,
                               'get_service_plugins',
                               return_value={'L3_ROUTER_NAT': l3plugin}),\
                mock.patch.object(l3plugin, 'get_dvr_routers_to_remove',
                                  return_value=[{'agent_id': 'foo_agent',
                                                 'router_id': 'foo_id',
                                                 'host': source_host}]),\
                mock.patch.object(l3plugin, '_get_floatingip_on_port',
                                  return_value=None):
            l3_dvrscheduler_db._notify_l3_agent_port_update(
                'port', 'after_update', plugin, **kwargs)

            self.assertEqual(
                1, l3plugin.delete_arp_entry_for_dvr_service_port.call_count)
            l3plugin.delete_arp_entry_for_dvr_service_port.\
                assert_called_once_with(
                    self.adminContext, mock.ANY)

            self.assertFalse(
                l3plugin.dvr_handle_new_service_port.called)
            (l3plugin.l3_rpc_notifier.router_removed_from_agent.
             assert_called_once_with(mock.ANY, 'foo_id', source_host))

    def test__notify_port_delete(self):
        plugin = manager.NeutronManager.get_plugin()
        l3plugin = mock.Mock()
        l3plugin.supported_extension_aliases = [
            'router', constants.L3_AGENT_SCHEDULER_EXT_ALIAS,
            constants.L3_DISTRIBUTED_EXT_ALIAS
        ]
        port = {
                'id': str(uuid.uuid4()),
                'device_id': 'abcd',
                'device_owner': DEVICE_OWNER_COMPUTE_NOVA,
                portbindings.HOST_ID: 'host1',
        }

        with mock.patch.object(manager.NeutronManager,
                               'get_service_plugins',
                               return_value={'L3_ROUTER_NAT': l3plugin}):
            kwargs = {
                'context': self.adminContext,
                'port': port,
                'removed_routers': [
                    {'agent_id': 'foo_agent', 'router_id': 'foo_id'},
                ],
            }
            removed_routers = [{'agent_id': 'foo_agent',
                                'router_id': 'foo_id',
                                'host': 'foo_host'}]
            l3plugin.get_dvr_routers_to_remove.return_value = removed_routers
            l3_dvrscheduler_db._notify_port_delete(
                'port', 'after_delete', plugin, **kwargs)
            l3plugin.delete_arp_entry_for_dvr_service_port.\
                assert_called_once_with(
                    self.adminContext, mock.ANY)
            (l3plugin.l3_rpc_notifier.router_removed_from_agent.
             assert_called_once_with(mock.ANY, 'foo_id', 'foo_host'))

    def test_dvr_handle_new_service_port(self):
        port = {
                'id': 'port1',
                'device_id': 'abcd',
                'device_owner': DEVICE_OWNER_COMPUTE_NOVA,
                portbindings.HOST_ID: 'host1',
                'fixed_ips': [
                    {
                        'subnet_id': '80947d4a-fbc8-484b-9f92-623a6bfcf3e0',
                        'ip_address': '10.10.10.3'
                    }
                ]
        }
        dvr_ports = [
            {
                'id': 'dvr_port1',
                'device_id': 'r1',
                'device_owner': constants.DEVICE_OWNER_DVR_INTERFACE,
                'fixed_ips': [
                    {
                        'subnet_id': '80947d4a-fbc8-484b-9f92-623a6bfcf3e0',
                        'ip_address': '10.10.10.1'
                    }
                ]
            },
            {
                'id': 'dvr_port2',
                'device_id': 'r2',
                'device_owner': constants.DEVICE_OWNER_DVR_INTERFACE,
                'fixed_ips': [
                    {
                        'subnet_id': '80947d4a-fbc8-484b-9f92-623a6bfcf3e0',
                        'ip_address': '10.10.10.123'
                    }
                ]
            }
        ]
        agent_on_host = {'id': 'agent1'}

        with mock.patch(
            'neutron.db.db_base_plugin_v2.NeutronDbPluginV2' '.get_ports',
            return_value=dvr_ports),\
                mock.patch('neutron.api.rpc.agentnotifiers.l3_rpc_agent_api'
                           '.L3AgentNotifyAPI'),\
                mock.patch.object(
                        self.dut, 'get_l3_agents',
                        return_value=[agent_on_host]) as get_l3_agents:
            self.dut.dvr_handle_new_service_port(
                self.adminContext, port)

            get_l3_agents.assert_called_once_with(
                self.adminContext,
                filters={'host': [port[portbindings.HOST_ID]]})
            (self.dut.l3_rpc_notifier.routers_updated_on_host.
                assert_called_once_with(
                    self.adminContext, {'r1', 'r2'}, 'host1'))
            self.assertFalse(self.dut.l3_rpc_notifier.routers_updated.called)

    def test_get_dvr_routers_by_subnet_ids(self):
        subnet_id = '80947d4a-fbc8-484b-9f92-623a6bfcf3e0'
        dvr_port = {
                'id': 'dvr_port1',
                'device_id': 'r1',
                'device_owner': constants.DEVICE_OWNER_DVR_INTERFACE,
                'fixed_ips': [
                    {
                        'subnet_id': subnet_id,
                        'ip_address': '10.10.10.1'
                    }
                ]
        }
        r1 = {
              'id': 'r1',
              'distributed': True,
        }

        with mock.patch(
            'neutron.db.db_base_plugin_v2.NeutronDbPluginV2' '.get_port',
            return_value=dvr_port),\
                mock.patch('neutron.db.db_base_plugin_v2.NeutronDbPluginV2'
                           '.get_ports', return_value=[dvr_port]):
            router_id = self.dut.get_dvr_routers_by_subnet_ids(
                self.adminContext, [subnet_id])
            self.assertEqual(r1['id'], router_id.pop())

    def test_get_subnet_ids_on_router(self):
        dvr_port = {
                'id': 'dvr_port1',
                'device_id': 'r1',
                'device_owner': constants.DEVICE_OWNER_DVR_INTERFACE,
                'fixed_ips': [
                    {
                        'subnet_id': '80947d4a-fbc8-484b-9f92-623a6bfcf3e0',
                        'ip_address': '10.10.10.1'
                    }
                ]
        }
        r1 = {
              'id': 'r1',
              'distributed': True,
        }

        with mock.patch(
            'neutron.db.db_base_plugin_v2.NeutronDbPluginV2' '.get_ports',
            return_value=[dvr_port]):
            sub_ids = self.dut.get_subnet_ids_on_router(self.adminContext,
                                                        r1['id'])
            self.assertEqual(sub_ids.pop(),
                            dvr_port.get('fixed_ips').pop(0).get('subnet_id'))

    def test_get_subnet_ids_on_router_no_subnet(self):
        dvr_port = {
                'id': 'dvr_port1',
                'device_id': 'r1',
                'device_owner': constants.DEVICE_OWNER_DVR_INTERFACE,
                'fixed_ips': []
        }
        r1 = {
              'id': 'r1',
              'distributed': True,
        }
        with mock.patch.object(db_v2.NeutronDbPluginV2, 'get_ports',
                               return_value=[dvr_port]):
            sub_ids = self.dut.get_subnet_ids_on_router(self.adminContext,
                                                        r1['id'])
            self.assertEqual(0, len(sub_ids))

    def _prepare_schedule_snat_tests(self):
        agent = agents_db.Agent()
        agent.admin_state_up = True
        agent.heartbeat_timestamp = timeutils.utcnow()
        router = {
            'id': 'foo_router_id',
            'distributed': True,
            'external_gateway_info': {
                'network_id': str(uuid.uuid4()),
                'enable_snat': True
            }
        }
        return agent, router


class L3HAPlugin(db_v2.NeutronDbPluginV2,
                 l3_hamode_db.L3_HA_NAT_db_mixin,
                 l3_hascheduler_db.L3_HA_scheduler_db_mixin):
    supported_extension_aliases = ["l3-ha", "router_availability_zone"]


class L3HATestCaseMixin(testlib_api.SqlTestCase,
                        L3SchedulerBaseMixin):

    def setUp(self):
        super(L3HATestCaseMixin, self).setUp()

        self.adminContext = n_context.get_admin_context()
        mock.patch('neutron.common.rpc.get_client').start()
        self.plugin = L3HAPlugin()

        self.setup_coreplugin('ml2')
        cfg.CONF.set_override('service_plugins',
                              ['neutron.services.l3_router.'
                              'l3_router_plugin.L3RouterPlugin'])

        cfg.CONF.set_override('max_l3_agents_per_router', 0)
        self.plugin.router_scheduler = importutils.import_object(
            'neutron.scheduler.l3_agent_scheduler.ChanceScheduler'
        )

        self._register_l3_agents()

    def _create_ha_router(self, ha=True, tenant_id='tenant1', az_hints=None):
        self.adminContext.tenant_id = tenant_id
        router = {'name': 'router1', 'admin_state_up': True,
                  'tenant_id': tenant_id}
        if ha is not None:
            router['ha'] = ha
        if az_hints is None:
            az_hints = []
        router['availability_zone_hints'] = az_hints
        return self.plugin.create_router(self.adminContext,
                                         {'router': router})

    def test_create_ha_port_and_bind_catch_integrity_error(self):
        router = self._create_ha_router(tenant_id='foo_tenant')
        agent = {'id': 'foo_agent'}

        orig_fn = orm.Session.add

        def db_ref_err_for_add_haportbinding(s, instance):
            if instance.__class__.__name__ == 'L3HARouterAgentPortBinding':
                instance.router_id = 'nonexistent_router'
            return orig_fn(s, instance)

        with mock.patch.object(self.plugin.router_scheduler,
                               'bind_router') as bind_router:
            with mock.patch.object(
                    orm.Session, 'add',
                    side_effect=db_ref_err_for_add_haportbinding,
                    autospec=True):
                self.plugin.router_scheduler.create_ha_port_and_bind(
                    self.plugin, self.adminContext,
                    router['id'], router['tenant_id'], agent)
                self.assertFalse(bind_router.called)

    def test_create_ha_port_and_bind_catch_router_not_found(self):
        router = self._create_ha_router(tenant_id='foo_tenant')
        agent = {'id': 'foo_agent'}

        with mock.patch.object(self.plugin.router_scheduler,
                               'bind_router') as bind_router:
            with mock.patch.object(
                    self.plugin, 'add_ha_port',
                    side_effect=l3.RouterNotFound(router_id='foo_router')):
                self.plugin.router_scheduler.create_ha_port_and_bind(
                    self.plugin, self.adminContext,
                    router['id'], router['tenant_id'], agent)
                self.assertFalse(bind_router.called)


class L3_HA_scheduler_db_mixinTestCase(L3HATestCaseMixin):

    def _register_l3_agents(self, plugin=None):
        super(L3_HA_scheduler_db_mixinTestCase,
              self)._register_l3_agents(plugin=plugin)

        self.agent3 = helpers.register_l3_agent(host='host_3')
        self.agent_id3 = self.agent3.id

        self.agent4 = helpers.register_l3_agent(host='host_4')
        self.agent_id4 = self.agent4.id

    def test_get_ha_routers_l3_agents_count(self):
        router1 = self._create_ha_router()
        router2 = self._create_ha_router()
        router3 = self._create_ha_router(ha=False)
        result = self.plugin.get_ha_routers_l3_agents_count(self.adminContext)

        self.assertEqual(2, len(result))
        check_result = [(router['id'], agents) for router, agents in result]
        self.assertIn((router1['id'], 4), check_result)
        self.assertIn((router2['id'], 4), check_result)
        self.assertNotIn((router3['id'], mock.ANY), check_result)

    def test_get_ordered_l3_agents_by_num_routers(self):
        # Mock scheduling so that the test can control it explicitly
        mock.patch.object(l3_hamode_db.L3_HA_NAT_db_mixin,
                          '_notify_ha_interfaces_updated').start()
        with mock.patch.object(self.plugin, 'schedule_router'):
            router1 = self._create_ha_router()
            router2 = self._create_ha_router()
            router3 = self._create_ha_router(ha=False)
            router4 = self._create_ha_router(ha=False)

        # Agent 1 will host 0 routers, agent 2 will host 1, agent 3 will
        # host 2, and agent 4 will host 3.
        self.plugin.schedule_router(self.adminContext, router1['id'],
                                    candidates=[self.agent2, self.agent4])
        self.plugin.schedule_router(self.adminContext, router2['id'],
                                    candidates=[self.agent3, self.agent4])
        self.plugin.schedule_router(self.adminContext, router3['id'],
                                    candidates=[self.agent3])
        self.plugin.schedule_router(self.adminContext, router4['id'],
                                    candidates=[self.agent4])

        agent_ids = [self.agent_id1, self.agent_id2, self.agent_id3,
                     self.agent_id4]
        result = self.plugin.get_l3_agents_ordered_by_num_routers(
            self.adminContext, agent_ids)

        self.assertEqual(agent_ids, [record['id'] for record in result])


class L3AgentSchedulerDbMixinTestCase(L3HATestCaseMixin):

    def _setup_ha_router(self):
        router = self._create_ha_router()
        agents = self._get_agents_scheduled_for_router(router)
        return router, agents

    def test_reschedule_ha_routers_from_down_agents(self):
        agents = self._setup_ha_router()[1]
        self.assertEqual(2, len(agents))
        self._set_l3_agent_dead(self.agent_id1)
        with mock.patch.object(self.plugin, 'reschedule_router') as reschedule:
            self.plugin.reschedule_routers_from_down_agents()
            self.assertFalse(reschedule.called)

    def test_list_l3_agents_hosting_ha_router(self):
        router = self._create_ha_router()
        agents = self.plugin.list_l3_agents_hosting_router(
            self.adminContext, router['id'])['agents']
        for agent in agents:
            self.assertEqual('standby', agent['ha_state'])

        self.plugin.update_routers_states(
            self.adminContext, {router['id']: 'active'}, self.agent1.host)
        agents = self.plugin.list_l3_agents_hosting_router(
            self.adminContext, router['id'])['agents']
        for agent in agents:
            expected_state = ('active' if agent['host'] == self.agent1.host
                              else 'standby')
            self.assertEqual(expected_state, agent['ha_state'])

    def test_list_l3_agents_hosting_legacy_router(self):
        router = self._create_ha_router(ha=False)
        self.plugin.schedule_router(self.adminContext, router['id'])

        agent = self.plugin.list_l3_agents_hosting_router(
            self.adminContext, router['id'])['agents'][0]
        self.assertIsNone(agent['ha_state'])

    def test_get_agents_dict_for_router_unscheduled_returns_empty_list(self):
        self.assertEqual({'agents': []},
                         self.plugin._get_agents_dict_for_router([]))

    def test_manual_add_ha_router_to_agent(self):
        cfg.CONF.set_override('max_l3_agents_per_router', 2)
        router, agents = self._setup_ha_router()
        self.assertEqual(2, len(agents))
        agent = helpers.register_l3_agent(host='myhost_3')
        # We allow to exceed max l3 agents per router via manual scheduling
        self.plugin.add_router_to_l3_agent(
            self.adminContext, agent.id, router['id'])
        agents = self._get_agents_scheduled_for_router(router)
        self.assertIn(agent.id, [_agent.id for _agent in agents])
        self.assertEqual(3, len(agents))

    def test_manual_remove_ha_router_from_agent(self):
        router, agents = self._setup_ha_router()
        self.assertEqual(2, len(agents))
        agent = agents.pop()
        # Remove router from agent and make sure it is removed
        self.plugin.remove_router_from_l3_agent(
            self.adminContext, agent.id, router['id'])
        agents = self._get_agents_scheduled_for_router(router)
        self.assertEqual(1, len(agents))
        self.assertNotIn(agent.id, [_agent.id for _agent in agents])

    def test_manual_remove_ha_router_from_all_agents(self):
        router, agents = self._setup_ha_router()
        self.assertEqual(2, len(agents))
        agent = agents.pop()
        self.plugin.remove_router_from_l3_agent(
            self.adminContext, agent.id, router['id'])
        agent = agents.pop()
        self.plugin.remove_router_from_l3_agent(
            self.adminContext, agent.id, router['id'])
        agents = self._get_agents_scheduled_for_router(router)
        self.assertEqual(0, len(agents))

    def _get_agents_scheduled_for_router(self, router):
        return self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [router['id']],
            admin_state_up=True)

    def test_delete_ha_interfaces_from_agent(self):
        router, agents = self._setup_ha_router()
        agent = agents.pop()
        self.plugin.remove_router_from_l3_agent(
            self.adminContext, agent.id, router['id'])
        session = self.adminContext.session
        db = l3_hamode_db.L3HARouterAgentPortBinding
        results = session.query(db).filter_by(
            router_id=router['id'])
        results = [binding.l3_agent_id for binding in results.all()]
        self.assertNotIn(agent.id, results)

    def test_add_ha_interface_to_l3_agent(self):
        agent = self.plugin.get_agents_db(self.adminContext)[0]
        router = self._create_ha_router()
        self.plugin.add_router_to_l3_agent(self.adminContext, agent.id,
                                           router['id'])
        # Verify agent has HA interface
        ha_ports = self.plugin.get_ha_router_port_bindings(self.adminContext,
                                                           [router['id']])
        self.assertIn(agent.id, [ha_port.l3_agent_id for ha_port in ha_ports])


class L3HAChanceSchedulerTestCase(L3HATestCaseMixin):

    def test_scheduler_with_ha_enabled(self):
        router = self._create_ha_router()
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [router['id']],
            admin_state_up=True)
        self.assertEqual(2, len(agents))

        for agent in agents:
            sync_data = self.plugin.get_ha_sync_data_for_host(
                self.adminContext, router_ids=[router['id']],
                host=agent.host, agent=agent)
            self.assertEqual(1, len(sync_data))
            interface = sync_data[0][constants.HA_INTERFACE_KEY]
            self.assertIsNotNone(interface)

    def test_auto_schedule(self):
        # Mock scheduling so that the test can control it explicitly
        mock.patch.object(l3_hamode_db.L3_HA_NAT_db_mixin,
                          '_notify_ha_interfaces_updated').start()

        router = self._create_ha_router()
        self.plugin.auto_schedule_routers(
            self.adminContext, self.agent1.host, None)
        self.plugin.auto_schedule_routers(
            self.adminContext, self.agent2.host, None)
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [router['id']])
        self.assertEqual(2, len(agents))

    def test_auto_schedule_specific_router_when_agent_added(self):
        self._auto_schedule_when_agent_added(True)

    def test_auto_schedule_all_routers_when_agent_added(self):
        self._auto_schedule_when_agent_added(False)

    def test_auto_schedule_ha_router_when_incompatible_agent_exist(self):
        handle_internal_only_routers_agent = helpers.register_l3_agent(
            'host_3', constants.L3_AGENT_MODE_LEGACY, internal_only=False)
        router = self._create_ha_router()

        self.plugin.auto_schedule_routers(
            self.adminContext, handle_internal_only_routers_agent.host, [])
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [router['id']],
            admin_state_up=True)
        agent_ids = [agent['id'] for agent in agents]
        self.assertEqual(2, len(agents))
        self.assertNotIn(handle_internal_only_routers_agent.id, agent_ids)

    def test_auto_schedule_ha_router_when_dvr_agent_exist(self):
        dvr_agent = helpers.register_l3_agent(
            HOST_DVR, constants.L3_AGENT_MODE_DVR)
        router = self._create_ha_router()

        self.plugin.auto_schedule_routers(self.adminContext, dvr_agent.host,
                                          [])
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [router['id']],
            admin_state_up=True)
        agent_ids = [agent['id'] for agent in agents]
        self.assertEqual(2, len(agents))
        self.assertNotIn(dvr_agent.id, agent_ids)

    def _auto_schedule_when_agent_added(self, specific_router):
        router = self._create_ha_router()
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [router['id']],
            admin_state_up=True)
        self.assertEqual(2, len(agents))
        agent_ids = [agent['id'] for agent in agents]
        self.assertIn(self.agent_id1, agent_ids)
        self.assertIn(self.agent_id2, agent_ids)

        agent = helpers.register_l3_agent(host='host_3')
        self.agent_id3 = agent.id
        routers_to_auto_schedule = [router['id']] if specific_router else []
        self.plugin.auto_schedule_routers(self.adminContext,
                                          'host_3',
                                          routers_to_auto_schedule)

        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [router['id']],
            admin_state_up=True)
        self.assertEqual(3, len(agents))

        # Simulate agent restart to make sure we don't try to re-bind
        self.plugin.auto_schedule_routers(self.adminContext,
                                          'host_3',
                                          routers_to_auto_schedule)

    def test_scheduler_with_ha_enabled_not_enough_agent(self):
        r1 = self._create_ha_router()
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [r1['id']],
            admin_state_up=True)
        self.assertEqual(2, len(agents))

        self._set_l3_agent_admin_state(self.adminContext,
                                       self.agent_id2, False)
        self.assertRaises(
            l3_ha.HANotEnoughAvailableAgents, self._create_ha_router)


class L3HALeastRoutersSchedulerTestCase(L3HATestCaseMixin):

    def _register_l3_agents(self, plugin=None):
        super(L3HALeastRoutersSchedulerTestCase,
              self)._register_l3_agents(plugin=plugin)

        agent = helpers.register_l3_agent(host='host_3')
        self.agent_id3 = agent.id

        agent = helpers.register_l3_agent(host='host_4')
        self.agent_id4 = agent.id

    def setUp(self):
        super(L3HALeastRoutersSchedulerTestCase, self).setUp()
        self.plugin.router_scheduler = importutils.import_object(
            'neutron.scheduler.l3_agent_scheduler.LeastRoutersScheduler'
        )

    def test_scheduler(self):
        cfg.CONF.set_override('max_l3_agents_per_router', 2)

        # disable the third agent to be sure that the router will
        # be scheduled of the two firsts
        self._set_l3_agent_admin_state(self.adminContext,
                                       self.agent_id3, False)
        self._set_l3_agent_admin_state(self.adminContext,
                                       self.agent_id4, False)

        r1 = self._create_ha_router()
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [r1['id']],
            admin_state_up=True)
        self.assertEqual(2, len(agents))
        agent_ids = [agent['id'] for agent in agents]
        self.assertIn(self.agent_id1, agent_ids)
        self.assertIn(self.agent_id2, agent_ids)

        self._set_l3_agent_admin_state(self.adminContext,
                                       self.agent_id3, True)
        self._set_l3_agent_admin_state(self.adminContext,
                                       self.agent_id4, True)

        r2 = self._create_ha_router()
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [r2['id']],
            admin_state_up=True)
        self.assertEqual(2, len(agents))
        agent_ids = [agent['id'] for agent in agents]
        self.assertIn(self.agent_id3, agent_ids)
        self.assertIn(self.agent_id4, agent_ids)


class TestGetL3AgentsWithAgentModeFilter(testlib_api.SqlTestCase,
                                         L3SchedulerBaseMixin):
    """Test cases to test get_l3_agents.

    This class tests the L3AgentSchedulerDbMixin.get_l3_agents()
    for the 'agent_mode' filter with various values.

    5 l3 agents are registered in the order - legacy, dvr_snat, dvr, fake_mode
    and legacy
    """

    scenarios = [
        ('no filter',
            dict(agent_modes=[],
                 expected_agent_modes=['legacy', 'dvr_snat', 'dvr',
                                       'fake_mode', 'legacy'])),

        ('legacy',
            dict(agent_modes=['legacy'],
                 expected_agent_modes=['legacy', 'legacy'])),

        ('dvr_snat',
            dict(agent_modes=['dvr_snat'],
                 expected_agent_modes=['dvr_snat'])),

        ('dvr ',
            dict(agent_modes=['dvr'],
                 expected_agent_modes=['dvr'])),

        ('legacy and dvr snat',
            dict(agent_modes=['legacy', 'dvr_snat', 'legacy'],
                 expected_agent_modes=['legacy', 'dvr_snat', 'legacy'])),

        ('legacy and dvr',
            dict(agent_modes=['legacy', 'dvr'],
                 expected_agent_modes=['legacy', 'dvr', 'legacy'])),

        ('dvr_snat and dvr',
            dict(agent_modes=['dvr_snat', 'dvr'],
                 expected_agent_modes=['dvr_snat', 'dvr'])),

        ('legacy, dvr_snat and dvr',
            dict(agent_modes=['legacy', 'dvr_snat', 'dvr'],
                 expected_agent_modes=['legacy', 'dvr_snat', 'dvr',
                                       'legacy'])),

        ('invalid',
            dict(agent_modes=['invalid'],
                 expected_agent_modes=[])),
    ]

    def setUp(self):
        super(TestGetL3AgentsWithAgentModeFilter, self).setUp()
        self.plugin = L3HAPlugin()
        self.setup_coreplugin('ml2')
        self.adminContext = n_context.get_admin_context()
        hosts = ['host_1', 'host_2', 'host_3', 'host_4', 'host_5']
        agent_modes = ['legacy', 'dvr_snat', 'dvr', 'fake_mode', 'legacy']
        for host, agent_mode in zip(hosts, agent_modes):
            helpers.register_l3_agent(host, agent_mode)

    def _get_agent_mode(self, agent):
        agent_conf = self.plugin.get_configuration_dict(agent)
        return agent_conf.get('agent_mode', 'None')

    def test_get_l3_agents(self):
        l3_agents = self.plugin.get_l3_agents(
            self.adminContext, filters={'agent_modes': self.agent_modes})
        self.assertEqual(len(self.expected_agent_modes), len(l3_agents))
        returned_agent_modes = [self._get_agent_mode(agent)
                                for agent in l3_agents]
        self.assertEqual(self.expected_agent_modes, returned_agent_modes)


class L3AgentAZLeastRoutersSchedulerTestCase(L3HATestCaseMixin):

    def setUp(self):
        super(L3AgentAZLeastRoutersSchedulerTestCase, self).setUp()
        self.plugin.router_scheduler = importutils.import_object(
            'neutron.scheduler.l3_agent_scheduler.AZLeastRoutersScheduler')
        # Mock scheduling so that the test can control it explicitly
        mock.patch.object(l3_hamode_db.L3_HA_NAT_db_mixin,
                          '_notify_ha_interfaces_updated').start()

    def _register_l3_agents(self):
        self.agent1 = helpers.register_l3_agent(host='az1-host1', az='az1')
        self.agent2 = helpers.register_l3_agent(host='az1-host2', az='az1')
        self.agent3 = helpers.register_l3_agent(host='az2-host1', az='az2')
        self.agent4 = helpers.register_l3_agent(host='az2-host2', az='az2')
        self.agent5 = helpers.register_l3_agent(host='az3-host1', az='az3')
        self.agent6 = helpers.register_l3_agent(host='az3-host2', az='az3')

    def test_az_scheduler_auto_schedule(self):
        r1 = self._create_ha_router(ha=False, az_hints=['az1'])
        self.plugin.auto_schedule_routers(self.adminContext,
                                          'az1-host2', None)
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [r1['id']])
        self.assertEqual(1, len(agents))
        self.assertEqual('az1-host2', agents[0]['host'])

    def test_az_scheduler_auto_schedule_no_match(self):
        r1 = self._create_ha_router(ha=False, az_hints=['az1'])
        self.plugin.auto_schedule_routers(self.adminContext,
                                          'az2-host1', None)
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [r1['id']])
        self.assertEqual(0, len(agents))

    def test_az_scheduler_default_az(self):
        cfg.CONF.set_override('default_availability_zones', ['az2'])
        r1 = self._create_ha_router(ha=False)
        r2 = self._create_ha_router(ha=False)
        r3 = self._create_ha_router(ha=False)
        self.plugin.schedule_router(self.adminContext, r1['id'])
        self.plugin.schedule_router(self.adminContext, r2['id'])
        self.plugin.schedule_router(self.adminContext, r3['id'])
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [r1['id'], r2['id'], r3['id']])
        self.assertEqual(3, len(agents))
        expected_hosts = set(['az2-host1', 'az2-host2'])
        hosts = set([a['host'] for a in agents])
        self.assertEqual(expected_hosts, hosts)

    def test_az_scheduler_az_hints(self):
        r1 = self._create_ha_router(ha=False, az_hints=['az3'])
        r2 = self._create_ha_router(ha=False, az_hints=['az3'])
        r3 = self._create_ha_router(ha=False, az_hints=['az3'])
        self.plugin.schedule_router(self.adminContext, r1['id'])
        self.plugin.schedule_router(self.adminContext, r2['id'])
        self.plugin.schedule_router(self.adminContext, r3['id'])
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [r1['id'], r2['id'], r3['id']])
        self.assertEqual(3, len(agents))
        expected_hosts = set(['az3-host1', 'az3-host2'])
        hosts = set([a['host'] for a in agents])
        self.assertEqual(expected_hosts, hosts)

    def test_az_scheduler_least_routers(self):
        r1 = self._create_ha_router(ha=False, az_hints=['az1'])
        r2 = self._create_ha_router(ha=False, az_hints=['az1'])
        r3 = self._create_ha_router(ha=False, az_hints=['az1'])
        r4 = self._create_ha_router(ha=False, az_hints=['az1'])
        self.plugin.schedule_router(self.adminContext, r1['id'])
        self.plugin.schedule_router(self.adminContext, r2['id'])
        self.plugin.schedule_router(self.adminContext, r3['id'])
        self.plugin.schedule_router(self.adminContext, r4['id'])
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [r1['id'], r2['id'], r3['id'], r4['id']])
        host_num = collections.defaultdict(int)
        for agent in agents:
            host_num[agent['host']] += 1
        self.assertEqual(2, host_num['az1-host1'])
        self.assertEqual(2, host_num['az1-host2'])

    def test_az_scheduler_ha_az_hints(self):
        cfg.CONF.set_override('max_l3_agents_per_router', 2)
        r1 = self._create_ha_router(az_hints=['az1', 'az3'])
        self.plugin.schedule_router(self.adminContext, r1['id'])
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [r1['id']])
        self.assertEqual(2, len(agents))
        expected_azs = set(['az1', 'az3'])
        azs = set([a['availability_zone'] for a in agents])
        self.assertEqual(expected_azs, azs)

    def test_az_scheduler_ha_auto_schedule(self):
        cfg.CONF.set_override('max_l3_agents_per_router', 3)
        self._set_l3_agent_admin_state(self.adminContext, self.agent2['id'],
                                       state=False)
        self._set_l3_agent_admin_state(self.adminContext, self.agent6['id'],
                                       state=False)
        r1 = self._create_ha_router(az_hints=['az1', 'az3'])
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [r1['id']])
        self.assertEqual(2, len(agents))
        hosts = set([a['host'] for a in agents])
        self.assertEqual(set(['az1-host1', 'az3-host1']), hosts)
        self._set_l3_agent_admin_state(self.adminContext, self.agent6['id'],
                                       state=True)
        self.plugin.auto_schedule_routers(self.adminContext,
                                          'az3-host2', None)
        agents = self.plugin.get_l3_agents_hosting_routers(
            self.adminContext, [r1['id']])
        self.assertEqual(3, len(agents))
        expected_hosts = set(['az1-host1', 'az3-host1', 'az3-host2'])
        hosts = set([a['host'] for a in agents])
        self.assertEqual(expected_hosts, hosts)


class L3DVRHAPlugin(db_v2.NeutronDbPluginV2,
                    l3_hamode_db.L3_HA_NAT_db_mixin,
                    l3_dvr_ha_scheduler_db.L3_DVR_HA_scheduler_db_mixin):
    pass


class L3DVRHATestCaseMixin(testlib_api.SqlTestCase,
                           L3SchedulerBaseMixin):

    def setUp(self):
        super(L3DVRHATestCaseMixin, self).setUp()

        self.adminContext = n_context.get_admin_context()
        self.plugin = L3DVRHAPlugin()
