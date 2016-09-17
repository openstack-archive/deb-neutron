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

from tempest.lib.common.utils import test_utils
from tempest.lib import exceptions as lib_exc
from tempest import test

from neutron.tests.tempest.api import base


def trunks_cleanup(client, trunks):
    for trunk in trunks:
        # NOTE(armax): deleting a trunk with subports is permitted, however
        # for testing purposes it is safer to be explicit and clean all the
        # resources associated with the trunk beforehand.
        subports = test_utils.call_and_ignore_notfound_exc(
            client.get_subports, trunk['id'])
        if subports:
            client.remove_subports(
                trunk['id'], subports['sub_ports'])
        test_utils.call_and_ignore_notfound_exc(
            client.delete_trunk, trunk['id'])


class TrunkTestJSONBase(base.BaseAdminNetworkTest):

    extension = 'trunk'

    def setUp(self):
        self.addCleanup(self.resource_cleanup)
        super(TrunkTestJSONBase, self).setUp()

    @classmethod
    def skip_checks(cls):
        super(TrunkTestJSONBase, cls).skip_checks()
        if not test.is_extension_enabled(cls.extension, 'network'):
            msg = "%s extension not enabled." % cls.extension
            raise cls.skipException(msg)

    @classmethod
    def resource_setup(cls):
        super(TrunkTestJSONBase, cls).resource_setup()
        cls.trunks = []

    @classmethod
    def resource_cleanup(cls):
        trunks_cleanup(cls.client, cls.trunks)
        super(TrunkTestJSONBase, cls).resource_cleanup()

    def _remove_timestamps(self, trunk):
        # Let's make sure these fields exist, but let's not
        # use them in the comparison, in case skews or
        # roundups get in the way.
        created_at = trunk.pop('created_at')
        updated_at = trunk.pop('updated_at')
        self.assertIsNotNone(created_at)
        self.assertIsNotNone(updated_at)

    def _create_trunk_with_network_and_parent(self, subports, **kwargs):
        network = self.create_network()
        parent_port = self.create_port(network)
        trunk = self.client.create_trunk(parent_port['id'], subports, **kwargs)
        self.trunks.append(trunk['trunk'])
        self._remove_timestamps(trunk['trunk'])
        return trunk

    def _show_trunk(self, trunk_id):
        trunk = self.client.show_trunk(trunk_id)
        self._remove_timestamps(trunk['trunk'])
        return trunk

    def _list_trunks(self):
        trunks = self.client.list_trunks()
        for t in trunks['trunks']:
            self._remove_timestamps(t)
        return trunks


class TrunkTestJSON(TrunkTestJSONBase):

    def _test_create_trunk(self, subports):
        trunk = self._create_trunk_with_network_and_parent(subports)
        observed_trunk = self._show_trunk(trunk['trunk']['id'])
        self.assertEqual(trunk, observed_trunk)

    @test.idempotent_id('e1a6355c-4768-41f3-9bf8-0f1d192bd501')
    def test_create_trunk_empty_subports_list(self):
        self._test_create_trunk([])

    @test.idempotent_id('382dfa39-ca03-4bd3-9a1c-91e36d2e3796')
    def test_create_trunk_subports_not_specified(self):
        self._test_create_trunk(None)

    @test.idempotent_id('7de46c22-e2b6-4959-ac5a-0e624632ab32')
    def test_create_show_delete_trunk(self):
        trunk = self._create_trunk_with_network_and_parent(None)
        trunk_id = trunk['trunk']['id']
        parent_port_id = trunk['trunk']['port_id']
        res = self._show_trunk(trunk_id)
        self.assertEqual(trunk_id, res['trunk']['id'])
        self.assertEqual(parent_port_id, res['trunk']['port_id'])
        self.client.delete_trunk(trunk_id)
        self.assertRaises(lib_exc.NotFound, self._show_trunk, trunk_id)

    @test.idempotent_id('4ce46c22-a2b6-4659-bc5a-0ef2463cab32')
    def test_create_update_trunk(self):
        trunk = self._create_trunk_with_network_and_parent(None)
        trunk_id = trunk['trunk']['id']
        res = self._show_trunk(trunk_id)
        self.assertTrue(res['trunk']['admin_state_up'])
        self.assertEqual("", res['trunk']['name'])
        self.assertEqual("", res['trunk']['description'])
        res = self.client.update_trunk(
            trunk_id, name='foo', admin_state_up=False)
        self.assertFalse(res['trunk']['admin_state_up'])
        self.assertEqual("foo", res['trunk']['name'])
        # enable the trunk so that it can be managed
        self.client.update_trunk(trunk_id, admin_state_up=True)

    @test.idempotent_id('5ff46c22-a2b6-5559-bc5a-0ef2463cab32')
    def test_create_update_trunk_with_description(self):
        trunk = self._create_trunk_with_network_and_parent(
            None, description="foo description")
        trunk_id = trunk['trunk']['id']
        self.assertEqual("foo description", trunk['trunk']['description'])
        trunk = self.client.update_trunk(trunk_id, description='')
        self.assertEqual('', trunk['trunk']['description'])

    @test.idempotent_id('73365f73-bed6-42cd-960b-ec04e0c99d85')
    def test_list_trunks(self):
        trunk1 = self._create_trunk_with_network_and_parent(None)
        trunk2 = self._create_trunk_with_network_and_parent(None)
        expected_trunks = {trunk1['trunk']['id']: trunk1['trunk'],
                           trunk2['trunk']['id']: trunk2['trunk']}
        trunk_list = self._list_trunks()['trunks']
        matched_trunks = [x for x in trunk_list if x['id'] in expected_trunks]
        self.assertEqual(2, len(matched_trunks))
        for trunk in matched_trunks:
            self.assertEqual(expected_trunks[trunk['id']], trunk)

    @test.idempotent_id('bb5fcead-09b5-484a-bbe6-46d1e06d6cc0')
    def test_add_subport(self):
        trunk = self._create_trunk_with_network_and_parent([])
        network = self.create_network()
        port = self.create_port(network)
        subports = [{'port_id': port['id'],
                     'segmentation_type': 'vlan',
                     'segmentation_id': 2}]
        self.client.add_subports(trunk['trunk']['id'], subports)
        trunk = self._show_trunk(trunk['trunk']['id'])
        observed_subports = trunk['trunk']['sub_ports']
        self.assertEqual(1, len(observed_subports))
        created_subport = observed_subports[0]
        self.assertEqual(subports[0], created_subport)

    @test.idempotent_id('ee5fcead-1abf-483a-bce6-43d1e06d6aa0')
    def test_delete_trunk_with_subport_is_allowed(self):
        network = self.create_network()
        port = self.create_port(network)
        subports = [{'port_id': port['id'],
                     'segmentation_type': 'vlan',
                     'segmentation_id': 2}]
        trunk = self._create_trunk_with_network_and_parent(subports)
        self.client.delete_trunk(trunk['trunk']['id'])

    @test.idempotent_id('96eea398-a03c-4c3e-a99e-864392c2ca53')
    def test_remove_subport(self):
        subport_parent1 = self.create_port(self.create_network())
        subport_parent2 = self.create_port(self.create_network())
        subports = [{'port_id': subport_parent1['id'],
                     'segmentation_type': 'vlan',
                     'segmentation_id': 2},
                    {'port_id': subport_parent2['id'],
                     'segmentation_type': 'vlan',
                     'segmentation_id': 4}]
        trunk = self._create_trunk_with_network_and_parent(subports)
        removed_subport = trunk['trunk']['sub_ports'][0]
        expected_subport = None

        for subport in subports:
            if subport['port_id'] != removed_subport['port_id']:
                expected_subport = subport
                break

        # Remove the subport and validate PUT response
        res = self.client.remove_subports(trunk['trunk']['id'],
                                          [removed_subport])
        self.assertEqual(1, len(res['sub_ports']))
        self.assertEqual(expected_subport, res['sub_ports'][0])

        # Validate the results of a subport list
        trunk = self._show_trunk(trunk['trunk']['id'])
        observed_subports = trunk['trunk']['sub_ports']
        self.assertEqual(1, len(observed_subports))
        self.assertEqual(expected_subport, observed_subports[0])

    @test.idempotent_id('bb5fcaad-09b5-484a-dde6-4cd1ea6d6ff0')
    def test_get_subports(self):
        network = self.create_network()
        port = self.create_port(network)
        subports = [{'port_id': port['id'],
                     'segmentation_type': 'vlan',
                     'segmentation_id': 2}]
        trunk = self._create_trunk_with_network_and_parent(subports)
        trunk = self.client.get_subports(trunk['trunk']['id'])
        observed_subports = trunk['sub_ports']
        self.assertEqual(1, len(observed_subports))


