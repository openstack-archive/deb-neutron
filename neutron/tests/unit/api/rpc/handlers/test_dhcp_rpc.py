# Copyright (c) 2012 OpenStack Foundation.
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
from neutron_lib import constants
from neutron_lib import exceptions as n_exc
from oslo_db import exception as db_exc

from neutron.api.rpc.handlers import dhcp_rpc
from neutron.callbacks import resources
from neutron.common import constants as n_const
from neutron.common import exceptions
from neutron.common import utils
from neutron.db import provisioning_blocks
from neutron.extensions import portbindings
from neutron.tests import base


class TestDhcpRpcCallback(base.BaseTestCase):

    def setUp(self):
        super(TestDhcpRpcCallback, self).setUp()
        self.plugin_p = mock.patch('neutron.manager.NeutronManager.get_plugin')
        get_plugin = self.plugin_p.start()
        self.plugin = mock.MagicMock()
        get_plugin.return_value = self.plugin
        self.callbacks = dhcp_rpc.DhcpRpcCallback()
        self.log_p = mock.patch('neutron.api.rpc.handlers.dhcp_rpc.LOG')
        self.log = self.log_p.start()
        set_dirty_p = mock.patch('neutron.quota.resource_registry.'
                                 'set_resources_dirty')
        self.mock_set_dirty = set_dirty_p.start()
        self.utils_p = mock.patch('neutron.plugins.common.utils.create_port')
        self.utils = self.utils_p.start()
        self.segment_p = mock.patch(
            'neutron.manager.NeutronManager.get_service_plugins')
        self.get_service_plugins = self.segment_p.start()
        self.segment_plugin = mock.MagicMock()

    def test_group_by_network_id(self):
        port1 = {'network_id': 'a'}
        port2 = {'network_id': 'b'}
        port3 = {'network_id': 'a'}
        grouped_ports = self.callbacks._group_by_network_id(
                                                        [port1, port2, port3])
        expected = {'a': [port1, port3], 'b': [port2]}
        self.assertEqual(expected, grouped_ports)

    def test_get_active_networks_info(self):
        plugin_retval = [{'id': 'a'}, {'id': 'b'}]
        self.plugin.get_networks.return_value = plugin_retval
        port = {'network_id': 'a'}
        subnet = {'network_id': 'b', 'id': 'c'}
        self.plugin.get_ports.return_value = [port]
        self.plugin.get_subnets.return_value = [subnet]
        networks = self.callbacks.get_active_networks_info(mock.Mock(),
                                                           host='host')
        expected = [{'id': 'a', 'subnets': [], 'ports': [port]},
                    {'id': 'b', 'subnets': [subnet], 'ports': []}]
        self.assertEqual(expected, networks)

    def test_get_active_networks_info_with_routed_networks(self):
        self.get_service_plugins.return_value = {
            'segments': self.segment_plugin
        }
        plugin_retval = [{'id': 'a'}, {'id': 'b'}]
        port = {'network_id': 'a'}
        subnets = [{'network_id': 'b', 'id': 'c', 'segment_id': '1'},
                   {'network_id': 'a', 'id': 'e'},
                   {'network_id': 'b', 'id': 'd', 'segment_id': '3'}]
        self.plugin.get_ports.return_value = [port]
        self.plugin.get_networks.return_value = plugin_retval
        hostseg_retval = ['1', '2']
        self.segment_plugin.get_segments_by_hosts.return_value = hostseg_retval
        self.plugin.get_subnets.return_value = subnets
        networks = self.callbacks.get_active_networks_info(mock.Mock(),
                                                           host='host')
        expected = [{'id': 'a', 'subnets': [subnets[1]], 'ports': [port]},
                    {'id': 'b', 'subnets': [subnets[0]], 'ports': []}]
        self.assertEqual(expected, networks)

    def _test__port_action_with_failures(self, exc=None, action=None):
        port = {
            'network_id': 'foo_network_id',
            'device_owner': constants.DEVICE_OWNER_DHCP,
            'fixed_ips': [{'subnet_id': 'foo_subnet_id'}]
        }
        self.plugin.create_port.side_effect = exc
        self.utils.side_effect = exc
        self.assertIsNone(self.callbacks._port_action(self.plugin,
                                                      mock.Mock(),
                                                      {'port': port},
                                                      action))

    def _test__port_action_good_action(self, action, port, expected_call):
        self.callbacks._port_action(self.plugin, mock.Mock(),
                                    port, action)
        if action == 'create_port':
            self.utils.assert_called_once_with(mock.ANY, mock.ANY, mock.ANY)
        else:
            self.plugin.assert_has_calls([expected_call])

    def test_port_action_create_port(self):
        self._test__port_action_good_action(
            'create_port', mock.Mock(),
            mock.call.create_port(mock.ANY, mock.ANY))

    def test_port_action_update_port(self):
        fake_port = {'id': 'foo_port_id', 'port': mock.Mock()}
        self._test__port_action_good_action(
            'update_port', fake_port,
            mock.call.update_port(mock.ANY, 'foo_port_id', mock.ANY))

    def test__port_action_bad_action(self):
        self.assertRaises(
            n_exc.Invalid,
            self._test__port_action_with_failures,
            exc=None,
            action='foo_action')

    def test_create_port_catch_network_not_found(self):
        self._test__port_action_with_failures(
            exc=n_exc.NetworkNotFound(net_id='foo_network_id'),
            action='create_port')

    def test_create_port_catch_subnet_not_found(self):
        self._test__port_action_with_failures(
            exc=n_exc.SubnetNotFound(subnet_id='foo_subnet_id'),
            action='create_port')

    def test_create_port_catch_db_reference_error(self):
        self._test__port_action_with_failures(
            exc=db_exc.DBReferenceError('a', 'b', 'c', 'd'),
            action='create_port')

    def test_create_port_catch_ip_generation_failure_reraise(self):
        self.assertRaises(
            n_exc.IpAddressGenerationFailure,
            self._test__port_action_with_failures,
            exc=n_exc.IpAddressGenerationFailure(net_id='foo_network_id'),
            action='create_port')

    def test_create_port_catch_and_handle_ip_generation_failure(self):
        self.plugin.get_subnet.side_effect = (
            n_exc.SubnetNotFound(subnet_id='foo_subnet_id'))
        self._test__port_action_with_failures(
            exc=n_exc.IpAddressGenerationFailure(net_id='foo_network_id'),
            action='create_port')
        self._test__port_action_with_failures(
            exc=n_exc.InvalidInput(error_message='sorry'),
            action='create_port')

    def test_get_network_info_return_none_on_not_found(self):
        self.plugin.get_network.side_effect = n_exc.NetworkNotFound(net_id='a')
        retval = self.callbacks.get_network_info(mock.Mock(), network_id='a')
        self.assertIsNone(retval)

    def _test_get_network_info(self, segmented_network=False,
                               routed_network=False):
        network_retval = dict(id='a')
        if not routed_network:
            subnet_retval = [dict(id='a'), dict(id='c'), dict(id='b')]
        else:
            subnet_retval = [dict(id='c', segment_id='1'),
                             dict(id='a', segment_id='1')]
        port_retval = mock.Mock()

        self.plugin.get_network.return_value = network_retval
        self.plugin.get_subnets.return_value = subnet_retval
        self.plugin.get_ports.return_value = port_retval
        if segmented_network:
            self.segment_plugin.get_segments.return_value = [dict(id='1'),
                                                             dict(id='2')]
            self.segment_plugin.get_segments_by_hosts.return_value = ['1']

        retval = self.callbacks.get_network_info(mock.Mock(), network_id='a')
        self.assertEqual(retval, network_retval)
        if not routed_network:
            sorted_subnet_retval = [dict(id='a'), dict(id='b'), dict(id='c')]
        else:
            sorted_subnet_retval = [dict(id='a', segment_id='1'),
                                    dict(id='c', segment_id='1')]
        self.assertEqual(retval['subnets'], sorted_subnet_retval)
        self.assertEqual(retval['ports'], port_retval)

    def test_get_network_info(self):
        self._test_get_network_info()

    def test_get_network_info_with_routed_network(self):
        self.get_service_plugins.return_value = {
            'segments': self.segment_plugin
        }
        self._test_get_network_info(segmented_network=True,
                                    routed_network=True)

    def test_get_network_info_with_segmented_network_but_not_routed(self):
        self.get_service_plugins.return_value = {
            'segments': self.segment_plugin
        }
        self._test_get_network_info(segmented_network=True)

    def test_get_network_info_with_non_segmented_network(self):
        self.get_service_plugins.return_value = {
            'segments': self.segment_plugin
        }
        self._test_get_network_info()

    def test_update_dhcp_port_verify_port_action_port_dict(self):
        port = {'port': {'network_id': 'foo_network_id',
                         'device_owner': constants.DEVICE_OWNER_DHCP,
                         'fixed_ips': [{'subnet_id': 'foo_subnet_id'}]}
                }
        expected_port = {'port': {'network_id': 'foo_network_id',
                                  'device_owner': constants.DEVICE_OWNER_DHCP,
                                  portbindings.HOST_ID: 'foo_host',
                                  'fixed_ips': [{'subnet_id': 'foo_subnet_id'}]
                                  },
                         'id': 'foo_port_id'
                         }

        def _fake_port_action(plugin, context, port, action):
            self.assertEqual(expected_port, port)

        self.plugin.get_port.return_value = {
            'device_id': n_const.DEVICE_ID_RESERVED_DHCP_PORT}
        self.callbacks._port_action = _fake_port_action
        self.callbacks.update_dhcp_port(mock.Mock(),
                                        host='foo_host',
                                        port_id='foo_port_id',
                                        port=port)

    def test_update_reserved_dhcp_port(self):
        port = {'port': {'network_id': 'foo_network_id',
                         'device_owner': constants.DEVICE_OWNER_DHCP,
                         'fixed_ips': [{'subnet_id': 'foo_subnet_id'}]}
                }
        expected_port = {'port': {'network_id': 'foo_network_id',
                                  'device_owner': constants.DEVICE_OWNER_DHCP,
                                  portbindings.HOST_ID: 'foo_host',
                                  'fixed_ips': [{'subnet_id': 'foo_subnet_id'}]
                                  },
                         'id': 'foo_port_id'
                         }

        def _fake_port_action(plugin, context, port, action):
            self.assertEqual(expected_port, port)

        self.plugin.get_port.return_value = {
            'device_id': utils.get_dhcp_agent_device_id('foo_network_id',
                                                        'foo_host')}
        self.callbacks._port_action = _fake_port_action
        self.callbacks.update_dhcp_port(
            mock.Mock(), host='foo_host', port_id='foo_port_id', port=port)

        self.plugin.get_port.return_value = {
            'device_id': 'other_id'}
        self.assertRaises(exceptions.DhcpPortInUse,
                          self.callbacks.update_dhcp_port,
                          mock.Mock(),
                          host='foo_host',
                          port_id='foo_port_id',
                          port=port)

    def test_update_dhcp_port(self):
        port = {'port': {'network_id': 'foo_network_id',
                         'device_owner': constants.DEVICE_OWNER_DHCP,
                         'fixed_ips': [{'subnet_id': 'foo_subnet_id'}]}
                }
        expected_port = {'port': {'network_id': 'foo_network_id',
                                  'device_owner': constants.DEVICE_OWNER_DHCP,
                                  portbindings.HOST_ID: 'foo_host',
                                  'fixed_ips': [{'subnet_id': 'foo_subnet_id'}]
                                  },
                         'id': 'foo_port_id'
                         }
        self.plugin.get_port.return_value = {
            'device_id': n_const.DEVICE_ID_RESERVED_DHCP_PORT}
        self.callbacks.update_dhcp_port(mock.Mock(),
                                        host='foo_host',
                                        port_id='foo_port_id',
                                        port=port)
        self.plugin.assert_has_calls([
            mock.call.update_port(mock.ANY, 'foo_port_id', expected_port)])

    def test_release_dhcp_port(self):
        port_retval = dict(id='port_id', fixed_ips=[dict(subnet_id='a')])
        self.plugin.get_ports.return_value = [port_retval]

        self.callbacks.release_dhcp_port(mock.ANY, network_id='netid',
                                         device_id='devid')

        self.plugin.assert_has_calls([
            mock.call.delete_ports_by_device_id(mock.ANY, 'devid', 'netid')])

    def test_dhcp_ready_on_ports(self):
        context = mock.Mock()
        port_ids = range(10)
        with mock.patch.object(provisioning_blocks,
                               'provisioning_complete') as pc:
            self.callbacks.dhcp_ready_on_ports(context, port_ids)
        calls = [mock.call(context, port_id, resources.PORT,
                           provisioning_blocks.DHCP_ENTITY)
                 for port_id in port_ids]
        pc.assert_has_calls(calls)
