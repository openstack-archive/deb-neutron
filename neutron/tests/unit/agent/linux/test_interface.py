# Copyright 2012 OpenStack Foundation
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

import mock
from neutron_lib import constants
from oslo_log import versionutils

from neutron.agent.common import config
from neutron.agent.common import ovs_lib
from neutron.agent.linux import interface
from neutron.agent.linux import ip_lib
from neutron.agent.linux import utils
from neutron.tests import base


class BaseChild(interface.LinuxInterfaceDriver):
    def plug_new(*args):
        pass

    def unplug(*args):
        pass


class FakeNetwork(object):
    id = '12345678-1234-5678-90ab-ba0987654321'


class FakeSubnet(object):
    cidr = '192.168.1.1/24'


class FakeAllocation(object):
    subnet = FakeSubnet()
    ip_address = '192.168.1.2'
    ip_version = 4


class FakePort(object):
    id = 'abcdef01-1234-5678-90ab-ba0987654321'
    fixed_ips = [FakeAllocation]
    device_id = 'cccccccc-cccc-cccc-cccc-cccccccccccc'
    network = FakeNetwork()
    network_id = network.id


class FakeInterfaceDriverNoMtu(interface.LinuxInterfaceDriver):
    # NOTE(ihrachys) this method intentially omit mtu= parameter, since that
    # was the method signature before Mitaka. We should make sure the old
    # signature still works.

    def __init__(self, *args, **kwargs):
        super(FakeInterfaceDriverNoMtu, self).__init__(*args, **kwargs)
        self.plug_called = False

    def plug_new(self, network_id, port_id, device_name, mac_address,
                 bridge=None, namespace=None, prefix=None):
        self.plug_called = True

    def unplug(self, device_name, bridge=None, namespace=None, prefix=None):
        pass


class TestBase(base.BaseTestCase):
    def setUp(self):
        super(TestBase, self).setUp()
        self.conf = config.setup_conf()
        self.conf.register_opts(interface.OPTS)
        self.ip_dev_p = mock.patch.object(ip_lib, 'IPDevice')
        self.ip_dev = self.ip_dev_p.start()
        self.ip_p = mock.patch.object(ip_lib, 'IPWrapper')
        self.ip = self.ip_p.start()
        self.device_exists_p = mock.patch.object(ip_lib, 'device_exists')
        self.device_exists = self.device_exists_p.start()


class TestABCDriverNoMtu(TestBase):

    def test_plug_with_no_mtu_works(self):
        driver = FakeInterfaceDriverNoMtu(self.conf)
        self.device_exists.return_value = False
        with mock.patch.object(
                versionutils, 'report_deprecated_feature') as report:
            driver.plug(
                mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock(), mtu=9000)
        self.assertTrue(driver.plug_called)
        self.assertTrue(report.called)


