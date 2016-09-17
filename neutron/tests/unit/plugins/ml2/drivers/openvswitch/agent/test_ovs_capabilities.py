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

from neutron.callbacks import events
from neutron.plugins.ml2.drivers.openvswitch.agent import ovs_capabilities
from neutron.services.trunk.drivers.openvswitch.agent import driver
from neutron.tests import base
from neutron_lib import constants


class CapabilitiesTest(base.BaseTestCase):

    @mock.patch("neutron.callbacks.manager.CallbacksManager.subscribe")
    def test_register(self, mocked_subscribe):
        ovs_capabilities.register()
        mocked_subscribe.assert_called_with(driver.init_handler,
                                            constants.AGENT_TYPE_OVS,
                                            events.AFTER_INIT)
