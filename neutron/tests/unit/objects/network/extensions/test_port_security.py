# Copyright 2013 VMware, Inc.  All rights reserved.
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

from neutron.objects.network.extensions import port_security
from neutron.tests.unit.objects import test_base as obj_test_base
from neutron.tests.unit import testlib_api


class NetworkPortSecurityIfaceObjTestCase(
    obj_test_base.BaseObjectIfaceTestCase):

    _test_class = port_security.NetworkPortSecurity


class NetworkPortSecurityDbObjTestCase(obj_test_base.BaseDbObjectTestCase,
                                       testlib_api.SqlTestCase):

    _test_class = port_security.NetworkPortSecurity

    def setUp(self):
        super(NetworkPortSecurityDbObjTestCase, self).setUp()
        for db_obj, obj_field in zip(self.db_objs, self.obj_fields):
            network = self._create_network()
            db_obj['network_id'] = network['id']
            obj_field['id'] = network['id']