class TestABCDriver(TestBase):
    def setUp(self):
        super(TestABCDriver, self).setUp()
        mock_link_addr = mock.PropertyMock(return_value='aa:bb:cc:dd:ee:ff')
        type(self.ip_dev().link).address = mock_link_addr

    def test_get_device_name(self):
        bc = BaseChild(self.conf)
        device_name = bc.get_device_name(FakePort())
        self.assertEqual('tapabcdef01-12', device_name)

    def test_init_router_port(self):
        addresses = [dict(scope='global',
                          dynamic=False, cidr='172.16.77.240/24')]
        self.ip_dev().addr.list = mock.Mock(return_value=addresses)
        self.ip_dev().route.list_onlink_routes.return_value = []

        bc = BaseChild(self.conf)
        ns = '12345678-1234-5678-90ab-ba0987654321'
        bc.init_router_port('tap0', ['192.168.1.2/24'], namespace=ns,
                            extra_subnets=[{'cidr': '172.20.0.0/24'}])
        self.ip_dev.assert_has_calls(
            [mock.call('tap0', namespace=ns),
             mock.call().addr.list(filters=['permanent']),
             mock.call().addr.add('192.168.1.2/24'),
             mock.call().addr.delete('172.16.77.240/24'),
             mock.call('tap0', namespace=ns),
             mock.call().route.list_onlink_routes(constants.IP_VERSION_4),
             mock.call().route.list_onlink_routes(constants.IP_VERSION_6),
             mock.call().route.add_onlink_route('172.20.0.0/24')])

    def test_init_router_port_delete_onlink_routes(self):
        addresses = [dict(scope='global',
                          dynamic=False, cidr='172.16.77.240/24')]
        self.ip_dev().addr.list = mock.Mock(return_value=addresses)
        self.ip_dev().route.list_onlink_routes.return_value = [
            {'cidr': '172.20.0.0/24'}]

        bc = BaseChild(self.conf)
        ns = '12345678-1234-5678-90ab-ba0987654321'
        bc.init_router_port('tap0', ['192.168.1.2/24'], namespace=ns)
        self.ip_dev.assert_has_calls(
            [mock.call().route.list_onlink_routes(constants.IP_VERSION_4),
             mock.call().route.list_onlink_routes(constants.IP_VERSION_6),
             mock.call().route.delete_onlink_route('172.20.0.0/24')])

    def test_l3_init_with_preserve(self):
        addresses = [dict(scope='global',
                          dynamic=False, cidr='192.168.1.3/32')]
        self.ip_dev().addr.list = mock.Mock(return_value=addresses)

        bc = BaseChild(self.conf)
        ns = '12345678-1234-5678-90ab-ba0987654321'
        bc.init_l3('tap0', ['192.168.1.2/24'], namespace=ns,
                   preserve_ips=['192.168.1.3/32'])
        self.ip_dev.assert_has_calls(
            [mock.call('tap0', namespace=ns),
             mock.call().addr.list(filters=['permanent']),
             mock.call().addr.add('192.168.1.2/24')])
        self.assertFalse(self.ip_dev().addr.delete.called)
        self.assertFalse(self.ip_dev().delete_addr_and_conntrack_state.called)

    def _test_l3_init_clean_connections(self, clean_connections):
        addresses = [
            dict(scope='global', dynamic=False, cidr='10.0.0.1/24'),
            dict(scope='global', dynamic=False, cidr='10.0.0.3/32')]
        self.ip_dev().addr.list = mock.Mock(return_value=addresses)

        bc = BaseChild(self.conf)
        ns = '12345678-1234-5678-90ab-ba0987654321'
        bc.init_l3('tap0', ['10.0.0.1/24'], namespace=ns,
                   clean_connections=clean_connections)

        delete = self.ip_dev().delete_addr_and_conntrack_state
        if clean_connections:
            delete.assert_called_once_with('10.0.0.3/32')
        else:
            self.assertFalse(delete.called)

    def test_l3_init_with_clean_connections(self):
        self._test_l3_init_clean_connections(True)

    def test_l3_init_without_clean_connections(self):
        self._test_l3_init_clean_connections(False)

    def test_init_router_port_ipv6_with_gw_ip(self):
        addresses = [dict(scope='global',
                          dynamic=False,
                          cidr='2001:db8:a::123/64')]
        self.ip_dev().addr.list = mock.Mock(return_value=addresses)
        self.ip_dev().route.list_onlink_routes.return_value = []

        bc = BaseChild(self.conf)
        ns = '12345678-1234-5678-90ab-ba0987654321'
        new_cidr = '2001:db8:a::124/64'
        kwargs = {'namespace': ns,
                  'extra_subnets': [{'cidr': '2001:db8:b::/64'}]}
        bc.init_router_port('tap0', [new_cidr], **kwargs)
        expected_calls = (
            [mock.call('tap0', namespace=ns),
             mock.call().addr.list(filters=['permanent']),
             mock.call().addr.add('2001:db8:a::124/64'),
             mock.call().addr.delete('2001:db8:a::123/64')])
        expected_calls += (
             [mock.call('tap0', namespace=ns),
              mock.call().route.list_onlink_routes(constants.IP_VERSION_4),
              mock.call().route.list_onlink_routes(constants.IP_VERSION_6),
              mock.call().route.add_onlink_route('2001:db8:b::/64')])
        self.ip_dev.assert_has_calls(expected_calls)

    def test_init_router_port_ext_gw_with_dual_stack(self):
        old_addrs = [dict(ip_version=4, scope='global',
                          dynamic=False, cidr='172.16.77.240/24'),
                     dict(ip_version=6, scope='global',
                          dynamic=False, cidr='2001:db8:a::123/64')]
        self.ip_dev().addr.list = mock.Mock(return_value=old_addrs)
        self.ip_dev().route.list_onlink_routes.return_value = []
        bc = BaseChild(self.conf)
        ns = '12345678-1234-5678-90ab-ba0987654321'
        new_cidrs = ['192.168.1.2/24', '2001:db8:a::124/64']
        bc.init_router_port('tap0', new_cidrs, namespace=ns,
            extra_subnets=[{'cidr': '172.20.0.0/24'}])
        self.ip_dev.assert_has_calls(
            [mock.call('tap0', namespace=ns),
             mock.call().addr.list(filters=['permanent']),
             mock.call().addr.add('192.168.1.2/24'),
             mock.call().addr.add('2001:db8:a::124/64'),
             mock.call().addr.delete('172.16.77.240/24'),
             mock.call().addr.delete('2001:db8:a::123/64'),
             mock.call().route.list_onlink_routes(constants.IP_VERSION_4),
             mock.call().route.list_onlink_routes(constants.IP_VERSION_6),
             mock.call().route.add_onlink_route('172.20.0.0/24')],
            any_order=True)

    def test_init_router_port_with_ipv6_delete_onlink_routes(self):
        addresses = [dict(scope='global',
                          dynamic=False, cidr='2001:db8:a::123/64')]
        route = '2001:db8:a::/64'
        self.ip_dev().addr.list = mock.Mock(return_value=addresses)
        self.ip_dev().route.list_onlink_routes.return_value = [{'cidr': route}]

        bc = BaseChild(self.conf)
        ns = '12345678-1234-5678-90ab-ba0987654321'
        bc.init_router_port('tap0', ['2001:db8:a::124/64'], namespace=ns)
        self.ip_dev.assert_has_calls(
            [mock.call().route.list_onlink_routes(constants.IP_VERSION_4),
             mock.call().route.list_onlink_routes(constants.IP_VERSION_6),
             mock.call().route.delete_onlink_route(route)])

    def test_l3_init_with_duplicated_ipv6(self):
        addresses = [dict(scope='global',
                          dynamic=False,
                          cidr='2001:db8:a::123/64')]
        self.ip_dev().addr.list = mock.Mock(return_value=addresses)
        bc = BaseChild(self.conf)
        ns = '12345678-1234-5678-90ab-ba0987654321'
        bc.init_l3('tap0', ['2001:db8:a::123/64'], namespace=ns)
        self.assertFalse(self.ip_dev().addr.add.called)

    def test_l3_init_with_duplicated_ipv6_uncompact(self):
        addresses = [dict(scope='global',
                          dynamic=False,
                          cidr='2001:db8:a::123/64')]
        self.ip_dev().addr.list = mock.Mock(return_value=addresses)
        bc = BaseChild(self.conf)
        ns = '12345678-1234-5678-90ab-ba0987654321'
        bc.init_l3('tap0',
                   ['2001:db8:a:0000:0000:0000:0000:0123/64'],
                   namespace=ns)
        self.assertFalse(self.ip_dev().addr.add.called)

    def test_add_ipv6_addr(self):
        device_name = 'tap0'
        cidr = '2001:db8::/64'
        ns = '12345678-1234-5678-90ab-ba0987654321'
        bc = BaseChild(self.conf)

        bc.add_ipv6_addr(device_name, cidr, ns)

        self.ip_dev.assert_has_calls(
            [mock.call(device_name, namespace=ns),
             mock.call().addr.add(cidr, 'global')])

    def test_delete_ipv6_addr(self):
        device_name = 'tap0'
        cidr = '2001:db8::/64'
        ns = '12345678-1234-5678-90ab-ba0987654321'
        bc = BaseChild(self.conf)

        bc.delete_ipv6_addr(device_name, cidr, ns)

        self.ip_dev.assert_has_calls(
            [mock.call(device_name, namespace=ns),
             mock.call().delete_addr_and_conntrack_state(cidr)])

    def test_delete_ipv6_addr_with_prefix(self):
        device_name = 'tap0'
        prefix = '2001:db8::/48'
        in_cidr = '2001:db8::/64'
        out_cidr = '2001:db7::/64'
        ns = '12345678-1234-5678-90ab-ba0987654321'
        in_addresses = [dict(scope='global',
                        dynamic=False,
                        cidr=in_cidr)]
        out_addresses = [dict(scope='global',
                         dynamic=False,
                         cidr=out_cidr)]
        # Initially set the address list to be empty
        self.ip_dev().addr.list = mock.Mock(return_value=[])

        bc = BaseChild(self.conf)

        # Call delete_v6addr_with_prefix when the address list is empty
        bc.delete_ipv6_addr_with_prefix(device_name, prefix, ns)
        # Assert that delete isn't called
        self.assertFalse(self.ip_dev().delete_addr_and_conntrack_state.called)

        # Set the address list to contain only an address outside of the range
        # of the given prefix
        self.ip_dev().addr.list = mock.Mock(return_value=out_addresses)
        bc.delete_ipv6_addr_with_prefix(device_name, prefix, ns)
        # Assert that delete isn't called
        self.assertFalse(self.ip_dev().delete_addr_and_conntrack_state.called)

        # Set the address list to contain only an address inside of the range
        # of the given prefix
        self.ip_dev().addr.list = mock.Mock(return_value=in_addresses)
        bc.delete_ipv6_addr_with_prefix(device_name, prefix, ns)
        # Assert that delete is called
        self.ip_dev.assert_has_calls(
            [mock.call(device_name, namespace=ns),
             mock.call().addr.list(scope='global', filters=['permanent']),
             mock.call().delete_addr_and_conntrack_state(in_cidr)])

    def test_get_ipv6_llas(self):
        ns = '12345678-1234-5678-90ab-ba0987654321'
        addresses = [dict(scope='link',
                          dynamic=False,
                          cidr='fe80:cafe::/64')]
        self.ip_dev().addr.list = mock.Mock(return_value=addresses)
        device_name = self.ip_dev().name
        bc = BaseChild(self.conf)

        llas = bc.get_ipv6_llas(device_name, ns)

        self.assertEqual(addresses, llas)
        self.ip_dev.assert_has_calls(
            [mock.call(device_name, namespace=ns),
             mock.call().addr.list(scope='link', ip_version=6)])


