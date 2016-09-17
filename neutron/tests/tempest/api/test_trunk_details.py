# Copyright 2016 Hewlett Packard Enterprise Development Company LP
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

from tempest import test

from neutron.tests.tempest.api import test_trunk


class TestTrunkDetailsJSON(test_trunk.TrunkTestJSONBase):

    extension = 'trunk-details'

    @test.idempotent_id('f0bed24f-d36a-498b-b4e7-0d66e3fb7308')
    def test_port_resource_trunk_details_no_subports(self):
        trunk = self._create_trunk_with_network_and_parent([])
        port = self.client.show_port(trunk['trunk']['port_id'])
        expected_trunk_details = {'sub_ports': [],
                                  'trunk_id': trunk['trunk']['id']}
        observed_trunk_details = port['port'].get('trunk_details')
        self.assertIsNotNone(observed_trunk_details)
        self.assertEqual(expected_trunk_details,
                         observed_trunk_details)

    @test.idempotent_id('544bcaf2-86fb-4930-93ab-ece1c3cc33df')
    def test_port_resource_trunk_details_with_subport(self):
        subport_network = self.create_network()
        parent_port = self.create_port(subport_network)
        subport_data = {'port_id': parent_port['id'],
                        'segmentation_type': 'vlan',
                        'segmentation_id': 2}
        trunk = self._create_trunk_with_network_and_parent([subport_data])
        port = self.client.show_port(trunk['trunk']['port_id'])
        expected_trunk_details = {'sub_ports': [subport_data],
                                  'trunk_id': trunk['trunk']['id']}
        observed_trunk_details = port['port'].get('trunk_details')
        self.assertIsNotNone(observed_trunk_details)
        self.assertEqual(expected_trunk_details,
                         observed_trunk_details)

    @test.idempotent_id('fe6d865f-1d5c-432e-b65d-904157172f24')
    def test_port_resource_empty_trunk_details(self):
        network = self.create_network()
        port = self.create_port(network)
        port = self.client.show_port(port['id'])
        observed_trunk_details = port['port'].get('trunk_details')
        self.assertIsNone(observed_trunk_details)
