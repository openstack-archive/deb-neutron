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

from neutron.api import extensions


REVISION = 'revision'
REVISION_BODY = {
    REVISION: {'allow_post': False, 'allow_put': False,
               'is_visible': True, 'default': None},
}
RESOURCES = ('security_group_rules', 'security_groups', 'ports', 'subnets',
             'networks', 'routers', 'floatingips', 'subnetpools')
EXTENDED_ATTRIBUTES_2_0 = {}
for resource in RESOURCES:
    EXTENDED_ATTRIBUTES_2_0[resource] = REVISION_BODY


class Revisions(extensions.ExtensionDescriptor):
    """Extension to expose revision number of standard attr resources."""

    @classmethod
    def get_name(cls):
        return "Resource revision numbers"

    @classmethod
    def get_alias(cls):
        return "revisions"

    @classmethod
    def get_description(cls):
        return ("This extension will display the revision number of neutron "
                "resources.")

    @classmethod
    def get_updated(cls):
        return "2016-04-11T10:00:00-00:00"

    def get_extended_resources(self, version):
        if version == "2.0":
            return EXTENDED_ATTRIBUTES_2_0
        else:
            return {}