class TrunksSearchCriteriaTest(base.BaseSearchCriteriaTest):

    resource = 'trunk'

    @classmethod
    def skip_checks(cls):
        super(TrunksSearchCriteriaTest, cls).skip_checks()
        if not test.is_extension_enabled('trunk', 'network'):
            msg = "trunk extension not enabled."
            raise cls.skipException(msg)

    @classmethod
    def resource_setup(cls):
        super(TrunksSearchCriteriaTest, cls).resource_setup()
        cls.trunks = []
        net = cls.create_network(network_name='trunk-search-test-net')
        for name in cls.resource_names:
            parent_port = cls.create_port(net)
            trunk = cls.client.create_trunk(parent_port['id'], [], name=name)
            cls.trunks.append(trunk['trunk'])

    @classmethod
    def resource_cleanup(cls):
        trunks_cleanup(cls.client, cls.trunks)
        super(TrunksSearchCriteriaTest, cls).resource_cleanup()

    @test.idempotent_id('fab73df4-960a-4ae3-87d3-60992b8d3e2d')
    def test_list_sorts_asc(self):
        self._test_list_sorts_asc()

    @test.idempotent_id('a426671d-7270-430f-82ff-8f33eec93010')
    def test_list_sorts_desc(self):
        self._test_list_sorts_desc()

    @test.idempotent_id('b202fdc8-6616-45df-b6a0-463932de6f94')
    def test_list_pagination(self):
        self._test_list_pagination()

    @test.idempotent_id('c4723b8e-8186-4b9a-bf9e-57519967e048')
    def test_list_pagination_with_marker(self):
        self._test_list_pagination_with_marker()

    @test.idempotent_id('dcd02a7a-f07e-4d5e-b0ca-b58e48927a9b')
    def test_list_pagination_with_href_links(self):
        self._test_list_pagination_with_href_links()

    @test.idempotent_id('eafe7024-77ab-4cfe-824b-0b2bf4217727')
    def test_list_no_pagination_limit_0(self):
        self._test_list_no_pagination_limit_0()

    @test.idempotent_id('f8857391-dc44-40cc-89b7-2800402e03ce')
    def test_list_pagination_page_reverse_asc(self):
        self._test_list_pagination_page_reverse_asc()

    @test.idempotent_id('ae51e9c9-ceae-4ec0-afd4-147569247699')
    def test_list_pagination_page_reverse_desc(self):
        self._test_list_pagination_page_reverse_desc()

    @test.idempotent_id('b4293e59-d794-4a93-be09-38667199ef68')
    def test_list_pagination_page_reverse_with_href_links(self):
        self._test_list_pagination_page_reverse_with_href_links()
