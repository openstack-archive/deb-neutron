# Copyright 2016 Hewlett Packard Enterprise Development LP
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import mock
from oslo_config import cfg
import oslo_messaging

from neutron.api.rpc.callbacks import resources
from neutron.api.rpc.handlers import resources_rpc
from neutron.services.trunk.rpc import agent
from neutron.tests import base


class TrunkSkeletonTest(base.BaseTestCase):
    # TODO(fitoduarte): add more test to improve coverage of module
    @mock.patch("neutron.api.rpc.callbacks.resource_manager."
                "ConsumerResourceCallbacksManager.register")
    @mock.patch("neutron.common.rpc.get_server")
    def test___init__(self, mocked_get_server, mocked_register):
        test_obj = agent.TrunkSkeleton()
        self.assertEqual(2, mocked_register.call_count)
        calls = [mock.call(test_obj.handle_trunks, resources.TRUNK),
                 mock.call(test_obj.handle_subports, resources.SUBPORT)]
        mocked_register.assert_has_calls(calls, any_order=True)

        # Test to see if the call to rpc.get_server has the correct
        # target and the correct endpoints
        topic = resources_rpc.resource_type_versioned_topic(resources.SUBPORT)
        subport_target = oslo_messaging.Target(
            topic=topic, server=cfg.CONF.host, fanout=True)
        topic = resources_rpc.resource_type_versioned_topic(resources.TRUNK)
        trunk_target = oslo_messaging.Target(
            topic=topic, server=cfg.CONF.host, fanout=True)
        calls = [mock.call(subport_target, mock.ANY),
                 mock.call(trunk_target, mock.ANY)]
        mocked_get_server.assert_has_calls(calls, any_order=True)
        self.assertIn("ResourcesPushRpcCallback",
                      str(mocked_get_server.call_args_list))
