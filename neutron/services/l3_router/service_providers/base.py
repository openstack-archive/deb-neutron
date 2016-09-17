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

from neutron._i18n import _


class _FeatureFlag(object):

    def is_compatible(self, value):
        if value == self.requires:
            return True
        if value and self.supports:
            return True
        return False

    def __init__(self, supports, requires):
        self.supports = supports
        self.requires = requires
        if requires and not supports:
            raise RuntimeError(_("A driver can't require a feature and not "
                                 "support it."))

UNSUPPORTED = _FeatureFlag(supports=False, requires=False)
OPTIONAL = _FeatureFlag(supports=True, requires=False)
MANDATORY = _FeatureFlag(supports=True, requires=True)


class L3ServiceProvider(object):
    """Base class for L3 service provider drivers.

    On __init__ this will be given a handle to the l3 plugin. It is then the
    responsibility of the driver to subscribe to the events it is interested
    in (e.g. router_create, router_update, router_delete, etc).

    The 'ha' and 'distributed' attributes below are used to determine if a
    router request with the 'ha' or 'distributed' attribute can be supported
    by this particular driver. These attributes must be present.
    """

    ha_support = UNSUPPORTED
    distributed_support = UNSUPPORTED

    def __init__(self, l3plugin):
        self.l3plugin = l3plugin