class TestOVSInterfaceDriver(TestBase):

    def test_get_device_name(self):
        br = interface.OVSInterfaceDriver(self.conf)
        device_name = br.get_device_name(FakePort())
        self.assertEqual('tapabcdef01-12', device_name)

    def test_plug_no_ns(self):
        self._test_plug()

    def test_plug_with_ns(self):
        self._test_plug(namespace='01234567-1234-1234-99')

    def test_plug_alt_bridge(self):
        self._test_plug(bridge='br-foo')

    def test_plug_configured_bridge(self):
        br = 'br-v'
        self.conf.set_override('ovs_use_veth', False)
        self.conf.set_override('ovs_integration_bridge', br)
        self.assertEqual(self.conf.ovs_integration_bridge, br)

        def device_exists(dev, namespace=None):
            return dev == br

        ovs = interface.OVSInterfaceDriver(self.conf)
        with mock.patch.object(ovs, '_ovs_add_port') as add_port:
            self.device_exists.side_effect = device_exists
            ovs.plug('01234567-1234-1234-99',
                     'port-1234',
                     'tap0',
                     'aa:bb:cc:dd:ee:ff',
                     bridge=None,
                     namespace=None)

        add_port.assert_called_once_with('br-v',
                                         'tap0',
                                         'port-1234',
                                         'aa:bb:cc:dd:ee:ff',
                                         internal=True)

    def _test_plug(self, bridge=None, namespace=None):
        with mock.patch('neutron.agent.ovsdb.native.connection.'
                        'Connection.start'):
            if not bridge:
                bridge = 'br-int'

            def device_exists(dev, namespace=None):
                return dev == bridge

            with mock.patch.object(ovs_lib.OVSBridge,
                                   'replace_port') as replace:
                ovs = interface.OVSInterfaceDriver(self.conf)
                self.device_exists.side_effect = device_exists
                ovs.plug('01234567-1234-1234-99',
                         'port-1234',
                         'tap0',
                         'aa:bb:cc:dd:ee:ff',
                         bridge=bridge,
                         namespace=namespace,
                         mtu=9000)
                replace.assert_called_once_with(
                    'tap0',
                    ('type', 'internal'),
                    ('external_ids', {
                        'iface-id': 'port-1234',
                        'iface-status': 'active',
                        'attached-mac': 'aa:bb:cc:dd:ee:ff'}))

            expected = [
                mock.call(),
                mock.call().device('tap0'),
                mock.call().device().link.set_address('aa:bb:cc:dd:ee:ff')]
            if namespace:
                expected.extend(
                    [mock.call().ensure_namespace(namespace),
                     mock.call().ensure_namespace().add_device_to_namespace(
                         mock.ANY)])
            expected.extend([
                mock.call().device().link.set_mtu(9000),
                mock.call().device().link.set_up(),
            ])

            self.ip.assert_has_calls(expected)

    def test_unplug(self, bridge=None):
        if not bridge:
            bridge = 'br-int'
        with mock.patch('neutron.agent.common.ovs_lib.OVSBridge') as ovs_br:
            ovs = interface.OVSInterfaceDriver(self.conf)
            ovs.unplug('tap0')
            ovs_br.assert_has_calls([mock.call(bridge),
                                     mock.call().delete_port('tap0')])


