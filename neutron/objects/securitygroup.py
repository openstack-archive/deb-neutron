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

from oslo_versionedobjects import base as obj_base
from oslo_versionedobjects import fields as obj_fields

from neutron.common import utils
from neutron.db import api as db_api
from neutron.db.models import securitygroup as sg_models
from neutron.objects import base
from neutron.objects import common_types


@obj_base.VersionedObjectRegistry.register
class SecurityGroup(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = sg_models.SecurityGroup

    fields = {
        'id': obj_fields.UUIDField(),
        'name': obj_fields.StringField(nullable=True),
        'project_id': obj_fields.StringField(nullable=True),
        'is_default': obj_fields.BooleanField(default=False),
        'rules': obj_fields.ListOfObjectsField(
            'SecurityGroupRule', nullable=True
        ),
        # NOTE(ihrachys): we don't include source_rules that is present in the
        # model until we realize it's actually needed
    }

    fields_no_update = ['project_id', 'is_default']

    synthetic_fields = ['is_default', 'rules']

    extra_filter_names = {'is_default'}

    def create(self):
        # save is_default before super() resets it to False
        is_default = self.is_default
        with db_api.autonested_transaction(self.obj_context.session):
            super(SecurityGroup, self).create()
            if is_default:
                default_group = _DefaultSecurityGroup(
                    self.obj_context,
                    project_id=self.project_id,
                    security_group_id=self.id)
                default_group.create()
                self.is_default = True
                self.obj_reset_changes(['is_default'])

    def from_db_object(self, *objs):
        super(SecurityGroup, self).from_db_object(*objs)
        for obj in objs:
            self._load_is_default(obj)

    def _load_is_default(self, db_obj):
        setattr(self, 'is_default', bool(db_obj.get('default_security_group')))
        self.obj_reset_changes(['is_default'])


@obj_base.VersionedObjectRegistry.register
class _DefaultSecurityGroup(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = sg_models.DefaultSecurityGroup

    fields = {
        'project_id': obj_fields.StringField(),
        'security_group_id': obj_fields.UUIDField(),
    }

    fields_no_update = ['security_group_id']

    primary_keys = ['project_id']


@obj_base.VersionedObjectRegistry.register
class SecurityGroupRule(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = sg_models.SecurityGroupRule

    fields = {
        'id': obj_fields.UUIDField(),
        'project_id': obj_fields.StringField(nullable=True),
        'security_group_id': obj_fields.UUIDField(),
        'remote_group_id': obj_fields.UUIDField(nullable=True),
        'direction': common_types.FlowDirectionEnumField(nullable=True),
        'ethertype': common_types.EtherTypeEnumField(nullable=True),
        'protocol': common_types.IpProtocolEnumField(nullable=True),
        'port_range_min': common_types.PortRangeField(nullable=True),
        'port_range_max': common_types.PortRangeField(nullable=True),
        'remote_ip_prefix': obj_fields.IPNetworkField(nullable=True),
    }

    foreign_keys = {'SecurityGroup': {'security_group_id': 'id'}}

    fields_no_update = ['project_id', 'security_group_id']

    # TODO(sayalilunkad): get rid of it once we switch the db model to using
    # custom types.
    @classmethod
    def modify_fields_to_db(cls, fields):
        result = super(SecurityGroupRule, cls).modify_fields_to_db(fields)
        remote_ip_prefix = result.get('remote_ip_prefix')
        if remote_ip_prefix:
            result['remote_ip_prefix'] = cls.filter_to_str(remote_ip_prefix)
        return result

    # TODO(sayalilunkad): get rid of it once we switch the db model to using
    # custom types.
    @classmethod
    def modify_fields_from_db(cls, db_obj):
        fields = super(SecurityGroupRule, cls).modify_fields_from_db(db_obj)
        if 'remote_ip_prefix' in fields:
            fields['remote_ip_prefix'] = (
                utils.AuthenticIPNetwork(fields['remote_ip_prefix']))
        return fields
