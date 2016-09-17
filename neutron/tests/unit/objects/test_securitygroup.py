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

import itertools

from neutron.objects import securitygroup
from neutron.tests.unit.objects import test_base
from neutron.tests.unit import testlib_api


class SecurityGroupIfaceObjTestCase(test_base.BaseObjectIfaceTestCase):

    _test_class = securitygroup.SecurityGroup


class SecurityGroupDbObjTestCase(test_base.BaseDbObjectTestCase,
                                 testlib_api.SqlTestCase):

    _test_class = securitygroup.SecurityGroup

    def setUp(self):
        super(SecurityGroupDbObjTestCase, self).setUp()
        # TODO(ihrachys): consider refactoring base test class to set None for
        # all nullable fields
        for db_obj in self.db_objs:
            for rule in db_obj['rules']:
                # we either make it null, or create remote groups for each rule
                # generated; we picked the former here
                rule['remote_group_id'] = None

    def test_is_default_True(self):
        fields = self.obj_fields[0].copy()
        sg_obj = self._make_object(fields)
        sg_obj.is_default = True
        sg_obj.create()

        default_sg_obj = securitygroup._DefaultSecurityGroup.get_object(
            self.context,
            project_id=sg_obj.project_id,
            security_group_id=sg_obj.id)
        self.assertIsNotNone(default_sg_obj)

        sg_obj = securitygroup.SecurityGroup.get_object(
            self.context,
            id=sg_obj.id,
            project_id=sg_obj.project_id
        )
        self.assertTrue(sg_obj.is_default)

    def test_is_default_False(self):
        fields = self.obj_fields[0].copy()
        sg_obj = self._make_object(fields)
        sg_obj.is_default = False
        sg_obj.create()

        default_sg_obj = securitygroup._DefaultSecurityGroup.get_object(
            self.context,
            project_id=sg_obj.project_id,
            security_group_id=sg_obj.id)
        self.assertIsNone(default_sg_obj)

        sg_obj = securitygroup.SecurityGroup.get_object(
            self.context,
            id=sg_obj.id,
            project_id=sg_obj.project_id
        )
        self.assertFalse(sg_obj.is_default)

    def test_get_object_filter_by_is_default(self):
        fields = self.obj_fields[0].copy()
        sg_obj = self._make_object(fields)
        sg_obj.is_default = True
        sg_obj.create()

        listed_obj = securitygroup.SecurityGroup.get_object(
            self.context,
            id=sg_obj.id,
            project_id=sg_obj.project_id,
            is_default=True
        )
        self.assertIsNotNone(listed_obj)
        self.assertEqual(sg_obj, listed_obj)

    def test_get_objects_queries_constant(self):
        # TODO(electrocucaracha) SecurityGroup is using SecurityGroupRule
        # object to reload rules, which costs extra SQL query each time
        # _load_is_default are called in get_object(s). SecurityGroup has
        # defined relationship for SecurityGroupRules, so it should be possible
        # to reuse side loaded values fo this. To be reworked in follow-up
        # patch.
        pass


class DefaultSecurityGroupIfaceObjTestCase(test_base.BaseObjectIfaceTestCase):

    _test_class = securitygroup._DefaultSecurityGroup


class DefaultSecurityGroupDbObjTestCase(test_base.BaseDbObjectTestCase,
                                        testlib_api.SqlTestCase):

    _test_class = securitygroup._DefaultSecurityGroup

    def setUp(self):
        super(DefaultSecurityGroupDbObjTestCase, self).setUp()
        sg_db_obj = self.get_random_fields(securitygroup.SecurityGroup)
        sg_fields = securitygroup.SecurityGroup.modify_fields_from_db(
            sg_db_obj)
        self.sg_obj = securitygroup.SecurityGroup(
            self.context, **test_base.remove_timestamps_from_fields(sg_fields))
        self.sg_obj.create()
        for obj in itertools.chain(self.db_objs, self.obj_fields):
            obj['security_group_id'] = self.sg_obj['id']


class SecurityGroupRuleIfaceObjTestCase(test_base.BaseObjectIfaceTestCase):

    _test_class = securitygroup.SecurityGroupRule


class SecurityGroupRuleDbObjTestCase(test_base.BaseDbObjectTestCase,
                                     testlib_api.SqlTestCase):

    _test_class = securitygroup.SecurityGroupRule

    def setUp(self):
        super(SecurityGroupRuleDbObjTestCase, self).setUp()
        sg_db_obj = self.get_random_fields(securitygroup.SecurityGroup)
        sg_fields = securitygroup.SecurityGroup.modify_fields_from_db(
            sg_db_obj)
        self.sg_obj = securitygroup.SecurityGroup(
            self.context, **test_base.remove_timestamps_from_fields(sg_fields))
        self.sg_obj.create()
        for obj in itertools.chain(self.db_objs, self.obj_fields):
            obj['security_group_id'] = self.sg_obj['id']
            obj['remote_group_id'] = self.sg_obj['id']