class TestOVSInterfaceDriverWithVeth(TestOVSInterfaceDriver):

    def setUp(self):
        super(TestOVSInterfaceDriverWithVeth, self).setUp()
        self.conf.set_override('ovs_use_veth', True)

    def test_get_device_name(self):
        br = interface.OVSInterfaceDriver(self.conf)
        device_name = br.get_device_name(FakePort())
        self.assertEqual('ns-abcdef01-12', device_name)

    def test_plug_with_prefix(self):
        self._test_plug(devname='qr-0', prefix='qr-')

    def _test_plug(self, devname=None, bridge=None, namespace=None,
                   prefix=None):
        with mock.patch('neutron.agent.ovsdb.native.connection.'
                        'Connection.start'):

            if not devname:
                devname = 'ns-0'
            if not bridge:
                bridge = 'br-int'

            def device_exists(dev, namespace=None):
                return dev == bridge

            ovs = interface.OVSInterfaceDriver(self.conf)
            self.device_exists.side_effect = device_exists

            root_dev = mock.Mock()
            ns_dev = mock.Mock()
            self.ip().add_veth = mock.Mock(return_value=(root_dev, ns_dev))
            expected = [mock.call(),
                        mock.call().add_veth('tap0', devname,
                                             namespace2=namespace)]

            with mock.patch.object(ovs_lib.OVSBridge,
                                   'replace_port') as replace:
                ovs.plug('01234567-1234-1234-99',
                         'port-1234',
                         devname,
                         'aa:bb:cc:dd:ee:ff',
                         bridge=bridge,
                         namespace=namespace,
                         prefix=prefix,
                         mtu=9000)
                replace.assert_called_once_with(
                    'tap0',
                    ('external_ids', {
                        'iface-id': 'port-1234',
                        'iface-status': 'active',
                        'attached-mac': 'aa:bb:cc:dd:ee:ff'}))

            ns_dev.assert_has_calls(
                [mock.call.link.set_address('aa:bb:cc:dd:ee:ff')])
            ns_dev.assert_has_calls([mock.call.link.set_mtu(9000)])
            root_dev.assert_has_calls([mock.call.link.set_mtu(9000)])

            self.ip.assert_has_calls(expected)
            root_dev.assert_has_calls([mock.call.link.set_up()])
            ns_dev.assert_has_calls([mock.call.link.set_up()])

    def test_unplug(self, bridge=None):
        if not bridge:
            bridge = 'br-int'
        with mock.patch('neutron.agent.common.ovs_lib.OVSBridge') as ovs_br:
            ovs = interface.OVSInterfaceDriver(self.conf)
            ovs.unplug('ns-0', bridge=bridge)
            ovs_br.assert_has_calls([mock.call(bridge),
                                     mock.call().delete_port('tap0')])
        self.ip_dev.assert_has_calls([mock.call('ns-0', namespace=None),
                                      mock.call().link.delete()])


