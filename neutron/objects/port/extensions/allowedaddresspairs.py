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
from neutron.db.models import allowed_address_pair as models
from neutron.objects import base
from neutron.objects import common_types


@obj_base.VersionedObjectRegistry.register
class AllowedAddressPair(base.NeutronDbObject):
    # Version 1.0: Initial version
    VERSION = '1.0'

    db_model = models.AllowedAddressPair

    primary_keys = ['port_id', 'mac_address', 'ip_address']

    fields = {
        'port_id': obj_fields.UUIDField(),
        'mac_address': common_types.MACAddressField(),
        'ip_address': common_types.IPNetworkField(),
    }

    # TODO(mhickey): get rid of it once we switch the db model to using
    # custom types.
    @classmethod
    def modify_fields_to_db(cls, fields):
        result = super(AllowedAddressPair, cls).modify_fields_to_db(fields)
        if 'ip_address' in result:
            result['ip_address'] = cls.filter_to_str(result['ip_address'])
        if 'mac_address' in result:
            result['mac_address'] = cls.filter_to_str(result['mac_address'])
        return result

    # TODO(mhickey): get rid of it once we switch the db model to using
    # custom types.
    @classmethod
    def modify_fields_from_db(cls, db_obj):
        fields = super(AllowedAddressPair, cls).modify_fields_from_db(db_obj)
        if 'ip_address' in fields:
            # retain string format as stored in the database
            fields['ip_address'] = utils.AuthenticIPNetwork(
                fields['ip_address'])
        if 'mac_address' in fields:
            # retain string format as stored in the database
            fields['mac_address'] = utils.AuthenticEUI(
                fields['mac_address'])
        return fields
