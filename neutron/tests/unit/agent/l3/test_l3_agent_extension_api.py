# Copyright 2016 Comcast
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
#

import uuid

import mock

from neutron.agent.l3 import l3_agent_extension_api as l3_agent_api
from neutron.agent.l3 import router_info
from neutron.agent.linux import ip_lib
from neutron.tests import base


class TestL3AgentExtensionApi(base.BaseTestCase):

    def _prepare_router_data(self, ports=None):
        self.router_id = str(uuid.uuid4())
        self.project_id = str(uuid.uuid4())
        ri_kwargs = {'router': {'id': self.router_id,
                                'project_id': self.project_id},
                     'agent_conf': mock.ANY,
                     'interface_driver': mock.ANY,
                     'use_ipv6': mock.ANY}
        ri = router_info.RouterInfo(self.router_id, **ri_kwargs)
        ri.internal_ports = ports
        return {ri.router_id: ri}, ri

    def test_get_router_hosting_port_for_router_not_in_ns(self):
        port_ids = [1, 2]
        ports = [{'id': pid} for pid in port_ids]
        router_info, ri = self._prepare_router_data(ports)

        with mock.patch.object(ip_lib.IPWrapper,
                               'get_namespaces') as mock_get_namespaces:

            mock_get_namespaces.return_value = []
            api_object = l3_agent_api.L3AgentExtensionAPI(router_info)
            router = api_object.get_router_hosting_port(port_ids[0])

        mock_get_namespaces.assert_called_once_with()
        self.assertFalse(router)

    def test_get_router_hosting_port_for_router_in_ns(self):
        port_ids = [1, 2]
        ports = [{'id': pid} for pid in port_ids]
        router_info, ri = self._prepare_router_data(ports)

        with mock.patch.object(ip_lib.IPWrapper,
                               'get_namespaces') as mock_get_namespaces:
            mock_get_namespaces.return_value = [ri.ns_name]
            api_object = l3_agent_api.L3AgentExtensionAPI(router_info)
            router = api_object.get_router_hosting_port(port_ids[0])
            self.assertEqual(ri, router)

    def test_get_routers_in_project(self):
        router_info, ri = self._prepare_router_data()

        with mock.patch.object(ip_lib.IPWrapper,
                               'get_namespaces') as mock_get_namespaces:
            mock_get_namespaces.return_value = [ri.ns_name]
            api_object = l3_agent_api.L3AgentExtensionAPI(router_info)
            routers = api_object.get_routers_in_project(self.project_id)
            self.assertEqual([ri], routers)

    def test_is_router_in_namespace_for_in_ns(self):
        router_info, ri = self._prepare_router_data()

        with mock.patch.object(ip_lib.IPWrapper,
                               'get_namespaces') as mock_get_namespaces:
            mock_get_namespaces.return_value = [ri.ns_name]
            api_object = l3_agent_api.L3AgentExtensionAPI(router_info)
            router_in_ns = api_object.is_router_in_namespace(ri.router_id)
            self.assertTrue(router_in_ns)

    def test_is_router_in_namespace_for_not_in_ns(self):
        router_info, ri = self._prepare_router_data()

        with mock.patch.object(ip_lib.IPWrapper,
                               'get_namespaces') as mock_get_namespaces:
            mock_get_namespaces.return_value = [str(uuid.uuid4())]
            api_object = l3_agent_api.L3AgentExtensionAPI(router_info)
            router_in_ns = api_object.is_router_in_namespace(ri.router_id)
            self.assertFalse(router_in_ns)