class TestBridgeInterfaceDriver(TestBase):
    def test_get_device_name(self):
        br = interface.BridgeInterfaceDriver(self.conf)
        device_name = br.get_device_name(FakePort())
        self.assertEqual('ns-abcdef01-12', device_name)

    def test_plug_no_ns(self):
        self._test_plug()

    def test_plug_with_ns(self):
        self._test_plug(namespace='01234567-1234-1234-99')

    def _test_plug(self, namespace=None):
        def device_exists(device, namespace=None):
            return device.startswith('brq')

        root_veth = mock.Mock()
        ns_veth = mock.Mock()

        self.ip().add_veth = mock.Mock(return_value=(root_veth, ns_veth))

        self.device_exists.side_effect = device_exists
        br = interface.BridgeInterfaceDriver(self.conf)
        mac_address = 'aa:bb:cc:dd:ee:ff'
        br.plug('01234567-1234-1234-99',
                'port-1234',
                'ns-0',
                mac_address,
                namespace=namespace,
                mtu=9000)

        ip_calls = [mock.call(),
                    mock.call().add_veth('tap0', 'ns-0', namespace2=namespace)]
        ns_veth.assert_has_calls([mock.call.link.set_address(mac_address)])
        ns_veth.assert_has_calls([mock.call.link.set_mtu(9000)])
        root_veth.assert_has_calls([mock.call.link.set_mtu(9000)])

        self.ip.assert_has_calls(ip_calls)

        root_veth.assert_has_calls([mock.call.link.set_up()])
        ns_veth.assert_has_calls([mock.call.link.set_up()])

    def test_plug_dev_exists(self):
        self.device_exists.return_value = True
        with mock.patch('neutron.agent.linux.interface.LOG.info') as log:
            br = interface.BridgeInterfaceDriver(self.conf)
            br.plug('01234567-1234-1234-99',
                    'port-1234',
                    'tap0',
                    'aa:bb:cc:dd:ee:ff')
            self.assertFalse(self.ip_dev.called)
            self.assertEqual(log.call_count, 1)

    def test_unplug_no_device(self):
        self.device_exists.return_value = False
        self.ip_dev().link.delete.side_effect = RuntimeError
        with mock.patch('neutron.agent.linux.interface.LOG') as log:
            br = interface.BridgeInterfaceDriver(self.conf)
            br.unplug('tap0')
            [mock.call(), mock.call('tap0'), mock.call().link.delete()]
            self.assertEqual(log.error.call_count, 1)

    def test_unplug(self):
        self.device_exists.return_value = True
        with mock.patch('neutron.agent.linux.interface.LOG.debug') as log:
            br = interface.BridgeInterfaceDriver(self.conf)
            br.unplug('tap0')
            self.assertEqual(log.call_count, 1)

        self.ip_dev.assert_has_calls([mock.call('tap0', namespace=None),
                                      mock.call().link.delete()])


