# Copyright (c) 2014 OpenStack Foundation, all rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import mock
from neutron_lib import constants as l3_const
from neutron_lib import exceptions
from oslo_utils import uuidutils
import testtools

from neutron.common import constants as n_const
from neutron import context
from neutron.db import agents_db
from neutron.db import common_db_mixin
from neutron.db import l3_agentschedulers_db
from neutron.db import l3_dvr_db
from neutron.extensions import portbindings
from neutron import manager
from neutron.plugins.common import constants as plugin_const
from neutron.tests.unit.db import test_db_base_plugin_v2

_uuid = uuidutils.generate_uuid


class FakeL3Plugin(common_db_mixin.CommonDbMixin,
                   l3_dvr_db.L3_NAT_with_dvr_db_mixin,
                   l3_agentschedulers_db.L3AgentSchedulerDbMixin,
                   agents_db.AgentDbMixin):
    pass


class L3DvrTestCase(test_db_base_plugin_v2.NeutronDbPluginV2TestCase):

    def setUp(self):
        super(L3DvrTestCase, self).setUp(plugin='ml2')
        self.core_plugin = manager.NeutronManager.get_plugin()
        self.ctx = context.get_admin_context()
        self.mixin = FakeL3Plugin()

    def _create_router(self, router):
        with self.ctx.session.begin(subtransactions=True):
            return self.mixin._create_router_db(self.ctx, router, 'foo_tenant')

    def _test__create_router_db(self, expected=False, distributed=None):
        router = {'name': 'foo_router', 'admin_state_up': True}
        if distributed is not None:
            router['distributed'] = distributed
        result = self._create_router(router)
        self.assertEqual(expected, result.extra_attributes['distributed'])

    def test_create_router_db_default(self):
        self._test__create_router_db(expected=False)

    def test_create_router_db_centralized(self):
        self._test__create_router_db(expected=False, distributed=False)

    def test_create_router_db_distributed(self):
        self._test__create_router_db(expected=True, distributed=True)

    def test__validate_router_migration_on_router_update(self):
        router = {
            'name': 'foo_router',
            'admin_state_up': True,
            'distributed': True
        }
        router_db = self._create_router(router)
        self.assertIsNone(self.mixin._validate_router_migration(
            self.ctx, router_db, {'name': 'foo_router_2'}))

    def test__validate_router_migration_raise_error(self):
        router = {
            'name': 'foo_router',
            'admin_state_up': True,
            'distributed': True
        }
        router_db = self._create_router(router)
        self.assertRaises(exceptions.BadRequest,
                          self.mixin._validate_router_migration,
                          self.ctx, router_db, {'distributed': False})

    def test_upgrade_active_router_to_distributed_validation_failure(self):
        router = {'name': 'foo_router', 'admin_state_up': True}
        router_db = self._create_router(router)
        update = {'distributed': True}
        self.assertRaises(exceptions.BadRequest,
                          self.mixin._validate_router_migration,
                          self.ctx, router_db, update)

    def test_update_router_db_centralized_to_distributed(self):
        router = {'name': 'foo_router', 'admin_state_up': True}
        agent = {'id': _uuid()}
        distributed = {'distributed': True}
        router_db = self._create_router(router)
        router_id = router_db['id']
        self.assertFalse(router_db.extra_attributes.distributed)
        self.mixin._get_router = mock.Mock(return_value=router_db)
        self.mixin._validate_router_migration = mock.Mock()
        self.mixin._update_distributed_attr = mock.Mock()
        self.mixin.list_l3_agents_hosting_router = mock.Mock(
            return_value={'agents': [agent]})
        self.mixin._unbind_router = mock.Mock()
        router_db = self.mixin._update_router_db(
            self.ctx, router_id, distributed)
        # Assert that the DB value has changed
        self.assertTrue(router_db.extra_attributes.distributed)
        self.assertEqual(1,
                         self.mixin._update_distributed_attr.call_count)

    def _test_get_device_owner(self, is_distributed=False,
                               expected=l3_const.DEVICE_OWNER_ROUTER_INTF,
                               pass_router_id=True):
        router = {
            'name': 'foo_router',
            'admin_state_up': True,
            'distributed': is_distributed
        }
        router_db = self._create_router(router)
        router_pass = router_db['id'] if pass_router_id else router_db
        with mock.patch.object(self.mixin, '_get_router') as f:
            f.return_value = router_db
            result = self.mixin._get_device_owner(self.ctx, router_pass)
            self.assertEqual(expected, result)

    def test_get_device_owner_by_router_id(self):
        self._test_get_device_owner()

    def test__get_device_owner_centralized(self):
        self._test_get_device_owner(pass_router_id=False)

    def test__get_device_owner_distributed(self):
        self._test_get_device_owner(
            is_distributed=True,
            expected=l3_const.DEVICE_OWNER_DVR_INTERFACE,
            pass_router_id=False)

    def _test__is_distributed_router(self, router, expected):
        result = l3_dvr_db.is_distributed_router(router)
        self.assertEqual(expected, result)

    def test__is_distributed_router_by_db_object(self):
        router = {'name': 'foo_router', 'admin_state_up': True}
        router_db = self._create_router(router)
        self.mixin._get_device_owner(mock.ANY, router_db)

    def test__is_distributed_router_default(self):
        router = {'id': 'foo_router_id'}
        self._test__is_distributed_router(router, False)

    def test__is_distributed_router_centralized(self):
        router = {'id': 'foo_router_id', 'distributed': False}
        self._test__is_distributed_router(router, False)

    def test__is_distributed_router_distributed(self):
        router = {'id': 'foo_router_id', 'distributed': True}
        self._test__is_distributed_router(router, True)

    def test__get_agent_gw_ports_exist_for_network(self):
        with mock.patch.object(manager.NeutronManager, 'get_plugin') as gp:
            plugin = mock.Mock()
            gp.return_value = plugin
            plugin.get_ports.return_value = []
            self.mixin._get_agent_gw_ports_exist_for_network(
                self.ctx, 'network_id', 'host', 'agent_id')
        plugin.get_ports.assert_called_with(self.ctx, {
            'network_id': ['network_id'],
            'device_id': ['agent_id'],
            'device_owner': [l3_const.DEVICE_OWNER_AGENT_GW]})

    def _test_prepare_direct_delete_dvr_internal_ports(self, port):
        with mock.patch.object(manager.NeutronManager, 'get_plugin') as gp:
            plugin = mock.Mock()
            gp.return_value = plugin
            plugin.get_port.return_value = port
            self.mixin._router_exists = mock.Mock(return_value=True)
            self.assertRaises(exceptions.ServicePortInUse,
                              self.mixin.prevent_l3_port_deletion,
                              self.ctx,
                              port['id'])

    def test_prevent_delete_floatingip_agent_gateway_port(self):
        port = {
            'id': 'my_port_id',
            'fixed_ips': mock.ANY,
            'device_id': 'r_id',
            'device_owner': l3_const.DEVICE_OWNER_AGENT_GW
        }
        self._test_prepare_direct_delete_dvr_internal_ports(port)

    def test_prevent_delete_csnat_port(self):
        port = {
            'id': 'my_port_id',
            'fixed_ips': mock.ANY,
            'device_id': 'r_id',
            'device_owner': l3_const.DEVICE_OWNER_ROUTER_SNAT
        }
        self._test_prepare_direct_delete_dvr_internal_ports(port)

    def test__create_gw_port_with_no_gateway(self):
        router = {
            'name': 'foo_router',
            'admin_state_up': True,
            'distributed': True,
        }
        router_db = self._create_router(router)
        router_id = router_db['id']
        self.assertTrue(router_db.extra_attributes.distributed)
        with mock.patch.object(l3_dvr_db.l3_db.L3_NAT_db_mixin,
                               '_create_gw_port'),\
                mock.patch.object(
                    self.mixin,
                    '_create_snat_intf_ports_if_not_exists') as cs:
            self.mixin._create_gw_port(
                self.ctx, router_id, router_db, mock.ANY,
                mock.ANY)
            self.assertFalse(cs.call_count)

    def test_build_routers_list_with_gw_port_mismatch(self):
        routers = [{'gw_port_id': 'foo_gw_port_id', 'id': 'foo_router_id'}]
        gw_ports = {}
        routers = self.mixin._build_routers_list(self.ctx, routers, gw_ports)
        self.assertIsNone(routers[0].get('gw_port'))

    def setup_port_has_ipv6_address(self, port):
        with mock.patch.object(l3_dvr_db.l3_db.L3_NAT_db_mixin,
                               '_port_has_ipv6_address') as pv6:
            pv6.return_value = True
            result = self.mixin._port_has_ipv6_address(port)
            return result, pv6

    def test__port_has_ipv6_address_for_dvr_snat_port(self):
        port = {
            'id': 'my_port_id',
            'device_owner': l3_const.DEVICE_OWNER_ROUTER_SNAT,
        }
        result, pv6 = self.setup_port_has_ipv6_address(port)
        self.assertFalse(result)
        self.assertFalse(pv6.called)

    def test__port_has_ipv6_address_for_non_snat_ports(self):
        port = {
            'id': 'my_port_id',
            'device_owner': l3_const.DEVICE_OWNER_DVR_INTERFACE,
        }
        result, pv6 = self.setup_port_has_ipv6_address(port)
        self.assertTrue(result)
        self.assertTrue(pv6.called)

    def _helper_delete_floatingip_agent_gateway_port(self, port_host):
        ports = [{
            'id': 'my_port_id',
            portbindings.HOST_ID: 'foo_host',
            'network_id': 'ext_network_id',
            'device_owner': l3_const.DEVICE_OWNER_ROUTER_GW
        },
                {
            'id': 'my_new_port_id',
            portbindings.HOST_ID: 'my_foo_host',
            'network_id': 'ext_network_id',
            'device_owner': l3_const.DEVICE_OWNER_ROUTER_GW
        }]
        with mock.patch.object(manager.NeutronManager, 'get_plugin') as gp:
            plugin = mock.Mock()
            gp.return_value = plugin
            plugin.get_ports.return_value = ports
            self.mixin.delete_floatingip_agent_gateway_port(
                self.ctx, port_host, 'ext_network_id')
        plugin.get_ports.assert_called_with(self.ctx, filters={
            'network_id': ['ext_network_id'],
            'device_owner': [l3_const.DEVICE_OWNER_AGENT_GW]})
        if port_host:
            plugin.ipam.delete_port.assert_called_once_with(
                self.ctx, 'my_port_id')
        else:
            plugin.ipam.delete_port.assert_called_with(
                self.ctx, 'my_new_port_id')

    def test_delete_floatingip_agent_gateway_port_without_host_id(self):
        self._helper_delete_floatingip_agent_gateway_port(None)

    def test_delete_floatingip_agent_gateway_port_with_host_id(self):
        self._helper_delete_floatingip_agent_gateway_port(
            'foo_host')

    def _setup_delete_current_gw_port_deletes_fip_agent_gw_port(
        self, port=None, gw_port=True):
        router = mock.MagicMock()
        router.extra_attributes.distributed = True
        if gw_port:
            gw_port_db = {
                'id': 'my_gw_id',
                'network_id': 'ext_net_id',
                'device_owner': l3_const.DEVICE_OWNER_ROUTER_GW
            }
            router.gw_port = gw_port_db
        else:
            router.gw_port = None

        with mock.patch.object(manager.NeutronManager, 'get_plugin') as gp,\
            mock.patch.object(l3_dvr_db.l3_db.L3_NAT_db_mixin,
                              '_delete_current_gw_port'),\
            mock.patch.object(
                self.mixin,
                '_get_router') as grtr,\
            mock.patch.object(
                self.mixin,
                'delete_csnat_router_interface_ports') as del_csnat_port,\
            mock.patch.object(
                self.mixin,
                'delete_floatingip_agent_gateway_port') as del_agent_gw_port,\
            mock.patch.object(
                self.mixin.l3_rpc_notifier,
                'delete_fipnamespace_for_ext_net') as del_fip:
            plugin = mock.Mock()
            gp.return_value = plugin
            plugin.get_ports.return_value = port
            grtr.return_value = router
            self.mixin._delete_current_gw_port(
                self.ctx, router['id'], router, 'ext_network_id')
            return router, plugin, del_csnat_port, del_agent_gw_port, del_fip

    def test_delete_current_gw_port_deletes_fip_agent_gw_port_and_fipnamespace(
            self):
        rtr, plugin, d_csnat_port, d_agent_gw_port, del_fip = (
            self._setup_delete_current_gw_port_deletes_fip_agent_gw_port())
        self.assertTrue(d_csnat_port.called)
        self.assertTrue(d_agent_gw_port.called)
        d_csnat_port.assert_called_once_with(
            mock.ANY, rtr)
        d_agent_gw_port.assert_called_once_with(mock.ANY, None, 'ext_net_id')
        del_fip.assert_called_once_with(mock.ANY, 'ext_net_id')

    def test_delete_current_gw_port_never_calls_delete_fip_agent_gw_port(self):
        port = [{
            'id': 'my_port_id',
            'network_id': 'ext_net_id',
            'device_owner': l3_const.DEVICE_OWNER_ROUTER_GW
        },
                {
            'id': 'my_new_port_id',
            'network_id': 'ext_net_id',
            'device_owner': l3_const.DEVICE_OWNER_ROUTER_GW
        }]
        rtr, plugin, d_csnat_port, d_agent_gw_port, del_fip = (
            self._setup_delete_current_gw_port_deletes_fip_agent_gw_port(
                port=port))
        self.assertTrue(d_csnat_port.called)
        self.assertFalse(d_agent_gw_port.called)
        self.assertFalse(del_fip.called)
        d_csnat_port.assert_called_once_with(
            mock.ANY, rtr)

    def test_delete_current_gw_port_never_calls_delete_fipnamespace(self):
        rtr, plugin, d_csnat_port, d_agent_gw_port, del_fip = (
            self._setup_delete_current_gw_port_deletes_fip_agent_gw_port(
                gw_port=False))
        self.assertFalse(d_csnat_port.called)
        self.assertFalse(d_agent_gw_port.called)
        self.assertFalse(del_fip.called)

    def _floatingip_on_port_test_setup(self, hostid):
        router = {'id': 'foo_router_id', 'distributed': True}
        floatingip = {
            'id': _uuid(),
            'port_id': _uuid(),
            'router_id': 'foo_router_id',
            'host': hostid
        }
        if not hostid:
            hostid = 'not_my_host_id'
        routers = {
            'foo_router_id': router
        }
        fipagent = {
            'id': _uuid()
        }

        # NOTE: mock.patch is not needed here since self.mixin is created fresh
        # for each test.  It doesn't work with some methods since the mixin is
        # tested in isolation (e.g. _get_agent_by_type_and_host).
        self.mixin._get_dvr_service_port_hostid = mock.Mock(
            return_value=hostid)
        self.mixin._get_agent_by_type_and_host = mock.Mock(
            return_value=fipagent)
        self.mixin._get_fip_sync_interfaces = mock.Mock(
            return_value='fip_interface')
        agent = mock.Mock()
        agent.id = fipagent['id']

        self.mixin._process_floating_ips_dvr(self.ctx, routers, [floatingip],
                                             hostid, agent)
        return (router, floatingip)

    def test_floatingip_on_port_not_host(self):
        router, fip = self._floatingip_on_port_test_setup(None)

        self.assertNotIn(l3_const.FLOATINGIP_KEY, router)
        self.assertNotIn(n_const.FLOATINGIP_AGENT_INTF_KEY, router)

    def test_floatingip_on_port_with_host(self):
        router, fip = self._floatingip_on_port_test_setup(_uuid())

        self.assertTrue(self.mixin._get_fip_sync_interfaces.called)

        self.assertIn(l3_const.FLOATINGIP_KEY, router)
        self.assertIn(n_const.FLOATINGIP_AGENT_INTF_KEY, router)
        self.assertIn(fip, router[l3_const.FLOATINGIP_KEY])
        self.assertIn('fip_interface',
            router[n_const.FLOATINGIP_AGENT_INTF_KEY])

    def _setup_test_create_floatingip(
        self, fip, floatingip_db, router_db):
        port = {
            'id': '1234',
            portbindings.HOST_ID: 'myhost',
            'network_id': 'external_net'
        }

        with mock.patch.object(self.mixin, 'get_router') as grtr,\
                mock.patch.object(self.mixin,
                                  '_get_dvr_service_port_hostid') as vmp,\
                mock.patch.object(
                    self.mixin,
                    '_get_dvr_migrating_service_port_hostid'
                                 ) as mvmp,\
                mock.patch.object(
                    self.mixin,
                    'create_fip_agent_gw_port_if_not_exists') as c_fip,\
                mock.patch.object(l3_dvr_db.l3_db.L3_NAT_db_mixin,
                                  '_update_fip_assoc'):
            grtr.return_value = router_db
            vmp.return_value = 'my-host'
            mvmp.return_value = 'my-future-host'
            self.mixin._update_fip_assoc(
                self.ctx, fip, floatingip_db, port)
            return c_fip

    def test_create_floatingip_agent_gw_port_with_dvr_router(self):
        floatingip = {
            'id': _uuid(),
            'router_id': 'foo_router_id'
        }
        router = {'id': 'foo_router_id', 'distributed': True}
        fip = {
            'id': _uuid(),
            'port_id': _uuid()
        }
        create_fip = (
            self._setup_test_create_floatingip(
                fip, floatingip, router))
        self.assertTrue(create_fip.called)

    def test_create_floatingip_agent_gw_port_with_non_dvr_router(self):
        floatingip = {
            'id': _uuid(),
            'router_id': 'foo_router_id'
        }
        router = {'id': 'foo_router_id', 'distributed': False}
        fip = {
            'id': _uuid(),
            'port_id': _uuid()
        }
        create_fip = (
            self._setup_test_create_floatingip(
                fip, floatingip, router))
        self.assertFalse(create_fip.called)

    def test_remove_router_interface_csnat_ports_removal(self):
        router_dict = {'name': 'test_router', 'admin_state_up': True,
                       'distributed': True}
        router = self._create_router(router_dict)
        plugin = mock.MagicMock()
        with self.network() as net_ext,\
                self.subnet() as subnet1,\
                self.subnet(cidr='20.0.0.0/24') as subnet2:
            ext_net_id = net_ext['network']['id']
            self.core_plugin.update_network(
                self.ctx, ext_net_id,
                {'network': {'router:external': True}})
            self.mixin.update_router(
                self.ctx, router['id'],
                {'router': {'external_gateway_info':
                            {'network_id': ext_net_id}}})
            self.mixin.add_router_interface(self.ctx, router['id'],
                {'subnet_id': subnet1['subnet']['id']})
            self.mixin.add_router_interface(self.ctx, router['id'],
                {'subnet_id': subnet2['subnet']['id']})

            csnat_filters = {'device_owner':
                             [l3_const.DEVICE_OWNER_ROUTER_SNAT]}
            csnat_ports = self.core_plugin.get_ports(
                self.ctx, filters=csnat_filters)
            self.assertEqual(2, len(csnat_ports))

            dvr_filters = {'device_owner':
                           [l3_const.DEVICE_OWNER_DVR_INTERFACE]}
            dvr_ports = self.core_plugin.get_ports(
                self.ctx, filters=dvr_filters)
            self.assertEqual(2, len(dvr_ports))

            with mock.patch.object(manager.NeutronManager,
                                  'get_service_plugins') as get_svc_plugin:
                get_svc_plugin.return_value = {
                    plugin_const.L3_ROUTER_NAT: plugin}
                self.mixin.manager = manager
                self.mixin.remove_router_interface(
                    self.ctx, router['id'], {'port_id': dvr_ports[0]['id']})

            csnat_ports = self.core_plugin.get_ports(
                self.ctx, filters=csnat_filters)
            self.assertEqual(1, len(csnat_ports))
            self.assertEqual(dvr_ports[1]['fixed_ips'][0]['subnet_id'],
                             csnat_ports[0]['fixed_ips'][0]['subnet_id'])

            dvr_ports = self.core_plugin.get_ports(
                self.ctx, filters=dvr_filters)
            self.assertEqual(1, len(dvr_ports))

    def _setup_router_with_v4_and_v6(self):
        router_dict = {'name': 'test_router', 'admin_state_up': True,
                       'distributed': True}
        router = self._create_router(router_dict)
        plugin = mock.MagicMock()
        with self.network() as net_ext, self.network() as net_int:
            ext_net_id = net_ext['network']['id']
            self.core_plugin.update_network(
                self.ctx, ext_net_id,
                {'network': {'router:external': True}})
            self.mixin.update_router(
                self.ctx, router['id'],
                {'router': {'external_gateway_info':
                            {'network_id': ext_net_id}}})
            with self.subnet(
                network=net_int, cidr='20.0.0.0/24') as subnet_v4,\
                self.subnet(
                    network=net_int, cidr='fe80::/64',
                    gateway_ip='fe80::1', ip_version=6) as subnet_v6:
                self.mixin.add_router_interface(self.ctx, router['id'],
                    {'subnet_id': subnet_v4['subnet']['id']})
                self.mixin.add_router_interface(self.ctx, router['id'],
                    {'subnet_id': subnet_v6['subnet']['id']})
                get_svc_plugin = mock.patch.object(
                    manager.NeutronManager, 'get_service_plugins').start()
                get_svc_plugin.return_value = {
                    plugin_const.L3_ROUTER_NAT: plugin}
                self.mixin.manager = manager
                return router, subnet_v4, subnet_v6

    def test_undo_router_interface_change_on_csnat_error(self):
        self._test_undo_router_interface_change_on_csnat_error(False)

    def test_undo_router_interface_change_on_csnat_error_revert_failure(self):
        self._test_undo_router_interface_change_on_csnat_error(True)

    def _test_undo_router_interface_change_on_csnat_error(self, fail_revert):
        router, subnet_v4, subnet_v6 = self._setup_router_with_v4_and_v6()
        net = {'network': {'id': subnet_v6['subnet']['network_id'],
                           'tenant_id': subnet_v6['subnet']['tenant_id']}}
        orig_update = self.mixin._core_plugin.update_port

        def update_port(*args, **kwargs):
            # 1st port update is the interface, 2nd is csnat, 3rd is revert
            # we want to simulate errors after the 1st
            update_port.calls += 1
            if update_port.calls == 2:
                raise RuntimeError('csnat update failure')
            if update_port.calls == 3 and fail_revert:
                # this is to ensure that if the revert fails, the original
                # exception is raised (not this ValueError)
                raise ValueError('failure from revert')
            return orig_update(*args, **kwargs)
        update_port.calls = 0
        self.mixin._core_plugin.update_port = update_port

        with self.subnet(network=net, cidr='fe81::/64',
                         gateway_ip='fe81::1', ip_version=6) as subnet2_v6:
            with testtools.ExpectedException(RuntimeError):
                self.mixin.add_router_interface(self.ctx, router['id'],
                    {'subnet_id': subnet2_v6['subnet']['id']})
            if fail_revert:
                # a revert failure will mean the interface is still added
                # so we can't re-add it
                return
            # starting over should work if first interface was cleaned up
            self.mixin.add_router_interface(self.ctx, router['id'],
                {'subnet_id': subnet2_v6['subnet']['id']})

    def test_remove_router_interface_csnat_ports_removal_with_ipv6(self):
        router, subnet_v4, subnet_v6 = self._setup_router_with_v4_and_v6()
        csnat_filters = {'device_owner':
                         [l3_const.DEVICE_OWNER_ROUTER_SNAT]}
        csnat_ports = self.core_plugin.get_ports(
            self.ctx, filters=csnat_filters)
        self.assertEqual(2, len(csnat_ports))
        dvr_filters = {'device_owner':
                       [l3_const.DEVICE_OWNER_DVR_INTERFACE]}
        dvr_ports = self.core_plugin.get_ports(
            self.ctx, filters=dvr_filters)
        self.assertEqual(2, len(dvr_ports))
        self.mixin.remove_router_interface(
            self.ctx, router['id'],
            {'subnet_id': subnet_v4['subnet']['id']})
        csnat_ports = self.core_plugin.get_ports(
            self.ctx, filters=csnat_filters)
        self.assertEqual(1, len(csnat_ports))
        self.assertEqual(
            subnet_v6['subnet']['id'],
            csnat_ports[0]['fixed_ips'][0]['subnet_id'])

        dvr_ports = self.core_plugin.get_ports(
            self.ctx, filters=dvr_filters)
        self.assertEqual(1, len(dvr_ports))

    def test_remove_router_interface_csnat_port_missing_ip(self):
        # NOTE(kevinbenton): this is a contrived scenario to reproduce
        # a condition observed in bug/1609540. Once we figure out why
        # these ports lose their IP we can remove this test.
        router, subnet_v4, subnet_v6 = self._setup_router_with_v4_and_v6()
        self.mixin.remove_router_interface(
            self.ctx, router['id'],
            {'subnet_id': subnet_v4['subnet']['id']})
        csnat_filters = {'device_owner':
                         [l3_const.DEVICE_OWNER_ROUTER_SNAT]}
        csnat_ports = self.core_plugin.get_ports(
            self.ctx, filters=csnat_filters)
        self.core_plugin.update_port(self.ctx, csnat_ports[0]['id'],
                                     {'port': {'fixed_ips': []}})
        self.mixin.remove_router_interface(
            self.ctx, router['id'],
            {'subnet_id': subnet_v6['subnet']['id']})

    def test__validate_router_migration_notify_advanced_services(self):
        router = {'name': 'foo_router', 'admin_state_up': False}
        router_db = self._create_router(router)
        with mock.patch.object(l3_dvr_db.registry, 'notify') as mock_notify:
            self.mixin._validate_router_migration(
                self.ctx, router_db, {'distributed': True})
            kwargs = {'context': self.ctx, 'router': router_db}
            mock_notify.assert_called_once_with(
                'router', 'before_update', self.mixin, **kwargs)

    def _test_update_arp_entry_for_dvr_service_port(
            self, device_owner, action):
        router_dict = {'name': 'test_router', 'admin_state_up': True,
                       'distributed': True}
        router = self._create_router(router_dict)
        with mock.patch.object(manager.NeutronManager, 'get_plugin') as gp:
            plugin = mock.Mock()
            l3_notify = self.mixin.l3_rpc_notifier = mock.Mock()
            gp.return_value = plugin
            port = {
                'id': 'my_port_id',
                'fixed_ips': [
                    {'subnet_id': '51edc9e0-24f9-47f2-8e1e-2a41cb691323',
                     'ip_address': '10.0.0.11'},
                    {'subnet_id': '2b7c8a07-6f8e-4937-8701-f1d5da1a807c',
                     'ip_address': '10.0.0.21'},
                    {'subnet_id': '48534187-f077-4e81-93ff-81ec4cc0ad3b',
                     'ip_address': 'fd45:1515:7e0:0:f816:3eff:fe1a:1111'}],
                'mac_address': 'my_mac',
                'device_owner': device_owner
            }
            dvr_port = {
                'id': 'dvr_port_id',
                'fixed_ips': mock.ANY,
                'device_owner': l3_const.DEVICE_OWNER_DVR_INTERFACE,
                'device_id': router['id']
            }
            plugin.get_ports.return_value = [dvr_port]
            if action == 'add':
                self.mixin.update_arp_entry_for_dvr_service_port(
                    self.ctx, port)
                self.assertEqual(3, l3_notify.add_arp_entry.call_count)
            elif action == 'del':
                self.mixin.delete_arp_entry_for_dvr_service_port(
                    self.ctx, port)
                self.assertEqual(3, l3_notify.del_arp_entry.call_count)

    def test_update_arp_entry_for_dvr_service_port_added(self):
        action = 'add'
        device_owner = l3_const.DEVICE_OWNER_LOADBALANCER
        self._test_update_arp_entry_for_dvr_service_port(device_owner, action)

    def test_update_arp_entry_for_dvr_service_port_deleted(self):
        action = 'del'
        device_owner = l3_const.DEVICE_OWNER_LOADBALANCER
        self._test_update_arp_entry_for_dvr_service_port(device_owner, action)

    def test_add_router_interface_csnat_ports_failure(self):
        router_dict = {'name': 'test_router', 'admin_state_up': True,
                       'distributed': True}
        router = self._create_router(router_dict)
        with self.network() as net_ext,\
                self.subnet() as subnet:
            ext_net_id = net_ext['network']['id']
            self.core_plugin.update_network(
                self.ctx, ext_net_id,
                {'network': {'router:external': True}})
            self.mixin.update_router(
                self.ctx, router['id'],
                {'router': {'external_gateway_info':
                            {'network_id': ext_net_id}}})
            with mock.patch.object(
                self.mixin, '_add_csnat_router_interface_port') as f:
                f.side_effect = RuntimeError()
                self.assertRaises(
                    RuntimeError,
                    self.mixin.add_router_interface,
                    self.ctx, router['id'],
                    {'subnet_id': subnet['subnet']['id']})
                filters = {
                    'device_id': [router['id']],
                }
                router_ports = self.core_plugin.get_ports(self.ctx, filters)
                self.assertEqual(1, len(router_ports))
                self.assertEqual(l3_const.DEVICE_OWNER_ROUTER_GW,
                                 router_ports[0]['device_owner'])
