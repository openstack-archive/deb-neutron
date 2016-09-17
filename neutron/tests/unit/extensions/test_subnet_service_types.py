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

import webob.exc

from neutron.db import db_base_plugin_v2
from neutron.extensions import subnet_service_types
from neutron.tests.unit.db import test_db_base_plugin_v2


class SubnetServiceTypesExtensionManager(object):

    def get_resources(self):
        return []

    def get_actions(self):
        return []

    def get_request_extensions(self):
        return []

    def get_extended_resources(self, version):
        return subnet_service_types.get_extended_resources(version)


class SubnetServiceTypesExtensionTestPlugin(
        db_base_plugin_v2.NeutronDbPluginV2):
    """Test plugin to mixin the subnet service_types extension.
    """

    supported_extension_aliases = ["subnet-service-types"]


class SubnetServiceTypesExtensionTestCase(
         test_db_base_plugin_v2.NeutronDbPluginV2TestCase):
    """Test API extension subnet_service_types attributes.
    """
    CIDRS = ['10.0.0.0/8', '20.0.0.0/8', '30.0.0.0/8']
    IP_VERSION = 4

    def setUp(self):
        plugin = ('neutron.tests.unit.extensions.test_subnet_service_types.' +
                  'SubnetServiceTypesExtensionTestPlugin')
        ext_mgr = SubnetServiceTypesExtensionManager()
        super(SubnetServiceTypesExtensionTestCase,
              self).setUp(plugin=plugin, ext_mgr=ext_mgr)

    def _create_service_subnet(self, service_types=None, cidr=None,
                               network=None):
        if not network:
            with self.network() as network:
                pass
        network = network['network']
        if not cidr:
            cidr = self.CIDRS[0]
        args = {'net_id': network['id'],
                'tenant_id': network['tenant_id'],
                'cidr': cidr,
                'ip_version': self.IP_VERSION}
        if service_types:
            args['service_types'] = service_types
        return self._create_subnet(self.fmt, **args)

    def _test_create_subnet(self, service_types, expect_fail=False):
        res = self._create_service_subnet(service_types)
        if expect_fail:
            self.assertEqual(webob.exc.HTTPClientError.code,
                             res.status_int)
        else:
            subnet = self.deserialize('json', res)
            subnet = subnet['subnet']
            self.assertEqual(len(service_types),
                             len(subnet['service_types']))
            for service in service_types:
                self.assertIn(service, subnet['service_types'])

    def test_create_subnet_blank_type(self):
        self._test_create_subnet([])

    def test_create_subnet_bar_type(self):
        self._test_create_subnet(['network:bar'])

    def test_create_subnet_foo_type(self):
        self._test_create_subnet(['compute:foo'])

    def test_create_subnet_bar_and_foo_type(self):
        self._test_create_subnet(['network:bar', 'compute:foo'])

    def test_create_subnet_invalid_type(self):
        self._test_create_subnet(['foo'], expect_fail=True)

    def test_create_subnet_no_type(self):
        res = self._create_service_subnet()
        subnet = self.deserialize('json', res)
        subnet = subnet['subnet']
        self.assertFalse(subnet['service_types'])

    def _test_update_subnet(self, subnet, service_types, expect_fail=False):
        data = {'subnet': {'service_types': service_types}}
        req = self.new_update_request('subnets', data, subnet['id'])
        res = self.deserialize(self.fmt, req.get_response(self.api))
        if expect_fail:
            self.assertEqual('InvalidSubnetServiceType',
                             res['NeutronError']['type'])
        else:
            subnet = res['subnet']
            self.assertEqual(len(service_types),
                             len(subnet['service_types']))
            for service in service_types:
                self.assertIn(service, subnet['service_types'])

    def test_update_subnet_zero_to_one(self):
        service_types = ['network:foo']
        # Create a subnet with no service type
        res = self._create_service_subnet()
        subnet = self.deserialize('json', res)['subnet']
        # Update it with a single service type
        self._test_update_subnet(subnet, service_types)

    def test_update_subnet_one_to_two(self):
        service_types = ['network:foo']
        # Create a subnet with one service type
        res = self._create_service_subnet(service_types)
        subnet = self.deserialize('json', res)['subnet']
        # Update it with two service types
        service_types.append('compute:bar')
        self._test_update_subnet(subnet, service_types)

    def test_update_subnet_two_to_one(self):
        service_types = ['network:foo', 'compute:bar']
        # Create a subnet with two service types
        res = self._create_service_subnet(service_types)
        subnet = self.deserialize('json', res)['subnet']
        # Update it with one service type
        service_types = ['network:foo']
        self._test_update_subnet(subnet, service_types)

    def test_update_subnet_one_to_zero(self):
        service_types = ['network:foo']
        # Create a subnet with one service type
        res = self._create_service_subnet(service_types)
        subnet = self.deserialize('json', res)['subnet']
        # Update it with zero service types
        service_types = []
        self._test_update_subnet(subnet, service_types)

    def test_update_subnet_invalid_type(self):
        service_types = ['foo']
        # Create a subnet with no service type
        res = self._create_service_subnet()
        subnet = self.deserialize('json', res)['subnet']
        # Update it with an invalid service type
        self._test_update_subnet(subnet, service_types, expect_fail=True)

    def _assert_port_res(self, port, service_type, subnet, fallback,
                         error='IpAddressGenerationFailureNoMatchingSubnet'):
        res = self.deserialize('json', port)
        if fallback:
            port = res['port']
            self.assertEqual(1, len(port['fixed_ips']))
            self.assertEqual(service_type, port['device_owner'])
            self.assertEqual(subnet['id'], port['fixed_ips'][0]['subnet_id'])
        else:
            self.assertEqual(error, res['NeutronError']['type'])

    def test_create_port_with_matching_service_type(self):
        with self.network() as network:
            pass
        matching_type = 'network:foo'
        non_matching_type = 'network:bar'
        # Create a subnet with no service types
        self._create_service_subnet(network=network)
        # Create a subnet with a non-matching service type
        self._create_service_subnet([non_matching_type],
                                    cidr=self.CIDRS[2],
                                    network=network)
        # Create a subnet with a service type to match the port device owner
        res = self._create_service_subnet([matching_type],
                                          cidr=self.CIDRS[1],
                                          network=network)
        service_subnet = self.deserialize('json', res)['subnet']
        # Create a port with device owner matching the correct service subnet
        network = network['network']
        port = self._create_port(self.fmt,
                                 net_id=network['id'],
                                 tenant_id=network['tenant_id'],
                                 device_owner=matching_type)
        self._assert_port_res(port, matching_type, service_subnet, True)

    def test_create_port_without_matching_service_type(self, fallback=True):
        with self.network() as network:
            pass
        subnet = ''
        matching_type = 'compute:foo'
        non_matching_type = 'network:foo'
        if fallback:
            # Create a subnet with no service types
            res = self._create_service_subnet(network=network)
            subnet = self.deserialize('json', res)['subnet']
        # Create a subnet with a non-matching service type
        self._create_service_subnet([non_matching_type],
                                    cidr=self.CIDRS[1],
                                    network=network)
        # Create a port with device owner not matching the service subnet
        network = network['network']
        port = self._create_port(self.fmt,
                                 net_id=network['id'],
                                 tenant_id=network['tenant_id'],
                                 device_owner=matching_type)
        self._assert_port_res(port, matching_type, subnet, fallback)

    def test_create_port_without_matching_service_type_no_fallback(self):
        self.test_create_port_without_matching_service_type(fallback=False)

    def test_create_port_no_device_owner(self, fallback=True):
        with self.network() as network:
            pass
        subnet = ''
        service_type = 'compute:foo'
        if fallback:
            # Create a subnet with no service types
            res = self._create_service_subnet(network=network)
            subnet = self.deserialize('json', res)['subnet']
        # Create a subnet with a service_type
        self._create_service_subnet([service_type],
                                    cidr=self.CIDRS[1],
                                    network=network)
        # Create a port without a device owner
        network = network['network']
        port = self._create_port(self.fmt,
                                 net_id=network['id'],
                                 tenant_id=network['tenant_id'])
        self._assert_port_res(port, '', subnet, fallback)

    def test_create_port_no_device_owner_no_fallback(self):
        self.test_create_port_no_device_owner(fallback=False)

    def test_create_port_exhausted_subnet(self, fallback=True):
        with self.network() as network:
            pass
        subnet = ''
        service_type = 'compute:foo'
        if fallback:
            # Create a subnet with no service types
            res = self._create_service_subnet(network=network)
            subnet = self.deserialize('json', res)['subnet']
        # Create a subnet with a service_type
        res = self._create_service_subnet([service_type],
                                          cidr=self.CIDRS[1],
                                          network=network)
        service_subnet = self.deserialize('json', res)['subnet']
        # Update the service subnet with empty allocation pools
        data = {'subnet': {'allocation_pools': []}}
        req = self.new_update_request('subnets', data, service_subnet['id'])
        res = self.deserialize(self.fmt, req.get_response(self.api))
        # Create a port with a matching device owner
        network = network['network']
        port = self._create_port(self.fmt,
                                 net_id=network['id'],
                                 tenant_id=network['tenant_id'],
                                 device_owner=service_type)
        self._assert_port_res(port, service_type, subnet, fallback,
                              error='IpAddressGenerationFailure')

    def test_create_port_exhausted_subnet_no_fallback(self):
        self.test_create_port_exhausted_subnet(fallback=False)


class SubnetServiceTypesExtensionTestCasev6(
        SubnetServiceTypesExtensionTestCase):
    CIDRS = ['2001:db8:2::/64', '2001:db8:3::/64', '2001:db8:4::/64']
    IP_VERSION = 6