class TestIVSInterfaceDriver(TestBase):

    def setUp(self):
        super(TestIVSInterfaceDriver, self).setUp()

    def test_get_device_name(self):
        br = interface.IVSInterfaceDriver(self.conf)
        device_name = br.get_device_name(FakePort())
        self.assertEqual('ns-abcdef01-12', device_name)

    def test_plug_with_prefix(self):
        self._test_plug(devname='qr-0', prefix='qr-')

    def _test_plug(self, devname=None, namespace=None, prefix=None):

        if not devname:
            devname = 'ns-0'

        def device_exists(dev, namespace=None):
            return dev == 'indigo'

        ivs = interface.IVSInterfaceDriver(self.conf)
        self.device_exists.side_effect = device_exists

        root_dev = mock.Mock()
        _ns_dev = mock.Mock()
        ns_dev = mock.Mock()
        self.ip().add_veth = mock.Mock(return_value=(root_dev, _ns_dev))
        self.ip().device = mock.Mock(return_value=(ns_dev))
        expected = [mock.call(), mock.call().add_veth('tap0', devname),
                    mock.call().device(devname)]

        ivsctl_cmd = ['ivs-ctl', 'add-port', 'tap0']

        with mock.patch.object(utils, 'execute') as execute:
            ivs.plug('01234567-1234-1234-99',
                     'port-1234',
                     devname,
                     'aa:bb:cc:dd:ee:ff',
                     namespace=namespace,
                     prefix=prefix,
                     mtu=9000)
            execute.assert_called_once_with(ivsctl_cmd, run_as_root=True)

        ns_dev.assert_has_calls(
            [mock.call.link.set_address('aa:bb:cc:dd:ee:ff')])
        ns_dev.assert_has_calls([mock.call.link.set_mtu(9000)])
        root_dev.assert_has_calls([mock.call.link.set_mtu(9000)])
        if namespace:
            expected.extend(
                [mock.call().ensure_namespace(namespace),
                 mock.call().ensure_namespace().add_device_to_namespace(
                     mock.ANY)])

        self.ip.assert_has_calls(expected)
        root_dev.assert_has_calls([mock.call.link.set_up()])
        ns_dev.assert_has_calls([mock.call.link.set_up()])

    def test_plug_namespace(self):
        self._test_plug(namespace='mynamespace')

    def test_unplug(self):
        ivs = interface.IVSInterfaceDriver(self.conf)
        ivsctl_cmd = ['ivs-ctl', 'del-port', 'tap0']
        with mock.patch.object(utils, 'execute') as execute:
            ivs.unplug('ns-0')
            execute.assert_called_once_with(ivsctl_cmd, run_as_root=True)
            self.ip_dev.assert_has_calls([mock.call('ns-0', namespace=None),
                                          mock.call().link.delete()])
