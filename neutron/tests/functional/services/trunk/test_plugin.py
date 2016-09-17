# (c) Copyright 2016 Hewlett Packard Enterprise Development LP
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from neutron.extensions import portbindings as pb
from neutron.services.trunk import plugin as trunk_plugin
from neutron.services.trunk import utils as trunk_utils
from neutron.tests.common import helpers
from neutron.tests.unit.plugins.ml2 import base as ml2_test_base


class TestTrunkServicePlugin(ml2_test_base.ML2TestFramework):

    def setUp(self):
        super(TestTrunkServicePlugin, self).setUp()
        self.trunk_plugin = trunk_plugin.TrunkPlugin()

    def test_ovs_bridge_name_set_when_trunk_bound(self):
        helpers.register_ovs_agent(host=helpers.HOST)
        with self.port() as port:
            trunk_port_id = port['port']['id']
            trunk_req = {'port_id': trunk_port_id,
                         'tenant_id': 'test_tenant',
                         'sub_ports': []}
            trunk_res = self.trunk_plugin.create_trunk(self.context,
                                                       {'trunk': trunk_req})
            port['port'][pb.HOST_ID] = helpers.HOST
            bound_port = self.core_plugin.update_port(self.context,
                                                      trunk_port_id, port)
            self.assertEqual(
                trunk_utils.gen_trunk_br_name(trunk_res['id']),
                bound_port[pb.VIF_DETAILS][pb.VIF_DETAILS_BRIDGE_NAME])

    def test_ovs_bridge_name_not_set_when_not_trunk(self):
        helpers.register_ovs_agent(host=helpers.HOST)
        with self.port() as port:
            port['port'][pb.HOST_ID] = helpers.HOST
            bound_port = self.core_plugin.update_port(self.context,
                                                      port['port']['id'], port)
            self.assertIsNone(
                bound_port[pb.VIF_DETAILS].get(pb.VIF_DETAILS_BRIDGE_NAME))
