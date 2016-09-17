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

from neutron.db import common_db_mixin
from neutron.db import portsecurity_db_common as pdc
from neutron.extensions import portsecurity as psec
from neutron.objects import base as objects_base
from neutron.objects.network.extensions import port_security as n_ps
from neutron.objects.port.extensions import port_security as p_ps
from neutron.tests import base


class FakePlugin(pdc.PortSecurityDbCommon, common_db_mixin.CommonDbMixin):
    pass


class PortSecurityDbCommonTestCase(base.BaseTestCase):

    def setUp(self):
        super(PortSecurityDbCommonTestCase, self).setUp()
        self.plugin = FakePlugin()

    def _test__get_security_binding_no_binding(self, getter):
        port_sec_enabled = True
        req = {psec.PORTSECURITY: port_sec_enabled}
        res = {}
        with mock.patch.object(
                objects_base.NeutronDbObject, 'get_object',
                return_value=None):
            val = getter(req, res)
        self.assertEqual(port_sec_enabled, val)

    def test__get_port_security_binding_no_binding(self):
        self._test__get_security_binding_no_binding(
            self.plugin._get_port_security_binding)

    def test__get_network_security_binding_no_binding(self):
        self._test__get_security_binding_no_binding(
            self.plugin._get_network_security_binding)

    def _test__process_security_update_no_binding(self, res_name, obj_cls,
                                                  updater):
        req = {psec.PORTSECURITY: False}
        res = {'id': 'fake-id'}
        context = mock.MagicMock()
        with mock.patch.object(
                self.plugin, '_process_port_security_create') as creator:
            with mock.patch.object(
                    objects_base.NeutronDbObject, 'get_object',
                    return_value=None):
                updater(context, req, res)
        creator.assert_called_with(context, obj_cls, res_name, req, res)

    def test__process_port_port_security_update_no_binding(self):
        self._test__process_security_update_no_binding(
            'port', p_ps.PortSecurity,
            self.plugin._process_port_port_security_update)

    def test__process_network_port_security_update_no_binding(self):
        self._test__process_security_update_no_binding(
            'network', n_ps.NetworkPortSecurity,
            self.plugin._process_network_port_security_update)

    def test__extend_port_security_dict_no_port_security(self):
        for db_data in ({'port_security': None, 'name': 'net1'}, {}):
            response_data = {}
            self.plugin._extend_port_security_dict(response_data, db_data)
            self.assertTrue(response_data[psec.PORTSECURITY])
